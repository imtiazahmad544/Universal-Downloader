// Authentication services — SDS section 8.
// Short-lived access token in memory; rotating refresh token protected with
// Windows DPAPI (never stored in plaintext). No third-party social credentials
// are ever collected (SDS section 19).

using System.Net.Http.Json;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using UniversalDownloader.Infrastructure.Api;

namespace UniversalDownloader.Infrastructure.Auth;

/// <summary>Secure token persistence.</summary>
public interface ITokenStore
{
    Task<string?> GetAccessTokenAsync();
    Task<DateTimeOffset> GetAccessTokenExpiryAsync();
    Task<string?> GetRefreshTokenAsync();
    Task SetTokensAsync(string accessToken, string refreshToken, DateTimeOffset accessTokenExpiresAt);
    Task ClearAsync();
}

/// <summary>
/// DPAPI-backed token store. The refresh token is encrypted with
/// <see cref="ProtectedData"/> (CurrentUser scope) and kept in a file under
/// %AppData%; the access token lives only in memory.
/// </summary>
public sealed class DpapiTokenStore : ITokenStore
{
    private readonly string _filePath;
    private string? _accessToken;
    private DateTimeOffset _accessTokenExpiresAt;

    // Fixed entropy binds the blob to this application.
    private static readonly byte[] Entropy =
        Encoding.UTF8.GetBytes("UniversalDownloader.TokenStore.v1");

    public DpapiTokenStore(string? filePath = null)
    {
        _filePath = filePath ?? Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
            "UniversalDownloader", "tokens.dat");
    }

    public Task<string?> GetAccessTokenAsync() => Task.FromResult(_accessToken);

    public Task<DateTimeOffset> GetAccessTokenExpiryAsync() =>
        Task.FromResult(_accessTokenExpiresAt);

    public Task<string?> GetRefreshTokenAsync()
    {
        try
        {
            if (!File.Exists(_filePath))
                return Task.FromResult<string?>(null);
            byte[] protectedBytes = File.ReadAllBytes(_filePath);
            byte[] raw = ProtectedData.Unprotect(protectedBytes, Entropy, DataProtectionScope.CurrentUser);
            return Task.FromResult<string?>(Encoding.UTF8.GetString(raw));
        }
        catch (Exception)
        {
            // Corrupt or foreign blob: treat as absent rather than crash.
            return Task.FromResult<string?>(null);
        }
    }

    public Task SetTokensAsync(string accessToken, string refreshToken, DateTimeOffset accessTokenExpiresAt)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(accessToken);
        ArgumentException.ThrowIfNullOrWhiteSpace(refreshToken);

        _accessToken = accessToken;
        _accessTokenExpiresAt = accessTokenExpiresAt;

        byte[] raw = Encoding.UTF8.GetBytes(refreshToken);
        byte[] protectedBytes = ProtectedData.Protect(raw, Entropy, DataProtectionScope.CurrentUser);
        Array.Clear(raw, 0, raw.Length);

        Directory.CreateDirectory(Path.GetDirectoryName(_filePath)!);
        File.WriteAllBytes(_filePath, protectedBytes);
        return Task.CompletedTask;
    }

    public Task ClearAsync()
    {
        _accessToken = null;
        _accessTokenExpiresAt = DateTimeOffset.MinValue;
        try
        {
            if (File.Exists(_filePath))
                File.Delete(_filePath);
        }
        catch (Exception) { /* best effort */ }
        return Task.CompletedTask;
    }
}

/// <summary>
/// Login / refresh / logout against /api/v1/auth/*. Uses its own plain
/// HttpClient so it never depends on the authenticated handler (no cycle).
/// </summary>
public sealed class AuthService
{
    private readonly HttpClient _http;
    private readonly ITokenStore _tokens;
    private readonly SemaphoreSlim _refreshGate = new(1, 1);

    public AuthService(HttpClient http, ITokenStore tokens)
    {
        _http = http ?? throw new ArgumentNullException(nameof(http));
        _tokens = tokens ?? throw new ArgumentNullException(nameof(tokens));
    }

    public async Task<bool> IsAuthenticatedAsync()
    {
        var refresh = await _tokens.GetRefreshTokenAsync().ConfigureAwait(false);
        return refresh is not null;
    }

    /// <summary>Authenticates with the admin-assigned customer code + password.</summary>
    public async Task<AuthResult> LoginAsync(string customerCode, string password, CancellationToken ct = default)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(customerCode);
        ArgumentException.ThrowIfNullOrWhiteSpace(password);

        using var response = await _http.PostAsJsonAsync("/api/v1/auth/login",
            new { identifier = customerCode, password }, ct).ConfigureAwait(false);
        if (!response.IsSuccessStatusCode)
        {
            string? body = await TryReadBodyAsync(response).ConfigureAwait(false);
            throw new ApiException(response.StatusCode, "Login failed. Check your customer ID and password.", body);
        }

        var result = await response.Content.ReadFromJsonAsync<AuthResult>(
            new JsonSerializerOptions { PropertyNameCaseInsensitive = true }, ct).ConfigureAwait(false)
            ?? throw new ApiException(response.StatusCode, "Empty login response.");

        await _tokens.SetTokensAsync(result.AccessToken, result.RefreshToken,
            DateTimeOffset.UtcNow.AddSeconds(result.ExpiresInSeconds)).ConfigureAwait(false);
        return result;
    }

    /// <summary>
    /// Rotates the session using the stored refresh token. Serialized so
    /// concurrent 401s trigger exactly one refresh.
    /// </summary>
    public async Task<bool> RefreshAsync(CancellationToken ct = default)
    {
        await _refreshGate.WaitAsync(ct).ConfigureAwait(false);
        try
        {
            var refreshToken = await _tokens.GetRefreshTokenAsync().ConfigureAwait(false);
            if (refreshToken is null)
                return false;

            using var response = await _http.PostAsJsonAsync("/api/v1/auth/refresh",
                new { refresh_token = refreshToken }, ct).ConfigureAwait(false);
            if (!response.IsSuccessStatusCode)
            {
                await _tokens.ClearAsync().ConfigureAwait(false);
                return false;
            }

            var result = await response.Content.ReadFromJsonAsync<AuthResult>(
                new JsonSerializerOptions { PropertyNameCaseInsensitive = true }, ct).ConfigureAwait(false);
            if (result is null)
                return false;

            await _tokens.SetTokensAsync(result.AccessToken, result.RefreshToken,
                DateTimeOffset.UtcNow.AddSeconds(result.ExpiresInSeconds)).ConfigureAwait(false);
            return true;
        }
        finally
        {
            _refreshGate.Release();
        }
    }

    public Task LogoutAsync() => _tokens.ClearAsync();

    private static async Task<string?> TryReadBodyAsync(HttpResponseMessage response)
    {
        try { return await response.Content.ReadAsStringAsync().ConfigureAwait(false); }
        catch (Exception) { return null; }
    }
}

/// <summary>
/// Attaches the bearer token to outgoing requests and performs a single
/// refresh-and-retry on 401 (SDS section 8: short-lived access tokens,
/// rotating refresh tokens; do not retry indefinitely).
/// </summary>
public sealed class AuthenticatedHttpHandler : DelegatingHandler
{
    private readonly AuthService _auth;
    private readonly ITokenStore _tokens;
    private static readonly TimeSpan ExpirySkew = TimeSpan.FromMinutes(1);

    public AuthenticatedHttpHandler(AuthService auth, ITokenStore tokens)
    {
        _auth = auth ?? throw new ArgumentNullException(nameof(auth));
        _tokens = tokens ?? throw new ArgumentNullException(nameof(tokens));
    }

    protected override async Task<HttpResponseMessage> SendAsync(
        HttpRequestMessage request, CancellationToken cancellationToken)
    {
        await EnsureFreshAccessTokenAsync(cancellationToken).ConfigureAwait(false);
        var token = await _tokens.GetAccessTokenAsync().ConfigureAwait(false);
        if (token is not null)
            request.Headers.Authorization =
                new System.Net.Http.Headers.AuthenticationHeaderValue("Bearer", token);

        var response = await base.SendAsync(request, cancellationToken).ConfigureAwait(false);
        if (response.StatusCode == System.Net.HttpStatusCode.Unauthorized)
        {
            response.Dispose();
            bool refreshed = await _auth.RefreshAsync(cancellationToken).ConfigureAwait(false);
            if (!refreshed)
                throw new ApiException(System.Net.HttpStatusCode.Unauthorized,
                    "Session expired. Please log in again.");

            var retry = CloneRequest(request);
            var newToken = await _tokens.GetAccessTokenAsync().ConfigureAwait(false);
            if (newToken is not null)
                retry.Headers.Authorization =
                    new System.Net.Http.Headers.AuthenticationHeaderValue("Bearer", newToken);
            response = await base.SendAsync(retry, cancellationToken).ConfigureAwait(false);
        }
        return response;
    }

    private async Task EnsureFreshAccessTokenAsync(CancellationToken ct)
    {
        var expiry = await _tokens.GetAccessTokenExpiryAsync().ConfigureAwait(false);
        var token = await _tokens.GetAccessTokenAsync().ConfigureAwait(false);
        if (token is null || expiry - ExpirySkew <= DateTimeOffset.UtcNow)
            await _auth.RefreshAsync(ct).ConfigureAwait(false);
    }

    private static HttpRequestMessage CloneRequest(HttpRequestMessage request)
    {
        var clone = new HttpRequestMessage(request.Method, request.RequestUri);
        foreach (var header in request.Headers)
            clone.Headers.TryAddWithoutValidation(header.Key, header.Value);
        // GET/HEAD/DELETE-without-body requests only reach the 401 path with a
        // null or re-readable content; buffer it so the retry can resend it.
        if (request.Content is not null)
        {
            var bytes = request.Content.ReadAsByteArrayAsync().GetAwaiter().GetResult();
            var content = new ByteArrayContent(bytes);
            foreach (var header in request.Content.Headers)
                content.Headers.TryAddWithoutValidation(header.Key, header.Value);
            clone.Content = content;
        }
        clone.Version = request.Version;
        return clone;
    }
}
