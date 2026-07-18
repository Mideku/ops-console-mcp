using OpsConsole.Mcp.Security;
using Xunit;

namespace OpsConsole.Mcp.Tests;

public class RedactorCgnatTests
{
    private readonly Redactor _redactor = new();

    [Theory]
    [InlineData("100.64.0.1")]
    [InlineData("100.100.42.7")]
    [InlineData("100.127.255.254")]
    public void CgnatAddresses_AreRedacted(string ip)
    {
        var (text, redacted) = _redactor.Redact($"snapshot endpoint unreachable: http://{ip}:8787/snapshot.json");

        Assert.True(redacted);
        Assert.DoesNotContain(ip, text);
        Assert.Contains("[REDACTED:private_ip]", text);
    }

    [Theory]
    [InlineData("100.63.255.255")]
    [InlineData("100.128.0.1")]
    public void PublicNeighborsOfCgnatRange_AreNotRedacted(string ip)
    {
        var (text, _) = _redactor.Redact($"resolved {ip}");

        Assert.Contains(ip, text);
    }
}
