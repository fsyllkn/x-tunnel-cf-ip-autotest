"""CF IP write policy regressions (YAML only; no network or procd needed)."""
import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml
import cfip_speedtest
import project_settings


EXAMPLE = """# Keep human comments and all node token/port fields intact.
defaults:
  token: "local-secret"
  cfip: "198.51.100.1,198.51.100.2"
  first_port: 32322
max_nodes: 5
nodes:
  - "hk|wss://example.org/about:443||198.51.100.3,198.51.100.4,198.51.100.5||32322" # HK
  - "sg|wss://example.net/about:443||||32323" # SG
settings:
  target_mbps: 300
telegram:
  token: "example-token"
  chat_id: "12345"
"""


class CFIPApplyPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = Path(self.tmp.name) / "x-tunnel.yaml"
        self.cfg.write_text(EXAMPLE, encoding="utf-8")
        os.chmod(self.cfg, 0o600)
        for mod in (project_settings, cfip_speedtest):
            patch = mock.patch.object(mod, "CONFIG_FILE", self.cfg)
            patch.start()
            self.addCleanup(patch.stop)

    def rows(self):
        document = yaml.safe_load(self.cfg.read_text(encoding="utf-8"))
        rows = {row.split("|", 1)[0]: row.split("|") for row in document["nodes"]}
        return document, rows

    def apply(self, assignments):
        with contextlib.redirect_stdout(io.StringIO()):
            return cfip_speedtest.apply_ips(assignments)

    def test_two_measured_replace_every_old_and_default_ip(self):
        self.assertTrue(self.apply({"hk": "203.0.113.10,203.0.113.11"}))
        document, rows = self.rows()
        self.assertEqual(rows["hk"][3], "203.0.113.10,203.0.113.11")
        self.assertEqual(rows["hk"][2], "")
        self.assertEqual(rows["hk"][5], "32322")
        self.assertEqual(rows["sg"][3], "")
        self.assertEqual(document["defaults"]["cfip"], "198.51.100.1,198.51.100.2")
        self.assertEqual(document["telegram"]["token"], "example-token")
        self.assertIn("# HK", self.cfg.read_text(encoding="utf-8"))

    def test_one_measured_uses_first_old_ip_as_fallback(self):
        self.assertTrue(self.apply({"hk": "203.0.113.12"}))
        self.assertEqual(self.rows()[1]["hk"][3], "203.0.113.12,198.51.100.3")

    def test_one_measured_already_in_old_list_skips_duplicate(self):
        self.assertTrue(self.apply({"hk": "198.51.100.3"}))
        self.assertEqual(self.rows()[1]["hk"][3], "198.51.100.3,198.51.100.4")

    def test_no_old_node_uses_default_only_to_fill_one_slot(self):
        self.assertTrue(self.apply({"sg": "203.0.113.20"}))
        doc, rows = self.rows()
        self.assertEqual(rows["sg"][3], "203.0.113.20,198.51.100.1")
        self.assertEqual(rows["hk"][3], "198.51.100.3,198.51.100.4,198.51.100.5")
        self.assertEqual(doc["defaults"]["cfip"], "198.51.100.1,198.51.100.2")

    def test_only_one_ip_is_valid_if_no_fallback_exists(self):
        self.cfg.write_text(EXAMPLE.replace(
            'cfip: "198.51.100.1,198.51.100.2"', 'cfip: ""'
        ), encoding="utf-8")
        self.assertTrue(self.apply({"sg": "203.0.113.21"}))
        self.assertEqual(self.rows()[1]["sg"][3], "203.0.113.21")

    def test_three_or_more_measured_truncated_to_two(self):
        self.assertTrue(self.apply({
            "hk": ["203.0.113.1,203.0.113.2", "203.0.113.3"]
        }))
        self.assertEqual(self.rows()[1]["hk"][3], "203.0.113.1,203.0.113.2")

    def test_duplicate_selected_ips_split_and_deduplicated(self):
        self.assertTrue(self.apply({
            "hk": ["203.0.113.1,203.0.113.1", "203.0.113.2", "203.0.113.1"]
        }))
        self.assertEqual(self.rows()[1]["hk"][3], "203.0.113.1,203.0.113.2")

    def test_many_successive_rounds_do_not_accumulate_historical_ips(self):
        for index in range(15):
            self.assertTrue(self.apply({
                "hk": "203.0.113.%d,203.0.113.%d" % (index * 2 + 1, index * 2 + 2)
            }))
            self.assertEqual(len(self.rows()[1]["hk"][3].split(",")), 2)
        self.assertEqual(self.rows()[1]["hk"][3], "203.0.113.29,203.0.113.30")

    def test_zero_measured_preserves_original_file_byte_for_byte(self):
        initial = self.cfg.read_bytes()
        inode = self.cfg.stat().st_ino
        self.assertFalse(self.apply({"hk": "", "sg": []}))
        self.assertEqual(self.cfg.read_bytes(), initial)
        self.assertEqual(self.cfg.stat().st_ino, inode)
        self.assertFalse(self.apply({}))
        self.assertEqual(self.cfg.read_bytes(), initial)

    def test_empty_node_does_not_change_when_other_node_has_a_new_ip(self):
        self.assertTrue(self.apply({"hk": "203.0.113.40", "sg": ""}))
        self.assertEqual(self.rows()[1]["sg"][3], "")

    def test_same_result_does_not_rewrite_and_restart_supervisor(self):
        self.assertTrue(self.apply({"hk": "203.0.113.51,203.0.113.52"}))
        inode = self.cfg.stat().st_ino
        original = self.cfg.read_bytes()
        self.assertFalse(self.apply({"hk": "203.0.113.51,203.0.113.52"}))
        self.assertEqual(self.cfg.stat().st_ino, inode)
        self.assertEqual(self.cfg.read_bytes(), original)
        self.assertEqual(self.cfg.stat().st_mode & 0o777, 0o600)

    def test_unknown_node_is_error_without_partial_mutation(self):
        before = self.cfg.read_bytes()
        with self.assertRaisesRegex(ValueError, "node.s. not found"):
            self.apply({"hk": "203.0.113.33", "unknown": "203.0.113.44"})
        self.assertEqual(self.cfg.read_bytes(), before)

    def test_no_partial_write_if_target_row_is_not_double_quoted(self):
        self.cfg.write_text(EXAMPLE.replace(
            '  - "sg|wss://example.net/about:443||||32323"',
            "  - 'sg|wss://example.net/about:443||||32323'"
        ), encoding="utf-8")
        before = self.cfg.read_bytes()
        with self.assertRaisesRegex(ValueError, "could not locate double-quoted"):
            self.apply({"hk": "203.0.113.35", "sg": "203.0.113.36"})
        self.assertEqual(self.cfg.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
