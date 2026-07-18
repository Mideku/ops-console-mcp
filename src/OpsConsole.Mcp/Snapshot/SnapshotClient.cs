using System.Text.Json;
using OpsConsole.Mcp.Configuration;

namespace OpsConsole.Mcp.Snapshot;

/// <summary>
/// Fetches the read-only snapshot published by the collector. This is the only network
/// dependency of the server: a plain GET of a static, already-redacted JSON file over the
/// tailnet (see ADR-001/ADR-002 in 01-architettura.md). No tool talks to Docker, systemd or
/// GitHub directly.
/// </summary>
public sealed class SnapshotClient
{
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);

    private readonly HttpClient _httpClient;
    private readonly ServerConfig _config;

    public SnapshotClient(HttpClient httpClient, ServerConfig config)
    {
        _httpClient = httpClient;
        _config = config;
        _httpClient.Timeout = TimeSpan.FromSeconds(5);
    }

    /// <summary>
    /// Downloads and deserializes the current snapshot. Throws
    /// <see cref="SnapshotUnavailableException"/> on network/timeout/parse failure so callers can
    /// map it to the uniform tool error model (UPSTREAM_UNAVAILABLE / TIMEOUT).
    /// </summary>
    public async Task<SnapshotResult> GetSnapshotAsync(CancellationToken cancellationToken)
    {
        using var request = new HttpRequestMessage(HttpMethod.Get, _config.SnapshotUrl);
        if (!string.IsNullOrEmpty(_config.SnapshotToken))
        {
            request.Headers.Add("X-Snapshot-Token", _config.SnapshotToken);
        }

        HttpResponseMessage response;
        try
        {
            response = await _httpClient.SendAsync(request, cancellationToken);
        }
        catch (TaskCanceledException) when (!cancellationToken.IsCancellationRequested)
        {
            throw new SnapshotUnavailableException("Snapshot request timed out.", isTimeout: true);
        }
        catch (HttpRequestException ex)
        {
            throw new SnapshotUnavailableException($"Snapshot endpoint unreachable: {ex.Message}", isTimeout: false);
        }

        using (response)
        {
            if (!response.IsSuccessStatusCode)
            {
                throw new SnapshotUnavailableException(
                    $"Snapshot endpoint returned HTTP {(int)response.StatusCode}.", isTimeout: false);
            }

            SnapshotDocument? document;
            try
            {
                await using var stream = await response.Content.ReadAsStreamAsync(cancellationToken);
                document = await JsonSerializer.DeserializeAsync<SnapshotDocument>(stream, JsonOptions, cancellationToken);
            }
            catch (JsonException ex)
            {
                throw new SnapshotUnavailableException($"Snapshot payload could not be parsed: {ex.Message}", isTimeout: false);
            }

            if (document is null)
            {
                throw new SnapshotUnavailableException("Snapshot payload was empty.", isTimeout: false);
            }

            var now = DateTimeOffset.UtcNow;
            return new SnapshotResult(document, document.IsStale(now));
        }
    }
}

public sealed record SnapshotResult(SnapshotDocument Document, bool Stale);

public sealed class SnapshotUnavailableException(string message, bool isTimeout) : Exception(message)
{
    public bool IsTimeout { get; } = isTimeout;
}
