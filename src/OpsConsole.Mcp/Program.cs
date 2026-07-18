using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using OpsConsole.Mcp.Audit;
using OpsConsole.Mcp.Configuration;
using OpsConsole.Mcp.RateLimiting;
using OpsConsole.Mcp.Security;
using OpsConsole.Mcp.Snapshot;
using OpsConsole.Mcp.Tools;

// Fail-closed startup: verify the audit chain's tail *before* the MCP server is built, so a
// tampered log prevents the process from ever accepting a tools/call (03-sicurezza-threat-model.md §5).
ServerConfig config;
AuditLogger auditLogger;
try
{
    config = ServerConfig.FromEnvironment();
    auditLogger = AuditLogger.CreateVerified(config.AuditPath);
}
catch (AuditChainException ex)
{
    Console.Error.WriteLine($"FATAL: audit log integrity check failed: {ex.Message}");
    Console.Error.WriteLine("Refusing to start (fail-closed). The log may have been tampered with.");
    return 1;
}
catch (Exception ex)
{
    Console.Error.WriteLine($"FATAL: startup configuration error: {ex.Message}");
    return 1;
}

var builder = Host.CreateApplicationBuilder(args);

// All logs must go to stderr: stdout is reserved for the MCP JSON-RPC stream.
builder.Logging.AddConsole(options => options.LogToStandardErrorThreshold = LogLevel.Trace);

builder.Services.AddSingleton(config);
builder.Services.AddSingleton(auditLogger);
builder.Services.AddSingleton<Redactor>();
builder.Services.AddSingleton(new RateLimiter(config.RatePerMinute));
builder.Services.AddSingleton(new HttpClient());
builder.Services.AddSingleton<SnapshotClient>();
builder.Services.AddSingleton<ToolRuntime>();

builder.Services
    .AddMcpServer()
    .WithStdioServerTransport()
    .WithTools<InfraTools>()
    .WithTools<CiTools>();

await builder.Build().RunAsync();
return 0;
