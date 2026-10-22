#!/usr/bin/env python3
"""No X-Tunnel daemon/network access required: python3 -m unittest discover -s tests"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    import yaml
except ImportError as exc:
    raise RuntimeError("Install PyYAML to run these tests") from exc

ROOT = Path(__file__).resolve().parents[1]


def load_exporter():
    # The exporter imports the supervisor for automatic port selection.
    # Stub that import so these tests cannot start children or load credentials.
    fake_supervisor = types.ModuleType("supervisor")
    fake_supervisor.read_specs = lambda **kwargs: []
    fake_settings = types.ModuleType("project_settings")
    fake_settings.load_project_env = lambda: None
    with patch.dict(sys.modules, {
        "supervisor": fake_supervisor,
        "project_settings": fake_settings,
    }):
        spec = importlib.util.spec_from_file_location("xt_export_under_test", ROOT / "sync_singbox.py")
        obj = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(obj)
    return obj


class MultiExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_exporter()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.provider = Path(self.temp.name) / "x-tunnel-provider.json"
        self.outbounds = [
            {
                "type": "socks",
                "tag": "🇭🇰HKG_x-tunnel",
                "server": "127.0.0.1",
                "server_port": 32322,
            },
            {
                "type": "socks",
                "tag": "🇯🇵东京 / test",
                "server": "127.0.0.1",
                "server_port": 32323,
            },
        ]

    def test_format_and_idempotency(self):
        export = self.module.export_companion_formats
        self.assertTrue(export(self.outbounds, self.provider))
        folder = self.provider.parent
        clash = yaml.safe_load((folder / "x-tunnel-clash.yaml").read_text(encoding="utf-8"))
        self.assertEqual(len(clash["proxies"]), 2)
        self.assertEqual(clash["proxies"][0]["type"], "socks5")
        self.assertEqual(clash["proxies"][0]["name"], self.outbounds[0]["tag"])
        self.assertNotIn("rules", clash)
        v2ray = json.loads((folder / "x-tunnel-v2ray.json").read_text(encoding="utf-8"))
        self.assertEqual(v2ray["outbounds"][1]["protocol"], "socks")
        self.assertEqual(v2ray["outbounds"][1]["settings"]["servers"][0]["port"], 32323)
        links = (folder / "x-tunnel-socks5.txt").read_text(encoding="utf-8")
        self.assertIn("socks5://127.0.0.1:32322", links)
        self.assertIn("%2F", links)
        self.assertNotIn("ss://", links)
        self.assertFalse(export(self.outbounds, self.provider))

    def test_manual_export_never_rewrites_provider(self):
        self.provider.write_text(json.dumps({"outbounds": self.outbounds}), encoding="utf-8")
        original = self.provider.read_bytes()
        with patch.object(self.module, "provider_path", return_value=self.provider), \
             patch.object(self.module.sys, "argv", ["sync_singbox.py", "--export-only"]):
            self.module.main()
        self.assertEqual(self.provider.read_bytes(), original)
        self.assertTrue((self.provider.parent / "x-tunnel-clash.yaml").exists())

    def test_reject_invalid_or_shadow_socks(self):
        for broken in [
            [{"type": "shadowsocks", "tag": "fake", "server": "127.0.0.1", "server_port": 32322}],
            [self.outbounds[0], self.outbounds[0]],
            [{**self.outbounds[0], "server_port": True}],
            [{**self.outbounds[0], "server_port": 70000}],
        ]:
            with self.subTest(value=broken):
                with self.assertRaises(ValueError):
                    self.module.export_companion_formats(broken, self.provider)
        self.assertFalse((self.provider.parent / "x-tunnel-clash.yaml").exists())

    def test_ipv6_uri_brackets(self):
        outbound = [{**self.outbounds[0], "server": "::1"}]
        self.module.export_companion_formats(outbound, self.provider)
        links = (self.provider.parent / "x-tunnel-socks5.txt").read_text(encoding="utf-8")
        self.assertIn("socks5://[::1]:32322#", links)


if __name__ == "__main__":
    unittest.main()
