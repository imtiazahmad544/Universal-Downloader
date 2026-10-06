// Tests for the v2.0 Netscape cookies.txt parsing (OS-independent).

using UniversalDownloader.Core.Cookies;

namespace UniversalDownloader.Tests;

public sealed class CookieJarTests : IDisposable
{
    private readonly string _dir;

    public CookieJarTests()
    {
        _dir = Path.Combine(Path.GetTempPath(), "UDCookieTests", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(_dir);
    }

    public void Dispose()
    {
        try { Directory.Delete(_dir, recursive: true); } catch { /* best effort */ }
    }

    private string Write(string name, string content)
    {
        var path = Path.Combine(_dir, name);
        File.WriteAllText(path, content);
        return path;
    }

    private static string SampleFile() =>
        "# Netscape HTTP Cookie File\n" +
        "# https://curl.se/docs/http-cookies.html\n" +
        ".example.com\tTRUE\t/\tFALSE\t4102444800\tsession\tabc123\n" +
        ".example.com\tTRUE\t/\tFALSE\t0\tpref\tlight\n" +
        "sub.example.com\tTRUE\t/\tFALSE\t4102444800\ttoken\txyz\n" +
        ".other.com\tTRUE\t/\tFALSE\t4102444800\tsid\t999\n" +
        ".example.com\tTRUE\t/\tFALSE\t1000000000\told\tgone\n";

    [Fact]
    public void IsNetscapeFormat_detects_header()
    {
        var path = Write("cookies.txt", SampleFile());
        Assert.True(CookieJar.IsNetscapeFormat(path));
    }

    [Fact]
    public void IsNetscapeFormat_detects_cookie_lines_without_header()
    {
        var path = Write("noheader.txt",
            ".example.com\tTRUE\t/\tFALSE\t4102444800\tsession\tabc123\n");
        Assert.True(CookieJar.IsNetscapeFormat(path));
    }

    [Fact]
    public void IsNetscapeFormat_rejects_plain_text_and_missing_files()
    {
        var path = Write("plain.txt", "hello world\njust some text\n");
        Assert.False(CookieJar.IsNetscapeFormat(path));
        Assert.False(CookieJar.IsNetscapeFormat(Path.Combine(_dir, "missing.txt")));
    }

    [Fact]
    public void Load_drops_expired_entries()
    {
        var jar = CookieJar.Load(Write("cookies.txt", SampleFile()));
        // 5 lines, 1 expired ("old") -> 4 loaded.
        Assert.Equal(4, jar.Count);
    }

    [Fact]
    public void BuildHeader_matches_domain_and_subdomains_only()
    {
        var jar = CookieJar.Load(Write("cookies.txt", SampleFile()));

        var header = jar.BuildHeader(new Uri("https://sub.example.com/watch"));
        Assert.NotNull(header);
        Assert.Contains("session=abc123", header);
        Assert.Contains("pref=light", header);
        Assert.Contains("token=xyz", header);
        Assert.DoesNotContain("sid=999", header);

        // Bare domain matches too (.example.com covers example.com).
        var bare = jar.BuildHeader(new Uri("https://example.com/"));
        Assert.NotNull(bare);
        Assert.Contains("session=abc123", bare);

        // No match -> null, never an empty header.
        Assert.Null(jar.BuildHeader(new Uri("https://unrelated.net/")));
    }

    [Fact]
    public void BuildHeader_joins_pairs_with_semicolons()
    {
        var jar = CookieJar.Load(Write("cookies.txt", SampleFile()));
        var header = jar.BuildHeader(new Uri("https://example.com/"));
        Assert.Equal("session=abc123; pref=light", header);
    }

    [Fact]
    public void DomainMatches_respects_leading_dot_rule()
    {
        Assert.True(CookieJar.DomainMatches(".example.com", "example.com"));
        Assert.True(CookieJar.DomainMatches(".example.com", "sub.example.com"));
        Assert.True(CookieJar.DomainMatches(".example.com", "deep.sub.example.com"));
        Assert.False(CookieJar.DomainMatches(".example.com", "notexample.com"));
        Assert.False(CookieJar.DomainMatches(".example.com", "example.com.evil.com"));
        Assert.True(CookieJar.DomainMatches("example.com", "example.com"));
        Assert.False(CookieJar.DomainMatches("example.com", "sub.example.com"));
    }
}
