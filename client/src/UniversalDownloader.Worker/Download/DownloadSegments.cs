// Segment math for parallel (multi-connection) downloads — v2.0.
// Pure, OS-independent: unit-testable.

namespace UniversalDownloader.Worker.Download;

/// <summary>
/// Computes byte-range segments for parallel downloads. Segments are
/// contiguous, non-overlapping, and cover [0, totalLength).
/// </summary>
public static class DownloadSegments
{
    public readonly record struct Segment(long StartOffset, long EndOffsetInclusive)
    {
        public long Length => EndOffsetInclusive - StartOffset + 1;
    }

    /// <summary>Files at or below this size stay on the single-connection path.</summary>
    public const long MinimumLengthBytes = 8L * 1024 * 1024;

    /// <summary>
    /// Splits <paramref name="totalLength"/> bytes into <paramref name="segmentCount"/>
    /// contiguous ranges. Remainder bytes distribute to the later segments.
    /// </summary>
    public static IReadOnlyList<Segment> Compute(long totalLength, int segmentCount)
    {
        if (totalLength <= 0)
            throw new ArgumentOutOfRangeException(nameof(totalLength), "Total length must be positive.");
        if (segmentCount < 1)
            throw new ArgumentOutOfRangeException(nameof(segmentCount), "Segment count must be >= 1.");
        var segments = new List<Segment>(segmentCount);
        for (int i = 0; i < segmentCount; i++)
        {
            long start = (i * totalLength) / segmentCount;
            long end = ((i + 1) * totalLength) / segmentCount - 1;
            segments.Add(new Segment(start, end));
        }
        return segments;
    }

    /// <summary>True when a segmented download is worthwhile.</summary>
    public static bool IsEligible(long? totalLength, int segmentCount) =>
        totalLength > MinimumLengthBytes && segmentCount >= 2;
}
