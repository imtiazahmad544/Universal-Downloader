// Tests for the retry/backoff policy — SRS FR-08, SDS section 11.

using UniversalDownloader.Worker.Download;

namespace UniversalDownloader.Tests;

public sealed class RetryPolicyTests
{
    [Fact]
    public void ComputeDelay_grows_exponentially_before_cap()
    {
        var baseDelay = TimeSpan.FromSeconds(2);
        var maxDelay = TimeSpan.FromMinutes(5);

        TimeSpan d1 = RetryPolicy.ComputeDelay(1, baseDelay, maxDelay);
        TimeSpan d2 = RetryPolicy.ComputeDelay(2, baseDelay, maxDelay);
        TimeSpan d3 = RetryPolicy.ComputeDelay(3, baseDelay, maxDelay);

        Assert.Equal(baseDelay, d1);
        Assert.Equal(baseDelay * 2, d2);
        Assert.Equal(baseDelay * 4, d3);
    }

    [Fact]
    public void ComputeDelay_is_capped_at_max_delay()
    {
        var maxDelay = TimeSpan.FromMinutes(5);
        TimeSpan delay = RetryPolicy.ComputeDelay(
            attempt: 30, baseDelay: TimeSpan.FromSeconds(2), maxDelay: maxDelay);
        Assert.Equal(maxDelay, delay);
    }

    [Fact]
    public void ComputeDelay_jitter_stays_within_25_percent()
    {
        var baseDelay = TimeSpan.FromSeconds(10);
        var maxDelay = TimeSpan.FromMinutes(5);
        var rng = new Random(42);

        for (int i = 0; i < 200; i++)
        {
            TimeSpan delay = RetryPolicy.ComputeDelay(3, baseDelay, maxDelay, rng);
            Assert.InRange(delay.TotalMilliseconds, 40.0, 50.0);
        }
    }

    [Fact]
    public void ComputeDelay_rejects_invalid_attempt()
    {
        Assert.Throws<ArgumentOutOfRangeException>(() =>
            RetryPolicy.ComputeDelay(0, TimeSpan.FromSeconds(2), TimeSpan.FromMinutes(5)));
    }

    [Theory]
    [InlineData(0, true)]
    [InlineData(4, true)]
    [InlineData(5, false)]
    [InlineData(6, false)]
    public void CanRetry_allows_up_to_max_attempts(int attemptsSoFar, bool expected)
    {
        Assert.Equal(expected, RetryPolicy.CanRetry(attemptsSoFar));
    }
}
