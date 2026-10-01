using System.Windows.Threading;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using UniversalDownloader.App.Services;
using UniversalDownloader.Core.Configuration;
using UniversalDownloader.Core.Models;
using UniversalDownloader.Infrastructure.Api;
using UniversalDownloader.Infrastructure.Cache;
using UniversalDownloader.Worker.Download;
using UniversalDownloader.Worker.Networking;

namespace UniversalDownloader.App.ViewModels;

/// <summary>
/// Customer dashboard (SRS FR-11): subscription status + expiry, T-3 renewal
/// banner, source/job counts, today's + upcoming batch, connectivity and worker state.
/// </summary>
public sealed partial class DashboardViewModel : ObservableObject
{
    private readonly SessionState _session;
    private readonly IApiClient _api;
    private readonly LocalCache _cache;
    private readonly NetworkMonitor _network;
    private readonly DownloadWorker _worker;
    private readonly ClientSettings _settings;
    private readonly DispatcherTimer _timer;

    [ObservableProperty] private string _subscriptionStatusText = "—";
    [ObservableProperty] private string _expiryText = "—";
    [ObservableProperty] private string _daysRemainingText = string.Empty;
    [ObservableProperty] private bool _showRenewalBanner;
    [ObservableProperty] private string _renewalBannerText = string.Empty;
    [ObservableProperty] private bool _showLockout;
    [ObservableProperty] private string _lockoutText = string.Empty;

    [ObservableProperty] private int _sourceCount;
    [ObservableProperty] private int _queuedCount;
    [ObservableProperty] private int _activeCount;
    [ObservableProperty] private int _completedCount;
    [ObservableProperty] private int _failedCount;

    [ObservableProperty] private string _todayBatchText = "—";
    [ObservableProperty] private string _upcomingBatchText = "—";
    [ObservableProperty] private string _connectivityText = "—";
    [ObservableProperty] private bool _isConnected;
    [ObservableProperty] private string _workerStateText = "—";
    [ObservableProperty] private string _lastSyncText = string.Empty;

    public DashboardViewModel(
        SessionState session,
        IApiClient api,
        LocalCache cache,
        NetworkMonitor network,
        DownloadWorker worker,
        ClientSettings settings)
    {
        _session = session;
        _api = api;
        _cache = cache;
        _network = network;
        _worker = worker;
        _settings = settings;

        _timer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(30) };
        _timer.Tick += async (_, _) => await RefreshAsync().ConfigureAwait(true);
        _timer.Start();
    }

    [RelayCommand]
    public async Task RefreshAsync()
    {
        var subscription = _session.Subscription;
        if (subscription is null)
            return;

        var now = DateTimeOffset.UtcNow;
        var status = subscription.DeriveStatus(now);

        SubscriptionStatusText = status.ToString();
        var tz = TimeZoneHelper.Find(_settings.TimezoneId);
        ExpiryText = TimeZoneInfo.ConvertTime(subscription.ExpiresAt, tz).ToString("yyyy-MM-dd HH:mm");
        DaysRemainingText = $"{subscription.DaysRemaining(now)} days remaining";

        ShowRenewalBanner = subscription.IsInWarningWindow(now);
        if (ShowRenewalBanner)
        {
            RenewalBannerText =
                $"Your subscription expires on {subscription.ExpiresAt:yyyy-MM-dd} " +
                $"({subscription.DaysRemaining(now)} days remaining). Contact your administrator to renew.";
        }

        ShowLockout = !subscription.AllowsProtectedOperations(now);
        LockoutText = status switch
        {
            SubscriptionStatus.Expired =>
                $"Subscription expired on {subscription.ExpiresAt:yyyy-MM-dd}. Protected features are locked until renewal.",
            SubscriptionStatus.Suspended => "Account suspended. Protected features are locked.",
            SubscriptionStatus.Disabled => "Account disabled. Protected features are locked.",
            _ => string.Empty,
        };

        try
        {
            var sources = await _cache.GetSourcesAsync().ConfigureAwait(true);
            var jobs = await _cache.GetJobsAsync().ConfigureAwait(true);

            SourceCount = sources.Count;
            QueuedCount = jobs.Count(j => j.State is JobState.Queued or JobState.Batched or JobState.Ready);
            ActiveCount = jobs.Count(j => j.State is JobState.Downloading or JobState.RetryWait);
            CompletedCount = jobs.Count(j => j.State == JobState.Completed);
            FailedCount = jobs.Count(j => j.State == JobState.Failed);
        }
        catch (Exception)
        {
            // Cache unavailable: keep previous counts.
        }

        try
        {
            var batches = (await _api.ListBatchesAsync().ConfigureAwait(true))
                .Select(DtoMapper.ToBatch)
                .OrderBy(b => b.BatchDate)
                .ToList();
            var today = TimeZoneHelper.TodayIn(_settings.TimezoneId);
            var todays = batches.FirstOrDefault(b => b.BatchDate == today);
            TodayBatchText = todays is null
                ? $"No batch created for {today:yyyy-MM-dd} yet."
                : $"{today:yyyy-MM-dd}: {todays.Size} jobs · {todays.Status}";
            var upcoming = batches.FirstOrDefault(b => b.BatchDate > today);
            UpcomingBatchText = upcoming is null
                ? "No upcoming batch scheduled."
                : $"{upcoming.BatchDate:yyyy-MM-dd}: {upcoming.Size} jobs · {upcoming.Status}";
        }
        catch (Exception)
        {
            TodayBatchText = "Batch information unavailable (offline).";
            UpcomingBatchText = string.Empty;
        }

        IsConnected = _network.IsConnected;
        ConnectivityText = _network.IsConnected ? "Connected" : "Offline";
        WorkerStateText = _worker.IsRunning
            ? $"Running ({_worker.ActiveCount} active)"
            : "Stopped";
        LastSyncText = $"Updated {DateTime.Now:HH:mm:ss}";
    }
}
