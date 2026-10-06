using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using Microsoft.Win32;
using UniversalDownloader.Core.Configuration;
using UniversalDownloader.Core.Cookies;
using UniversalDownloader.Infrastructure.Api;
using UniversalDownloader.Worker.Download;

namespace UniversalDownloader.App.ViewModels;

/// <summary>
/// Local settings (SRS FR-07, FR-10): destination directory, naming template,
/// batch length, concurrency, timezone, API base URL — plus v2.0: daily start
/// time (synced to the server), global cookies file, server cookies management.
/// </summary>
public sealed partial class SettingsViewModel : ObservableObject
{
    private readonly ClientSettings _settings;
    private readonly WorkerOptions _workerOptions;
    private readonly IApiClient _api;

    [ObservableProperty] private string _destinationDirectory = string.Empty;
    [ObservableProperty] private string _namingTemplate = string.Empty;
    [ObservableProperty] private int _batchLength;
    [ObservableProperty] private int _concurrencyLimit;
    [ObservableProperty] private string _timezoneId = string.Empty;
    [ObservableProperty] private string _apiBaseUrl = string.Empty;
    [ObservableProperty] private string _dailyStartTime = "00:00";
    [ObservableProperty] private string? _globalCookiesFilePath;
    [ObservableProperty] private bool _hasServerCookies;
    [ObservableProperty] private string _saveMessage = string.Empty;
    [ObservableProperty] private bool _saveSucceeded;

    public bool HasSaveMessage => !string.IsNullOrWhiteSpace(SaveMessage);

    partial void OnSaveMessageChanged(string value) => OnPropertyChanged(nameof(HasSaveMessage));

    public SettingsViewModel(ClientSettings settings, WorkerOptions workerOptions, IApiClient api)
    {
        _settings = settings;
        _workerOptions = workerOptions;
        _api = api;
        LoadFromSettings();
    }

    [RelayCommand]
    private void Browse()
    {
        using var dialog = new System.Windows.Forms.FolderBrowserDialog
        {
            Description = "Select the download destination directory",
            SelectedPath = DestinationDirectory,
            ShowNewFolderButton = true,
        };
        if (dialog.ShowDialog() == System.Windows.Forms.DialogResult.OK)
            DestinationDirectory = dialog.SelectedPath;
    }

    [RelayCommand]
    private void BrowseCookies()
    {
        var dialog = new OpenFileDialog
        {
            Title = "Select global cookies.txt",
            Filter = "Cookies files (*.txt)|*.txt|All files (*.*)|*.*",
            CheckFileExists = true,
        };
        if (dialog.ShowDialog() != true)
            return;
        GlobalCookiesFilePath = dialog.FileName;
        if (!CookieJar.IsNetscapeFormat(dialog.FileName))
        {
            SaveSucceeded = false;
            SaveMessage = "Warning: this file doesn't look like a Netscape cookies.txt — it will still be used as-is.";
        }
    }

    [RelayCommand]
    private async Task UploadGlobalCookiesAsync()
    {
        if (string.IsNullOrWhiteSpace(GlobalCookiesFilePath) || !File.Exists(GlobalCookiesFilePath))
        {
            SaveSucceeded = false;
            SaveMessage = "Pick a cookies.txt file first (Browse...).";
            return;
        }
        try
        {
            await _api.UploadMyCookiesAsync(GlobalCookiesFilePath).ConfigureAwait(true);
            HasServerCookies = true;
            SaveSucceeded = true;
            SaveMessage = "Global cookies uploaded to the server.";
        }
        catch (Exception ex)
        {
            SaveSucceeded = false;
            SaveMessage = $"Upload failed: {ex.Message}";
        }
    }

    [RelayCommand]
    private async Task ClearGlobalCookiesAsync()
    {
        try
        {
            await _api.DeleteMyCookiesAsync().ConfigureAwait(true);
            HasServerCookies = false;
            SaveSucceeded = true;
            SaveMessage = "Server-side global cookies cleared.";
        }
        catch (Exception ex)
        {
            SaveSucceeded = false;
            SaveMessage = $"Clear failed: {ex.Message}";
        }
    }

    /// <summary>v2.0: refreshes the server-side global-cookies presence flag.</summary>
    public async Task RefreshServerCookiesStatusAsync()
    {
        try
        {
            var status = await _api.GetMyCookiesStatusAsync().ConfigureAwait(true);
            HasServerCookies = status.Present;
        }
        catch (Exception)
        {
            // Not logged in or offline: keep the previous flag.
        }
    }

    [RelayCommand]
    private void Reset() => LoadFromSettings();

    [RelayCommand]
    private async Task Save()
    {
        SaveSucceeded = false;

        if (string.IsNullOrWhiteSpace(DestinationDirectory))
        {
            SaveMessage = "Destination directory is required.";
            return;
        }
        if (BatchLength is < 1 or > 1000)
        {
            SaveMessage = "Batch length must be between 1 and 1000.";
            return;
        }
        if (!ClientSettingsValidator.IsValidConcurrencyLimit(ConcurrencyLimit))
        {
            SaveMessage = $"Concurrency limit must be between {ClientSettingsValidator.MinConcurrencyLimit} and {ClientSettingsValidator.MaxConcurrencyLimit}.";
            return;
        }
        if (!ClientSettingsValidator.TryParseDailyStartTime(DailyStartTime, out var startTime))
        {
            SaveMessage = "Daily start time must be in 24-hour HH:MM format (e.g. 00:00).";
            return;
        }
        if (!string.IsNullOrWhiteSpace(GlobalCookiesFilePath) && !File.Exists(GlobalCookiesFilePath))
        {
            SaveMessage = "Global cookies file does not exist.";
            return;
        }
        if (string.IsNullOrWhiteSpace(NamingTemplate) || !NamingTemplate.Contains("{ext}", StringComparison.Ordinal))
        {
            SaveMessage = "Naming template must contain the {ext} token.";
            return;
        }

        var tz = TimeZoneHelper.Find(TimezoneId);
        if (!string.IsNullOrWhiteSpace(TimezoneId) &&
            !string.Equals(tz.Id, TimezoneId, StringComparison.OrdinalIgnoreCase))
        {
            SaveMessage = $"Timezone '{TimezoneId}' not recognized; using {tz.Id} instead.";
            TimezoneId = tz.Id;
        }
        else
        {
            SaveMessage = string.Empty;
        }

        _settings.DestinationDirectory = DestinationDirectory.Trim();
        _settings.NamingTemplate = NamingTemplate.Trim();
        _settings.BatchLength = BatchLength;
        _settings.ConcurrencyLimit = ConcurrencyLimit;
        _settings.DailyStartTime = ClientSettingsValidator.NormalizeDailyStartTime(startTime);
        DailyStartTime = _settings.DailyStartTime;
        _settings.GlobalCookiesFilePath = string.IsNullOrWhiteSpace(GlobalCookiesFilePath)
            ? null
            : GlobalCookiesFilePath.Trim();
        _settings.TimezoneId = TimezoneId.Trim();
        _settings.ApiBaseUrl = ApiBaseUrl.Trim().TrimEnd('/');
        _settings.Save();

        // Apply live where possible; the job semaphore and API URL need a restart.
        _workerOptions.DestinationDirectory = _settings.DestinationDirectory;
        _workerOptions.NamingTemplate = _settings.NamingTemplate;
        _workerOptions.GlobalCookiesFilePath = _settings.GlobalCookiesFilePath;
        _workerOptions.ConcurrencyLimit = _settings.ConcurrencyLimit;

        // Best effort: sync the daily start time to the server (v2.0).
        string syncNote = string.Empty;
        try
        {
            await _api.SyncStartTimeToServerAsync(_settings.DailyStartTime).ConfigureAwait(true);
        }
        catch (Exception ex)
        {
            syncNote = $" (start time not synced to server: {ex.Message})";
        }

        SaveSucceeded = true;
        if (string.IsNullOrEmpty(SaveMessage))
            SaveMessage = "Settings saved. Job-concurrency and API URL changes apply after restart." + syncNote;
        else
            SaveMessage += syncNote;
    }

    private void LoadFromSettings()
    {
        DestinationDirectory = _settings.DestinationDirectory;
        NamingTemplate = _settings.NamingTemplate;
        BatchLength = _settings.BatchLength;
        ConcurrencyLimit = _settings.ConcurrencyLimit;
        DailyStartTime = _settings.DailyStartTime;
        GlobalCookiesFilePath = _settings.GlobalCookiesFilePath;
        TimezoneId = _settings.TimezoneId;
        ApiBaseUrl = _settings.ApiBaseUrl;
        SaveMessage = string.Empty;
        SaveSucceeded = false;
    }
}
