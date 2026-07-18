using System.Text.RegularExpressions;

namespace OpsConsole.Mcp.Security;

/// <summary>
/// Single centralized secret redactor (03-sicurezza-threat-model.md §5/§6). Applied to every
/// outgoing string: tool output, error messages, and audit log arguments/results. Matches are
/// replaced with a fixed placeholder "[REDACTED:&lt;category&gt;]" rather than removed, so the
/// caller can see redaction happened without ever seeing the original value.
/// </summary>
public sealed partial class Redactor
{
    public const string Placeholder = "[REDACTED:";

    // Order matters: more specific patterns run before the generic entropy fallback so we
    // report the most useful category, not just "high-entropy".
    private static readonly (string Category, Regex Pattern)[] Patterns =
    [
        ("pem_private_key", PemBlockRegex()),
        ("known_token", KnownTokenRegex()),
        ("connection_string", ConnectionStringRegex()),
        ("healthcheck_url", HealthcheckUrlRegex()),
        ("generic_secret_kv", GenericSecretKeyValueRegex()),
        ("private_ip", PrivateIpRegex()),
        ("internal_hostname", InternalHostnameRegex()),
    ];

    /// <summary>Redacts all known categories plus a fail-safe entropy heuristic.</summary>
    /// <returns>The redacted string and whether at least one redaction was applied.</returns>
    public (string Text, bool Redacted) Redact(string? input)
    {
        if (string.IsNullOrEmpty(input))
        {
            return (input ?? "", false);
        }

        var text = input;
        var redacted = false;

        foreach (var (category, pattern) in Patterns)
        {
            text = pattern.Replace(text, m =>
            {
                redacted = true;
                return $"{Placeholder}{category}]";
            });
        }

        // Fail-safe: any remaining long, high-entropy, space-free token-like run is redacted
        // even if it matched no known pattern. Prefer a false positive here to a leaked secret.
        text = HighEntropyTokenRegex().Replace(text, m =>
        {
            if (m.Value.Contains(Placeholder))
            {
                return m.Value;
            }

            if (LooksHighEntropy(m.Value))
            {
                redacted = true;
                return $"{Placeholder}entropy]";
            }

            return m.Value;
        });

        return (text, redacted);
    }

    /// <summary>Convenience overload for a batch of lines (e.g. container/journal logs).</summary>
    public IReadOnlyList<string> RedactLines(IEnumerable<string> lines, out int redactedCount)
    {
        var result = new List<string>();
        var count = 0;
        foreach (var line in lines)
        {
            var (text, redacted) = Redact(line);
            result.Add(text);
            if (redacted)
            {
                count++;
            }
        }

        redactedCount = count;
        return result;
    }

    private static bool LooksHighEntropy(string candidate)
    {
        if (candidate.Length < 24)
        {
            return false;
        }

        var hasDigit = false;
        var hasUpper = false;
        var hasLower = false;
        foreach (var c in candidate)
        {
            if (char.IsDigit(c)) hasDigit = true;
            else if (char.IsUpper(c)) hasUpper = true;
            else if (char.IsLower(c)) hasLower = true;
        }

        // Require a mix of classes so ordinary words/sentences are left alone.
        var classCount = (hasDigit ? 1 : 0) + (hasUpper ? 1 : 0) + (hasLower ? 1 : 0);
        return classCount >= 2;
    }

    [GeneratedRegex(@"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")]
    private static partial Regex PemBlockRegex();

    // Known provider token prefixes (GitHub PAT/fine-grained, GitHub Actions, Slack, generic AWS).
    [GeneratedRegex(@"\b(ghp_|gho_|ghu_|ghs_|ghr_|github_pat_)[A-Za-z0-9_]{20,}\b|\bAKIA[0-9A-Z]{16}\b|\bxox[baprs]-[A-Za-z0-9-]{10,}\b")]
    private static partial Regex KnownTokenRegex();

    // scheme://user:password@host[:port][/path] — postgres, redis, mysql, amqp, etc.
    [GeneratedRegex(@"\b[a-zA-Z][a-zA-Z0-9+.-]*://[^\s/@:]+:[^\s/@]+@[^\s/]+")]
    private static partial Regex ConnectionStringRegex();

    // Healthcheck ping URLs (e.g. hc-ping.com/<uuid>-style tokens): the path segment itself acts
    // as a bearer credential, so the whole URL is treated as a secret, not just logged as info.
    [GeneratedRegex(@"https?://[^\s]*(?:ping|healthchecks?)[^\s]*/[0-9a-fA-F-]{8,}")]
    private static partial Regex HealthcheckUrlRegex();

    // key=value / header-style secrets: token=, key=, secret=, password=, authorization:, bearer .
    [GeneratedRegex(@"(?i)\b(token|api[_-]?key|secret|password|passwd)\s*[=:]\s*[""']?[A-Za-z0-9_\-.+/=]{8,}[""']?|(?i)\b(authorization)\s*:\s*[""']?bearer\s+[A-Za-z0-9_\-.+/=]{8,}[""']?|(?i)\bbearer\s+[A-Za-z0-9_\-.+/=]{8,}")]
    private static partial Regex GenericSecretKeyValueRegex();

    // RFC1918 private ranges + CGNAT 100.64.0.0/10 (used by mesh VPNs such as Tailscale) + loopback.
    [GeneratedRegex(@"\b(10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}|127\.0\.0\.1)\b")]
    private static partial Regex PrivateIpRegex();

    // Internal-looking hostnames: .local / .internal / .lan TLD-style suffixes, or bare
    // "host.tailnet-ish" single-label + reserved suffix combos.
    [GeneratedRegex(@"\b[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.(local|internal|lan|home\.arpa)\b")]
    private static partial Regex InternalHostnameRegex();

    // Fallback token shape: 24+ contiguous non-space alnum/symbol characters.
    [GeneratedRegex(@"[A-Za-z0-9_\-./+=]{24,}")]
    private static partial Regex HighEntropyTokenRegex();
}
