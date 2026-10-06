using System.Collections.ObjectModel;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using Microsoft.Win32;
using UniversalDownloader.Core.Cookies;
using UniversalDownloader.Core.Models;
using UniversalDownloader.Infrastructure.Api;
using UniversalDownloader.Infrastructure.Cache;

namespace UniversalDownloader.App.ViewModels;

/// <summary>Single source row in the sources list.</summary>
public sealed partial class SourceRowViewModel : ObservableObject
{
    public int Id { get; }
    public Source Model { get; }

    [ObservableProperty] private Platform _platform;
    [ObservableProperty] private string _inputValue = string.Empty;
    [ObservableProperty] private string _canonicalId = string.Empty;
    [ObservableProperty] private SourceStatus _status;
    [ObservableProperty] private bool _hasCookies;

    public string StatusText => Status.ToString();
    public bool CanPause => Status == SourceStatus.Active;
    public bool CanResume => Status == SourceStatus.Paused;

    /// <summary>v2.0: subtle indicator that the server holds cookies for this source.</summary>
    public string CookiesBadge => HasCookies ? "Cookies ✓" : string.Empty;

    public SourceRowViewModel(Source model)
    {
        Model = model;
        Id = model.Id;
        _platform = model.Platform;
        _inputValue = model.InputValue;
        _canonicalId = model.CanonicalId;
        _status = model.Status;
        _hasCookies = model.HasCookies;
    }

    public void SyncStatus(SourceStatus status)
    {
        Model.Status = status;
        Status = status;
        OnPropertyChanged(nameof(StatusText));
        OnPropertyChanged(nameof(CanPause));
        OnPropertyChanged(nameof(CanResume));
    }

    public void SetHasCookies(bool hasCookies)
    {
        Model.HasCookies = hasCookies;
        HasCookies = hasCookies;
        OnPropertyChanged(nameof(CookiesBadge));
    }
}

/// <summary>Source management (SRS FR-04): add / pause / resume / refresh / remove.</summary>
public sealed partial class SourcesViewModel : ObservableObject
{
    private readonly IApiClient _api;
    private readonly LocalCache _cache;

    [ObservableProperty]
    private ObservableCollection<SourceRowViewModel> _sources = new();

    public Array Platforms { get; } = Enum.GetValues(typeof(Platform));

    [ObservableProperty]
    private Platform _selectedPlatform = Platform.TikTok;

    [ObservableProperty]
    private string _newSourceInput = string.Empty;

    [ObservableProperty]
    private string _statusMessage = string.Empty;

    public SourcesViewModel(IApiClient api, LocalCache cache)
    {
        _api = api;
        _cache = cache;
    }

    [RelayCommand]
    public async Task RefreshAsync()
    {
        try
        {
            var dtos = await _api.ListSourcesAsync().ConfigureAwait(true);
            var models = dtos.Select(DtoMapper.ToSource).ToList();
            await _cache.UpsertSourcesAsync(models).ConfigureAwait(true);
            LoadRows(models);
            await RefreshCookiesStatusAsync().ConfigureAwait(true);
        }
        catch (Exception)
        {
            // Offline: fall back to the local cache.
            var cached = await _cache.GetSourcesAsync().ConfigureAwait(true);
            LoadRows(cached.ToList());
            StatusMessage = "Offline: showing cached sources.";
        }
    }

    /// <summary>
    /// v2.0: the sources list carries no cookie flags, so the presence-only
    /// GET /api/v1/sources/{id}/cookies status is fetched per source
    /// (best effort; cookie values are never returned).
    /// </summary>
    private async Task RefreshCookiesStatusAsync()
    {
        foreach (var row in Sources)
        {
            try
            {
                var status = await _api.GetSourceCookiesStatusAsync(row.Id).ConfigureAwait(true);
                row.SetHasCookies(status.Present);
            }
            catch (Exception)
            {
                // Best effort: keep the previous flag.
            }
        }
        await _cache.UpsertSourcesAsync(Sources.Select(r => r.Model)).ConfigureAwait(true);
    }

    [RelayCommand]
    private async Task AddSourceAsync()
    {
        if (string.IsNullOrWhiteSpace(NewSourceInput))
        {
            StatusMessage = "Enter a username, handle, or profile/channel URL.";
            return;
        }
        try
        {
            var dto = await _api.CreateSourceAsync(
                SelectedPlatform.ToString().ToLowerInvariant(), NewSourceInput.Trim()).ConfigureAwait(true);
            var model = DtoMapper.ToSource(dto);
            await _cache.UpsertSourcesAsync(new[] { model }).ConfigureAwait(true);
            Sources.Add(new SourceRowViewModel(model));
            NewSourceInput = string.Empty;
            StatusMessage = "Source added.";
        }
        catch (ApiException ex)
        {
            StatusMessage = $"Could not add source: {ex.Message}";
        }
    }

    [RelayCommand]
    private async Task PauseSourceAsync(SourceRowViewModel? row)
    {
        if (row is null) return;
        await UpdateSourceAsync(row, new UpdateSourceRequest("paused"), "Source paused.").ConfigureAwait(true);
    }

    [RelayCommand]
    private async Task ResumeSourceAsync(SourceRowViewModel? row)
    {
        if (row is null) return;
        await UpdateSourceAsync(row, new UpdateSourceRequest("active"), "Source resumed.").ConfigureAwait(true);
    }

    [RelayCommand]
    private async Task RefreshSourceAsync(SourceRowViewModel? row)
    {
        if (row is null) return;
        try
        {
            await _api.RequestDiscoveryAsync(row.Id).ConfigureAwait(true);
            StatusMessage = "Discovery refresh requested.";
        }
        catch (ApiException ex)
        {
            StatusMessage = $"Refresh failed: {ex.Message}";
        }
    }

    [RelayCommand]
    private async Task RemoveSourceAsync(SourceRowViewModel? row)
    {
        if (row is null) return;
        try
        {
            // Server applies the v1.1 removal policy (queued/batched/ready jobs
            // cancelled; completed history preserved).
            await _api.DeleteSourceAsync(row.Id).ConfigureAwait(true);
            Sources.Remove(row);
            StatusMessage = "Source removed.";
            await RefreshAsync().ConfigureAwait(true);
        }
        catch (ApiException ex)
        {
            StatusMessage = $"Remove failed: {ex.Message}";
        }
    }

    /// <summary>v2.0: uploads a cookies.txt for a source (bot-check fallback).</summary>
    [RelayCommand]
    private async Task UploadSourceCookiesAsync(SourceRowViewModel? row)
    {
        if (row is null) return;
        var dialog = new OpenFileDialog
        {
            Title = $"Select cookies.txt for {row.InputValue}",
            Filter = "Cookies files (*.txt)|*.txt|All files (*.*)|*.*",
            CheckFileExists = true,
        };
        if (dialog.ShowDialog() != true)
            return;
        if (!CookieJar.IsNetscapeFormat(dialog.FileName))
            StatusMessage = "Warning: file doesn't look like a Netscape cookies.txt — uploading anyway.";
        try
        {
            await _api.UploadSourceCookiesAsync(row.Id, dialog.FileName).ConfigureAwait(true);
            row.SetHasCookies(true);
            await _cache.UpsertSourcesAsync(new[] { row.Model }).ConfigureAwait(true);
            StatusMessage = "Cookies uploaded for source.";
        }
        catch (Exception ex)
        {
            StatusMessage = $"Cookies upload failed: {ex.Message}";
        }
    }

    /// <summary>v2.0: clears a source's server-side cookies.</summary>
    [RelayCommand]
    private async Task ClearSourceCookiesAsync(SourceRowViewModel? row)
    {
        if (row is null) return;
        try
        {
            await _api.DeleteSourceCookiesAsync(row.Id).ConfigureAwait(true);
            row.SetHasCookies(false);
            await _cache.UpsertSourcesAsync(new[] { row.Model }).ConfigureAwait(true);
            StatusMessage = "Source cookies cleared.";
        }
        catch (Exception ex)
        {
            StatusMessage = $"Clear cookies failed: {ex.Message}";
        }
    }

    private async Task UpdateSourceAsync(SourceRowViewModel row, UpdateSourceRequest request, string okMessage)
    {
        try
        {
            var dto = await _api.UpdateSourceAsync(row.Id, request).ConfigureAwait(true);
            var status = string.Equals(dto.Status, "paused", StringComparison.OrdinalIgnoreCase)
                ? SourceStatus.Paused : SourceStatus.Active;
            row.SyncStatus(status);
            await _cache.UpsertSourcesAsync(new[] { row.Model }).ConfigureAwait(true);
            StatusMessage = okMessage;
        }
        catch (ApiException ex)
        {
            StatusMessage = $"Update failed: {ex.Message}";
        }
    }

    private void LoadRows(List<Source> models)
    {
        Sources = new ObservableCollection<SourceRowViewModel>(models.Select(m => new SourceRowViewModel(m)));
    }
}
