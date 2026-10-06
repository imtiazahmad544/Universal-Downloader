// Netscape cookies.txt parsing for the optional cookies feature (v2.0).
// OS-independent: unit-testable. Cookie *values* always come from this
// user-provided local file — the server only ever exposes present-flags.

using System.Linq;

namespace UniversalDownloader.Core.Cookies;

/// <summary>
/// Reads a Netscape-format cookies.txt file and builds Cookie request headers
/// for download requests (bot-check / captcha fallback, v2.0).
/// </summary>
public sealed class CookieJar
{
    public sealed record Cookie(string Domain, string Name, string Value, DateTimeOffset? Expires);

    private readonly List<Cookie> _cookies;

    private CookieJar(List<Cookie> cookies) => _cookies = cookies;

    public int Count => _cookies.Count;

    /// <summary>
    /// Sniffs whether <paramref name="path"/> looks like a Netscape cookies file:
    /// the "# Netscape HTTP Cookie File" header or tab-separated 7-field lines.
    /// Returns false for missing/unreadable files.
    /// </summary>
    public static bool IsNetscapeFormat(string path)
    {
        try
        {
            using var reader = new StreamReader(path);
            for (int i = 0; i < 50; i++)
            {
                var line = reader.ReadLine();
                if (line is null)
                    break;
                if (line.StartsWith("# Netscape HTTP Cookie File", StringComparison.Ordinal))
                    return true;
                if (!line.StartsWith('#') && IsCookieLine(line))
                    return true;
            }
        }
        catch (Exception)
        {
            // Unreadable -> not a usable cookies file.
        }
        return false;
    }

    private static bool IsCookieLine(string line)
    {
        var fields = line.Split('\t');
        return fields.Length >= 7
            && !string.IsNullOrWhiteSpace(fields[0])
            && !string.IsNullOrWhiteSpace(fields[5])
            && (string.IsNullOrWhiteSpace(fields[4]) || long.TryParse(fields[4].Trim(), out _));
    }

    /// <summary>Loads cookies, silently dropping expired entries.</summary>
    public static CookieJar Load(string path)
    {
        var cookies = new List<Cookie>();
        var now = DateTimeOffset.UtcNow;
        foreach (var raw in File.ReadLines(path))
        {
            var line = raw.Trim();
            if (line.Length == 0 || line.StartsWith('#'))
                continue;
            var fields = line.Split('\t');
            if (fields.Length < 7)
                continue;
            var name = fields[5].Trim();
            if (name.Length == 0)
                continue;
            DateTimeOffset? expires = null;
            if (long.TryParse(fields[4].Trim(), out long exp) && exp > 0)
            {
                expires = DateTimeOffset.FromUnixTimeSeconds(exp);
                if (expires <= now)
                    continue; // expired
            }
            cookies.Add(new Cookie(fields[0].Trim(), name, fields[6], expires));
        }
        return new CookieJar(cookies);
    }

    /// <summary>
    /// Builds a Cookie header value for <paramref name="requestUri"/> from
    /// domain-matching, non-expired cookies. Returns null when none match.
    /// </summary>
    public string? BuildHeader(Uri requestUri)
    {
        ArgumentNullException.ThrowIfNull(requestUri);
        var pairs = _cookies
            .Where(c => DomainMatches(c.Domain, requestUri.Host))
            .Select(c => $"{c.Name}={c.Value}")
            .ToList();
        return pairs.Count == 0 ? null : string.Join("; ", pairs);
    }

    /// <summary>
    /// Netscape domain rule: a leading dot matches the domain itself and all
    /// subdomains; without the dot, only the exact host matches.
    /// </summary>
    public static bool DomainMatches(string cookieDomain, string host)
    {
        if (string.IsNullOrWhiteSpace(cookieDomain) || string.IsNullOrWhiteSpace(host))
            return false;
        var d = cookieDomain.Trim().ToLowerInvariant();
        var h = host.Trim().ToLowerInvariant();
        if (d.StartsWith('.'))
        {
            var bare = d[1..];
            return h == bare || h.EndsWith(d, StringComparison.Ordinal);
        }
        return h == d;
    }
}
