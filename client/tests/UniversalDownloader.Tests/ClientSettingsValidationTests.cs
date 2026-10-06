// Tests for v2.0 settings validation — daily start time and concurrency limit.

using UniversalDownloader.Core.Configuration;

namespace UniversalDownloader.Tests;

public sealed class ClientSettingsValidationTests
{
    [Theory]
    [InlineData("00:00", 0, 0)]
    [InlineData("23:59", 23, 59)]
    [InlineData("12:30", 12, 30)]
    [InlineData("9:05", 9, 5)]
    public void TryParseDailyStartTime_accepts_valid(string input, int hour, int minute)
    {
        Assert.True(ClientSettingsValidator.TryParseDailyStartTime(input, out var time));
        Assert.Equal(new TimeOnly(hour, minute), time);
    }

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData("   ")]
    [InlineData("24:00")]
    [InlineData("12:60")]
    [InlineData("ab:cd")]
    [InlineData("12345")]
    [InlineData("12-30")]
    [InlineData("1:2:3")]
    public void TryParseDailyStartTime_rejects_invalid(string? input)
    {
        Assert.False(ClientSettingsValidator.TryParseDailyStartTime(input, out _));
    }

    [Fact]
    public void NormalizeDailyStartTime_pads_to_HH_MM()
    {
        Assert.Equal("09:05", ClientSettingsValidator.NormalizeDailyStartTime(new TimeOnly(9, 5)));
        Assert.Equal("00:00", ClientSettingsValidator.NormalizeDailyStartTime(new TimeOnly(0, 0)));
        Assert.Equal("23:59", ClientSettingsValidator.NormalizeDailyStartTime(new TimeOnly(23, 59)));
    }

    [Theory]
    [InlineData(1, true)]
    [InlineData(3, true)]
    [InlineData(5, true)]
    [InlineData(0, false)]
    [InlineData(6, false)]
    [InlineData(8, false)]
    [InlineData(-1, false)]
    public void IsValidConcurrencyLimit_enforces_1_to_5(int value, bool expected)
    {
        Assert.Equal(expected, ClientSettingsValidator.IsValidConcurrencyLimit(value));
    }

    [Fact]
    public void ClientSettings_defaults_match_v2_contract()
    {
        var settings = new ClientSettings();
        Assert.Equal("00:00", settings.DailyStartTime);
        Assert.Equal(3, settings.ConcurrencyLimit);
        Assert.Null(settings.GlobalCookiesFilePath);
        Assert.True(ClientSettingsValidator.TryParseDailyStartTime(settings.DailyStartTime, out _));
        Assert.True(ClientSettingsValidator.IsValidConcurrencyLimit(settings.ConcurrencyLimit));
    }
}
