// Tests for file-naming template resolution — SRS FR-10 (v1.1).

using UniversalDownloader.Core.Models;
using UniversalDownloader.Core.Naming;

namespace UniversalDownloader.Tests;

public sealed class FileNamingTemplateTests : IDisposable
{
    private readonly string _root;

    public FileNamingTemplateTests()
    {
        _root = Path.Combine(Path.GetTempPath(), "UDNamingTests", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(_root);
    }

    public void Dispose()
    {
        try { Directory.Delete(_root, recursive: true); } catch { /* best effort */ }
    }

    private static MediaMetadata Meta(
        string title = "some title",
        string platform = "TikTok") => new()
    {
        Platform = Enum.Parse<Platform>(platform),
        SourceName = "some_creator",
        Date = new DateOnly(2026, 9, 30),
        Title = title,
        MediaId = "abc123",
        Extension = "mp4",
    };

    [Fact]
    public void ResolveUniquePath_builds_default_template_path()
    {
        string path = FileNamingTemplate.ResolveUniquePath(
            _root, FileNamingTemplate.DefaultTemplate, Meta());

        string expected = Path.Combine(
            _root, "TikTok", "some_creator", "2026-09-30_some title_abc123.mp4");
        Assert.Equal(expected, path);
    }

    [Fact]
    public void ResolveUniquePath_sanitizes_invalid_characters()
    {
        string path = FileNamingTemplate.ResolveUniquePath(
            _root, FileNamingTemplate.DefaultTemplate,
            Meta(title: "a<b>c:d\"e/f\\g|h?i*j"));

        Assert.DoesNotContain("<", Path.GetFileName(path));
        Assert.DoesNotContain(">", Path.GetFileName(path));
        Assert.DoesNotContain(":", Path.GetFileName(path));
        Assert.DoesNotContain("\"", Path.GetFileName(path));
        Assert.DoesNotContain("|", Path.GetFileName(path));
        Assert.DoesNotContain("?", Path.GetFileName(path));
        Assert.DoesNotContain("*", Path.GetFileName(path));
    }

    [Fact]
    public void ResolveUniquePath_appends_counter_on_collision()
    {
        string first = FileNamingTemplate.ResolveUniquePath(
            _root, FileNamingTemplate.DefaultTemplate, Meta());
        Directory.CreateDirectory(Path.GetDirectoryName(first)!);
        File.WriteAllText(first, "existing");

        string second = FileNamingTemplate.ResolveUniquePath(
            _root, FileNamingTemplate.DefaultTemplate, Meta());

        Assert.NotEqual(first, second);
        Assert.EndsWith(" (2).mp4", second);
        Assert.False(File.Exists(second), "Resolved path must never overwrite an existing file.");
    }

    [Fact]
    public void ResolveUniquePath_rejects_template_escaping_destination_root()
    {
        var ex = Assert.Throws<ArgumentException>(() =>
            FileNamingTemplate.ResolveUniquePath(
                _root, "{title}/../../{media_id}.{ext}", Meta()));
        Assert.Contains("destination", ex.Message, StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public void ResolveUniquePath_throws_on_blank_destination()
    {
        Assert.Throws<ArgumentException>(() =>
            FileNamingTemplate.ResolveUniquePath("   ", FileNamingTemplate.DefaultTemplate, Meta()));
    }
}
