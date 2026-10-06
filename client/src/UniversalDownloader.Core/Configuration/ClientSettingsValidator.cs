// Validation helpers for ClientSettings — v2.0.
// Kept in Core (net8.0, no OS dependencies) so the rules are unit-testable.

using System.Globalization;

namespace UniversalDownloader.Core.Configuration;

/// <summary>Validation rules for <see cref="ClientSettings"/> (v2.0).</summary>
public static class ClientSettingsValidator
{
    public const int MinConcurrencyLimit = 1;
    public const int MaxConcurrencyLimit = 5;

    private static readonly string[] DailyStartTimeFormats = ["HH:mm", "H:mm"];

    /// <summary>
    /// Parses a daily start time in 24-hour "HH:MM" form. The single-digit
    /// "H:mm" form is accepted too; use <see cref="NormalizeDailyStartTime"/>
    /// to canonicalize.
    /// </summary>
    public static bool TryParseDailyStartTime(string? value, out TimeOnly time)
    {
        time = default;
        if (string.IsNullOrWhiteSpace(value))
            return false;
        return TimeOnly.TryParseExact(value.Trim(), DailyStartTimeFormats,
            CultureInfo.InvariantCulture, DateTimeStyles.None, out time);
    }

    /// <summary>Canonical "HH:MM" representation of a parsed start time.</summary>
    public static string NormalizeDailyStartTime(TimeOnly time) =>
        time.ToString("HH:mm", CultureInfo.InvariantCulture);

    /// <summary>v2.0: concurrency (parallel downloaders) is 1–5.</summary>
    public static bool IsValidConcurrencyLimit(int value) =>
        value is >= MinConcurrencyLimit and <= MaxConcurrencyLimit;
}
