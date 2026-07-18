namespace OpsConsole.Mcp.Configuration;

/// <summary>
/// Server configuration read once at startup from environment variables.
/// No value here is a secret by itself: the snapshot token (if any) is only
/// ever used as an outgoing header, never logged or reflected in output.
/// </summary>
public sealed class ServerConfig
{
    public required Uri SnapshotUrl { get; init; }
    public string? SnapshotToken { get; init; }
    public required string AuditPath { get; init; }
    public required int RatePerMinute { get; init; }

    public static ServerConfig FromEnvironment()
    {
        var snapshotUrlRaw = Environment.GetEnvironmentVariable("OPS_CONSOLE_SNAPSHOT_URL");
        if (string.IsNullOrWhiteSpace(snapshotUrlRaw))
        {
            throw new InvalidOperationException(
                "OPS_CONSOLE_SNAPSHOT_URL is required (URL of the read-only snapshot endpoint).");
        }

        if (!Uri.TryCreate(snapshotUrlRaw, UriKind.Absolute, out var snapshotUrl))
        {
            throw new InvalidOperationException("OPS_CONSOLE_SNAPSHOT_URL is not a valid absolute URL.");
        }

        var token = Environment.GetEnvironmentVariable("OPS_CONSOLE_SNAPSHOT_TOKEN");

        var auditPath = Environment.GetEnvironmentVariable("OPS_CONSOLE_AUDIT_PATH");
        if (string.IsNullOrWhiteSpace(auditPath))
        {
            auditPath = "./audit.jsonl";
        }

        var rateRaw = Environment.GetEnvironmentVariable("OPS_CONSOLE_RATE_PER_MINUTE");
        var ratePerMinute = 30;
        if (!string.IsNullOrWhiteSpace(rateRaw))
        {
            if (!int.TryParse(rateRaw, out ratePerMinute) || ratePerMinute <= 0)
            {
                throw new InvalidOperationException(
                    "OPS_CONSOLE_RATE_PER_MINUTE must be a positive integer.");
            }
        }

        return new ServerConfig
        {
            SnapshotUrl = snapshotUrl,
            SnapshotToken = token,
            AuditPath = auditPath,
            RatePerMinute = ratePerMinute,
        };
    }
}
