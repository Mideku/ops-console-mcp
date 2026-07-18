#!/usr/bin/env python3
"""Minimal read-only HTTP file server for the collector's snapshot.json.

Serves exactly one resource, GET /snapshot.json, and nothing else: no
directory listing, no other paths, no other HTTP methods. Intended to be
bound only to a Tailscale IP (see 01-architettura.md ADR-002 and
03-sicurezza-threat-model.md section 3.7) — it refuses to start if asked
to bind to 0.0.0.0 or an empty address, since that would turn the one
network-reachable component of this project into a public listener.

Python 3.12, stdlib only (http.server).

Usage:
    python snapshot_server.py <path-to-config.json>

Config keys used (same config file as collector.py):
    bind_address  - required, must not be "0.0.0.0" or empty
    bind_port     - required, int
    output_path   - required, path to the snapshot.json file to serve

Optional env var:
    OPS_CONSOLE_SNAPSHOT_TOKEN - if set, every request must carry a
        matching "X-Snapshot-Token" header, otherwise 401.
"""

from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SNAPSHOT_ROUTE = "/snapshot.json"
FORBIDDEN_BIND_ADDRESSES = {"0.0.0.0", "", "::"}


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def validate_bind_address(bind_address: str) -> None:
    if not bind_address or bind_address in FORBIDDEN_BIND_ADDRESSES:
        raise ValueError(
            "refusing to start: bind_address must be a specific (e.g. Tailscale) "
            "address, not empty or a wildcard like 0.0.0.0/::"
        )


def make_handler(snapshot_path: str, expected_token: str | None):
    class SnapshotHandler(BaseHTTPRequestHandler):
        # Silence default logging of request lines to avoid leaking data
        # (paths/headers) into process logs.
        def log_message(self, format: str, *args) -> None:  # noqa: A002
            return

        def _check_token(self) -> bool:
            if not expected_token:
                return True
            provided = self.headers.get("X-Snapshot-Token")
            return provided == expected_token

        def _send_json_file(self) -> None:
            try:
                with open(snapshot_path, "rb") as f:
                    body = f.read()
            except OSError:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path != SNAPSHOT_ROUTE:
                self.send_response(404)
                self.end_headers()
                return
            if not self._check_token():
                self.send_response(401)
                self.end_headers()
                return
            self._send_json_file()

        def do_HEAD(self) -> None:  # noqa: N802
            self.send_response(405)
            self.end_headers()

        def do_POST(self) -> None:  # noqa: N802
            self.send_response(405)
            self.end_headers()

        def do_PUT(self) -> None:  # noqa: N802
            self.send_response(405)
            self.end_headers()

        def do_DELETE(self) -> None:  # noqa: N802
            self.send_response(405)
            self.end_headers()

    return SnapshotHandler


def main(argv: list) -> int:
    if len(argv) < 2:
        print("usage: snapshot_server.py <config.json>", file=sys.stderr)
        return 2

    config = load_config(argv[1])
    bind_address = config.get("bind_address", "")

    try:
        validate_bind_address(bind_address)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    bind_port = config.get("bind_port")
    output_path = config.get("output_path")
    if not bind_port or not output_path:
        print("config error: 'bind_port' and 'output_path' are required", file=sys.stderr)
        return 2

    expected_token = os.environ.get("OPS_CONSOLE_SNAPSHOT_TOKEN")
    handler_cls = make_handler(output_path, expected_token)

    server = ThreadingHTTPServer((bind_address, int(bind_port)), handler_cls)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
