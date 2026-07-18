using System.Text;
using OpsConsole.Mcp.Audit;
using OpsConsole.Mcp.Security;
using Xunit;

namespace OpsConsole.Mcp.Tests;

public class AuditLoggerTests
{
    private static ClientIdentity Identity() => new("test-client", "1.0.0", "session-1", "tester");

    [Fact]
    public void First_record_chains_from_genesis()
    {
        var path = TempPath();
        try
        {
            var logger = AuditLogger.CreateVerified(path);
            logger.Append(new Redactor(), "infra_list_services", new Dictionary<string, object?>(), "{\"ok\":true}", Identity());

            var line = File.ReadAllLines(path).Single();
            Assert.Contains($"\"prev_hash\":\"{AuditLogger.Genesis}\"", line);
        }
        finally
        {
            Cleanup(path);
        }
    }

    [Fact]
    public void Chain_of_multiple_records_verifies_successfully()
    {
        var path = TempPath();
        try
        {
            var logger = AuditLogger.CreateVerified(path);
            for (var i = 0; i < 5; i++)
            {
                logger.Append(new Redactor(), "infra_get_job_result",
                    new Dictionary<string, object?> { ["job"] = "deploy" }, $"{{\"i\":{i}}}", Identity());
            }

            // Re-opening and verifying must not throw.
            var reopened = AuditLogger.CreateVerified(path);
            Assert.NotNull(reopened);
        }
        finally
        {
            Cleanup(path);
        }
    }

    [Fact]
    public void Tampering_with_a_middle_record_is_detected_on_reopen()
    {
        var path = TempPath();
        try
        {
            var logger = AuditLogger.CreateVerified(path);
            for (var i = 0; i < 4; i++)
            {
                logger.Append(new Redactor(), "ci_get_runner_status", new Dictionary<string, object?>(), $"{{\"i\":{i}}}", Identity());
            }

            var lines = File.ReadAllLines(path);
            // Corrupt the tool name in the second record without recomputing hashes.
            lines[1] = lines[1].Replace("ci_get_runner_status", "ci_get_runner_status_TAMPERED");
            File.WriteAllLines(path, lines);

            Assert.Throws<AuditChainException>(() => AuditLogger.CreateVerified(path));
        }
        finally
        {
            Cleanup(path);
        }
    }

    [Fact]
    public void Chain_continues_correctly_after_simulated_rotation()
    {
        var path = TempPath();
        try
        {
            var logger = AuditLogger.CreateVerified(path);
            logger.Append(new Redactor(), "infra_list_services", new Dictionary<string, object?>(), "{\"a\":1}", Identity());
            logger.Append(new Redactor(), "infra_list_services", new Dictionary<string, object?>(), "{\"a\":2}", Identity());

            // Simulate rotation: move current file aside, start a new file whose first
            // prev_hash must equal the last hash of the rotated-out file.
            var rotatedPath = path + ".1";
            File.Move(path, rotatedPath);

            var rotatedLastLine = File.ReadAllLines(rotatedPath).Last();
            var expectedPrevHash = Convert.ToHexString(
                System.Security.Cryptography.SHA256.HashData(Encoding.UTF8.GetBytes(rotatedLastLine))).ToLowerInvariant();

            var newLogger = AuditLogger.CreateVerified(path, expectedPrevHash);
            newLogger.Append(new Redactor(), "infra_list_services", new Dictionary<string, object?>(), "{\"a\":3}", Identity());

            var continuationLine = File.ReadAllLines(path).Single();
            Assert.Contains($"\"prev_hash\":\"{expectedPrevHash}\"", continuationLine);

            // Re-verifying the new file with the same seed must succeed (chain intact across rotation).
            var reverified = AuditLogger.CreateVerified(path, expectedPrevHash);
            Assert.NotNull(reverified);
        }
        finally
        {
            Cleanup(path);
            Cleanup(path + ".1");
        }
    }

    private static string TempPath() => Path.Combine(Path.GetTempPath(), $"audit-test-{Guid.NewGuid():n}.jsonl");

    private static void Cleanup(string path)
    {
        if (File.Exists(path))
        {
            File.Delete(path);
        }
    }
}
