"""Stdlib unittest suite for the ops-console-mcp collector.

Run with:
    python -m unittest discover -s collector/tests -t collector

No network/docker/systemd access is required: all subprocess/urllib calls
are patched.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import urllib.error
from http.client import HTTPConnection
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collector  # noqa: E402
import redactor  # noqa: E402
import snapshot_server  # noqa: E402


class TestRedactor(unittest.TestCase):
    def test_github_token_prefix(self):
        text = "auth header: ghp_abcdefghijklmnopqrstuvwxyz0123456789"
        out = redactor.redact(text)
        self.assertNotIn("ghp_abcdefghijklmnopqrstuvwxyz0123456789", out)
        self.assertIn("[REDACTED:token]", out)

    def test_generic_key_value(self):
        text = "token=supersecretvalue1234567890"
        out = redactor.redact(text)
        self.assertNotIn("supersecretvalue1234567890", out)
        self.assertIn("[REDACTED:token]", out)

    def test_bearer_header(self):
        text = "Authorization: Bearer abcDEF012345678901234"
        out = redactor.redact(text)
        self.assertNotIn("abcDEF012345678901234", out)
        self.assertIn("[REDACTED:token]", out)

    def test_pem_private_key(self):
        text = (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEpAIBAAKCAQEA1234567890\nabcdefg\n"
            "-----END RSA PRIVATE KEY-----"
        )
        out = redactor.redact(text)
        self.assertNotIn("MIIEpAIBAAKCAQEA1234567890", out)
        self.assertIn("[REDACTED:private_key]", out)

    def test_connection_string(self):
        text = "DATABASE_URL=postgres://dbuser:hunter2@dbhost.example.com:5432/mydb"
        out = redactor.redact(text)
        self.assertNotIn("hunter2", out)
        self.assertIn("[REDACTED:conn_string]", out)

    def test_healthcheck_ping_url(self):
        text = "curl https://hc-ping.com/11111111-2222-3333-4444-555555555555"
        out = redactor.redact(text)
        self.assertNotIn("11111111-2222-3333-4444-555555555555", out)
        self.assertIn("[REDACTED:healthcheck]", out)

    def test_private_ipv4(self):
        for ip in ("10.0.0.5", "172.16.5.5", "192.168.1.1", "127.0.0.1"):
            out = redactor.redact(f"connecting to {ip} now")
            self.assertNotIn(ip, out, msg=f"failed for {ip}")
            self.assertIn("[REDACTED:internal_host]", out)

    def test_public_ip_not_redacted(self):
        out = redactor.redact("connecting to 8.8.8.8 now")
        self.assertIn("8.8.8.8", out)

    def test_internal_hostname(self):
        out = redactor.redact("ssh to myhost.internal for details")
        self.assertNotIn("myhost.internal", out)
        self.assertIn("[REDACTED:internal_host]", out)

    def test_entropy_fallback(self):
        # No known pattern matches this, but it is long and high entropy.
        candidate = "aG3x9Qw7Lp2Rt8Yz1Kd4Vb6Nm0Fj5Hc3"
        out = redactor.redact(f"debug value: {candidate}")
        self.assertNotIn(candidate, out)
        self.assertIn("[REDACTED:entropy]", out)

    def test_plain_text_untouched(self):
        text = "container started successfully, all checks passed"
        self.assertEqual(redactor.redact(text), text)

    def test_empty_and_falsy_input(self):
        self.assertEqual(redactor.redact(""), "")
        self.assertEqual(redactor.redact(None), "")


class TestJobCollection(unittest.TestCase):
    def test_parses_log_and_exit_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "deploy.log")
            exit_path = os.path.join(tmp, "deploy.exit")
            with open(log_path, "w", encoding="utf-8") as f:
                f.write("line1\nline2\ntoken=abcdefgh12345678\n")
            with open(exit_path, "w", encoding="utf-8") as f:
                f.write("0\n")

            config = {
                "jobs": {"deploy": {"log": log_path, "exit": exit_path}},
                "log_tail_lines": 2,
            }
            errors: list = []
            results = collector.collect_jobs(config, errors)

            self.assertEqual(len(results), 1)
            entry = results[0]
            self.assertEqual(entry["name"], "deploy")
            self.assertEqual(entry["last_exit_code"], 0)
            self.assertIsNotNone(entry["last_run_at"])
            self.assertEqual(len(entry["log_tail"]), 2)
            self.assertTrue(any("[REDACTED:token]" in line for line in entry["log_tail"]))
            self.assertEqual(errors, [])

    def test_unknown_job_name_is_skipped_and_reported(self):
        config = {"jobs": {"not-a-real-job": {"log": "x", "exit": "y"}}}
        errors: list = []
        results = collector.collect_jobs(config, errors)
        self.assertEqual(results, [])
        self.assertTrue(any("unknown job name" in e for e in errors))

    def test_missing_files_do_not_crash(self):
        config = {"jobs": {"deploy": {"log": "/no/such/log", "exit": "/no/such/exit"}}}
        errors: list = []
        results = collector.collect_jobs(config, errors)
        self.assertEqual(len(results), 1)
        self.assertIsNone(results[0]["last_exit_code"])
        self.assertEqual(results[0]["log_tail"], [])


class TestSnapshotAssembly(unittest.TestCase):
    def test_build_snapshot_with_mocked_sources(self):
        config = {
            "compose_projects": {"prod": "real_project"},
            "backup_unit": "backup.service",
            "jobs": {},
            "output_path": "unused.json",
            "stale_after_seconds": 120,
        }

        with mock.patch.object(
            collector, "collect_docker", return_value={"projects": []}
        ), mock.patch.object(
            collector,
            "collect_backup",
            return_value={"last_run_at": None, "last_result": "unknown", "next_scheduled_at": None},
        ), mock.patch.object(
            collector, "collect_jobs", return_value=[]
        ), mock.patch.object(
            collector, "collect_ci", return_value={"runners": [], "recent_runs": []}
        ):
            snapshot = collector.build_snapshot(config)

        self.assertEqual(snapshot["stale_after_seconds"], 120)
        self.assertIn("generated_at", snapshot)
        self.assertEqual(snapshot["errors"], [])
        self.assertIn("docker", snapshot)
        self.assertIn("backup", snapshot)
        self.assertIn("ops_scripts", snapshot)
        self.assertIn("ci", snapshot)

    def test_stale_after_seconds_present_with_default_when_omitted(self):
        config = {
            "compose_projects": {},
            "backup_unit": None,
            "jobs": {},
            "output_path": "unused.json",
        }

        with mock.patch.object(
            collector, "collect_docker", return_value={"projects": []}
        ), mock.patch.object(
            collector,
            "collect_backup",
            return_value={"last_run_at": None, "last_result": "unknown", "next_scheduled_at": None},
        ), mock.patch.object(
            collector, "collect_jobs", return_value=[]
        ), mock.patch.object(
            collector, "collect_ci", return_value={"runners": [], "recent_runs": []}
        ):
            snapshot = collector.build_snapshot(config)

        self.assertIn("stale_after_seconds", snapshot)
        self.assertEqual(snapshot["stale_after_seconds"], collector.DEFAULT_STALE_AFTER_SECONDS)

    def test_atomic_write_produces_valid_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_path = os.path.join(tmp, "snapshot.json")
            snapshot = {"generated_at": "2026-01-01T00:00:00Z", "errors": []}
            collector.write_snapshot_atomic(snapshot, output_path)

            self.assertTrue(os.path.isfile(output_path))
            with open(output_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            self.assertEqual(loaded["generated_at"], "2026-01-01T00:00:00Z")

            # no leftover tmp file
            leftovers = [
                name for name in os.listdir(tmp) if name != "snapshot.json"
            ]
            self.assertEqual(leftovers, [])


class TestDockerCollection(unittest.TestCase):
    def test_maps_real_project_to_abstract_label(self):
        config = {"compose_projects": {"prod": "real_project"}}
        errors: list = []

        fake_container = json.dumps(
            {
                "Names": "real_project-web-1",
                "State": "running",
                "Status": "Up 2 hours (healthy)",
                "Image": "myimage:latest",
                "Labels": "com.docker.compose.project=real_project,com.docker.compose.service=web",
            }
        )
        fake_proc = mock.Mock(stdout=fake_container + "\n", returncode=0)

        with mock.patch.object(collector.subprocess, "run", return_value=fake_proc):
            result = collector.collect_docker(config, errors)

        self.assertEqual(len(result["projects"]), 1)
        project = result["projects"][0]
        self.assertEqual(project["name"], "prod")
        self.assertEqual(len(project["containers"]), 1)
        container = project["containers"][0]
        self.assertEqual(container["state"], "running")
        self.assertEqual(container["health"], "healthy")

    def test_docker_failure_is_recorded_in_errors_not_raised(self):
        config = {"compose_projects": {"prod": "real_project"}}
        errors: list = []

        with mock.patch.object(
            collector.subprocess, "run", side_effect=FileNotFoundError("docker not found")
        ):
            result = collector.collect_docker(config, errors)

        self.assertEqual(result["projects"], [])
        self.assertEqual(len(errors), 1)

    def test_container_log_tail_present_and_redacted(self):
        config = {"compose_projects": {"prod": "real_project"}, "log_tail_lines": 5}
        errors: list = []

        fake_container = json.dumps(
            {
                "ID": "abc123def456",
                "Names": "real_project-web-1",
                "State": "running",
                "Status": "Up 2 hours (healthy)",
                "Image": "myimage:latest",
                "Labels": "com.docker.compose.project=real_project,com.docker.compose.service=web",
            }
        )

        def fake_run(cmd, **kwargs):
            if cmd[:2] == ["docker", "ps"]:
                return mock.Mock(stdout=fake_container + "\n", returncode=0)
            if cmd[:2] == ["docker", "logs"]:
                # The real container id (never a compose project/service name)
                # is what must be passed to the docker CLI.
                self.assertIn("abc123def456", cmd)
                return mock.Mock(
                    stdout="line one\ntoken=abcdefgh12345678\n", returncode=0
                )
            raise AssertionError(f"unexpected command {cmd}")

        with mock.patch.object(collector.subprocess, "run", side_effect=fake_run):
            result = collector.collect_docker(config, errors)

        container = result["projects"][0]["containers"][0]
        self.assertIn("log_tail", container)
        self.assertEqual(len(container["log_tail"]), 2)
        self.assertTrue(any("[REDACTED:token]" in line for line in container["log_tail"]))
        self.assertNotIn("abcdefgh12345678", json.dumps(container["log_tail"]))
        self.assertEqual(errors, [])

        # Real container id/project name are only used for the docker CLI
        # call; they must never leak into the snapshot itself.
        serialized = json.dumps(result)
        self.assertNotIn("abc123def456", serialized)
        self.assertNotIn("real_project", serialized)

    def test_container_log_tail_failure_is_isolated_per_container(self):
        config = {"compose_projects": {"prod": "real_project"}}
        errors: list = []

        fake_container = json.dumps(
            {
                "ID": "abc123def456",
                "Names": "real_project-web-1",
                "State": "running",
                "Status": "Up 2 hours (healthy)",
                "Image": "myimage:latest",
                "Labels": "com.docker.compose.project=real_project,com.docker.compose.service=web",
            }
        )

        def fake_run(cmd, **kwargs):
            if cmd[:2] == ["docker", "ps"]:
                return mock.Mock(stdout=fake_container + "\n", returncode=0)
            if cmd[:2] == ["docker", "logs"]:
                raise FileNotFoundError("docker not found")
            raise AssertionError(f"unexpected command {cmd}")

        with mock.patch.object(collector.subprocess, "run", side_effect=fake_run):
            result = collector.collect_docker(config, errors)

        container = result["projects"][0]["containers"][0]
        self.assertEqual(container["log_tail"], [])
        self.assertEqual(len(errors), 1)
        # container/project info in the container itself is still present
        # (a log fetch failure must not drop the container from the snapshot)
        self.assertEqual(container["state"], "running")

    def test_log_tail_lines_zero_skips_docker_logs_call(self):
        config = {"compose_projects": {"prod": "real_project"}, "log_tail_lines": 0}
        errors: list = []

        fake_container = json.dumps(
            {
                "ID": "abc123def456",
                "Names": "real_project-web-1",
                "State": "running",
                "Status": "Up 2 hours (healthy)",
                "Image": "myimage:latest",
                "Labels": "com.docker.compose.project=real_project,com.docker.compose.service=web",
            }
        )

        def fake_run(cmd, **kwargs):
            if cmd[:2] == ["docker", "ps"]:
                return mock.Mock(stdout=fake_container + "\n", returncode=0)
            raise AssertionError(f"unexpected docker call: {cmd}")

        with mock.patch.object(collector.subprocess, "run", side_effect=fake_run):
            result = collector.collect_docker(config, errors)

        container = result["projects"][0]["containers"][0]
        self.assertEqual(container["log_tail"], [])
        self.assertEqual(errors, [])


class TestCiCollection(unittest.TestCase):
    def _config(self, workflows=None):
        return {
            "github": {
                "owner": "some-owner",
                "repo": "some-repo",
                "workflows": workflows or {"ci": "ci.yml", "docker-build": "docker-build.yml"},
            }
        }

    def test_missing_token_degrades_to_empty_lists_and_error(self):
        config = self._config()
        errors: list = []
        original = os.environ.pop("OPS_CONSOLE_GH_PAT", None)
        try:
            result = collector.collect_ci(config, errors)
        finally:
            if original is not None:
                os.environ["OPS_CONSOLE_GH_PAT"] = original

        self.assertEqual(result, {"runners": [], "recent_runs": []})
        self.assertTrue(any("token not configured" in e for e in errors))

    def test_no_github_config_returns_empty_lists_without_error(self):
        errors: list = []
        result = collector.collect_ci({}, errors)
        self.assertEqual(result, {"runners": [], "recent_runs": []})
        self.assertEqual(errors, [])

    def test_runner_shape_excludes_real_name_and_labels(self):
        fake_runners_response = {
            "runners": [
                {
                    "id": 42,
                    "name": "real-hostname-runner-example",
                    "os": "linux",
                    "status": "online",
                    "busy": False,
                    "labels": [
                        {"id": 1, "name": "self-hosted", "type": "read-only"},
                        {"id": 2, "name": "X64", "type": "read-only"},
                        {"id": 3, "name": "Linux", "type": "read-only"},
                        {"id": 4, "name": "secret-custom-label-example-host", "type": "custom"},
                    ],
                }
            ]
        }

        def fake_get(url, token):
            if url.endswith("/actions/runners"):
                return fake_runners_response
            return {"workflow_runs": []}

        config = self._config()
        errors: list = []
        with mock.patch.object(collector, "_github_get", side_effect=fake_get), mock.patch.dict(
            os.environ, {"OPS_CONSOLE_GH_PAT": "fake-token"}
        ):
            result = collector.collect_ci(config, errors)

        self.assertEqual(len(result["runners"]), 1)
        runner = result["runners"][0]
        self.assertEqual(set(runner.keys()), {"id", "status", "busy", "os", "architecture"})
        self.assertEqual(runner["id"], "42")
        self.assertEqual(runner["status"], "online")
        self.assertEqual(runner["busy"], False)
        self.assertEqual(runner["os"], "linux")
        self.assertEqual(runner["architecture"], "X64")

        serialized = json.dumps(result)
        self.assertNotIn("real-hostname-runner-example", serialized)
        self.assertNotIn("secret-custom-label-example-host", serialized)
        self.assertNotIn("labels", serialized)
        self.assertNotIn('"name"', serialized)

    def test_recent_runs_populate_failed_jobs_only_for_failure_conclusion(self):
        workflow_runs_response = {
            "workflow_runs": [
                {
                    "id": 1001,
                    "status": "completed",
                    "conclusion": "failure",
                    "head_branch": "main",
                    "run_started_at": "2026-07-18T10:00:00Z",
                    "updated_at": "2026-07-18T10:05:00Z",
                    "html_url": "https://github.com/some-owner/some-repo/actions/runs/1001",
                },
                {
                    "id": 1000,
                    "status": "completed",
                    "conclusion": "success",
                    "head_branch": "main",
                    "run_started_at": "2026-07-18T09:00:00Z",
                    "updated_at": "2026-07-18T09:05:00Z",
                    "html_url": "https://github.com/some-owner/some-repo/actions/runs/1000",
                },
            ]
        }
        jobs_response = {
            "jobs": [
                {
                    "name": "build",
                    "conclusion": "failure",
                    "steps": [
                        {"name": "Checkout", "number": 1, "conclusion": "success"},
                        {"name": "Run tests", "number": 3, "conclusion": "failure"},
                    ],
                },
                {"name": "lint", "conclusion": "success", "steps": []},
            ]
        }

        def fake_get(url, token):
            if url.endswith("/actions/runners"):
                return {"runners": []}
            if "/actions/runs/1001/jobs" in url:
                return jobs_response
            if "/runs?per_page=" in url:
                return workflow_runs_response
            raise AssertionError(f"unexpected url {url}")

        config = self._config(workflows={"ci": "ci.yml"})
        errors: list = []
        with mock.patch.object(collector, "_github_get", side_effect=fake_get), mock.patch.dict(
            os.environ, {"OPS_CONSOLE_GH_PAT": "fake-token"}
        ):
            result = collector.collect_ci(config, errors)

        self.assertEqual(len(result["recent_runs"]), 2)

        failed_run = next(r for r in result["recent_runs"] if r["run_id"] == 1001)
        self.assertEqual(failed_run["workflow"], "ci")
        self.assertEqual(failed_run["branch"], "main")
        self.assertEqual(failed_run["conclusion"], "failure")
        self.assertEqual(len(failed_run["failed_jobs"]), 1)
        job = failed_run["failed_jobs"][0]
        self.assertEqual(job["job_name"], "build")
        self.assertEqual(job["conclusion"], "failure")
        self.assertEqual(len(job["failed_steps"]), 1)
        self.assertEqual(job["failed_steps"][0]["step_name"], "Run tests")
        self.assertEqual(job["failed_steps"][0]["number"], 3)
        self.assertEqual(job["failed_steps"][0]["conclusion"], "failure")

        success_run = next(r for r in result["recent_runs"] if r["run_id"] == 1000)
        self.assertEqual(success_run["failed_jobs"], [])
        self.assertEqual(errors, [])

    def test_failed_jobs_capped_at_three_most_recent_failures_per_workflow(self):
        runs = []
        for i in range(5):
            runs.append(
                {
                    "id": 2000 + i,
                    "status": "completed",
                    "conclusion": "failure",
                    "head_branch": "main",
                    "run_started_at": f"2026-07-18T{10 + i:02d}:00:00Z",
                    "updated_at": f"2026-07-18T{10 + i:02d}:05:00Z",
                    "html_url": f"https://github.com/some-owner/some-repo/actions/runs/{2000 + i}",
                }
            )

        jobs_calls: list = []

        def fake_get(url, token):
            if url.endswith("/actions/runners"):
                return {"runners": []}
            if "/jobs" in url:
                jobs_calls.append(url)
                return {"jobs": []}
            return {"workflow_runs": runs}

        config = self._config(workflows={"ci": "ci.yml"})
        errors: list = []
        with mock.patch.object(collector, "_github_get", side_effect=fake_get), mock.patch.dict(
            os.environ, {"OPS_CONSOLE_GH_PAT": "fake-token"}
        ):
            collector.collect_ci(config, errors)

        self.assertEqual(len(jobs_calls), 3)
        top_three = {2004, 2003, 2002}
        oldest_two = {2001, 2000}
        for call_url in jobs_calls:
            self.assertTrue(any(f"/runs/{i}/jobs" in call_url for i in top_three))
            self.assertFalse(any(f"/runs/{i}/jobs" in call_url for i in oldest_two))

    def test_jobs_api_failure_is_isolated_and_recorded_in_errors(self):
        workflow_runs_response = {
            "workflow_runs": [
                {
                    "id": 1,
                    "status": "completed",
                    "conclusion": "failure",
                    "head_branch": "main",
                    "run_started_at": "2026-07-18T10:00:00Z",
                    "updated_at": "2026-07-18T10:00:00Z",
                    "html_url": "https://github.com/some-owner/some-repo/actions/runs/1",
                }
            ]
        }

        def fake_get(url, token):
            if url.endswith("/actions/runners"):
                return {"runners": []}
            if "/jobs" in url:
                raise urllib.error.URLError("boom")
            return workflow_runs_response

        config = self._config(workflows={"ci": "ci.yml"})
        errors: list = []
        with mock.patch.object(collector, "_github_get", side_effect=fake_get), mock.patch.dict(
            os.environ, {"OPS_CONSOLE_GH_PAT": "fake-token"}
        ):
            result = collector.collect_ci(config, errors)

        self.assertEqual(result["recent_runs"][0]["failed_jobs"], [])
        self.assertTrue(any("github jobs" in e for e in errors))

    def test_runners_api_failure_is_isolated_from_recent_runs(self):
        def fake_get(url, token):
            if url.endswith("/actions/runners"):
                raise urllib.error.URLError("boom")
            return {"workflow_runs": []}

        config = self._config(workflows={"ci": "ci.yml"})
        errors: list = []
        with mock.patch.object(collector, "_github_get", side_effect=fake_get), mock.patch.dict(
            os.environ, {"OPS_CONSOLE_GH_PAT": "fake-token"}
        ):
            result = collector.collect_ci(config, errors)

        self.assertEqual(result["runners"], [])
        self.assertEqual(result["recent_runs"], [])
        self.assertTrue(any("github runners" in e for e in errors))

    def test_runner_repo_used_for_runners_call_workflow_runs_use_repo(self):
        calls: list = []

        def fake_get(url, token):
            calls.append(url)
            if "/actions/runners" in url:
                return {"runners": []}
            return {"workflow_runs": []}

        config = self._config(workflows={"ci": "ci.yml"})
        config["github"]["runner_repo"] = "some-runner-repo"
        errors: list = []
        with mock.patch.object(collector, "_github_get", side_effect=fake_get), mock.patch.dict(
            os.environ, {"OPS_CONSOLE_GH_PAT": "fake-token"}
        ):
            collector.collect_ci(config, errors)

        runners_calls = [u for u in calls if "/actions/runners" in u]
        runs_calls = [u for u in calls if "/runs?per_page=" in u]
        self.assertEqual(len(runners_calls), 1)
        self.assertIn(
            "/repos/some-owner/some-runner-repo/actions/runners", runners_calls[0]
        )
        self.assertEqual(len(runs_calls), 1)
        self.assertIn(
            "/repos/some-owner/some-repo/actions/workflows/ci.yml/runs", runs_calls[0]
        )
        self.assertEqual(errors, [])

    def test_runner_repo_absent_falls_back_to_repo(self):
        calls: list = []

        def fake_get(url, token):
            calls.append(url)
            if "/actions/runners" in url:
                return {"runners": []}
            return {"workflow_runs": []}

        config = self._config(workflows={"ci": "ci.yml"})
        # no "runner_repo" key set: current/fallback behavior must be unchanged.
        errors: list = []
        with mock.patch.object(collector, "_github_get", side_effect=fake_get), mock.patch.dict(
            os.environ, {"OPS_CONSOLE_GH_PAT": "fake-token"}
        ):
            collector.collect_ci(config, errors)

        runners_calls = [u for u in calls if "/actions/runners" in u]
        self.assertEqual(len(runners_calls), 1)
        self.assertIn("/repos/some-owner/some-repo/actions/runners", runners_calls[0])
        self.assertEqual(errors, [])

    def test_runner_repo_error_is_labeled_with_runner_repo_not_repo(self):
        def fake_get(url, token):
            if "/actions/runners" in url:
                raise urllib.error.URLError("boom")
            return {"workflow_runs": []}

        config = self._config(workflows={"ci": "ci.yml"})
        config["github"]["runner_repo"] = "some-runner-repo"
        errors: list = []
        with mock.patch.object(collector, "_github_get", side_effect=fake_get), mock.patch.dict(
            os.environ, {"OPS_CONSOLE_GH_PAT": "fake-token"}
        ):
            result = collector.collect_ci(config, errors)

        self.assertEqual(result["runners"], [])
        self.assertTrue(any("github runners (some-runner-repo)" in e for e in errors))
        self.assertFalse(any("github runners (some-repo)" in e for e in errors))


class TestSnapshotServer(unittest.TestCase):
    def test_rejects_wildcard_bind_address(self):
        with self.assertRaises(ValueError):
            snapshot_server.validate_bind_address("0.0.0.0")

    def test_rejects_empty_bind_address(self):
        with self.assertRaises(ValueError):
            snapshot_server.validate_bind_address("")

    def test_accepts_specific_bind_address(self):
        # Should not raise.
        snapshot_server.validate_bind_address("100.64.0.1")

    def test_server_main_exits_nonzero_on_wildcard(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = os.path.join(tmp, "config.json")
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(
                    {"bind_address": "0.0.0.0", "bind_port": 8787, "output_path": "x.json"},
                    f,
                )
            rc = snapshot_server.main(["snapshot_server.py", config_path])
            self.assertNotEqual(rc, 0)

    def _run_live_server(self, tmp, token=None):
        snapshot_path = os.path.join(tmp, "snapshot.json")
        with open(snapshot_path, "w", encoding="utf-8") as f:
            json.dump({"ok": True}, f)

        handler_cls = snapshot_server.make_handler(snapshot_path, token)
        httpd = snapshot_server.ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        import threading

        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        return httpd, thread

    def test_get_snapshot_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            httpd, thread = self._run_live_server(tmp)
            try:
                conn = HTTPConnection(*httpd.server_address)
                conn.request("GET", "/snapshot.json")
                resp = conn.getresponse()
                self.assertEqual(resp.status, 200)
                body = json.loads(resp.read())
                self.assertEqual(body, {"ok": True})
            finally:
                httpd.shutdown()
                thread.join(timeout=5)

    def test_unknown_path_is_404(self):
        with tempfile.TemporaryDirectory() as tmp:
            httpd, thread = self._run_live_server(tmp)
            try:
                conn = HTTPConnection(*httpd.server_address)
                conn.request("GET", "/other")
                resp = conn.getresponse()
                self.assertEqual(resp.status, 404)
            finally:
                httpd.shutdown()
                thread.join(timeout=5)

    def test_post_is_405(self):
        with tempfile.TemporaryDirectory() as tmp:
            httpd, thread = self._run_live_server(tmp)
            try:
                conn = HTTPConnection(*httpd.server_address)
                conn.request("POST", "/snapshot.json")
                resp = conn.getresponse()
                self.assertEqual(resp.status, 405)
            finally:
                httpd.shutdown()
                thread.join(timeout=5)

    def test_401_without_token_when_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            httpd, thread = self._run_live_server(tmp, token="secret-token")
            try:
                conn = HTTPConnection(*httpd.server_address)
                conn.request("GET", "/snapshot.json")
                resp = conn.getresponse()
                self.assertEqual(resp.status, 401)
            finally:
                httpd.shutdown()
                thread.join(timeout=5)

    def test_200_with_correct_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            httpd, thread = self._run_live_server(tmp, token="secret-token")
            try:
                conn = HTTPConnection(*httpd.server_address)
                conn.request(
                    "GET", "/snapshot.json", headers={"X-Snapshot-Token": "secret-token"}
                )
                resp = conn.getresponse()
                self.assertEqual(resp.status, 200)
            finally:
                httpd.shutdown()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
