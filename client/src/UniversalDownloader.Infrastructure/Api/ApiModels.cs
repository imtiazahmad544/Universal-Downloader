// API data-transfer objects. JSON uses snake_case per the FastAPI backend.

using System.Text.Json.Serialization;

namespace UniversalDownloader.Infrastructure.Api;

public sealed record AuthResult(
    [property: JsonPropertyName("access_token")] string AccessToken,
    [property: JsonPropertyName("refresh_token")] string RefreshToken,
    [property: JsonPropertyName("expires_in")] int ExpiresInSeconds);

public sealed record CustomerDto(
    [property: JsonPropertyName("id")] int Id,
    [property: JsonPropertyName("customer_code")] string CustomerCode,
    [property: JsonPropertyName("name")] string Name,
    [property: JsonPropertyName("contact_email")] string? ContactEmail,
    [property: JsonPropertyName("contact_phone")] string? ContactPhone,
    [property: JsonPropertyName("status")] string Status,
    [property: JsonPropertyName("created_at")] DateTimeOffset CreatedAt);

public sealed record SubscriptionDto(
    [property: JsonPropertyName("id")] int Id,
    [property: JsonPropertyName("customer_id")] int CustomerId,
    [property: JsonPropertyName("starts_at")] DateTimeOffset StartsAt,
    [property: JsonPropertyName("expires_at")] DateTimeOffset ExpiresAt,
    [property: JsonPropertyName("effective_status")] string? AdminStatus);

public sealed record MeResponseDto(
    [property: JsonPropertyName("customer")] CustomerDto Customer,
    [property: JsonPropertyName("subscription")] SubscriptionDto? Subscription);

public sealed record SourceDto(
    [property: JsonPropertyName("id")] int Id,
    [property: JsonPropertyName("customer_id")] int CustomerId,
    [property: JsonPropertyName("platform")] string Platform,
    [property: JsonPropertyName("input_value")] string InputValue,
    [property: JsonPropertyName("canonical_id")] string CanonicalId,
    [property: JsonPropertyName("status")] string Status,
    [property: JsonPropertyName("has_cookies")] bool HasCookies,
    [property: JsonPropertyName("created_at")] DateTimeOffset CreatedAt,
    [property: JsonPropertyName("updated_at")] DateTimeOffset UpdatedAt);

public sealed record CreateSourceRequest(
    [property: JsonPropertyName("platform")] string Platform,
    [property: JsonPropertyName("input_value")] string InputValue);

/// <summary>Status: "active" | "paused" (PATCH /api/v1/sources/{id}).
/// Removal uses DELETE /api/v1/sources/{id} (server-side removal policy:
/// queued/batched/ready jobs cancelled, completed history preserved).</summary>
public sealed record UpdateSourceRequest(
    [property: JsonPropertyName("status")] string? Status);

public sealed record MediaItemDto(
    [property: JsonPropertyName("id")] int Id,
    [property: JsonPropertyName("source_id")] int SourceId,
    [property: JsonPropertyName("source_name")] string? SourceName,
    [property: JsonPropertyName("platform")] string Platform,
    [property: JsonPropertyName("canonical_url")] string CanonicalUrl,
    [property: JsonPropertyName("external_id")] string? ExternalId,
    [property: JsonPropertyName("title")] string? Title,
    [property: JsonPropertyName("media_type")] string? MediaType,
    [property: JsonPropertyName("checksum_sha256")] string? ChecksumSha256,
    [property: JsonPropertyName("size_bytes")] long? SizeBytes,
    [property: JsonPropertyName("discovered_at")] DateTimeOffset DiscoveredAt);

public sealed record JobDto(
    [property: JsonPropertyName("id")] int Id,
    [property: JsonPropertyName("customer_id")] int CustomerId,
    [property: JsonPropertyName("media_item_id")] int MediaItemId,
    [property: JsonPropertyName("batch_id")] int? BatchId,
    [property: JsonPropertyName("status")] string Status,
    [property: JsonPropertyName("attempts")] int Attempts,
    [property: JsonPropertyName("provider")] string? Provider,
    [property: JsonPropertyName("progress")] double Progress,
    [property: JsonPropertyName("bytes_downloaded")] long BytesDownloaded,
    [property: JsonPropertyName("total_bytes")] long? TotalBytes,
    [property: JsonPropertyName("error_message")] string? ErrorMessage,
    [property: JsonPropertyName("captcha_required")] bool CaptchaRequired,
    [property: JsonPropertyName("media")] MediaItemDto? Media,
    [property: JsonPropertyName("created_at")] DateTimeOffset CreatedAt,
    [property: JsonPropertyName("updated_at")] DateTimeOffset UpdatedAt);

public sealed record JobFileMetadata(
    [property: JsonPropertyName("path")] string Path,
    [property: JsonPropertyName("size_bytes")] long SizeBytes,
    [property: JsonPropertyName("checksum_sha256")] string? ChecksumSha256,
    [property: JsonPropertyName("completed_at")] DateTimeOffset CompletedAt);

/// <summary>
/// Client-reported job status (progress / state / completion with file metadata).
/// Sent to PATCH /api/v1/jobs/{id}/status.
/// </summary>
/// <summary>
/// Client telemetry for PATCH /api/v1/jobs/{id}/status. Shape matches the
/// server's JobStatusUpdate exactly (status/progress/file_path/file_size/
/// checksum/error); re-reporting the current status is a 200 no-op.
/// </summary>
public sealed record JobStatusReport(
    [property: JsonPropertyName("status")] string Status,
    [property: JsonPropertyName("progress")] double Progress,
    [property: JsonPropertyName("file_path")] string? FilePath,
    [property: JsonPropertyName("file_size")] long? FileSize,
    [property: JsonPropertyName("checksum")] string? Checksum,
    [property: JsonPropertyName("error")] string? Error);

public sealed record BatchDto(
    [property: JsonPropertyName("id")] int Id,
    [property: JsonPropertyName("customer_id")] int CustomerId,
    [property: JsonPropertyName("batch_date")] DateOnly BatchDate,
    [property: JsonPropertyName("timezone")] string Timezone,
    [property: JsonPropertyName("size")] int Size,
    [property: JsonPropertyName("status")] string Status);

/// <summary>v2.0: PATCH /api/v1/me/settings body.</summary>
public sealed record UpdateMySettingsRequest(
    [property: JsonPropertyName("daily_start_time")] string? DailyStartTime);

/// <summary>v2.0: link-extractor test request (POST /api/v1/extract).</summary>
public sealed record ExtractRequest(
    [property: JsonPropertyName("url")] string Url);

/// <summary>
/// v2.0: link-extractor test result (POST /api/v1/extract).
/// Field names match the server's ExtractResponse exactly.
/// </summary>
public sealed record ExtractResultDto(
    [property: JsonPropertyName("ok")] bool Ok,
    [property: JsonPropertyName("media_url")] string? MediaUrl,
    [property: JsonPropertyName("title")] string? Title,
    [property: JsonPropertyName("ext")] string? Ext,
    [property: JsonPropertyName("strategy")] string? Strategy,
    [property: JsonPropertyName("error")] string? Error,
    [property: JsonPropertyName("captcha_required")] bool CaptchaRequired);

/// <summary>
/// v2.0: presence-only cookies status. Raw cookie values are NEVER returned
/// by any endpoint — only this flag.
/// </summary>
public sealed record CookiesStatusDto(
    [property: JsonPropertyName("present")] bool Present,
    [property: JsonPropertyName("updated_at")] DateTimeOffset? UpdatedAt);
