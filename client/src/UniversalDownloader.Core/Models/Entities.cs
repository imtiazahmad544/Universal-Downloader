namespace UniversalDownloader.Core.Models;

/// <summary>Customer identity. <see cref="CustomerCode"/> is the manually assigned admin ID.</summary>
public sealed class Customer
{
    public int Id { get; init; }
    public string CustomerCode { get; init; } = string.Empty;
    public string Name { get; init; } = string.Empty;
    public string? ContactEmail { get; init; }
    public string? ContactPhone { get; init; }
    public string Status { get; init; } = "active";
    public DateTimeOffset CreatedAt { get; init; }
}

/// <summary>
/// Subscription state. Status derivation follows SDS section 7 exactly.
/// </summary>
public sealed class Subscription
{
    public int Id { get; init; }
    public int CustomerId { get; init; }
    public DateTimeOffset StartsAt { get; init; }
    public DateTimeOffset ExpiresAt { get; init; }

    /// <summary>
    /// Explicit administrative status from the server ("active", "suspended", "disabled").
    /// Null or "active" means the status is date-derived.
    /// </summary>
    public string? AdminStatus { get; init; }

    private static readonly TimeSpan WarningWindow = TimeSpan.FromDays(3);

    /// <summary>Derives the effective status at <paramref name="now"/>.</summary>
    public SubscriptionStatus DeriveStatus(DateTimeOffset now)
    {
        if (string.Equals(AdminStatus, "suspended", StringComparison.OrdinalIgnoreCase))
            return SubscriptionStatus.Suspended;
        if (string.Equals(AdminStatus, "disabled", StringComparison.OrdinalIgnoreCase))
            return SubscriptionStatus.Disabled;
        if (ExpiresAt <= now)
            return SubscriptionStatus.Expired;
        if (ExpiresAt <= now + WarningWindow)
            return SubscriptionStatus.ExpiringSoon;
        return SubscriptionStatus.Active;
    }

    /// <summary>Whole days remaining, rounded up; never negative.</summary>
    public int DaysRemaining(DateTimeOffset now)
    {
        var remaining = ExpiresAt - now;
        return remaining <= TimeSpan.Zero ? 0 : (int)Math.Ceiling(remaining.TotalDays);
    }

    /// <summary>True when the T-3 renewal banner must be shown (SRS FR-03/FR-13).</summary>
    public bool IsInWarningWindow(DateTimeOffset now) =>
        DeriveStatus(now) == SubscriptionStatus.ExpiringSoon;

    /// <summary>True when protected discovery/download operations are allowed.</summary>
    public bool AllowsProtectedOperations(DateTimeOffset now) =>
        DeriveStatus(now) is SubscriptionStatus.Active or SubscriptionStatus.ExpiringSoon;
}

/// <summary>Configured creator source (TikTok/Instagram username or YouTube channel).</summary>
public sealed class Source
{
    public int Id { get; init; }
    public int CustomerId { get; init; }
    public Platform Platform { get; init; }
    public string InputValue { get; init; } = string.Empty;
    public string CanonicalId { get; init; } = string.Empty;
    public SourceStatus Status { get; set; }
    public DateTimeOffset CreatedAt { get; init; }
    public DateTimeOffset UpdatedAt { get; set; }
}

/// <summary>Deduplicated discovered media metadata (SRS FR-05).</summary>
public sealed class MediaItem
{
    public int Id { get; init; }
    public int SourceId { get; init; }
    public Platform Platform { get; init; }
    public string CanonicalUrl { get; init; } = string.Empty;
    public string? ExternalId { get; init; }
    public string? Title { get; init; }
    public string? MediaType { get; init; }
    public string? ChecksumSha256 { get; init; }
    public long? SizeBytes { get; init; }
    public DateTimeOffset DiscoveredAt { get; init; }
}

/// <summary>Daily scheduling unit (SRS FR-07).</summary>
public sealed class Batch
{
    public int Id { get; init; }
    public int CustomerId { get; init; }
    public DateOnly BatchDate { get; init; }
    public string Timezone { get; init; } = string.Empty;
    public int Size { get; init; }
    public BatchStatus Status { get; init; }
}

/// <summary>
/// Download job. State transitions must go through
/// <see cref="StateMachine.JobStateMachine"/> (SRS FR-10, section 8).
/// </summary>
public sealed class DownloadJob
{
    public int Id { get; init; }
    public int CustomerId { get; init; }
    public int MediaItemId { get; init; }
    public int? BatchId { get; init; }

    public JobState State { get; set; }
    public int Attempts { get; set; }
    public string? Provider { get; set; }

    public double Progress { get; set; }
    public long BytesDownloaded { get; set; }
    public long? TotalBytes { get; set; }
    public double SpeedBytesPerSec { get; set; }
    public string? ErrorMessage { get; set; }

    // Denormalized display/download metadata (from the media item + source).
    public string Title { get; init; } = string.Empty;
    public Platform Platform { get; init; }
    public string SourceName { get; init; } = string.Empty;
    public string MediaUrl { get; init; } = string.Empty;
    public string? ChecksumSha256 { get; init; }
    public DateOnly MediaDate { get; init; }
    public string FileExtension { get; init; } = "mp4";

    public DateTimeOffset CreatedAt { get; init; }
    public DateTimeOffset UpdatedAt { get; set; }
}
