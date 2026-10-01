// Background download worker — SDS sections 4.1, 4.5, 13, 14.
// SemaphoreSlim-bounded concurrency, HTTP Range resume, exponential backoff,
// WAITING_FOR_NETWORK handling (never marks network loss as failure), and
// completion verification (checksum when provided, else size/existence).

using System.Collections.Concurrent;
using System.Diagnostics;
using System.Net;
using System.Net.Http.Headers;
using System.Security.Cryptography;
using UniversalDownloader.Core.Models;
using UniversalDownloader.Core.Naming;
using UniversalDownloader.Core.StateMachine;
using UniversalDownloader.Infrastructure.Api;
using UniversalDownloader.Infrastructure.Cache;
using UniversalDownloader.Worker.Networking;

namespace UniversalDownloader.Worker.Download;

/// <summary>Worker tunables.</summary>
public sealed class WorkerOptions
{
    public int ConcurrencyLimit { get; init; } = 3;
    public string DestinationDirectory { get; set; } = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
        "Downloads", "UniversalDownloader");
    public string NamingTemplate { get; set; } = FileNamingTemplate.DefaultTemplate;
    public TimeSpan BaseRetryDelay { get; init; } = TimeSpan.FromSeconds(2);
    public TimeSpan MaxRetryDelay { get; init; } = TimeSpan.FromMinutes(5);
    public TimeSpan ReadTimeout { get; init; } = TimeSpan.FromSeconds(60);
    public TimeSpan ServerReportInterval { get; init; } = TimeSpan.FromSeconds(15);
}

/// <summary>Minimal logging abstraction so the worker stays UI-agnostic.</summary>
public interface IWorkerLog
{
    void Info(string message);
    void Warn(string message);
    void Error(string message, Exception? exception = null);
}

public sealed class NullWorkerLog : IWorkerLog
{
    public static readonly NullWorkerLog Instance = new();
    public void Info(string message) { }
    public void Warn(string message) { }
    public void Error(string message, Exception? exception = null) { }
}

public sealed class TraceWorkerLog : IWorkerLog
{
    public void Info(string message) => Trace.WriteLine($"[INFO] {message}");
    public void Warn(string message) => Trace.WriteLine($"[WARN] {message}");
    public void Error(string message, Exception? exception = null) =>
        Trace.WriteLine($"[ERROR] {message}{(exception is null ? "" : " " + exception)}");
}

public sealed class JobProgressEventArgs : EventArgs
{
    public required int JobId { get; init; }
    public required double Progress { get; init; }
    public required long BytesDownloaded { get; init; }
    public required long? TotalBytes { get; init; }
    public required double SpeedBytesPerSec { get; init; }
    public required TimeSpan? Eta { get; init; }
}

public sealed class JobStateEventArgs : EventArgs
{
    public required int JobId { get; init; }
    public required JobState OldState { get; init; }
    public required JobState NewState { get; init; }
}

/// <summary>
/// Executes download jobs with bounded concurrency. All state changes go
/// through <see cref="JobStateMachine"/> and are mirrored to the server
/// (best effort) and the local cache.
/// </summary>
public sealed class DownloadWorker : IAsyncDisposable
{
    private readonly IApiClient _api;
    private readonly LocalCache _cache;
    private readonly NetworkMonitor _network;
    private readonly WorkerOptions _options;
    private readonly IWorkerLog _log;
    private readonly HttpClient _http;

    private readonly SemaphoreSlim _slots;
    private readonly ConcurrentDictionary<int, JobExecution> _active = new();
    private readonly ConcurrentDictionary<int, JobState> _resumeTargets = new();
    private readonly ConcurrentQueue<DownloadJob> _pending = new();
    private readonly ConcurrentDictionary<int, DateTimeOffset> _lastServerReport = new();

    private CancellationTokenSource? _workerCts;
    private bool _running;
    private bool _disposed;

    public event EventHandler<JobProgressEventArgs>? ProgressChanged;
    public event EventHandler<JobStateEventArgs>? StateChanged;

    public bool IsRunning => _running;
    public int ActiveCount => _active.Count;

    public DownloadWorker(
        IApiClient api,
        LocalCache cache,
        NetworkMonitor network,
        WorkerOptions options,
        IWorkerLog? log = null)
    {
        _api = api ?? throw new ArgumentNullException(nameof(api));
        _cache = cache ?? throw new ArgumentNullException(nameof(cache));
        _network = network ?? throw new ArgumentNullException(nameof(network));
        _options = options ?? throw new ArgumentNullException(nameof(options));
        _log = log ?? NullWorkerLog.Instance;
        if (_options.ConcurrencyLimit < 1)
            throw new ArgumentOutOfRangeException(nameof(options), "ConcurrencyLimit must be >= 1.");
        _slots = new SemaphoreSlim(_options.ConcurrencyLimit, _options.ConcurrencyLimit);
        // Infinite overall timeout: per-read bounds are enforced via WaitAsync.
        _http = new HttpClient(new HttpClientHandler { AllowAutoRedirect = true })
        {
            Timeout = Timeout.InfiniteTimeSpan,
        };
    }

    public Task StartAsync(CancellationToken ct = default)
    {
        ObjectDisposedException.ThrowIf(_disposed, this);
        if (_running)
            return Task.CompletedTask;
        _running = true;
        _workerCts = CancellationTokenSource.CreateLinkedTokenSource(ct);
        _network.ConnectivityChanged += OnConnectivityChanged;
        _log.Info("Download worker started.");
        while (_pending.TryDequeue(out var job))
            EnqueueInternal(job);
        return Task.CompletedTask;
    }

    public async Task StopAsync()
    {
        if (!_running)
            return;
        _running = false;
        _network.ConnectivityChanged -= OnConnectivityChanged;
        if (_workerCts is not null)
        {
            _workerCts.Cancel();
            var tasks = _active.Values.Select(e => e.Task).ToArray();
            try
            {
                await Task.WhenAll(tasks).WaitAsync(TimeSpan.FromSeconds(30)).ConfigureAwait(false);
            }
            catch (Exception ex) when (ex is OperationCanceledException or TimeoutException)
            {
                _log.Warn("Worker stop timed out waiting for active jobs.");
            }
            _workerCts.Dispose();
            _workerCts = null;
        }
        _log.Info("Download worker stopped.");
    }

    /// <summary>
    /// Queues a job for download. Only jobs in Ready/RetryWait/WaitingForNetwork
    /// are picked up; Queued/Batched jobs wait for server batch activation.
    /// </summary>
    public Task EnqueueAsync(DownloadJob job, CancellationToken ct = default)
    {
        ArgumentNullException.ThrowIfNull(job);
        if (!_running)
        {
            _pending.Enqueue(job);
            return Task.CompletedTask;
        }
        EnqueueInternal(job);
        return Task.CompletedTask;
    }

    private void EnqueueInternal(DownloadJob job)
    {
        if (_active.ContainsKey(job.Id))
            return;
        if (job.State is not (JobState.Ready or JobState.RetryWait or JobState.WaitingForNetwork))
        {
            _log.Info($"Job {job.Id} not enqueued: state {job.State} is not downloadable.");
            return;
        }
        var exec = new JobExecution { Job = job };
        if (!_active.TryAdd(job.Id, exec))
            return;
        exec.Task = ProcessJobAsync(job, exec, _workerCts!.Token);
    }

    public async Task PauseJobAsync(int jobId, CancellationToken ct = default)
    {
        if (_active.TryGetValue(jobId, out var exec))
        {
            var job = exec.Job;
            _resumeTargets[jobId] = ResumeTargetFor(job.State);
            exec.PauseCts.Cancel(); // the attempt loop transitions to Paused
            await NotifyServerPauseAsync(jobId).ConfigureAwait(false);
            return;
        }

        var cached = await _cache.GetJobAsync(jobId, ct).ConfigureAwait(false);
        if (cached is null)
            return;
        if (cached.State == JobState.RetryWait)
        {
            // No RETRY_WAIT -> PAUSED edge: walk RETRY_WAIT -> READY -> PAUSED.
            await TransitionAsync(cached, JobState.Ready, ct).ConfigureAwait(false);
        }
        if (!JobStateMachine.CanTransition(cached.State, JobState.Paused))
            return;
        _resumeTargets[jobId] = ResumeTargetFor(cached.State);
        await TransitionAsync(cached, JobState.Paused, ct).ConfigureAwait(false);
        await NotifyServerPauseAsync(jobId).ConfigureAwait(false);
    }

    public async Task ResumeJobAsync(int jobId, CancellationToken ct = default)
    {
        var job = await _cache.GetJobAsync(jobId, ct).ConfigureAwait(false);
        if (job is null || job.State != JobState.Paused)
            return;
        var target = _resumeTargets.GetValueOrDefault(jobId, JobState.Ready);
        _resumeTargets.TryRemove(jobId, out _);
        await TransitionAsync(job, target, ct).ConfigureAwait(false);
        try
        {
            await _api.ResumeJobAsync(jobId, ct).ConfigureAwait(false);
        }
        catch (Exception ex)
        {
            _log.Warn($"Server resume for job {jobId} failed: {ex.Message}");
        }
        await EnqueueAsync(job, ct).ConfigureAwait(false);
    }

    /// <summary>Manual retry: Failed -&gt; Ready (SRS section 8, v1.1), attempts reset.</summary>
    public async Task RetryJobAsync(int jobId, CancellationToken ct = default)
    {
        var job = await _cache.GetJobAsync(jobId, ct).ConfigureAwait(false);
        if (job is null || job.State != JobState.Failed)
            return;
        try
        {
            await _api.RetryJobAsync(jobId, ct).ConfigureAwait(false);
        }
        catch (Exception ex)
        {
            _log.Warn($"Server retry for job {jobId} failed: {ex.Message}");
        }
        job.Attempts = 0;
        job.ErrorMessage = null;
        job.Progress = 0;
        await TransitionAsync(job, JobState.Ready, ct).ConfigureAwait(false);
        await EnqueueAsync(job, ct).ConfigureAwait(false);
    }

    /// <summary>
    /// After connectivity returns, moves locally-known WAITING_FOR_NETWORK jobs
    /// back to READY and re-queues them (SDS section 13).
    /// </summary>
    public async Task ResumeWaitingJobsAsync(CancellationToken ct = default)
    {
        var jobs = await _cache.GetJobsAsync(ct).ConfigureAwait(false);
        foreach (var job in jobs.Where(j => j.State == JobState.WaitingForNetwork))
        {
            try
            {
                await TransitionAsync(job, JobState.Ready, ct).ConfigureAwait(false);
                await EnqueueAsync(job, ct).ConfigureAwait(false);
            }
            catch (Exception ex)
            {
                _log.Warn($"Could not resume waiting job {job.Id}: {ex.Message}");
            }
        }
    }

    private async Task ProcessJobAsync(DownloadJob job, JobExecution exec, CancellationToken workerCt)
    {
        try
        {
            await _slots.WaitAsync(workerCt).ConfigureAwait(false);
            try
            {
                await RunJobLoopAsync(job, exec, workerCt).ConfigureAwait(false);
            }
            finally
            {
                _slots.Release();
            }
        }
        catch (OperationCanceledException)
        {
            // Worker is stopping; server remains the source of truth.
        }
        catch (Exception ex)
        {
            _log.Error($"Unexpected error processing job {job.Id}.", ex);
        }
        finally
        {
            _active.TryRemove(job.Id, out _);
        }
    }

    private async Task RunJobLoopAsync(DownloadJob job, JobExecution exec, CancellationToken workerCt)
    {
        while (!workerCt.IsCancellationRequested)
        {
            if (job.State is not (JobState.Ready or JobState.RetryWait or JobState.WaitingForNetwork))
                return;

            if (job.State == JobState.WaitingForNetwork)
            {
                bool restored = await WaitForConnectivityAsync(exec, workerCt).ConfigureAwait(false);
                if (!restored)
                {
                    if (exec.PauseRequested && !workerCt.IsCancellationRequested)
                    {
                        _resumeTargets[job.Id] = JobState.Ready;
                        await TransitionAsync(job, JobState.Paused, workerCt).ConfigureAwait(false);
                    }
                    return;
                }
                await TransitionAsync(job, JobState.Ready, workerCt).ConfigureAwait(false);
            }

            if (!_network.IsConnected)
            {
                await TransitionAsync(job, JobState.WaitingForNetwork, workerCt).ConfigureAwait(false);
                continue;
            }

            await TransitionAsync(job, JobState.Downloading, workerCt).ConfigureAwait(false);
            AttemptOutcome outcome = await TryDownloadOnceAsync(job, exec, workerCt).ConfigureAwait(false);

            switch (outcome.Kind)
            {
                case AttemptKind.TransferSucceeded:
                    if (await VerifyAndFinalizeAsync(job, outcome.PartPath!, outcome.FinalPath!, workerCt).ConfigureAwait(false))
                        return; // completed
                    if (!await HandleTransientFailureAsync(job, exec, "Downloaded file failed verification.", workerCt).ConfigureAwait(false))
                        return;
                    break;

                case AttemptKind.Paused:
                    return; // already transitioned to Paused

                case AttemptKind.NetworkLost:
                    // Never a failure: hold in WAITING_FOR_NETWORK (SDS section 13).
                    await TransitionAsync(job, JobState.WaitingForNetwork, workerCt).ConfigureAwait(false);
                    break;

                case AttemptKind.TransientFailure:
                    if (!await HandleTransientFailureAsync(job, exec, outcome.Reason!, workerCt).ConfigureAwait(false))
                        return;
                    break;

                case AttemptKind.Stopping:
                    return;
            }
        }
    }

    private async Task<bool> WaitForConnectivityAsync(JobExecution exec, CancellationToken workerCt)
    {
        if (_network.IsConnected)
            return true;
        var tcs = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
        void Handler(object? s, bool connected)
        {
            if (connected)
                tcs.TrySetResult(true);
        }
        _network.ConnectivityChanged += Handler;
        try
        {
            if (_network.IsConnected)
                return true; // recovered between check and subscribe
            using var reg1 = exec.PauseCts.Token.Register(() => tcs.TrySetResult(false));
            using var reg2 = workerCt.Register(() => tcs.TrySetResult(false));
            return await tcs.Task.ConfigureAwait(false);
        }
        finally
        {
            _network.ConnectivityChanged -= Handler;
        }
    }

    private void OnConnectivityChanged(object? sender, bool connected)
    {
        if (connected)
            return;
        // Abort in-flight attempts; each maps the abort to WAITING_FOR_NETWORK.
        foreach (var exec in _active.Values)
        {
            try { exec.NetworkCts.Cancel(); }
            catch (ObjectDisposedException) { /* shutting down */ }
        }
    }

    private async Task<bool> HandleTransientFailureAsync(
        DownloadJob job, JobExecution exec, string reason, CancellationToken workerCt)
    {
        job.Attempts++;
        job.ErrorMessage = reason;
        _log.Warn($"Job {job.Id} transient failure (attempt {job.Attempts}): {reason}");

        if (!RetryPolicy.CanRetry(job.Attempts))
        {
            await TransitionAsync(job, JobState.Failed, workerCt).ConfigureAwait(false);
            return false;
        }

        await TransitionAsync(job, JobState.RetryWait, workerCt).ConfigureAwait(false);
        var delay = RetryPolicy.ComputeDelay(job.Attempts, _options.BaseRetryDelay, _options.MaxRetryDelay, Random.Shared);
        _log.Info($"Job {job.Id} retrying in {delay.TotalSeconds:F1}s.");
        try
        {
            await Task.Delay(delay, CancellationTokenSource.CreateLinkedTokenSource(
                workerCt, exec.PauseCts.Token).Token).ConfigureAwait(false);
        }
        catch (OperationCanceledException)
        {
            if (exec.PauseRequested && !workerCt.IsCancellationRequested)
            {
                // RetryWait -> Ready -> Paused (both validated transitions).
                await TransitionAsync(job, JobState.Ready, workerCt).ConfigureAwait(false);
                _resumeTargets[job.Id] = JobState.Ready;
                await TransitionAsync(job, JobState.Paused, workerCt).ConfigureAwait(false);
            }
            return false;
        }

        await TransitionAsync(job, JobState.Ready, workerCt).ConfigureAwait(false);
        return true;
    }

    private async Task<AttemptOutcome> TryDownloadOnceAsync(
        DownloadJob job, JobExecution exec, CancellationToken workerCt)
    {
        string finalPath;
        try
        {
            finalPath = FileNamingTemplate.ResolveUniquePath(
                _options.DestinationDirectory, _options.NamingTemplate,
                MediaMetadata.FromJob(job));
        }
        catch (Exception ex)
        {
            return AttemptOutcome.Transient($"Could not resolve destination path: {ex.Message}");
        }

        string partPath = finalPath + ".part";
        long offset = 0;
        try
        {
            if (File.Exists(partPath))
                offset = new FileInfo(partPath).Length;
            Directory.CreateDirectory(Path.GetDirectoryName(finalPath)!);
        }
        catch (Exception ex)
        {
            return AttemptOutcome.Transient($"Cannot prepare destination: {ex.Message}");
        }

        using var linked = CancellationTokenSource.CreateLinkedTokenSource(
            workerCt, exec.PauseCts.Token, exec.NetworkCts.Token);

        // Up to two range attempts: the second restarts from zero if the
        // server does not honor Range requests.
        for (int rangeAttempt = 0; rangeAttempt < 2; rangeAttempt++)
        {
            try
            {
                using var request = new HttpRequestMessage(HttpMethod.Get, job.MediaUrl);
                if (offset > 0)
                    request.Headers.Range = new RangeHeaderValue(offset, null);

                using var response = await _http.SendAsync(
                    request, HttpCompletionOption.ResponseHeadersRead, linked.Token).ConfigureAwait(false);

                if (offset > 0 && response.StatusCode != HttpStatusCode.PartialContent)
                {
                    TryDelete(partPath); // server ignored Range; restart cleanly
                    offset = 0;
                    continue;
                }
                response.EnsureSuccessStatusCode();

                long? total = response.Content.Headers.ContentRange?.Length
                    ?? response.Content.Headers.ContentLength;
                job.TotalBytes = total;

                using var contentStream = await response.Content.ReadAsStreamAsync(linked.Token).ConfigureAwait(false);
                using var fileStream = new FileStream(partPath, FileMode.OpenOrCreate,
                    FileAccess.Write, FileShare.None, bufferSize: 81920, useAsync: true);
                fileStream.Seek(offset, SeekOrigin.Begin);

                var buffer = new byte[81920];
                long downloaded = offset;
                long windowBytes = 0;
                var windowStart = DateTimeOffset.UtcNow;
                var lastUiReport = DateTimeOffset.UtcNow;

                while (true)
                {
                    int read;
                    try
                    {
                        read = await contentStream.ReadAsync(buffer, linked.Token)
                            .WaitAsync(_options.ReadTimeout, linked.Token).ConfigureAwait(false);
                    }
                    catch (TimeoutException)
                    {
                        throw new IOException(
                            $"No data received for {_options.ReadTimeout.TotalSeconds:F0}s (read timeout).");
                    }
                    if (read == 0)
                        break;

                    await fileStream.WriteAsync(buffer.AsMemory(0, read), linked.Token).ConfigureAwait(false);
                    downloaded += read;
                    windowBytes += read;
                    job.BytesDownloaded = downloaded;
                    if (total is > 0)
                        job.Progress = Math.Min(1.0, (double)downloaded / total.Value);

                    var now = DateTimeOffset.UtcNow;
                    if ((now - lastUiReport).TotalMilliseconds >= 250)
                    {
                        double windowSecs = (now - windowStart).TotalSeconds;
                        if (windowSecs > 0)
                            job.SpeedBytesPerSec = windowBytes / windowSecs;
                        windowBytes = 0;
                        windowStart = now;
                        lastUiReport = now;

                        TimeSpan? eta = job.SpeedBytesPerSec > 0 && total is > 0
                            ? TimeSpan.FromSeconds(Math.Max(0, (total.Value - downloaded) / job.SpeedBytesPerSec))
                            : null;
                        ProgressChanged?.Invoke(this, new JobProgressEventArgs
                        {
                            JobId = job.Id,
                            Progress = job.Progress,
                            BytesDownloaded = downloaded,
                            TotalBytes = total,
                            SpeedBytesPerSec = job.SpeedBytesPerSec,
                            Eta = eta,
                        });
                        await ReportAsync(job, force: false, workerCt).ConfigureAwait(false);
                    }
                }

                await fileStream.FlushAsync(linked.Token).ConfigureAwait(false);
                return AttemptOutcome.TransferSucceeded(partPath, finalPath);
            }
            catch (OperationCanceledException) when (exec.PauseCts.IsCancellationRequested)
            {
                // Pause during download. The machine has no DOWNLOADING -> PAUSED
                // edge, so walk the validated chain: DOWNLOADING -> RETRY_WAIT
                // -> READY -> PAUSED. The .part file is kept for Range resume.
                await TransitionAsync(job, JobState.RetryWait, workerCt).ConfigureAwait(false);
                _resumeTargets[job.Id] = JobState.Ready;
                await TransitionAsync(job, JobState.Ready, workerCt).ConfigureAwait(false);
                await TransitionAsync(job, JobState.Paused, workerCt).ConfigureAwait(false);
                return AttemptOutcome.Paused();
            }
            catch (OperationCanceledException) when (exec.NetworkCts.IsCancellationRequested || !_network.IsConnected)
            {
                return AttemptOutcome.NetworkLost();
            }
            catch (OperationCanceledException)
            {
                return AttemptOutcome.Stopping();
            }
            catch (Exception ex) when (ex is HttpRequestException or IOException or TimeoutException)
            {
                return AttemptOutcome.Transient(ex.Message);
            }
        }

        return AttemptOutcome.Transient("Server did not honor HTTP Range requests after retry.");
    }

    /// <summary>
    /// Verifies the transfer (checksum when the server provided one, else
    /// size/existence) and finalizes the file. Returns true when completed.
    /// </summary>
    private async Task<bool> VerifyAndFinalizeAsync(
        DownloadJob job, string partPath, string finalPath, CancellationToken ct)
    {
        bool ok = await VerifyFileAsync(partPath, job.ChecksumSha256, job.TotalBytes).ConfigureAwait(false);
        if (!ok)
        {
            TryDelete(partPath);
            return false;
        }

        string? sha256 = null;
        try { sha256 = await ComputeSha256Async(partPath, ct).ConfigureAwait(false); }
        catch (Exception ex) { _log.Warn($"Checksum computation failed for job {job.Id}: {ex.Message}"); }

        try
        {
            if (File.Exists(finalPath))
                finalPath = FileNamingTemplate.ResolveUniquePath(
                    _options.DestinationDirectory, _options.NamingTemplate, MediaMetadata.FromJob(job));
            File.Move(partPath, finalPath, overwrite: false);
        }
        catch (Exception ex)
        {
            _log.Error($"Could not finalize file for job {job.Id}.", ex);
            return false;
        }

        var info = new FileInfo(finalPath);
        job.BytesDownloaded = info.Length;
        job.Progress = 1.0;
        job.SpeedBytesPerSec = 0;
        job.ErrorMessage = null;
        await TransitionAsync(job, JobState.Completed, ct, file: new JobFileMetadata(
            finalPath, info.Length, sha256, DateTimeOffset.UtcNow)).ConfigureAwait(false);
        _log.Info($"Job {job.Id} completed: {finalPath}");
        return true;
    }

    private async Task TransitionAsync(
        DownloadJob job, JobState next, CancellationToken ct, JobFileMetadata? file = null)
    {
        var old = job.State;
        JobStateMachine.Validate(old, next);
        job.State = next;
        job.UpdatedAt = DateTimeOffset.UtcNow;
        StateChanged?.Invoke(this, new JobStateEventArgs
        {
            JobId = job.Id,
            OldState = old,
            NewState = next,
        });
        await _cache.UpdateJobAsync(job, ct).ConfigureAwait(false);
        await ReportAsync(job, force: true, ct, file).ConfigureAwait(false);
    }

    private async Task ReportAsync(DownloadJob job, bool force, CancellationToken ct, JobFileMetadata? file = null)
    {
        try
        {
            var now = DateTimeOffset.UtcNow;
            if (!force)
            {
                var last = _lastServerReport.GetValueOrDefault(job.Id, DateTimeOffset.MinValue);
                if (now - last < _options.ServerReportInterval)
                    return;
            }
            _lastServerReport[job.Id] = now;
            await _api.ReportJobStatusAsync(job.Id, new JobStatusReport(
                DtoMapper.ToApiState(job.State),
                job.Progress,
                FilePath: file?.Path,
                FileSize: file?.SizeBytes,
                Checksum: file?.ChecksumSha256,
                Error: job.ErrorMessage), ct).ConfigureAwait(false);
        }
        catch (Exception ex)
        {
            // Best effort: a failed report must never break the download loop.
            _log.Warn($"Status report for job {job.Id} failed: {ex.Message}");
        }
    }

    private async Task NotifyServerPauseAsync(int jobId)
    {
        try
        {
            await _api.PauseJobAsync(jobId).ConfigureAwait(false);
        }
        catch (Exception ex)
        {
            _log.Warn($"Server pause for job {jobId} failed: {ex.Message}");
        }
    }

    private static JobState ResumeTargetFor(JobState prePause) => prePause switch
    {
        JobState.Queued or JobState.Batched => JobState.Queued,
        _ => JobState.Ready,
    };

    private static async Task<bool> VerifyFileAsync(string path, string? expectedSha256, long? expectedSize)
    {
        FileInfo info;
        try { info = new FileInfo(path); }
        catch (Exception) { return false; }
        if (!info.Exists || info.Length == 0)
            return false;
        if (expectedSize.HasValue && info.Length != expectedSize.Value)
            return false;
        if (!string.IsNullOrWhiteSpace(expectedSha256))
        {
            string actual;
            try { actual = await ComputeSha256Async(path).ConfigureAwait(false); }
            catch (Exception) { return false; }
            if (!string.Equals(actual, expectedSha256, StringComparison.OrdinalIgnoreCase))
                return false;
        }
        return true;
    }

    private static async Task<string> ComputeSha256Async(string path, CancellationToken ct = default)
    {
        using var sha = SHA256.Create();
        await using var stream = new FileStream(path, FileMode.Open, FileAccess.Read,
            FileShare.Read, bufferSize: 81920, useAsync: true);
        byte[] hash = await sha.ComputeHashAsync(stream, ct).ConfigureAwait(false);
        return Convert.ToHexString(hash).ToLowerInvariant();
    }

    private static void TryDelete(string path)
    {
        try { if (File.Exists(path)) File.Delete(path); }
        catch (Exception) { /* best effort */ }
    }

    private sealed class JobExecution
    {
        public required DownloadJob Job { get; init; }
        public CancellationTokenSource PauseCts { get; } = new();
        public CancellationTokenSource NetworkCts { get; } = new();
        public Task Task { get; set; } = Task.CompletedTask;
        public bool PauseRequested => PauseCts.IsCancellationRequested;
    }

    private enum AttemptKind
    {
        TransferSucceeded,
        Paused,
        NetworkLost,
        TransientFailure,
        Stopping,
    }

    private sealed class AttemptOutcome
    {
        public required AttemptKind Kind { get; init; }
        public string? Reason { get; init; }
        public string? PartPath { get; init; }
        public string? FinalPath { get; init; }

        public static AttemptOutcome TransferSucceeded(string partPath, string finalPath) => new()
        {
            Kind = AttemptKind.TransferSucceeded, PartPath = partPath, FinalPath = finalPath,
        };
        public static AttemptOutcome Paused() => new() { Kind = AttemptKind.Paused };
        public static AttemptOutcome NetworkLost() => new() { Kind = AttemptKind.NetworkLost };
        public static AttemptOutcome Transient(string reason) => new()
        {
            Kind = AttemptKind.TransientFailure, Reason = reason,
        };
        public static AttemptOutcome Stopping() => new() { Kind = AttemptKind.Stopping };
    }

    public async ValueTask DisposeAsync()
    {
        if (_disposed)
            return;
        _disposed = true;
        await StopAsync().ConfigureAwait(false);
        _slots.Dispose();
        _http.Dispose();
        foreach (var exec in _active.Values)
        {
            exec.PauseCts.Dispose();
            exec.NetworkCts.Dispose();
        }
    }
}
