using System.Runtime.CompilerServices;
using System.Text.Json;
using ModelContextProtocol.Protocol;
using ModelContextProtocol.Server;
using OpsConsole.Mcp.Audit;
using OpsConsole.Mcp.Errors;
using OpsConsole.Mcp.RateLimiting;
using OpsConsole.Mcp.Security;
using OpsConsole.Mcp.Snapshot;

namespace OpsConsole.Mcp.Tools;

/// <summary>
/// Shared plumbing every tool goes through: rate limiting, snapshot fetch, uniform error
/// mapping, and synchronous audit logging of both the arguments and a hash of the (already
/// redacted) result — all before the response is handed back to the framework.
/// </summary>
public sealed class ToolRuntime(
    SnapshotClient snapshotClient,
    Redactor redactor,
    AuditLogger auditLog,
    RateLimiter rateLimiter)
{
    private static readonly ConditionalWeakTable<McpServer, StrongBox<string>> SessionIds = new();
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);

    public Redactor Redactor => redactor;

    public async Task<CallToolResult> RunAsync(
        McpServer server,
        string toolName,
        IReadOnlyDictionary<string, object?> args,
        Func<SnapshotDocument, bool, CallToolResult> handler)
    {
        var identity = ResolveIdentity(server);

        var decision = rateLimiter.CheckAndRecord(identity.SessionId);
        if (!decision.IsAllowed)
        {
            auditLog.AppendRateLimited(toolName, identity);
            return ToolErrors.Build(redactor, toolName, ToolErrorCode.RATE_LIMITED,
                decision.Reason ?? "Rate limit exceeded.");
        }

        CallToolResult result;
        try
        {
            var snapshot = await snapshotClient.GetSnapshotAsync(CancellationToken.None);
            result = handler(snapshot.Document, snapshot.Stale);
        }
        catch (Exception ex)
        {
            result = ToolErrors.FromException(redactor, toolName, ex);
        }

        var resultJson = JsonSerializer.Serialize(result, JsonOptions);
        auditLog.Append(redactor, toolName, args, resultJson, identity);
        return result;
    }

    private static ClientIdentity ResolveIdentity(McpServer server)
    {
        var box = SessionIds.GetOrCreateValue(server);
        box.Value ??= Guid.NewGuid().ToString("n");

        var clientInfo = server.ClientInfo;
        return new ClientIdentity(
            ClientName: clientInfo?.Name ?? "unknown",
            ClientVersion: clientInfo?.Version ?? "unknown",
            SessionId: box.Value,
            OsUser: Environment.UserName);
    }
}

/// <summary>Manual, explicit enum/range validation so every tool produces the exact same
/// INVALID_ARGUMENT shape regardless of what the MCP SDK's auto-generated JSON Schema does.</summary>
public static class ToolValidation
{
    public static string RequireEnum(string toolName, string paramName, string? value, params string[] allowed)
    {
        if (value is null || Array.IndexOf(allowed, value) < 0)
        {
            throw new ToolException(ToolErrorCode.INVALID_ARGUMENT,
                $"Parameter '{paramName}' must be one of: {string.Join(", ", allowed)}.");
        }

        return value;
    }

    public static int RequireRange(string toolName, string paramName, int? value, int min, int max, int @default)
    {
        var effective = value ?? @default;
        if (effective < min || effective > max)
        {
            throw new ToolException(ToolErrorCode.INVALID_ARGUMENT,
                $"Parameter '{paramName}' must be between {min} and {max}.");
        }

        return effective;
    }

    public static string? OptionalBranch(string? branch)
    {
        if (branch is null)
        {
            return null;
        }

        if (branch.Length is < 1 or > 200 || !System.Text.RegularExpressions.Regex.IsMatch(branch, "^[A-Za-z0-9._/-]+$"))
        {
            throw new ToolException(ToolErrorCode.INVALID_ARGUMENT,
                "Parameter 'branch' must match ^[A-Za-z0-9._/-]{1,200}$.");
        }

        return branch;
    }

    public static long RequirePositiveLong(string paramName, long value)
    {
        if (value < 1)
        {
            throw new ToolException(ToolErrorCode.INVALID_ARGUMENT, $"Parameter '{paramName}' must be a positive integer.");
        }

        return value;
    }
}
