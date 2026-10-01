// Session lifecycle coordinator — implements SDS section 14:
// login -> /me -> expired lockout / expiring banner -> sync -> wait for batch ->
// network check -> download with concurrency -> persist + sync to server.

using UniversalDownloader.Core.Models;
using UniversalDownloader.Infrastructure.Api;
using UniversalDownloader.Infrastructure.Auth;
using UniversalDownloader.Infrastructure.Cache;
using UniversalDownloader.Worker.Download;
using UniversalDownloader.Worker.Networking;

namespace UniversalDownloader.App.Services;

/// <summary>
/// Owns the post-login session: starts the network monitor and download worker,
/// syncs sources/jobs into the local cache, enqueues downloadable jobs, and
/// re-syncs when connectivity returns. Stops everything on logout.
/// </summary>
public sealed class SessionCoordinator
{
    private readonly IApiClient _api;
    private readonly AuthService _auth;
    private readonly LocalCache _cache;
    private readonly NetworkMonitor _network;
    private readonly DownloadWorker _worker;
    private readonly SessionState _session;
    private readonly IWorkerLog _log = new TraceWorkerLog();

    private bool _started;

    public SessionCoordinator(
        IApiClient api,
        AuthService auth,
        LocalCache cache,
        NetworkMonitor network,
        DownloadWorker worker,
        SessionState session)
    {
        _api = api;
        _auth = auth;
        _cache = cache;
        _network = network;
        _worker = worker;
        _session = session;
    }

    /// <summary>Starts monitor + worker and performs the initial sync.</summary>
    public async Task StartSessionAsync(CancellationToken ct = default)
    {
        if (_started)
            return;
        _started = true;
        _network.Start();
        _network.ConnectivityChanged += OnConnectivityChanged;
        await _worker.StartAsync(ct).ConfigureAwait(false);
        await SyncAsync(ct).ConfigureAwait(false);
    }

    /// <summary>Stops the worker/monitor, clears tokens and cached customer data.</summary>
    public async Task EndSessionAsync()
    {
        _network.ConnectivityChanged -= OnConnectivityChanged;
        await _worker.StopAsync().ConfigureAwait(false);
        _network.Stop();
        await _auth.LogoutAsync().ConfigureAwait(false);
        await _cache.ClearCustomerDataAsync().ConfigureAwait(false);
        _session.Clear();
        _started = false;
    }

    /// <summary>
    /// Pulls /me, sources, and jobs; mirrors them to the cache; enqueues jobs
    /// that are ready to download. Skips enqueueing when the subscription does
    /// not allow protected operations (SRS FR-03); the server enforces this too.
    /// </summary>
    public async Task SyncAsync(CancellationToken ct = default)
    {
        var me = await _api.GetMeAsync(ct).ConfigureAwait(false);
        var customer = DtoMapper.ToCustomer(me.Customer);
        var subscription = DtoMapper.ToSubscription(me.Subscription, me.Customer.Id);
        _session.Set(customer, subscription);

        var sources = await _api.ListSourcesAsync(ct).ConfigureAwait(false);
        await _cache.UpsertSourcesAsync(sources.Select(DtoMapper.ToSource), ct).ConfigureAwait(false);

        var jobs = await _api.ListJobsAsync(ct: ct).ConfigureAwait(false);
        var mapped = jobs.Select(DtoMapper.ToJob).ToList();
        await _cache.UpsertJobsAsync(mapped, ct).ConfigureAwait(false);

        if (subscription.AllowsProtectedOperations(DateTimeOffset.UtcNow))
        {
            foreach (var job in mapped.Where(j =>
                         j.State is JobState.Ready or JobState.RetryWait or JobState.WaitingForNetwork))
            {
                await _worker.EnqueueAsync(job, ct).ConfigureAwait(false);
            }
        }
        else
        {
            _log.Warn("Subscription does not allow protected operations; downloads not started.");
        }
    }

    private async void OnConnectivityChanged(object? sender, bool connected)
    {
        if (!connected)
            return;
        try
        {
            // Connectivity returned: move held jobs back to READY, then pick up
            // anything the server activated while we were offline.
            await _worker.ResumeWaitingJobsAsync().ConfigureAwait(false);
            await SyncAsync().ConfigureAwait(false);
        }
        catch (Exception ex)
        {
            _log.Warn($"Post-reconnect sync failed: {ex.Message}");
        }
    }
}
