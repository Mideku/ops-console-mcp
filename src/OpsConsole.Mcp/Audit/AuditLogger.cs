using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using OpsConsole.Mcp.Security;

namespace OpsConsole.Mcp.Audit;

/// <summary>
/// Append-only, hash-chained JSONL audit log (03-sicurezza-threat-model.md §5). One record per
/// tool invocation (plus rate-limit events). A single writer, synchronous, flushed to disk
/// before the tool response is returned to the client — this is what makes the chain
/// trustworthy: there is never a window where a response left the process without a matching
/// durable record.
/// </summary>
public sealed class AuditLogger
{
    private const int GenesisLength = 64;
    private const int VerifySuffixCount = 100;

    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);

    /// <summary>Convention genesis hash for the first record in the chain: 64 hex zeros.</summary>
    public static string Genesis { get; } = new('0', GenesisLength);

    private readonly string _path;
    private readonly object _writeLock = new();
    private string _lastHash;

    private AuditLogger(string path, string lastHash)
    {
        _path = path;
        _lastHash = lastHash;
    }

    /// <summary>
    /// Verifies the last <see cref="VerifySuffixCount"/> records (or fewer if the file is
    /// shorter) and returns a ready-to-use logger. Fail-closed: any mismatch throws, and the
    /// caller (Program.cs) must log to stderr and exit non-zero rather than start the server.
    /// </summary>
    public static AuditLogger CreateVerified(string path) => CreateVerified(path, Genesis);

    /// <summary>
    /// Same fail-closed verification as <see cref="CreateVerified(string)"/>, but for a fresh
    /// file created by log rotation: <paramref name="continuedFromHash"/> is the last hash of
    /// the rotated-out file, so the chain stays continuous across the rotation boundary
    /// (03-sicurezza-threat-model.md §5, "Dove vive").
    /// </summary>
    public static AuditLogger CreateVerified(string path, string continuedFromHash)
    {
        var directory = Path.GetDirectoryName(Path.GetFullPath(path));
        if (!string.IsNullOrEmpty(directory))
        {
            Directory.CreateDirectory(directory);
        }

        if (!File.Exists(path))
        {
            return new AuditLogger(path, continuedFromHash);
        }

        var allLines = File.ReadAllLines(path);
        if (allLines.Length == 0)
        {
            return new AuditLogger(path, continuedFromHash);
        }

        var start = Math.Max(0, allLines.Length - VerifySuffixCount);
        var expectedPrev = start == 0 ? continuedFromHash : ComputeRecordHash(allLines[start - 1]);

        for (var i = start; i < allLines.Length; i++)
        {
            var record = ReadRecord(allLines[i]);
            if (!string.Equals(record.PrevHash, expectedPrev, StringComparison.Ordinal))
            {
                throw new AuditChainException(
                    $"Audit chain broken at record index {i}: expected prev_hash '{expectedPrev}' but found '{record.PrevHash}'.");
            }

            expectedPrev = ComputeRecordHash(allLines[i]);
        }

        return new AuditLogger(path, expectedPrev);
    }

    /// <summary>Appends one record for a tool invocation, redacting args/result first.</summary>
    public void Append(Redactor redactor, string tool, IReadOnlyDictionary<string, object?> args, string resultJson, ClientIdentity identity)
    {
        var redactedArgs = RedactArgs(redactor, args);
        var (redactedResult, _) = redactor.Redact(resultJson);
        var resultHash = Sha256Hex(redactedResult);
        WriteRecord(tool, redactedArgs, resultHash, identity);
    }

    /// <summary>Appends a rate-limit event: throttling is itself a security-relevant event.</summary>
    public void AppendRateLimited(string tool, ClientIdentity identity)
    {
        WriteRecord(tool, new Dictionary<string, object?> { ["rate_limited"] = true }, Sha256Hex(""), identity);
    }

    private void WriteRecord(string tool, IReadOnlyDictionary<string, object?> args, string resultHash, ClientIdentity identity)
    {
        lock (_writeLock)
        {
            var record = new AuditRecord
            {
                Timestamp = DateTimeOffset.UtcNow,
                Tool = tool,
                Args = args,
                Identity = identity,
                ResultHash = resultHash,
                PrevHash = _lastHash,
            };

            var line = JsonSerializer.Serialize(record, JsonOptions);
            using (var stream = new FileStream(_path, FileMode.Append, FileAccess.Write, FileShare.Read))
            // No BOM: the chain must be re-verifiable by any external tool hashing raw lines.
            using (var writer = new StreamWriter(stream, new UTF8Encoding(encoderShouldEmitUTF8Identifier: false)))
            {
                writer.WriteLine(line);
                writer.Flush();
                stream.Flush(flushToDisk: true);
            }

            _lastHash = ComputeRecordHash(line);
        }
    }

    private static Dictionary<string, object?> RedactArgs(Redactor redactor, IReadOnlyDictionary<string, object?> args)
    {
        var result = new Dictionary<string, object?>();
        foreach (var (key, value) in args)
        {
            if (value is string s)
            {
                result[key] = redactor.Redact(s).Text;
            }
            else
            {
                result[key] = value;
            }
        }

        return result;
    }

    private static string ComputeRecordHash(string line) => Sha256Hex(line);

    private static string Sha256Hex(string text)
    {
        var bytes = SHA256.HashData(Encoding.UTF8.GetBytes(text));
        return Convert.ToHexString(bytes).ToLowerInvariant();
    }

    private static AuditRecord ReadRecord(string line) =>
        JsonSerializer.Deserialize<AuditRecord>(line, JsonOptions)
        ?? throw new AuditChainException("Audit record could not be parsed.");
}

public sealed class AuditChainException(string message) : Exception(message);

public sealed record ClientIdentity(string ClientName, string ClientVersion, string SessionId, string OsUser);

public sealed class AuditRecord
{
    [JsonPropertyName("timestamp")]
    public DateTimeOffset Timestamp { get; init; }

    [JsonPropertyName("tool")]
    public required string Tool { get; init; }

    [JsonPropertyName("args")]
    public required IReadOnlyDictionary<string, object?> Args { get; init; }

    [JsonPropertyName("identity")]
    public required ClientIdentity Identity { get; init; }

    [JsonPropertyName("result_hash")]
    public required string ResultHash { get; init; }

    [JsonPropertyName("prev_hash")]
    public required string PrevHash { get; init; }
}
