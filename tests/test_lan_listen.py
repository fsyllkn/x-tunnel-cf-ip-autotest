"""Single-source LAN SOCKS5 listener regressions: only defaults.listen_hosts."""
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
        self.config = Path(self.tmp.name) / "x-tunnel.yaml"
        self.config.write_text(
            """defaults:
  token: test-token
  cfip: 1.1.1.1
  connections: 1
  listen_hosts:
    - "127.0.0.1"
    - "192.168.68.11"
  first_port: 32322
settings:
  process_uid: 29002
nodes:
  - "hk|wss://example.com/about:443||||"
""", encoding="utf-8"
        )

    def resolve(self):
        with mock.patch.object(project_settings, "CONFIG_FILE", self.config), \
             mock.patch.object(supervisor, "CONFIG", self.config), \
             mock.patch.dict(os.environ, {"X_TUNNEL_LISTEN_HOSTS": "192.168.68.21"}):
            return supervisor.read_specs(check_port_availability=False)

    def test_yaml_is_authoritative_even_with_old_env_override(self):
        specs = self.resolve()
        self.assertEqual(specs[0]["listen_hosts"], ["127.0.0.1", "192.168.68.11"])
        self.assertEqual(specs[0]["port"], 32322)
        command = supervisor.command_for(specs[0])
        self.assertIn(
            "socks5://127.0.0.1:32322,socks5://192.168.68.11:32322",
            command,
        )

    def test_missing_yaml_listen_defaults_to_localhost(self):
        source = self.config.read_text(encoding="utf-8")
        self.config.write_text(source.replace(
            '  listen_hosts:\n    - "127.0.0.1"\n    - "192.168.68.11"\n', ""
        ), encoding="utf-8")
        self.assertEqual(self.resolve()[0]["listen_hosts"], ["127.0.0.1"])

    def test_non_numeric_yaml_listener_is_rejected(self):
        source = self.config.read_text(encoding="utf-8")
        self.config.write_text(source.replace("192.168.68.11", "lan.example.org"), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "numeric IP"):
            self.resolve()


if __name__ == "__main__":
    unittest.main()
