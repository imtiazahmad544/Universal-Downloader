// File-naming template resolver — SRS FR-10 (v1.1).
// Default template: {platform}/{source}/{date}_{title}_{media_id}.{ext}
// Rules: sanitize invalid characters, resolve collisions with a counter suffix,
// never overwrite an existing file silently.

using UniversalDownloader.Core.Models;

namespace UniversalDownloader.Core.Naming;

/// <summary>Metadata used to fill a naming template.</summary>
public sealed class MediaMetadata
{
    public required Platform Platform { get; init; }
    public required string SourceName { get; init; }
    public required DateOnly Date { get; init; }
    public required string Title { get; init; }
    public required string MediaId { get; init; }
    public required string Extension { get; init; }
}

/// <summary>
/// Resolves naming templates to unique, collision-free file paths.
/// </summary>
public static class FileNamingTemplate
{
    public const string DefaultTemplate = "{platform}/{source}/{date}_{title}_{media_id}.{ext}";

    // Explicit Windows-invalid set so behavior is deterministic on any OS.
    private static readonly char[] InvalidChars =
        ['<', '>', ':', '"', '/', '\\', '|', '?', '*'];

    private static readonly char[] InvalidPathChars =
        ['<', '>', '"', '|', '?', '*'];

    /// <summary>
    /// Resolves <paramref name="template"/> to a full path under
    /// <paramref name="destinationRoot"/>. If the resolved path already exists,
    /// appends " (2)", " (3)", ... before the extension. Never returns a path
    /// that would silently overwrite an existing file.
    /// </summary>
    public static string ResolveUniquePath(
        string destinationRoot,
        string template,
        MediaMetadata metadata)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(destinationRoot);
        ArgumentException.ThrowIfNullOrWhiteSpace(template);
        ArgumentNullException.ThrowIfNull(metadata);

        string relative = template
            .Replace("{platform}", SanitizeSegment(metadata.Platform.ToString()), StringComparison.Ordinal)
            .Replace("{source}", SanitizeSegment(metadata.SourceName), StringComparison.Ordinal)
            .Replace("{date}", metadata.Date.ToString("yyyy-MM-dd", System.Globalization.CultureInfo.InvariantCulture), StringComparison.Ordinal)
            .Replace("{title}", SanitizeSegment(Truncate(metadata.Title, 80)), StringComparison.Ordinal)
            .Replace("{media_id}", SanitizeSegment(metadata.MediaId), StringComparison.Ordinal)
            .Replace("{ext}", SanitizeSegment(metadata.Extension.TrimStart('.')), StringComparison.Ordinal);

        // Normalize separators and guard against absolute/parent traversal in template.
        relative = relative.Replace('/', Path.DirectorySeparatorChar)
                           .Replace('\\', Path.DirectorySeparatorChar);
        foreach (var seg in InvalidPathChars)
            relative = relative.Replace(seg, '_');

        string full = Path.GetFullPath(Path.Combine(destinationRoot, relative));
        string root = Path.GetFullPath(destinationRoot);
        if (!full.StartsWith(root + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase)
            && !string.Equals(full, root, StringComparison.OrdinalIgnoreCase))
        {
            throw new ArgumentException("Naming template resolves outside the destination directory.", nameof(template));
        }

        if (!File.Exists(full))
            return full;

        string dir = Path.GetDirectoryName(full)!;
        string stem = Path.GetFileNameWithoutExtension(full);
        string ext = Path.GetExtension(full);
        for (int i = 2; ; i++)
        {
            string candidate = Path.Combine(dir, $"{stem} ({i}){ext}");
            if (!File.Exists(candidate))
                return candidate;
        }
    }

    /// <summary>Builds <see cref="MediaMetadata"/> from a download job.</summary>
    public static MediaMetadata FromJob(DownloadJob job) => new()
    {
        Platform = job.Platform,
        SourceName = job.SourceName,
        Date = job.MediaDate,
        Title = string.IsNullOrWhiteSpace(job.Title) ? job.MediaItemId.ToString("N") : job.Title,
        MediaId = job.MediaItemId.ToString("N"),
        Extension = job.FileExtension,
    };

    private static string SanitizeSegment(string value)
    {
        if (string.IsNullOrWhiteSpace(value))
            return "untitled";
        var sb = new System.Text.StringBuilder(value.Length);
        foreach (char c in value.Trim())
        {
            if (Array.IndexOf(InvalidChars, c) >= 0 || char.IsControl(c))
                sb.Append('_');
            else
                sb.Append(c);
        }
        string s = sb.ToString().Trim().TrimEnd('.');
        return s.Length == 0 ? "untitled" : s;
    }

    private static string Truncate(string value, int max)
        => value.Length <= max ? value : value[..max];
}
