"""Regression tests: a blank OpenWrt project.env must honor YAML LAN listeners.

No network sockets are opened and no X-Tunnel processes are started.
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import project_settings
import supervisor


class LanListenerConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = self.root / "x-tunnel.yaml"
        self.env = self.root / "project.env"
        self.config.write_text(
            """defaults:
  token: test-token
  cfip: 1.1.1.1
  connections: 1
  listen_hosts:
    - "127.0.0.1"
    - "192.168.68.11"
  first_port: 32322
nodes:
  - "hk|wss://example.com/about:443||||"
""", encoding="utf-8"
        )

    def resolve(self, env_content):
        self.env.write_text(env_content, encoding="utf-8")
        with mock.patch.object(project_settings, "PROJECT_ENV_FILE", self.env), \
             mock.patch.object(supervisor, "CONFIG", self.config), \
             mock.patch.dict(os.environ, {}, clear=True):
            return supervisor.read_specs(check_port_availability=False)

    def test_default_env_blank_uses_yaml_lan_listener(self):
        specs = self.resolve("X_TUNNEL_LISTEN_HOSTS=\nX_TUNNEL_PROCESS_UID=29002\n")
        self.assertEqual(specs[0]["listen_hosts"], ["127.0.0.1", "192.168.68.11"])
        self.assertEqual(specs[0]["port"], 32322)
        command = supervisor.command_for(specs[0])
        self.assertIn(
            "socks5://127.0.0.1:32322,socks5://192.168.68.11:32322",
            command,
        )

    def test_missing_env_uses_yaml(self):
        specs = self.resolve("X_TUNNEL_PROCESS_UID=29002\n")
        self.assertEqual(specs[0]["listen_hosts"], ["127.0.0.1", "192.168.68.11"])

    def test_explicit_env_overrides_yaml(self):
        specs = self.resolve("X_TUNNEL_LISTEN_HOSTS=192.168.68.21,127.0.0.1\n")
        self.assertEqual(specs[0]["listen_hosts"], ["192.168.68.21", "127.0.0.1"])

    def test_default_remains_local_only_without_any_yaml_listeners(self):
        source = self.config.read_text(encoding="utf-8")
        self.config.write_text(
            source.replace(
                '  listen_hosts:\n    - "127.0.0.1"\n    - "192.168.68.11"\n',
                "",
            ),
            encoding="utf-8",
        )
        specs = self.resolve("X_TUNNEL_LISTEN_HOSTS=\n")
        self.assertEqual(specs[0]["listen_hosts"], ["127.0.0.1"])

    def test_invalid_nonempty_env_does_not_silently_fallback(self):
        with self.assertRaisesRegex(ValueError, "did not contain any IP addresses"):
            self.resolve("X_TUNNEL_LISTEN_HOSTS=,,,\n")


if __name__ == "__main__":
    unittest.main()
