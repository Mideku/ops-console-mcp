using OpsConsole.Mcp.Errors;
using OpsConsole.Mcp.Tools;
using Xunit;

namespace OpsConsole.Mcp.Tests;

public class ToolValidationTests
{
    [Fact]
    public void RequireEnum_accepts_known_value()
    {
        var result = ToolValidation.RequireEnum("infra_get_service_logs", "service", "postgres", "caddy", "web", "postgres");
        Assert.Equal("postgres", result);
    }

    [Fact]
    public void RequireEnum_rejects_value_outside_enum()
    {
        var ex = Assert.Throws<ToolException>(() =>
            ToolValidation.RequireEnum("infra_get_service_logs", "service", "redis", "caddy", "web", "postgres"));
        Assert.Equal(ToolErrorCode.INVALID_ARGUMENT, ex.Code);
    }

    [Fact]
    public void RequireEnum_rejects_null_value()
    {
        var ex = Assert.Throws<ToolException>(() =>
            ToolValidation.RequireEnum("infra_get_job_result", "job", null, "deploy", "restore-test", "prepush-proof"));
        Assert.Equal(ToolErrorCode.INVALID_ARGUMENT, ex.Code);
    }

    [Theory]
    [InlineData(0)]
    [InlineData(501)]
    [InlineData(-5)]
    public void RequireRange_rejects_out_of_range_lines(int lines)
    {
        var ex = Assert.Throws<ToolException>(() =>
            ToolValidation.RequireRange("infra_get_service_logs", "lines", lines, 1, 500, 100));
        Assert.Equal(ToolErrorCode.INVALID_ARGUMENT, ex.Code);
    }

    [Fact]
    public void RequireRange_uses_default_when_absent()
    {
        var result = ToolValidation.RequireRange("infra_get_service_logs", "lines", null, 1, 500, 100);
        Assert.Equal(100, result);
    }

    [Fact]
    public void OptionalBranch_accepts_null()
    {
        Assert.Null(ToolValidation.OptionalBranch(null));
    }

    [Fact]
    public void OptionalBranch_accepts_conforming_ref_name()
    {
        Assert.Equal("feature/my-branch.1", ToolValidation.OptionalBranch("feature/my-branch.1"));
    }

    [Theory]
    [InlineData("feature branch")]
    [InlineData("$(rm -rf /)")]
    [InlineData("")]
    public void OptionalBranch_rejects_non_conforming_values(string branch)
    {
        var ex = Assert.Throws<ToolException>(() => ToolValidation.OptionalBranch(branch));
        Assert.Equal(ToolErrorCode.INVALID_ARGUMENT, ex.Code);
    }

    [Fact]
    public void RequirePositiveLong_rejects_zero_and_negative()
    {
        Assert.Throws<ToolException>(() => ToolValidation.RequirePositiveLong("run_id", 0));
        Assert.Throws<ToolException>(() => ToolValidation.RequirePositiveLong("run_id", -1));
    }

    [Fact]
    public void RequirePositiveLong_accepts_positive_value()
    {
        Assert.Equal(42, ToolValidation.RequirePositiveLong("run_id", 42));
    }
}
