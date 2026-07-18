using System.ComponentModel;
using System.Text.Json;
using ModelContextProtocol.Protocol;
using ModelContextProtocol.Server;
using OpsConsole.Mcp.Errors;

namespace OpsConsole.Mcp.Tools;

/// <summary>
/// ci_* tools (02-contratto-tool.md §6). The GitHub repository queried is configured once on
/// the collector side; no tool accepts owner/repo as input (single-scope, least privilege).
/// </summary>
[McpServerToolType]
public sealed class CiTools(ToolRuntime runtime)
{
    private static readonly string[] KnownWorkflows = ["ci", "docker-build"];
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);

    [McpServerTool(Name = "ci_get_latest_run"), Description(
        "Returns the latest GitHub Actions run of the configured repository for one of the " +
        "two known workflows (ci, docker-build), optionally filtered by branch. When NOT to " +
        "use: for repositories other than the one configured server-side (no repo parameter " +
        "exists, by design); to trigger a new run (no write tool exists); for workflows outside " +
        "the enum.")]
    public async Task<CallToolResult> GetLatestRun(
        McpServer server,
        [Description("Workflow of the configured repository: ci or docker-build.")] string workflow,
        [Description("Optional git ref/branch name. Defaults to the repository's default branch.")] string? branch = null)
    {
        var args = new Dictionary<string, object?> { ["workflow"] = workflow, ["branch"] = branch };
        return await runtime.RunAsync(server, "ci_get_latest_run", args, (snapshot, stale) =>
        {
            var validatedWorkflow = ToolValidation.RequireEnum("ci_get_latest_run", "workflow", workflow, KnownWorkflows);
            var validatedBranch = ToolValidation.OptionalBranch(branch);

            var candidates = snapshot.Ci.RecentRuns.Where(r => r.Workflow == validatedWorkflow);
            if (validatedBranch is not null)
            {
                candidates = candidates.Where(r => r.Branch == validatedBranch);
            }

            var run = candidates.OrderByDescending(r => r.UpdatedAt).FirstOrDefault();
            if (run is null)
            {
                throw new ToolException(ToolErrorCode.NOT_FOUND,
                    $"No run found for workflow '{validatedWorkflow}'" + (validatedBranch is null ? "." : $" on branch '{validatedBranch}'."));
            }

            return Ok(new
            {
                generated_at = snapshot.GeneratedAt,
                stale,
                workflow = validatedWorkflow,
                branch = validatedBranch ?? run.Branch,
                run = new
                {
                    run_id = run.RunId,
                    status = run.Status,
                    conclusion = run.Conclusion,
                    started_at = run.StartedAt,
                    updated_at = run.UpdatedAt,
                    html_url = run.HtmlUrl,
                },
            });
        });
    }

    [McpServerTool(Name = "ci_list_failed_jobs"), Description(
        "Lists failed jobs and steps for a GitHub Actions run of the configured repository. " +
        "When NOT to use: for runs that do not belong to the configured repository — these are " +
        "rejected with NOT_FOUND without distinguishing 'does not exist' from 'exists in " +
        "another repo', to avoid a cross-repo existence oracle; to get the full log of a step " +
        "(only a synthetic name/outcome is returned, never log content).")]
    public async Task<CallToolResult> ListFailedJobs(
        McpServer server,
        [Description("GitHub Actions run id; must belong to the configured repository.")] long runId)
    {
        var args = new Dictionary<string, object?> { ["run_id"] = runId };
        return await runtime.RunAsync(server, "ci_list_failed_jobs", args, (snapshot, stale) =>
        {
            var validatedRunId = ToolValidation.RequirePositiveLong("run_id", runId);
            var run = snapshot.Ci.RecentRuns.FirstOrDefault(r => r.RunId == validatedRunId);
            if (run is null)
            {
                throw new ToolException(ToolErrorCode.NOT_FOUND,
                    $"Run '{validatedRunId}' was not found in the configured repository.");
            }

            const int maxJobs = 50;
            const int maxMessageLength = 300;
            var truncated = run.FailedJobs.Count > maxJobs;

            var failedJobs = run.FailedJobs.Take(maxJobs).Select(j => new
            {
                job_name = Truncate(runtime.Redactor.Redact(j.JobName).Text, maxMessageLength),
                conclusion = j.Conclusion,
                failed_steps = j.FailedSteps.Select(s => new
                {
                    step_name = Truncate(runtime.Redactor.Redact(s.StepName).Text, maxMessageLength),
                    number = s.Number,
                    conclusion = s.Conclusion,
                }).ToArray(),
            }).ToArray();

            return Ok(new
            {
                generated_at = snapshot.GeneratedAt,
                stale,
                run_id = validatedRunId,
                failed_jobs = failedJobs,
                truncated,
            });
        });
    }

    [McpServerTool(Name = "ci_get_runner_status"), Description(
        "Returns online/offline and busy status of the self-hosted runner(s) registered on " +
        "the configured repository. When NOT to use: to start, stop or reconfigure the runner " +
        "(read-only, no process control); to list queued jobs on the runner (out of scope).")]
    public async Task<CallToolResult> GetRunnerStatus(McpServer server) =>
        await runtime.RunAsync(server, "ci_get_runner_status", new Dictionary<string, object?>(), (snapshot, stale) =>
        {
            var runners = snapshot.Ci.Runners.Select(r => new
            {
                id = r.Id,
                status = r.Status,
                busy = r.Busy,
                os = r.Os,
                architecture = r.Architecture,
            }).ToArray();

            return Ok(new
            {
                generated_at = snapshot.GeneratedAt,
                stale,
                runners,
            });
        });

    private static string Truncate(string value, int maxLength) =>
        value.Length <= maxLength ? value : string.Concat(value.AsSpan(0, maxLength), "…");

    private static CallToolResult Ok(object payload)
    {
        var json = JsonSerializer.Serialize(payload, JsonOptions);
        return new CallToolResult { Content = [new TextContentBlock { Text = json }] };
    }
}
