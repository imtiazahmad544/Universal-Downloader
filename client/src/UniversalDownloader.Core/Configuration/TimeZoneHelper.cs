// Timezone helpers. The server stores IANA ids (SDS section 12); Windows
// prefers Windows ids, so convert when necessary.

namespace UniversalDownloader.Core.Configuration;

/// <summary>Resolves customer timezones for batch-date calculations.</summary>
public static class TimeZoneHelper
{
    /// <summary>
    /// Finds the timezone for <paramref name="id"/> (IANA or Windows).
    /// Falls back to the local timezone when unresolvable.
    /// </summary>
    public static TimeZoneInfo Find(string? id)
    {
        if (!string.IsNullOrWhiteSpace(id))
        {
            try
            {
                return TimeZoneInfo.FindSystemTimeZoneById(id);
            }
            catch (TimeZoneNotFoundException) { }
            catch (InvalidTimeZoneException) { }

            try
            {
                if (TimeZoneInfo.TryConvertIanaIdToWindowsId(id, out string? windowsId)
                    && windowsId is not null)
                {
                    return TimeZoneInfo.FindSystemTimeZoneById(windowsId);
                }
            }
            catch (TimeZoneNotFoundException) { }
            catch (InvalidTimeZoneException) { }
        }
        return TimeZoneInfo.Local;
    }

    /// <summary>Today's date in the customer's timezone.</summary>
    public static DateOnly TodayIn(string? timezoneId)
    {
        var tz = Find(timezoneId);
        var now = TimeZoneInfo.ConvertTime(DateTimeOffset.UtcNow, tz);
        return DateOnly.FromDateTime(now.DateTime);
    }
}
