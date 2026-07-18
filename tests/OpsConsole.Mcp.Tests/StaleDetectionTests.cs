using OpsConsole.Mcp.Snapshot;
using Xunit;

namespace OpsConsole.Mcp.Tests;

public class StaleDetectionTests
{
    [Fact]
    public void Snapshot_within_freshness_window_is_not_stale()
    {
        var doc = new SnapshotDocument
        {
            GeneratedAt = DateTimeOffset.UtcNow.AddSeconds(-60),
            StaleAfterSeconds = 300,
        };

        Assert.False(doc.IsStale(DateTimeOffset.UtcNow));
    }

    [Fact]
    public void Snapshot_older_than_stale_after_seconds_is_stale()
    {
        var doc = new SnapshotDocument
        {
            GeneratedAt = DateTimeOffset.UtcNow.AddSeconds(-600),
            StaleAfterSeconds = 300,
        };

        Assert.True(doc.IsStale(DateTimeOffset.UtcNow));
    }

    [Fact]
    public void Snapshot_exactly_at_boundary_is_not_yet_stale()
    {
        var now = DateTimeOffset.UtcNow;
        var doc = new SnapshotDocument
        {
            GeneratedAt = now.AddSeconds(-300),
            StaleAfterSeconds = 300,
        };

        Assert.False(doc.IsStale(now));
    }
}
