using System.Collections.ObjectModel;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using Microsoft.Win32;
using UniversalDownloader.Core.Models;
using UniversalDownloader.Infrastructure.Api;
using UniversalDownloader.Infrastructure.Cache;
using UniversalDownloader.Worker.Download;

namespace UniversalDownloader.App.ViewModels;

/// <summary>Single job row in the jobs list (SRS FR-10).</summary>
public sealed partial class JobRowViewModel : ObservableObject
{
    public int Id { get; }

    [ObservableProperty] private string _title = string.Empty;
    [ObservableProperty] private Platform _platform;
    [ObservableProperty] private string _sourceName = string.Empty;
    [ObservableProperty] private JobState _state;
    [ObservableProperty] private double _progress;
    [ObservableProperty] private long _bytesDownloaded;
    [ObservableProperty] private long? _totalBytes;
    [ObservableProperty] private double _speedBytesPerSec;
    [ObservableProperty] private TimeSpan? _eta;
    [ObservableProperty] private string? _errorMessage;
    [ObservableProperty] private bool _captchaRequired;
    [ObservableProperty] private int _sourceId;

    public string StateText => State.ToString();
    public double ProgressPercent => Progress * 100.0;
    public string ProgressText => $"{ProgressPercent:F0}%";
    public string SpeedText => SpeedBytesPerSec > 0 ? FormatBytes(SpeedBytesPerSec) + "/s" : "—";
    public string EtaText => Eta.HasValue ? Eta.Value.ToString(@"hh\:mm\:ss") : "—";
    public string SizeText => TotalBytes.HasValue
        ? $"{FormatBytes(BytesDownloaded)} / {FormatBytes(TotalBytes.Value)}"
        : FormatBytes(BytesDownloaded);

    public bool CanPause => State is JobState.Ready or JobState.Downloading
        or JobState.RetryWait or JobState.WaitingForNetwork
        or JobState.Queued or JobState.Batched;
    public bool CanResume => State == JobState.Paused;
    public bool CanRetry => State == JobState.Failed;

    /// <summary>v2.0: job is blocked by a bot check and needs cookies.</summary>
    public bool CanProvideCookies => CaptchaRequired;

    public JobRowViewModel(DownloadJob job)
    {
        Id = job.Id;
        UpdateFrom(job);
    }

    public void UpdateFrom(DownloadJob job)
    {
        Title = string.IsNullOrWhiteSpace(job.Title) ? job.MediaUrl : job.Title;
        Platform = job.Platform;
        SourceName = job.SourceName;
        State = job.State;
        Progress = job.Progress;
        BytesDownloaded = job.BytesDownloaded;
        TotalBytes = job.TotalBytes;
        SpeedBytesPerSec = job.SpeedBytesPerSec;
        ErrorMessage = job.ErrorMessage;
        CaptchaRequired = job.CaptchaRequired;
        SourceId = job.SourceId;
        RefreshDerived();
    }

    public void ApplyProgress(JobProgressEventArgs e)
    {
        Progress = e.Progress;
        BytesDownloaded = e.BytesDownloaded;
        TotalBytes = e.TotalBytes;
        SpeedBytesPerSec = e.SpeedBytesPerSec;
        Eta = e.Eta;
        RefreshDerived();
    }

    private void RefreshDerived()
    {
        OnPropertyChanged(nameof(StateText));
        OnPropertyChanged(nameof(ProgressPercent));
        OnPropertyChanged(nameof(ProgressText));
        OnPropertyChanged(nameof(SpeedText));
        OnPropertyChanged(nameof(EtaText));
        OnPropertyChanged(nameof(SizeText));
        OnPropertyChanged(nameof(CanPause));
        OnPropertyChanged(nameof(CanResume));
        OnPropertyChanged(nameof(CanRetry));
        OnPropertyChanged(nameof(CanProvideCookies));
    }

    private static string FormatBytes(double bytes) => bytes switch
    {
        < 1024 => $"{bytes:F0} B",
        < 1024 * 1024 => $"{bytes / 1024:F1} KB",
        < 1024 * 1024 * 1024 => $"{bytes / (1024 * 1024):F1} MB",
        _ => $"{bytes / (1024 * 1024 * 1024):F2} GB",
    };
}

/// <summary>Job monitoring and control (SRS FR-10).</summary>
public sealed partial class JobsViewModel : ObservableObject
{
    private readonly IApiClient _api;
    private readonly LocalCache _cache;
    private readonly DownloadWorker _worker;
    private readonly SynchronizationContext? _uiContext;

    private readonly Dictionary<int, JobRowViewModel> _rows = new();

    [ObservableProperty]
    private ObservableCollection<JobRowViewModel> _jobs = new();

    public string[] Filters { get; } =
        ["All", "Queued", "Active", "Completed", "Failed", "Paused", "Waiting"];

    [ObservableProperty]
    private string _selectedFilter = "All";

    [ObservableProperty]
    private string _statusMessage = string.Empty;

    /// <summary>v2.0: jobs currently blocked by a bot check.</summary>
    public int BlockedByCaptchaCount => _rows.Values.Count(r => r.CaptchaRequired);

    public bool ShowCaptchaBanner => BlockedByCaptchaCount > 0;

    public string CaptchaBannerText => BlockedByCaptchaCount == 1
        ? "1 job is blocked by a bot check — provide cookies to continue."
        : $"{BlockedByCaptchaCount} jobs are blocked by bot checks — provide cookies to continue.";

    public JobsViewModel(IApiClient api, LocalCache cache, DownloadWorker worker)
    {
        _api = api;
        _cache = cache;
        _worker = worker;
        _uiContext = SynchronizationContext.Current;

        _worker.ProgressChanged += OnWorkerProgress;
        _worker.StateChanged += OnWorkerStateChanged;
    }

    [RelayCommand]
    public async Task RefreshAsync()
    {
        List<DownloadJob> jobs;
        try
        {
            var dtos = await _api.ListJobsAsync(ct: default).ConfigureAwait(true);
            jobs = dtos.Select(DtoMapper.ToJob).ToList();
            await _cache.UpsertJobsAsync(jobs).ConfigureAwait(true);
        }
        catch (Exception)
        {
            jobs = (await _cache.GetJobsAsync().ConfigureAwait(true)).ToList();
            StatusMessage = "Offline: showing cached jobs.";
        }

        _rows.Clear();
        foreach (var job in jobs.OrderByDescending(j => j.UpdatedAt))
            _rows[job.Id] = new JobRowViewModel(job);
        ApplyFilter();
        RefreshCaptchaBanner();
    }

    [RelayCommand]
    private void SetFilter(string? filter)
    {
        SelectedFilter = filter ?? "All";
        ApplyFilter();
    }

    [RelayCommand]
    private async Task PauseJobAsync(JobRowViewModel? row)
    {
        if (row is null) return;
        await _worker.PauseJobAsync(row.Id).ConfigureAwait(true);
    }

    [RelayCommand]
    private async Task ResumeJobAsync(JobRowViewModel? row)
    {
        if (row is null) return;
        await _worker.ResumeJobAsync(row.Id).ConfigureAwait(true);
    }

    [RelayCommand]
    private async Task RetryJobAsync(JobRowViewModel? row)
    {
        if (row is null) return;
        await _worker.RetryJobAsync(row.Id).ConfigureAwait(true);
    }

    /// <summary>
    /// v2.0 captcha flow: pick a cookies.txt, upload it per-source (or globally
    /// when the job has no source), then ask the server to resume the job.
    /// </summary>
    [RelayCommand]
    private async Task ProvideCookiesAsync(JobRowViewModel? row)
    {
        row ??= _rows.Values.FirstOrDefault(r => r.CaptchaRequired);
        if (row is null)
            return;
        var dialog = new OpenFileDialog
        {
            Title = $"Select cookies.txt for job '{row.Title}'",
            Filter = "Cookies files (*.txt)|*.txt|All files (*.*)|*.*",
            CheckFileExists = true,
        };
        if (dialog.ShowDialog() != true)
            return;
        try
        {
            if (row.SourceId > 0)
                await _api.UploadSourceCookiesAsync(row.SourceId, dialog.FileName).ConfigureAwait(true);
            else
                await _api.UploadMyCookiesAsync(dialog.FileName).ConfigureAwait(true);
            await _api.ResumeJobAsync(row.Id).ConfigureAwait(true);
            StatusMessage = "Cookies provided; resume requested.";
            await RefreshAsync().ConfigureAwait(true);
        }
        catch (Exception ex)
        {
            StatusMessage = $"Provide cookies failed: {ex.Message}";
        }
    }

    private void ApplyFilter()
    {
        IEnumerable<JobRowViewModel> rows = _rows.Values;
        rows = SelectedFilter switch
        {
            "Queued" => rows.Where(r => r.State is JobState.Queued or JobState.Batched or JobState.Ready),
            "Active" => rows.Where(r => r.State is JobState.Downloading or JobState.RetryWait),
            "Completed" => rows.Where(r => r.State == JobState.Completed),
            "Failed" => rows.Where(r => r.State == JobState.Failed),
            "Paused" => rows.Where(r => r.State == JobState.Paused),
            "Waiting" => rows.Where(r => r.State == JobState.WaitingForNetwork),
            _ => rows,
        };
        Jobs = new ObservableCollection<JobRowViewModel>(rows);
    }

    private void OnWorkerProgress(object? sender, JobProgressEventArgs e)
    {
        if (!_rows.TryGetValue(e.JobId, out var row))
            return;
        PostToUi(() => row.ApplyProgress(e));
    }

    private void OnWorkerStateChanged(object? sender, JobStateEventArgs e)
    {
        PostToUi(async () =>
        {
            try
            {
                var job = await _cache.GetJobAsync(e.JobId).ConfigureAwait(true);
                if (job is null)
                    return;
                if (_rows.TryGetValue(e.JobId, out var row))
                    row.UpdateFrom(job);
                else
                    _rows[e.JobId] = new JobRowViewModel(job);
                ApplyFilter();
                RefreshCaptchaBanner();
            }
            catch (Exception)
            {
                // Cache read failed; the next refresh will reconcile.
            }
        });
    }

    private void RefreshCaptchaBanner()
    {
        OnPropertyChanged(nameof(BlockedByCaptchaCount));
        OnPropertyChanged(nameof(ShowCaptchaBanner));
        OnPropertyChanged(nameof(CaptchaBannerText));
    }

    private void PostToUi(Action action)
    {
        if (_uiContext is not null)
            _uiContext.Post(_ => action(), null);
        else
            action();
    }
}
