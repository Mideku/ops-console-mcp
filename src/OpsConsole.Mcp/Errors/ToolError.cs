using System.Text.Json;
using System.Text.Json.Serialization;
using ModelContextProtocol.Protocol;
using OpsConsole.Mcp.Security;

namespace OpsConsole.Mcp.Errors;

/// <summary>Stable error codes shared by every tool (02-contratto-tool.md §1).</summary>
public enum ToolErrorCode
{
    INVALID_ARGUMENT,
    NOT_FOUND,
    TIMEOUT,
    UPSTREAM_UNAVAILABLE,
    RATE_LIMITED,
    INTERNAL,
}

/// <summary>Thrown by tool implementations; translated into the uniform error payload.</summary>
public sealed class ToolException(ToolErrorCode code, string message) : Exception(message)
{
    public ToolErrorCode Code { get; } = code;
}

public sealed class ToolErrorPayload
{
    [JsonPropertyName("error")]
    public required ToolErrorBody Error { get; init; }
}

public sealed class ToolErrorBody
{
    [JsonPropertyName("code")]
    public required string Code { get; init; }

    [JsonPropertyName("message")]
    public required string Message { get; init; }

    [JsonPropertyName("tool")]
    public required string Tool { get; init; }

    [JsonPropertyName("retryable")]
    public required bool Retryable { get; init; }
}

/// <summary>
/// Builds the MCP-level result for a failed tool call. Per 02 §1 this is a *recoverable* tool
/// result (CallToolResult.IsError = true) carrying the structured error JSON as content, not a
/// JSON-RPC protocol-level failure.
/// </summary>
public static class ToolErrors
{
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);

    public static bool IsRetryable(ToolErrorCode code) => code is ToolErrorCode.TIMEOUT
        or ToolErrorCode.UPSTREAM_UNAVAILABLE
        or ToolErrorCode.RATE_LIMITED;

    public static CallToolResult Build(Redactor redactor, string tool, ToolErrorCode code, string message)
    {
        var (redactedMessage, _) = redactor.Redact(message);

        var payload = new ToolErrorPayload
        {
            Error = new ToolErrorBody
            {
                Code = code.ToString(),
                Message = redactedMessage,
                Tool = tool,
                Retryable = IsRetryable(code),
            },
        };

        var json = JsonSerializer.Serialize(payload, JsonOptions);
        return new CallToolResult
        {
            IsError = true,
            Content = [new TextContentBlock { Text = json }],
        };
    }

    public static CallToolResult FromException(Redactor redactor, string tool, Exception ex) => ex switch
    {
        ToolException te => Build(redactor, tool, te.Code, te.Message),
        Snapshot.SnapshotUnavailableException se => Build(
            redactor, tool, se.IsTimeout ? ToolErrorCode.TIMEOUT : ToolErrorCode.UPSTREAM_UNAVAILABLE, se.Message),
        OperationCanceledException => Build(redactor, tool, ToolErrorCode.TIMEOUT, "The operation timed out."),
        _ => Build(redactor, tool, ToolErrorCode.INTERNAL, "An unexpected internal error occurred."),
    };
}
