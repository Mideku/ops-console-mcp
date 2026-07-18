#!/usr/bin/env python3
"""
Smoke test for the OpsConsole MCP stdio server.

Starts `dotnet run --project src/OpsConsole.Mcp` with an unreachable snapshot URL
(the point is only to complete the JSON-RPC handshake, not to reach a real
collector), then drives it through:

  1. `initialize`
  2. `notifications/initialized`
  3. `tools/list`               -> must report exactly the 8 expected tool names
  4. `tools/call` (infra_list_services) -> must fail gracefully with
     error code UPSTREAM_UNAVAILABLE (never crash the process), because the
     snapshot endpoint is unreachable.

Usage:
    python tools/smoke_stdio.py [--dotnet PATH_TO_DOTNET]

Exit code is 0 on success, non-zero otherwise. Prints diagnostics to stderr and
a final summary (tool names + upstream-error check) to stdout.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PROJECT_DIR = REPO_ROOT / "src" / "OpsConsole.Mcp"

EXPECTED_TOOLS = {
    "infra_list_services",
    "infra_get_service_logs",
    "infra_get_last_backup_status",
    "infra_get_last_deploy_status",
    "infra_get_job_result",
    "ci_get_latest_run",
    "ci_list_failed_jobs",
    "ci_get_runner_status",
}


class JsonRpcStdioClient:
    """Minimal newline-delimited JSON-RPC client over a subprocess's stdio."""

    def __init__(self, proc: subprocess.Popen):
        self.proc = proc
        self._next_id = 1
        self._stderr_lines: list[str] = []
        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_thread.start()

    def _drain_stderr(self) -> None:
        assert self.proc.stderr is not None
        for line in self.proc.stderr:
            self._stderr_lines.append(line.rstrip("\n"))

    def stderr_tail(self, n: int = 40) -> str:
        return "\n".join(self._stderr_lines[-n:])

    def send_request(self, method: str, params: dict | None = None) -> dict:
        req_id = self._next_id
        self._next_id += 1
        message = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params is not None:
            message["params"] = params
        self._write(message)
        return self._read_matching(req_id)

    def send_notification(self, method: str, params: dict | None = None) -> None:
        message = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self._write(message)

    def _write(self, message: dict) -> None:
        assert self.proc.stdin is not None
        line = json.dumps(message)
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()

    def _read_matching(self, req_id: int, timeout: float = 60.0) -> dict:
        assert self.proc.stdout is not None
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(
                    f"server process exited early (code {self.proc.returncode}) while waiting "
                    f"for response to id={req_id}.\n--- stderr tail ---\n{self.stderr_tail()}"
                )
            line = self.proc.stdout.readline()
            if not line:
                time.sleep(0.05)
                continue
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                # Not JSON-RPC (shouldn't happen on stdout); ignore.
                continue
            if obj.get("id") == req_id:
                return obj
            # Otherwise it's a notification or a response to something else; skip it.
        raise TimeoutError(f"timed out waiting for response to id={req_id}")


def find_dotnet(explicit: str | None) -> str:
    if explicit:
        return explicit
    local = REPO_ROOT / ".dotnet" / "dotnet.exe"
    if local.exists():
        return str(local)
    local_unix = REPO_ROOT / ".dotnet" / "dotnet"
    if local_unix.exists():
        return str(local_unix)
    return "dotnet"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dotnet", default=None, help="Path to the dotnet executable to use.")
    args = parser.parse_args()

    dotnet_path = find_dotnet(args.dotnet)

    env = os.environ.copy()
    env.setdefault("OPS_CONSOLE_SNAPSHOT_URL", "http://127.0.0.1:9/snapshot.json")
    env.setdefault("OPS_CONSOLE_AUDIT_PATH", str(REPO_ROOT / "tools" / ".smoke_audit.jsonl"))
    env.setdefault("DOTNET_NOLOGO", "1")
    env.setdefault("DOTNET_CLI_TELEMETRY_OPTOUT", "1")

    audit_path = Path(env["OPS_CONSOLE_AUDIT_PATH"])
    if audit_path.exists():
        audit_path.unlink()

    print(f"[smoke] using dotnet: {dotnet_path}", file=sys.stderr)
    print(f"[smoke] project dir : {PROJECT_DIR}", file=sys.stderr)

    proc = subprocess.Popen(
        [dotnet_path, "run", "--project", str(PROJECT_DIR)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(REPO_ROOT),
        env=env,
        text=True,
        bufsize=1,
    )

    client = JsonRpcStdioClient(proc)

    try:
        # 1. initialize
        init_resp = client.send_request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "smoke-stdio", "version": "0.0.1"},
            },
        )
        if "error" in init_resp:
            print(f"[smoke] initialize FAILED: {init_resp}", file=sys.stderr)
            return 1
        print("[smoke] initialize OK", file=sys.stderr)

        # 2. notifications/initialized
        client.send_notification("notifications/initialized")

        # 3. tools/list
        list_resp = client.send_request("tools/list")
        if "error" in list_resp:
            print(f"[smoke] tools/list FAILED: {list_resp}", file=sys.stderr)
            return 1

        tools = list_resp.get("result", {}).get("tools", [])
        tool_names = sorted(t["name"] for t in tools)
        print("tools/list ->", ", ".join(tool_names))

        if set(tool_names) != EXPECTED_TOOLS:
            missing = EXPECTED_TOOLS - set(tool_names)
            extra = set(tool_names) - EXPECTED_TOOLS
            print(
                f"[smoke] FAIL: tool set mismatch. missing={sorted(missing)} extra={sorted(extra)}",
                file=sys.stderr,
            )
            return 1
        print(f"[smoke] tools/list OK: exactly the {len(EXPECTED_TOOLS)} expected tools", file=sys.stderr)

        # 4. tools/call infra_list_services with an unreachable snapshot -> must be a
        #    graceful tool-level error (UPSTREAM_UNAVAILABLE), not a crash.
        call_resp = client.send_request(
            "tools/call",
            {"name": "infra_list_services", "arguments": {}},
        )
        if "error" in call_resp:
            print(f"[smoke] tools/call transport-level error (unexpected): {call_resp}", file=sys.stderr)
            return 1

        result = call_resp.get("result", {})
        is_error = result.get("isError", False)
        content = result.get("content", [])
        text = ""
        for block in content:
            if block.get("type") == "text":
                text = block.get("text", "")
                break

        print("tools/call infra_list_services ->", json.dumps(result))

        if not is_error:
            print("[smoke] FAIL: expected isError=true (upstream unreachable) but call succeeded", file=sys.stderr)
            return 1

        try:
            payload = json.loads(text)
            error_code = payload.get("error", {}).get("code")
        except json.JSONDecodeError:
            error_code = None

        if error_code != "UPSTREAM_UNAVAILABLE":
            print(f"[smoke] FAIL: expected error code UPSTREAM_UNAVAILABLE, got {error_code!r}", file=sys.stderr)
            return 1

        print(f"[smoke] tools/call OK: graceful UPSTREAM_UNAVAILABLE, process still alive", file=sys.stderr)

        if proc.poll() is not None:
            print(f"[smoke] FAIL: server process exited (code {proc.returncode}) after the call", file=sys.stderr)
            return 1

        print("[smoke] ALL CHECKS PASSED", file=sys.stderr)
        return 0
    finally:
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:
            pass
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)


if __name__ == "__main__":
    sys.exit(main())
