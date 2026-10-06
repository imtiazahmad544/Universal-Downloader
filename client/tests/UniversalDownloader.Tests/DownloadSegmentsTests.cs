// Tests for v2.0 parallel-download segment math (OS-independent).

using UniversalDownloader.Worker.Download;

namespace UniversalDownloader.Tests;

public sealed class DownloadSegmentsTests
{
    [Fact]
    public void Compute_even_split_covers_full_range()
    {
        var segments = DownloadSegments.Compute(100, 4);
        Assert.Equal(4, segments.Count);
        Assert.Equal(new DownloadSegments.Segment(0, 24), segments[0]);
        Assert.Equal(new DownloadSegments.Segment(25, 49), segments[1]);
        Assert.Equal(new DownloadSegments.Segment(50, 74), segments[2]);
        Assert.Equal(new DownloadSegments.Segment(75, 99), segments[3]);
    }

    [Fact]
    public void Compute_distributes_remainder_to_later_segments()
    {
        var segments = DownloadSegments.Compute(10, 3);
        Assert.Equal(3, segments.Count);
        Assert.Equal(new DownloadSegments.Segment(0, 2), segments[0]);
        Assert.Equal(new DownloadSegments.Segment(3, 5), segments[1]);
        Assert.Equal(new DownloadSegments.Segment(6, 9), segments[2]);
    }

    [Fact]
    public void Compute_single_segment_covers_everything()
    {
        var segments = DownloadSegments.Compute(100, 1);
        var only = Assert.Single(segments);
        Assert.Equal(new DownloadSegments.Segment(0, 99), only);
    }

    [Theory]
    [InlineData(1000, 3)]
    [InlineData(1000, 5)]
    [InlineData(7, 5)]
    [InlineData(8 * 1024 * 1024 + 1, 4)]
    public void Compute_segments_are_contiguous_and_cover_all_bytes(long total, int count)
    {
        var segments = DownloadSegments.Compute(total, count);
        Assert.Equal(count, segments.Count);
        Assert.Equal(0, segments[0].StartOffset);
        Assert.Equal(total - 1, segments[^1].EndOffsetInclusive);
        long covered = 0;
        for (int i = 0; i < segments.Count; i++)
        {
            Assert.True(segments[i].Length > 0);
            if (i > 0)
                Assert.Equal(segments[i - 1].EndOffsetInclusive + 1, segments[i].StartOffset);
            covered += segments[i].Length;
        }
        Assert.Equal(total, covered);
    }

    [Theory]
    [InlineData(0, 3)]
    [InlineData(-5, 3)]
    [InlineData(100, 0)]
    [InlineData(100, -2)]
    public void Compute_rejects_invalid_arguments(long total, int count)
    {
        Assert.Throws<ArgumentOutOfRangeException>(() => DownloadSegments.Compute(total, count));
    }

    [Theory]
    [InlineData(9L * 1024 * 1024, 3, true)]
    [InlineData(100L * 1024 * 1024, 5, true)]
    [InlineData(8L * 1024 * 1024, 3, false)] // at the threshold -> single path
    [InlineData(100L * 1024 * 1024, 1, false)]
    [InlineData(null, 3, false)]
    public void IsEligible_gates_segmented_downloads(long? total, int count, bool expected)
    {
        Assert.Equal(expected, DownloadSegments.IsEligible(total, count));
    }
}
