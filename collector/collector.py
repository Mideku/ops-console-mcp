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


def collect_docker(config: dict, errors: list) -> dict:
    compose_projects = config.get("compose_projects", {})
    # real project name -> abstract label
    real_to_label = {real: label for label, real in compose_projects.items()}
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
        state = (container.get("State") or "unknown").lower()
        health = _parse_docker_health(container.get("Status", ""))

        by_label.setdefault(label, []).append(
            {
                "name": redact(service),
                "state": state,
                "health": health,
                "image": redact(container.get("Image", "")),
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
# d. GitHub API (CI runner status + recent workflow runs)
# --------------------------------------------------------------------------


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


def collect_ci(config: dict, errors: list) -> dict:
    result = {"runner_status": "unknown", "recent_runs": []}

    github_cfg = config.get("github")
    if not github_cfg:
        return result

    token = os.environ.get("OPS_CONSOLE_GH_PAT")
    if not token:
        errors.append("github: token not configured")
        return result

    owner = github_cfg.get("owner")
    repo = github_cfg.get("repo")
    workflows = github_cfg.get("workflows", {})

    if not owner or not repo:
        errors.append("github: owner/repo not configured")
        return result

    base_url = f"https://api.github.com/repos/{owner}/{repo}"

    try:
        runners_data = _github_get(f"{base_url}/actions/runners", token)
        runners = runners_data.get("runners", [])
        if any(r.get("status") == "online" for r in runners):
            result["runner_status"] = "online"
        elif runners:
            result["runner_status"] = "offline"
        else:
            result["runner_status"] = "unknown"
    except (urllib.error.URLError, urllib.error.HTTPError) as exc:
        errors.append(redact(f"github runners: {type(exc).__name__}: {exc}"))
    except Exception as exc:  # noqa: BLE001
        errors.append(redact(f"github runners: {type(exc).__name__}: {exc}"))

    for workflow_name, workflow_file in workflows.items():
        try:
            runs_data = _github_get(
                f"{base_url}/actions/workflows/{workflow_file}/runs?per_page=10",
                token,
            )
            for run in runs_data.get("workflow_runs", []):
                result["recent_runs"].append(
                    {
                        "workflow": workflow_name,
                        "run_id": run.get("id"),
                        "status": run.get("status"),
                        "conclusion": run.get("conclusion"),
                        "branch": redact(run.get("head_branch", "")),
                        "started_at": run.get("run_started_at"),
                        "updated_at": run.get("updated_at"),
                        "html_url": run.get("html_url"),
                    }
                )
        except (urllib.error.URLError, urllib.error.HTTPError) as exc:
            errors.append(
                redact(f"github runs ({workflow_name}): {type(exc).__name__}: {exc}")
            )
        except Exception as exc:  # noqa: BLE001
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
