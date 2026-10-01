// Typed HTTP client for the Universal Downloader SaaS API.
// Covers every customer endpoint in SDS section 9 (v1.1), plus
// PATCH /api/v1/jobs/{id}/status for client progress/state/completion reporting.

using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using UniversalDownloader.Core.Models;

namespace UniversalDownloader.Infrastructure.Api;

/// <summary>API error with the HTTP status code preserved.</summary>
public sealed class ApiException : Exception
{
    public HttpStatusCode StatusCode { get; }
    public string? ResponseBody { get; }

    public ApiException(HttpStatusCode statusCode, string message, string? responseBody = null)
        : base(message)
    {
        StatusCode = statusCode;
        ResponseBody = responseBody;
    }
}

/// <summary>Customer-facing API surface (SDS section 9, v1.1).</summary>
public interface IApiClient
{
    Task<MeResponseDto> GetMeAsync(CancellationToken ct = default);
    Task<SubscriptionDto> GetSubscriptionAsync(CancellationToken ct = default);

    Task<IReadOnlyList<SourceDto>> ListSourcesAsync(CancellationToken ct = default);
    Task<SourceDto> CreateSourceAsync(string platform, string inputValue, CancellationToken ct = default);
    Task<SourceDto> UpdateSourceAsync(int id, UpdateSourceRequest request, CancellationToken ct = default);
    Task<SourceDto> DeleteSourceAsync(int id, CancellationToken ct = default);
    Task RequestDiscoveryAsync(int sourceId, CancellationToken ct = default);

    Task<IReadOnlyList<JobDto>> ListJobsAsync(string? status = null, CancellationToken ct = default);
    Task<JobDto> RetryJobAsync(int id, CancellationToken ct = default);
    Task<JobDto> PauseJobAsync(int id, CancellationToken ct = default);
    Task<JobDto> ResumeJobAsync(int id, CancellationToken ct = default);
    Task ReportJobStatusAsync(int id, JobStatusReport report, CancellationToken ct = default);

    Task<IReadOnlyList<BatchDto>> ListBatchesAsync(CancellationToken ct = default);
}

/// <summary>HttpClient-based implementation. Authentication headers are attached by
/// <see cref="Auth.AuthenticatedHttpHandler"/>; this class only maps endpoints.</summary>
public sealed class ApiClient : IApiClient
{
    private readonly HttpClient _http;
    private readonly JsonSerializerOptions _json;

    public ApiClient(HttpClient http)
    {
        _http = http ?? throw new ArgumentNullException(nameof(http));
        _json = new JsonSerializerOptions
        {
            PropertyNameCaseInsensitive = true,
        };
    }

    public Task<MeResponseDto> GetMeAsync(CancellationToken ct = default) =>
        GetAsync<MeResponseDto>("/api/v1/me", ct);

    public Task<SubscriptionDto> GetSubscriptionAsync(CancellationToken ct = default) =>
        GetAsync<SubscriptionDto>("/api/v1/subscription", ct);

    public Task<IReadOnlyList<SourceDto>> ListSourcesAsync(CancellationToken ct = default) =>
        GetAsync<IReadOnlyList<SourceDto>>("/api/v1/sources", ct);

    public Task<SourceDto> CreateSourceAsync(string platform, string inputValue, CancellationToken ct = default) =>
        PostAsync<CreateSourceRequest, SourceDto>("/api/v1/sources", new CreateSourceRequest(platform, inputValue), ct);

    public Task<SourceDto> UpdateSourceAsync(int id, UpdateSourceRequest request, CancellationToken ct = default) =>
        PatchAsync<UpdateSourceRequest, SourceDto>($"/api/v1/sources/{id}", request, ct);

    public Task<SourceDto> DeleteSourceAsync(int id, CancellationToken ct = default) =>
        DeleteAsync<SourceDto>($"/api/v1/sources/{id}", ct);

    public async Task RequestDiscoveryAsync(int sourceId, CancellationToken ct = default)
    {
        using var response = await _http.PostAsync($"/api/v1/sources/{sourceId}/discover", null, ct)
            .ConfigureAwait(false);
        await EnsureSuccessAsync(response).ConfigureAwait(false);
    }

    public Task<IReadOnlyList<JobDto>> ListJobsAsync(string? status = null, CancellationToken ct = default) =>
        GetAsync<IReadOnlyList<JobDto>>(
            status is null ? "/api/v1/jobs" : $"/api/v1/jobs?status={Uri.EscapeDataString(status)}", ct);

    public Task<JobDto> RetryJobAsync(int id, CancellationToken ct = default) =>
        PostEmptyAsync<JobDto>($"/api/v1/jobs/{id}/retry", ct);

    public Task<JobDto> PauseJobAsync(int id, CancellationToken ct = default) =>
        PostEmptyAsync<JobDto>($"/api/v1/jobs/{id}/pause", ct);

    public Task<JobDto> ResumeJobAsync(int id, CancellationToken ct = default) =>
        PostEmptyAsync<JobDto>($"/api/v1/jobs/{id}/resume", ct);

    public async Task ReportJobStatusAsync(int id, JobStatusReport report, CancellationToken ct = default)
    {
        using var response = await _http.PatchAsJsonAsync($"/api/v1/jobs/{id}/status", report, _json, ct)
            .ConfigureAwait(false);
        await EnsureSuccessAsync(response).ConfigureAwait(false);
    }

    public Task<IReadOnlyList<BatchDto>> ListBatchesAsync(CancellationToken ct = default) =>
        GetAsync<IReadOnlyList<BatchDto>>("/api/v1/batches", ct);

    private async Task<T> GetAsync<T>(string path, CancellationToken ct)
    {
        using var response = await _http.GetAsync(path, ct).ConfigureAwait(false);
        await EnsureSuccessAsync(response).ConfigureAwait(false);
        var result = await response.Content.ReadFromJsonAsync<T>(_json, ct).ConfigureAwait(false);
        return result ?? throw new ApiException(response.StatusCode, $"Empty response from GET {path}.");
    }

    private async Task<TResponse> PostAsync<TRequest, TResponse>(string path, TRequest body, CancellationToken ct)
    {
        using var response = await _http.PostAsJsonAsync(path, body, _json, ct).ConfigureAwait(false);
        await EnsureSuccessAsync(response).ConfigureAwait(false);
        var result = await response.Content.ReadFromJsonAsync<TResponse>(_json, ct).ConfigureAwait(false);
        return result ?? throw new ApiException(response.StatusCode, $"Empty response from POST {path}.");
    }

    private async Task<T> PostEmptyAsync<T>(string path, CancellationToken ct)
    {
        using var response = await _http.PostAsync(path, null, ct).ConfigureAwait(false);
        await EnsureSuccessAsync(response).ConfigureAwait(false);
        var result = await response.Content.ReadFromJsonAsync<T>(_json, ct).ConfigureAwait(false);
        return result ?? throw new ApiException(response.StatusCode, $"Empty response from POST {path}.");
    }

    private async Task<T> DeleteAsync<T>(string path, CancellationToken ct)
    {
        using var response = await _http.DeleteAsync(path, ct).ConfigureAwait(false);
        await EnsureSuccessAsync(response).ConfigureAwait(false);
        var result = await response.Content.ReadFromJsonAsync<T>(_json, ct).ConfigureAwait(false);
        return result ?? throw new ApiException(response.StatusCode, $"Empty response from DELETE {path}.");
    }

    private async Task<TResponse> PatchAsync<TRequest, TResponse>(string path, TRequest body, CancellationToken ct)
    {
        using var response = await _http.PatchAsJsonAsync(path, body, _json, ct).ConfigureAwait(false);
        await EnsureSuccessAsync(response).ConfigureAwait(false);
        var result = await response.Content.ReadFromJsonAsync<TResponse>(_json, ct).ConfigureAwait(false);
        return result ?? throw new ApiException(response.StatusCode, $"Empty response from PATCH {path}.");
    }

    private static async Task EnsureSuccessAsync(HttpResponseMessage response)
    {
        if (response.IsSuccessStatusCode)
            return;
        string? body = null;
        try { body = await response.Content.ReadAsStringAsync().ConfigureAwait(false); }
        catch (Exception) { /* best effort */ }
        throw new ApiException(response.StatusCode,
            $"API request failed: {(int)response.StatusCode} {response.ReasonPhrase}.", body);
    }
}

/// <summary>Maps API DTOs to Core domain models.</summary>
public static class DtoMapper
{
    /// <summary>
    /// The API emits naive UTC timestamps (SDS section 7); System.Text.Json parses
    /// those into DateTimeOffset anchored at local time, so re-anchor the wire
    /// wall-clock to +00:00.
    /// </summary>
    private static DateTimeOffset AsUtc(DateTimeOffset value) =>
        new(value.DateTime, TimeSpan.Zero);

    public static Customer ToCustomer(CustomerDto d) => new()
    {
        Id = d.Id,
        CustomerCode = d.CustomerCode,
        Name = d.Name,
        ContactEmail = d.ContactEmail,
        ContactPhone = d.ContactPhone,
        Status = d.Status,
        CreatedAt = AsUtc(d.CreatedAt),
    };

    public static Subscription ToSubscription(SubscriptionDto d) => new()
    {
        Id = d.Id,
        CustomerId = d.CustomerId,
        StartsAt = AsUtc(d.StartsAt),
        ExpiresAt = AsUtc(d.ExpiresAt),
        AdminStatus = d.AdminStatus,
    };

    /// <summary>
    /// Maps /me's subscription, which is null when the customer has no
    /// subscription row. Synthesizes an expired subscription so the UI treats
    /// it as no-access (SRS FR-03).
    /// </summary>
    public static Subscription ToSubscription(SubscriptionDto? d, int customerId) =>
        d is null
            ? new Subscription
            {
                Id = 0,
                CustomerId = customerId,
                StartsAt = DateTimeOffset.MinValue,
                ExpiresAt = DateTimeOffset.MinValue,
                AdminStatus = "expired",
            }
            : ToSubscription(d);

    public static Source ToSource(SourceDto d) => new()
    {
        Id = d.Id,
        CustomerId = d.CustomerId,
        Platform = ParsePlatform(d.Platform),
        InputValue = d.InputValue,
        CanonicalId = d.CanonicalId,
        Status = string.Equals(d.Status, "paused", StringComparison.OrdinalIgnoreCase)
            ? SourceStatus.Paused : SourceStatus.Active,
        CreatedAt = AsUtc(d.CreatedAt),
        UpdatedAt = AsUtc(d.UpdatedAt),
    };

    public static Batch ToBatch(BatchDto d) => new()
    {
        Id = d.Id,
        CustomerId = d.CustomerId,
        BatchDate = d.BatchDate,
        Timezone = d.Timezone,
        Size = d.Size,
        Status = Enum.TryParse<BatchStatus>(d.Status, true, out var s) ? s : BatchStatus.Pending,
    };

    public static DownloadJob ToJob(JobDto d)
    {
        var media = d.Media;
        return new DownloadJob
        {
            Id = d.Id,
            CustomerId = d.CustomerId,
            MediaItemId = d.MediaItemId,
            BatchId = d.BatchId,
            State = ParseJobState(d.Status),
            Attempts = d.Attempts,
            Provider = d.Provider,
            Progress = d.Progress,
            BytesDownloaded = d.BytesDownloaded,
            TotalBytes = d.TotalBytes,
            ErrorMessage = d.ErrorMessage,
            Title = media?.Title ?? string.Empty,
            Platform = media is null ? Platform.YouTube : ParsePlatform(media.Platform),
            // Optional in the API payload; falls back to "untitled" in naming.
            SourceName = media?.SourceName ?? string.Empty,
            MediaUrl = media?.CanonicalUrl ?? string.Empty,
            ChecksumSha256 = media?.ChecksumSha256,
            MediaDate = DateOnly.FromDateTime(AsUtc(media?.DiscoveredAt ?? d.CreatedAt).UtcDateTime),
            CreatedAt = AsUtc(d.CreatedAt),
            UpdatedAt = AsUtc(d.UpdatedAt),
        };
    }

    public static Platform ParsePlatform(string value) => value.Trim().ToLowerInvariant() switch
    {
        "tiktok" => Platform.TikTok,
        "instagram" => Platform.Instagram,
        "youtube" => Platform.YouTube,
        _ => throw new ArgumentOutOfRangeException(nameof(value), $"Unknown platform '{value}'."),
    };

    public static JobState ParseJobState(string value) => value.Trim().ToLowerInvariant() switch
    {
        "discovered" => JobState.Discovered,
        "queued" => JobState.Queued,
        "batched" => JobState.Batched,
        "ready" => JobState.Ready,
        "downloading" => JobState.Downloading,
        "retry_wait" => JobState.RetryWait,
        "completed" => JobState.Completed,
        "failed" => JobState.Failed,
        "paused" => JobState.Paused,
        "cancelled" => JobState.Cancelled,
        "waiting_for_network" => JobState.WaitingForNetwork,
        "invalid" => JobState.Invalid,
        "duplicate" => JobState.Duplicate,
        "requeued" => JobState.Requeued,
        _ => throw new ArgumentOutOfRangeException(nameof(value), $"Unknown job state '{value}'."),
    };

    public static string ToApiState(JobState state) => state switch
    {
        JobState.Discovered => "discovered",
        JobState.Queued => "queued",
        JobState.Batched => "batched",
        JobState.Ready => "ready",
        JobState.Downloading => "downloading",
        JobState.RetryWait => "retry_wait",
        JobState.Completed => "completed",
        JobState.Failed => "failed",
        JobState.Paused => "paused",
        JobState.Cancelled => "cancelled",
        JobState.WaitingForNetwork => "waiting_for_network",
        JobState.Invalid => "invalid",
        JobState.Duplicate => "duplicate",
        JobState.Requeued => "requeued",
        _ => throw new ArgumentOutOfRangeException(nameof(state)),
    };
}
