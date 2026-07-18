using System.Collections.Concurrent;

namespace OpsConsole.Mcp.RateLimiting;

/// <summary>
/// Two independent sliding-window (1 minute) limits (03-sicurezza-threat-model.md §7):
/// a per-session limit (contains a single session looping/retrying) and a global,
/// process-wide limit (contains a client opening multiple sessions to dodge the first one).
/// Thread-safe: stdio is effectively single-session, but the server may still process
/// concurrent tool calls on the same connection.
/// </summary>
public sealed class RateLimiter(int ratePerMinute)
{
    private static readonly TimeSpan Window = TimeSpan.FromMinutes(1);

    private readonly ConcurrentDictionary<string, ConcurrentQueue<DateTimeOffset>> _perSession = new();
    private readonly ConcurrentQueue<DateTimeOffset> _global = new();
    private readonly object _globalLock = new();

    public RateLimitDecision CheckAndRecord(string sessionId)
    {
        var now = DateTimeOffset.UtcNow;

        var sessionQueue = _perSession.GetOrAdd(sessionId, _ => new ConcurrentQueue<DateTimeOffset>());
        lock (sessionQueue)
        {
            Trim(sessionQueue, now);
            if (sessionQueue.Count >= ratePerMinute)
            {
                return RateLimitDecision.Denied("Per-session rate limit exceeded.");
            }
        }

        lock (_globalLock)
        {
            Trim(_global, now);
            if (_global.Count >= ratePerMinute * Environment.ProcessorCount)
            {
                return RateLimitDecision.Denied("Global rate limit exceeded.");
            }

            _global.Enqueue(now);
        }

        sessionQueue.Enqueue(now);
        return RateLimitDecision.Allowed;
    }

    private static void Trim(ConcurrentQueue<DateTimeOffset> queue, DateTimeOffset now)
    {
        while (queue.TryPeek(out var oldest) && now - oldest > Window)
        {
            queue.TryDequeue(out _);
        }
    }
}

public sealed class RateLimitDecision
{
    public bool IsAllowed { get; private init; }
    public string? Reason { get; private init; }

    public static RateLimitDecision Allowed { get; } = new() { IsAllowed = true };

    public static RateLimitDecision Denied(string reason) => new() { IsAllowed = false, Reason = reason };
}
