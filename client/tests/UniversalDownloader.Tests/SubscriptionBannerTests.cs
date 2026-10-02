// Tests for subscription status derivation and the T-3 renewal banner —
// SDS section 7, SRS FR-03 / FR-13.

using UniversalDownloader.Core.Models;

namespace UniversalDownloader.Tests;

public sealed class SubscriptionBannerTests
{
    private static readonly DateTimeOffset Now =
        new(2026, 10, 1, 0, 0, 0, TimeSpan.Zero);

    private static Subscription Sub(DateTimeOffset expiresAt, string? adminStatus = null) => new()
    {
        Id = 1,
        CustomerId = 7,
        StartsAt = Now.AddDays(-30),
        ExpiresAt = expiresAt,
        AdminStatus = adminStatus,
    };

    [Fact]
    public void Active_subscription_is_active_with_full_access()
    {
        var sub = Sub(Now.AddDays(30));
        Assert.Equal(SubscriptionStatus.Active, sub.DeriveStatus(Now));
        Assert.True(sub.AllowsProtectedOperations(Now));
        Assert.False(sub.IsInWarningWindow(Now));
    }

    [Theory]
    [InlineData(3)]
    [InlineData(2)]
    [InlineData(1)]
    public void Expiring_soon_within_three_days_shows_banner(int daysUntilExpiry)
    {
        var sub = Sub(Now.AddDays(daysUntilExpiry).AddMinutes(-1));
        Assert.Equal(SubscriptionStatus.ExpiringSoon, sub.DeriveStatus(Now));
        Assert.True(sub.IsInWarningWindow(Now));
        Assert.True(sub.AllowsProtectedOperations(Now),
            "Expiring subscriptions keep downloading until expiry.");
    }

    [Fact]
    public void Expired_subscription_blocks_protected_operations()
    {
        var sub = Sub(Now.AddSeconds(-1));
        Assert.Equal(SubscriptionStatus.Expired, sub.DeriveStatus(Now));
        Assert.False(sub.AllowsProtectedOperations(Now));
        Assert.False(sub.IsInWarningWindow(Now));
        Assert.Equal(0, sub.DaysRemaining(Now));
    }

    [Theory]
    [InlineData("suspended", SubscriptionStatus.Suspended)]
    [InlineData("disabled", SubscriptionStatus.Disabled)]
    public void Admin_status_overrides_date_derived_status(string adminStatus, SubscriptionStatus expected)
    {
        var sub = Sub(Now.AddDays(30), adminStatus);
        Assert.Equal(expected, sub.DeriveStatus(Now));
        Assert.False(sub.AllowsProtectedOperations(Now));
    }

    [Fact]
    public void DaysRemaining_rounds_up_partial_days()
    {
        var sub = Sub(Now.AddHours(25));
        Assert.Equal(2, sub.DaysRemaining(Now));
    }
}
