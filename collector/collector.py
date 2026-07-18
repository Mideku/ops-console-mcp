#!/usr/bin/env python3
"""ops-console-mcp collector.

Runs on the monitored Linux host as a systemd timer (see
../01-architettura.md). Reads read-only state from Docker, the backup
systemd unit's journal, a static whitelist of ops-script log+exit file
pairs, and (optionally) the GitHub REST API, then writes a single
redacted JSON snapshot atomically.

Python 3.12, stdlib only (the target host has no guaranteed pip). Every
subprocess/network call happens at call time inside a function, never at
import time, so this module stays import-safe on any platform (including
Windows, for local testing).

Usage:
    python collector.py <path-to-config.json>

See collector.config.example.json for the config shape.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

from redactor import redact

DEFAULT_STALE_AFTER_SECONDS = 300
DEFAULT_LOG_TAIL_LINES = 40
ALLOWED_JOB_NAMES = {"deploy", "restore-test", "prepush-proof"}

DOCKER_TIMEOUT_SECONDS = 5
JOURNAL_TIMEOUT_SECONDS = 5
GITHUB_TIMEOUT_SECONDS = 10


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# --------------------------------------------------------------------------
# a. Docker Compose / container state
# --------------------------------------------------------------------------


def _parse_docker_health(status_text: str) -> str:
    text = (status_text or "").lower()
    if "(healthy)" in text:
        return "healthy"
    if "(unhealthy)" in text:
        return "unhealthy"
    if "(health: starting)" in text or "(starting)" in text:
        return "starting"
    return "none"


def _docker_log_tail(container_ref: str, log_tail_lines: int) -> list:
    """Return the last `log_tail_lines` lines of `docker logs` for the given
    real container reference (id or name), stdout+stderr combined in
    chronological order, each line redacted. Raises on failure; the caller
    is responsible for the per-container try/except + errors[] entry so one
    bad container never aborts the whole collection."""
    if log_tail_lines <= 0 or not container_ref:
        return []
    proc = subprocess.run(
        ["docker", "logs", "--tail", str(log_tail_lines), container_ref],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,  # merge streams so ordering reflects reality,
        # rather than capturing stdout/stderr separately and concatenating
        # (which would lose interleaving order).
        text=True,
        timeout=DOCKER_TIMEOUT_SECONDS,
        check=True,
    )
    return [redact(line) for line in proc.stdout.splitlines()]


def collect_docker(config: dict, errors: list) -> dict:
    compose_projects = config.get("compose_projects", {})
    # real project name -> abstract label
    real_to_label = {real: label for label, real in compose_projects.items()}
    log_tail_lines = config.get("log_tail_lines", DEFAULT_LOG_TAIL_LINES)
    result = {"projects": []}

    if not compose_projects:
        return result

    try:
        proc = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{json .}}"],
            capture_output=True,
            text=True,
            timeout=DOCKER_TIMEOUT_SECONDS,
            check=True,
        )
    except Exception as exc:  # noqa: BLE001 - deliberately broad, isolated per source
        errors.append(redact(f"docker: {type(exc).__name__}: {exc}"))
        return result

    by_label: dict[str, list] = {}
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            container = json.loads(line)
        except json.JSONDecodeError:
            continue

        labels_raw = container.get("Labels", "") or ""
        labels = {}
        for pair in labels_raw.split(","):
            if "=" in pair:
                key, _, value = pair.partition("=")
                labels[key] = value

        project_real = labels.get("com.docker.compose.project")
        if project_real is None or project_real not in real_to_label:
            continue  # only report containers of configured projects

        label = real_to_label[project_real]
        service = labels.get("com.docker.compose.service") or container.get("Names", "unknown")
        service_label = redact(service)
        state = (container.get("State") or "unknown").lower()
        health = _parse_docker_health(container.get("Status", ""))

        # Real container id/name used only for the docker CLI call below;
        # never stored in the snapshot (only the abstract service_label is).
        container_ref = container.get("ID") or container.get("Names") or ""
        try:
            log_tail = _docker_log_tail(container_ref, log_tail_lines)
        except Exception as exc:  # noqa: BLE001 - isolated per container, never crash
            log_tail = []
            errors.append(
                redact(f"docker logs ({label}/{service_label}): {type(exc).__name__}: {exc}")
            )

        by_label.setdefault(label, []).append(
            {
                "name": service_label,
                "state": state,
                "health": health,
                "image": redact(container.get("Image", "")),
                "log_tail": log_tail,
            }
        )

    for label in compose_projects:
        result["projects"].append({"name": label, "containers": by_label.get(label, [])})

    return result


# --------------------------------------------------------------------------
# b. Backup systemd unit (journal + timer)
# --------------------------------------------------------------------------


def collect_backup(config: dict, errors: list) -> dict:
    backup_unit = config.get("backup_unit")
    result = {"last_run_at": None, "last_result": "unknown", "next_scheduled_at": None}

    if not backup_unit:
        return result

    try:
        proc = subprocess.run(
            ["journalctl", "-u", backup_unit, "-n", "50", "--no-pager", "-o", "json"],
            capture_output=True,
            text=True,
            timeout=JOURNAL_TIMEOUT_SECONDS,
            check=True,
        )
        latest_timestamp = None
        latest_outcome = "unknown"
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            message = (entry.get("MESSAGE") or "")
            if isinstance(message, list):
                # journalctl -o json may emit non-UTF8 fields as byte arrays
                continue
            realtime = entry.get("__REALTIME_TIMESTAMP")
            outcome = None
            lowered = message.lower()
            if "finished" in lowered or "deactivated successfully" in lowered:
                outcome = "success"
            elif "failed" in lowered:
                outcome = "failure"
            if outcome is not None and realtime is not None:
                if latest_timestamp is None or int(realtime) >= int(latest_timestamp):
                    latest_timestamp = realtime
                    latest_outcome = outcome

        result["last_result"] = latest_outcome
        if latest_timestamp is not None:
            seconds = int(latest_timestamp) / 1_000_000
            result["last_run_at"] = datetime.fromtimestamp(
                seconds, tz=timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception as exc:  # noqa: BLE001
        errors.append(redact(f"backup journal: {type(exc).__name__}: {exc}"))

    # Best-effort: next scheduled run from systemctl list-timers.
    try:
        proc = subprocess.run(
            ["systemctl", "list-timers", "--no-pager"],
            capture_output=True,
            text=True,
            timeout=JOURNAL_TIMEOUT_SECONDS,
            check=True,
        )
        for line in proc.stdout.splitlines():
            if backup_unit in line:
                parts = line.split()
                if len(parts) >= 2:
                    # "NEXT" column is typically the first two tokens
                    # (weekday + date/time), best-effort join.
                    result["next_scheduled_at"] = redact(" ".join(parts[:5]))
                break
    except Exception as exc:  # noqa: BLE001
        errors.append(redact(f"backup timer: {type(exc).__name__}: {exc}"))

    return result


# --------------------------------------------------------------------------
# c. Ops scripts: log + exit file pattern
# --------------------------------------------------------------------------


def collect_jobs(config: dict, errors: list) -> list:
    jobs_cfg = config.get("jobs", {})
    log_tail_lines = config.get("log_tail_lines", DEFAULT_LOG_TAIL_LINES)
    results = []

    for name, paths in jobs_cfg.items():
        if name not in ALLOWED_JOB_NAMES:
            errors.append(f"ops_scripts: unknown job name '{name}' skipped")
            continue
        entry = {
            "name": name,
            "last_exit_code": None,
            "last_run_at": None,
            "log_tail": [],
        }
        try:
            exit_path = paths.get("exit")
            if exit_path and os.path.isfile(exit_path):
                with open(exit_path, "r", encoding="utf-8") as f:
                    entry["last_exit_code"] = int(f.read().strip())
                mtime = os.path.getmtime(exit_path)
                entry["last_run_at"] = datetime.fromtimestamp(
                    mtime, tz=timezone.utc
                ).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception as exc:  # noqa: BLE001
            errors.append(redact(f"job {name} exit: {type(exc).__name__}: {exc}"))

        try:
            log_path = paths.get("log")
            if log_path and os.path.isfile(log_path):
                with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
                tail = lines[-log_tail_lines:] if log_tail_lines > 0 else []
                entry["log_tail"] = [redact(line.rstrip("\n")) for line in tail]
        except Exception as exc:  # noqa: BLE001
            errors.append(redact(f"job {name} log: {type(exc).__name__}: {exc}"))

        results.append(entry)

    return results


# --------------------------------------------------------------------------
# d. GitHub API (CI runner status + recent workflow runs + failed jobs)
# --------------------------------------------------------------------------

# GitHub attaches the runner's architecture as one of these fixed, read-only
# labels (type "read-only", assigned by GitHub itself) rather than as a
# top-level field of the runner object. Only these known-safe label names
# are ever surfaced as "architecture" — arbitrary/custom labels (which could
# contain a real hostname) are never inspected or included.
_KNOWN_RUNNER_ARCHITECTURES = {"X64", "ARM64", "ARM", "ARM32", "X86"}

# Per-workflow cap on how many of the most recent failed runs get their
# failed_jobs populated (each requires one extra GitHub API call).
MAX_FAILED_RUNS_WITH_JOBS = 3
RECENT_RUNS_PER_WORKFLOW = 10


def _github_get(url: str, token: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ops-console-mcp-collector",
        },
    )
    with urllib.request.urlopen(request, timeout=GITHUB_TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


def _redact_str(value):
    """Apply redact() to string values sourced from the GitHub API before
    they land in the snapshot; passes non-string values (None, int, bool)
    through unchanged so nullable/typed fields keep their real type."""
    if isinstance(value, str):
        return redact(value)
    return value


def _runner_architecture(labels) -> str:
    for label in labels or []:
        if not isinstance(label, dict):
            continue
        if label.get("type") == "read-only" and label.get("name") in _KNOWN_RUNNER_ARCHITECTURES:
            return label.get("name", "")
    return ""


def _normalize_runner(raw: dict) -> dict:
    # Deliberately whitelist only id/status/busy/os/architecture. Never pass
    # through "name" (the real, human-assigned runner name) or the raw
    # "labels" array (custom labels can contain a real hostname).
    return {
        "id": _redact_str(str(raw.get("id", ""))),
        "status": _redact_str(raw.get("status", "offline")),
        "busy": bool(raw.get("busy", False)),
        "os": _redact_str(raw.get("os", "")),
        "architecture": _redact_str(_runner_architecture(raw.get("labels"))),
    }


def _collect_failed_jobs(base_url: str, run_id, token: str) -> list:
    jobs_data = _github_get(f"{base_url}/actions/runs/{run_id}/jobs", token)
    failed_jobs = []
    for job in jobs_data.get("jobs", []):
        if job.get("conclusion") != "failure":
            continue
        failed_steps = []
        for step in job.get("steps", []) or []:
            if step.get("conclusion") == "failure":
                failed_steps.append(
                    {
                        "step_name": _redact_str(step.get("name", "")),
                        "number": step.get("number"),
                        "conclusion": _redact_str(step.get("conclusion")),
                    }
                )
        failed_jobs.append(
            {
                "job_name": _redact_str(job.get("name", "")),
                "conclusion": _redact_str(job.get("conclusion")),
                "failed_steps": failed_steps,
            }
        )
    return failed_jobs


def collect_ci(config: dict, errors: list) -> dict:
    result: dict = {"runners": [], "recent_runs": []}

    github_cfg = config.get("github")
    if not github_cfg:
        return result

    token = os.environ.get("OPS_CONSOLE_GH_PAT")
    if not token:
        errors.append("github: token not configured")
        return result

    owner = github_cfg.get("owner")
    repo = github_cfg.get("repo")
    # Optional: the self-hosted runner may be registered under a different
    # repository of the same owner than the one whose workflow runs are
    # monitored (e.g. a dedicated infra/runners repo). Falls back to `repo`
    # (current behavior) when absent. Workflow runs always use `repo`,
    # never `runner_repo` — only the runners endpoint below is affected.
    runner_repo = github_cfg.get("runner_repo") or repo
    workflows = github_cfg.get("workflows", {})

    if not owner or not repo:
        errors.append("github: owner/repo not configured")
        return result

    base_url = f"https://api.github.com/repos/{owner}/{repo}"
    runner_base_url = f"https://api.github.com/repos/{owner}/{runner_repo}"

    try:
        runners_data = _github_get(f"{runner_base_url}/actions/runners", token)
        for raw_runner in runners_data.get("runners", []):
            result["runners"].append(_normalize_runner(raw_runner))
    except Exception as exc:  # noqa: BLE001 - isolated, degrades to errors[]
        errors.append(
            redact(f"github runners ({runner_repo}): {type(exc).__name__}: {exc}")
        )

    for workflow_name, workflow_file in workflows.items():
        try:
            runs_data = _github_get(
                f"{base_url}/actions/workflows/{workflow_file}/runs"
                f"?per_page={RECENT_RUNS_PER_WORKFLOW}",
                token,
            )
            runs = runs_data.get("workflow_runs", [])
            # Sort explicitly (most recent first) instead of trusting the
            # API's default ordering, so "3 most recent failures" is correct
            # regardless of what order GitHub actually returns runs in.
            # run_started_at is ISO-8601 UTC, so lexicographic sort is
            # equivalent to chronological sort.
            runs.sort(key=lambda r: r.get("run_started_at") or "", reverse=True)
            runs = runs[:RECENT_RUNS_PER_WORKFLOW]

            failure_budget = MAX_FAILED_RUNS_WITH_JOBS
            for run in runs:
                run_id = run.get("id")
                failed_jobs: list = []
                if run.get("conclusion") == "failure" and failure_budget > 0:
                    failure_budget -= 1
                    try:
                        failed_jobs = _collect_failed_jobs(base_url, run_id, token)
                    except Exception as exc:  # noqa: BLE001 - isolated per run
                        errors.append(
                            redact(f"github jobs (run {run_id}): {type(exc).__name__}: {exc}")
                        )

                result["recent_runs"].append(
                    {
                        "workflow": workflow_name,
                        "branch": _redact_str(run.get("head_branch") or ""),
                        "run_id": run_id,
                        "status": _redact_str(run.get("status")),
                        "conclusion": _redact_str(run.get("conclusion")),
                        "started_at": run.get("run_started_at"),
                        "updated_at": run.get("updated_at"),
                        "html_url": run.get("html_url"),
                        "failed_jobs": failed_jobs,
                    }
                )
        except Exception as exc:  # noqa: BLE001 - isolated per workflow
            errors.append(
                redact(f"github runs ({workflow_name}): {type(exc).__name__}: {exc}")
            )

    return result


# --------------------------------------------------------------------------
# Snapshot assembly + atomic write
# --------------------------------------------------------------------------


def build_snapshot(config: dict) -> dict:
    errors: list = []

    docker = collect_docker(config, errors)
    backup = collect_backup(config, errors)
    ops_scripts = collect_jobs(config, errors)
    ci = collect_ci(config, errors)

    snapshot = {
        "generated_at": _utc_now_iso(),
        "stale_after_seconds": config.get("stale_after_seconds", DEFAULT_STALE_AFTER_SECONDS),
        "docker": docker,
        "backup": backup,
        "ops_scripts": ops_scripts,
        "ci": ci,
        "errors": errors,
    }
    return snapshot


def write_snapshot_atomic(snapshot: dict, output_path: str) -> None:
    directory = os.path.dirname(os.path.abspath(output_path)) or "."
    tmp_path = os.path.join(directory, f".{os.path.basename(output_path)}.tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp_path, output_path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def main(argv: list) -> int:
    if len(argv) < 2:
        print("usage: collector.py <config.json>", file=sys.stderr)
        return 2

    config = load_config(argv[1])
    snapshot = build_snapshot(config)
    output_path = config.get("output_path")
    if not output_path:
        print("config error: 'output_path' is required", file=sys.stderr)
        return 2

    write_snapshot_atomic(snapshot, output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
