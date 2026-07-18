using System.Text.Json.Serialization;

namespace OpsConsole.Mcp.Snapshot;

// Shape of the read-only snapshot produced by the collector (see 01-architettura.md §"Formato
// dello snapshot"). The base shape documented there is indicative; this is the concrete
// superset needed to satisfy every tool in 02-contratto-tool.md. Property names are snake_case
// on the wire because the collector is a separate (non-.NET) process.
public sealed class SnapshotDocument
{
    [JsonPropertyName("generated_at")]
    public DateTimeOffset GeneratedAt { get; init; }

    [JsonPropertyName("stale_after_seconds")]
    public int StaleAfterSeconds { get; init; } = 300;

    [JsonPropertyName("docker")]
    public DockerSection Docker { get; init; } = new();

    [JsonPropertyName("backup")]
    public BackupSection Backup { get; init; } = new();

    [JsonPropertyName("ops_scripts")]
    public List<OpsScriptEntry> OpsScripts { get; init; } = [];

    [JsonPropertyName("ci")]
    public CiSection Ci { get; init; } = new();

    [JsonPropertyName("errors")]
    public List<string> Errors { get; init; } = [];

    public bool IsStale(DateTimeOffset now) =>
        now - GeneratedAt > TimeSpan.FromSeconds(StaleAfterSeconds);
}

public sealed class DockerSection
{
    // One entry per known environment ("prod" / "tests"), matching infra_list_services.
    [JsonPropertyName("projects")]
    public List<DockerProject> Projects { get; init; } = [];
}

public sealed class DockerProject
{
    [JsonPropertyName("name")]
    public string Name { get; init; } = "";

    [JsonPropertyName("containers")]
    public List<DockerContainer> Containers { get; init; } = [];
}

public sealed class DockerContainer
{
    [JsonPropertyName("name")]
    public string Name { get; init; } = "";

    [JsonPropertyName("state")]
    public string State { get; init; } = "unknown";

    [JsonPropertyName("health")]
    public string Health { get; init; } = "none";

    [JsonPropertyName("image")]
    public string Image { get; init; } = "";

    // Recent log tail, already redacted once by the collector; the server redacts again
    // as defense in depth before it ever reaches a tool response.
    [JsonPropertyName("log_tail")]
    public List<string> LogTail { get; init; } = [];
}

public sealed class BackupSection
{
    [JsonPropertyName("last_run_at")]
    public DateTimeOffset? LastRunAt { get; init; }

    [JsonPropertyName("last_result")]
    public string LastResult { get; init; } = "unknown"; // success|failure|unknown

    [JsonPropertyName("duration_seconds")]
    public int? DurationSeconds { get; init; }

    [JsonPropertyName("healthcheck_status")]
    public string HealthcheckStatus { get; init; } = "not_configured"; // available|not_configured|unavailable

    [JsonPropertyName("healthcheck_outcome")]
    public string? HealthcheckOutcome { get; init; } // success|failure|unknown|null
}

public sealed class OpsScriptEntry
{
    // One of: deploy | restore-test | prepush-proof (static whitelist owned by the collector).
    [JsonPropertyName("name")]
    public string Name { get; init; } = "";

    [JsonPropertyName("last_exit_code")]
    public int LastExitCode { get; init; }

    [JsonPropertyName("last_run_at")]
    public DateTimeOffset LastRunAt { get; init; }

    [JsonPropertyName("log_tail")]
    public string LogTail { get; init; } = "";
}

public sealed class CiSection
{
    [JsonPropertyName("runners")]
    public List<CiRunnerEntry> Runners { get; init; } = [];

    [JsonPropertyName("recent_runs")]
    public List<CiRunEntry> RecentRuns { get; init; } = [];
}

public sealed class CiRunnerEntry
{
    [JsonPropertyName("id")]
    public string Id { get; init; } = "";

    [JsonPropertyName("status")]
    public string Status { get; init; } = "offline"; // online|offline

    [JsonPropertyName("busy")]
    public bool Busy { get; init; }

    [JsonPropertyName("os")]
    public string Os { get; init; } = "";

    [JsonPropertyName("architecture")]
    public string Architecture { get; init; } = "";
}

public sealed class CiRunEntry
{
    [JsonPropertyName("workflow")]
    public string Workflow { get; init; } = ""; // ci|docker-build

    [JsonPropertyName("branch")]
    public string Branch { get; init; } = "";

    [JsonPropertyName("run_id")]
    public long RunId { get; init; }

    [JsonPropertyName("status")]
    public string Status { get; init; } = "completed"; // queued|in_progress|completed

    [JsonPropertyName("conclusion")]
    public string? Conclusion { get; init; }

    [JsonPropertyName("started_at")]
    public DateTimeOffset StartedAt { get; init; }

    [JsonPropertyName("updated_at")]
    public DateTimeOffset UpdatedAt { get; init; }

    [JsonPropertyName("html_url")]
    public string HtmlUrl { get; init; } = "";

    [JsonPropertyName("failed_jobs")]
    public List<CiFailedJob> FailedJobs { get; init; } = [];
}

public sealed class CiFailedJob
{
    [JsonPropertyName("job_name")]
    public string JobName { get; init; } = "";

    [JsonPropertyName("conclusion")]
    public string Conclusion { get; init; } = "failure";

    [JsonPropertyName("failed_steps")]
    public List<CiFailedStep> FailedSteps { get; init; } = [];
}

public sealed class CiFailedStep
{
    [JsonPropertyName("step_name")]
    public string StepName { get; init; } = "";

    [JsonPropertyName("number")]
    public int Number { get; init; }

    [JsonPropertyName("conclusion")]
    public string Conclusion { get; init; } = "failure";
}
