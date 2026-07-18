"""CGNAT (100.64.0.0/10) addresses — e.g. Tailscale node IPs — must be redacted."""

import unittest

import redactor


class CgnatRedactionTests(unittest.TestCase):
    def test_cgnat_addresses_are_redacted(self):
        for ip in ("100.64.0.1", "100.100.42.7", "100.127.255.254"):
            out = redactor.redact(f"fetched http://{ip}:8787/snapshot.json")
            self.assertNotIn(ip, out)
            self.assertIn("[REDACTED:internal_host]", out)

    def test_public_neighbors_of_cgnat_range_are_not_redacted(self):
        for ip in ("100.63.255.255", "100.128.0.1"):
            out = redactor.redact(f"resolved {ip}")
            self.assertIn(ip, out)


if __name__ == "__main__":
    unittest.main()
