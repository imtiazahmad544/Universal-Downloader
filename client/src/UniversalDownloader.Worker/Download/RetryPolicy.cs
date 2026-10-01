// Retry/backoff policy — SRS FR-08, SDS section 11.
// Bounded exponential backoff with jitter for transient failures.

namespace UniversalDownloader.Worker.Download;

/// <summary>Bounded exponential backoff with jitter (SRS FR-08).</summary>
public static class RetryPolicy
{
    /// <summary>Maximum download attempts per job before it becomes Failed.</summary>
    public const int MaxAttempts = 5;

    public static readonly TimeSpan DefaultBaseDelay = TimeSpan.FromSeconds(2);
    public static readonly TimeSpan DefaultMaxDelay = TimeSpan.FromMinutes(5);

    /// <summary>
    /// Computes the delay before attempt <paramref name="attempt"/> (1-based):
    /// min(baseDelay * 2^(attempt-1), maxDelay), plus up to 25% jitter.
    /// </summary>
    public static TimeSpan ComputeDelay(int attempt, TimeSpan baseDelay, TimeSpan maxDelay, Random? rng = null)
    {
        if (attempt < 1)
            throw new ArgumentOutOfRangeException(nameof(attempt));
        if (baseDelay <= TimeSpan.Zero)
            throw new ArgumentOutOfRangeException(nameof(baseDelay));
        if (maxDelay <= TimeSpan.Zero)
            throw new ArgumentOutOfRangeException(nameof(maxDelay));

        double exponential = baseDelay.TotalMilliseconds * Math.Pow(2, attempt - 1);
        double capped = Math.Min(exponential, maxDelay.TotalMilliseconds);
        double jitter = capped * (rng?.NextDouble() ?? 0.0) * 0.25;
        return TimeSpan.FromMilliseconds(capped + jitter);
    }

    /// <summary>True when another attempt is allowed after <paramref name="attemptsSoFar"/> failures.</summary>
    public static bool CanRetry(int attemptsSoFar, int maxAttempts = MaxAttempts) =>
        attemptsSoFar < maxAttempts;
}
