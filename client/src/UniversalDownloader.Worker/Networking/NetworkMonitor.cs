// Network connectivity monitor — SDS section 13.
// Tests actual internet reachability (not just local interface state) via a
// periodic probe of the API health endpoint, combined with OS network events.
// Connectivity loss moves affected jobs to WAITING_FOR_NETWORK — never to FAILED.

namespace UniversalDownloader.Worker.Networking;

/// <summary>
/// Monitors usable internet connectivity. Raises <see cref="ConnectivityChanged"/>
/// whenever reachability flips. Loss never marks jobs as failed; the
/// <see cref="Download.DownloadWorker"/> holds them in WAITING_FOR_NETWORK.
/// </summary>
public sealed class NetworkMonitor : IDisposable
{
    private readonly HttpClient _http;
    private readonly string _probeUrl;
    private readonly TimeSpan _probeInterval;
    private Timer? _timer;
    private int _isConnected; // 0/1 for interlocked access
    private bool _disposed;

    /// <summary>Raised with the new connectivity value whenever it changes.</summary>
    public event EventHandler<bool>? ConnectivityChanged;

    public bool IsConnected => Volatile.Read(ref _isConnected) == 1;

    /// <param name="apiBaseUrl">API base URL; <c>/health</c> is probed.</param>
    /// <param name="probeInterval">How often to probe reachability.</param>
    public NetworkMonitor(string apiBaseUrl, TimeSpan? probeInterval = null)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(apiBaseUrl);
        _probeUrl = apiBaseUrl.TrimEnd('/') + "/health";
        _probeInterval = probeInterval ?? TimeSpan.FromSeconds(30);
        _http = new HttpClient { Timeout = TimeSpan.FromSeconds(5) };
    }

    public void Start()
    {
        ObjectDisposedException.ThrowIf(_disposed, this);
        if (_timer is not null)
            return;

        System.Net.NetworkInformation.NetworkChange.NetworkAvailabilityChanged += OnNetworkAvailabilityChanged;
        System.Net.NetworkInformation.NetworkChange.NetworkAddressChanged += OnNetworkAddressChanged;

        // Probe immediately, then on the interval.
        _ = ProbeAsync();
        _timer = new Timer(_ => _ = ProbeAsync(), null, _probeInterval, _probeInterval);
    }

    public void Stop()
    {
        _timer?.Dispose();
        _timer = null;
        System.Net.NetworkInformation.NetworkChange.NetworkAvailabilityChanged -= OnNetworkAvailabilityChanged;
        System.Net.NetworkInformation.NetworkChange.NetworkAddressChanged -= OnNetworkAddressChanged;
    }

    private void OnNetworkAvailabilityChanged(object? sender, System.Net.NetworkInformation.NetworkAvailabilityEventArgs e)
    {
        if (!e.IsAvailable)
            SetConnected(false); // Fast path down; the probe confirms recovery.
        else
            _ = ProbeAsync();
    }

    private void OnNetworkAddressChanged(object? sender, EventArgs e) => _ = ProbeAsync();

    /// <summary>
    /// Tests actual reachability. Any HTTP response (even 404) proves the path
    /// works; only transport-level failures count as offline.
    /// </summary>
    public async Task<bool> ProbeAsync()
    {
        bool reachable;
        try
        {
            using var response = await _http.GetAsync(_probeUrl).ConfigureAwait(false);
            reachable = true;
        }
        catch (HttpRequestException)
        {
            reachable = false;
        }
        catch (TaskCanceledException)
        {
            reachable = false;
        }
        SetConnected(reachable);
        return reachable;
    }

    private void SetConnected(bool connected)
    {
        int next = connected ? 1 : 0;
        if (Interlocked.Exchange(ref _isConnected, next) != next)
            ConnectivityChanged?.Invoke(this, connected);
    }

    public void Dispose()
    {
        if (_disposed)
            return;
        _disposed = true;
        Stop();
        _http.Dispose();
    }
}
