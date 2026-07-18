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
            collector, "collect_ci", return_value={"runner_status": "unknown", "recent_runs": []}
        ):
            snapshot = collector.build_snapshot(config)

        self.assertEqual(snapshot["stale_after_seconds"], 120)
        self.assertIn("generated_at", snapshot)
        self.assertEqual(snapshot["errors"], [])
        self.assertIn("docker", snapshot)
        self.assertIn("backup", snapshot)
        self.assertIn("ops_scripts", snapshot)
        self.assertIn("ci", snapshot)

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
