using OpsConsole.Mcp.Security;
using Xunit;

namespace OpsConsole.Mcp.Tests;

public class RedactorTests
{
    private readonly Redactor _redactor = new();

    [Fact]
    public void Redacts_github_pat()
    {
        var (text, redacted) = _redactor.Redact("token: ghp_abcdefghijklmnopqrstuvwxyz012345 in config");
        Assert.True(redacted);
        Assert.Contains("[REDACTED:known_token]", text);
        Assert.DoesNotContain("ghp_abcdefghijklmnopqrstuvwxyz012345", text);
    }

    [Fact]
    public void Redacts_pem_private_key_block()
    {
        var pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIBogIBAAJ...==\n-----END RSA PRIVATE KEY-----";
        var (text, redacted) = _redactor.Redact($"here is a key {pem} end");
        Assert.True(redacted);
        Assert.Contains("[REDACTED:pem_private_key]", text);
        Assert.DoesNotContain("BEGIN RSA PRIVATE KEY", text);
    }

    [Fact]
    public void Redacts_connection_string_with_credentials()
    {
        var (text, redacted) = _redactor.Redact("db=postgres://appuser:s3cr3tPass@10.0.0.5:5432/prod");
        Assert.True(redacted);
        Assert.Contains("[REDACTED:", text);
        Assert.DoesNotContain("s3cr3tPass", text);
    }

    [Fact]
    public void Redacts_healthcheck_ping_url()
    {
        var (text, redacted) = _redactor.Redact("ping https://hc-ping.com/9d1e0a3c-1234-4a5b-8abc-9f1a2b3c4d5e ok");
        Assert.True(redacted);
        Assert.Contains("[REDACTED:healthcheck_url]", text);
    }

    [Fact]
    public void Redacts_private_ip_and_internal_hostname()
    {
        var (ipText, ipRedacted) = _redactor.Redact("connect to 192.168.1.42 now");
        Assert.True(ipRedacted);
        Assert.Contains("[REDACTED:private_ip]", ipText);

        var (hostText, hostRedacted) = _redactor.Redact("resolve db1.internal please");
        Assert.True(hostRedacted);
        Assert.Contains("[REDACTED:internal_hostname]", hostText);
    }

    [Fact]
    public void Redacts_generic_key_value_secret()
    {
        var (text, redacted) = _redactor.Redact("Authorization: Bearer abcDEF1234567890xyzQQQQ");
        Assert.True(redacted);
        Assert.Contains("[REDACTED:", text);
        Assert.DoesNotContain("abcDEF1234567890xyzQQQQ", text);
    }

    [Fact]
    public void Entropy_fallback_redacts_unknown_high_entropy_token()
    {
        // Not a known token format, no key=value marker, but long + mixed-case + digits.
        var (text, redacted) = _redactor.Redact("value=Zk9pQ7mR2xVt8wLb4nHs6yTc1dEa3fGj");
        Assert.True(redacted);
        Assert.Contains("[REDACTED:", text);
    }

    [Fact]
    public void Does_not_redact_ordinary_sentence()
    {
        var (text, redacted) = _redactor.Redact("container web is healthy and running normally");
        Assert.False(redacted);
        Assert.Equal("container web is healthy and running normally", text);
    }

    [Fact]
    public void Does_not_over_redact_short_identifiers()
    {
        var (text, redacted) = _redactor.Redact("exit_code=0 outcome=success");
        Assert.False(redacted);
        Assert.Equal("exit_code=0 outcome=success", text);
    }

    [Fact]
    public void RedactLines_counts_only_lines_with_redactions()
    {
        var lines = new[] { "hello world", "token=abcdefghijklmnopqrstuvwx12", "all fine here" };
        var result = _redactor.RedactLines(lines, out var count);
        Assert.Equal(1, count);
        Assert.Equal(3, result.Count);
    }
}
