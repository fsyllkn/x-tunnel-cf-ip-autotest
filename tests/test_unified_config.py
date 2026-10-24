"""Unified YAML migration and writer tests; no network, procd or real credentials."""
import contextlib
import io
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cfip_speedtest
import migrate_config
import project_settings
import telegram_speedtest_bot
import yaml


OLD_YAML = """# preserve this comment and all node row quoting
defaults:
  token: test-tunnel-secret
  cfip: "162.159.1.1"
  listen_hosts:
    - "127.0.0.1"
nodes:
  - "hk|wss://example.org/about:443||||"
"""


class UnifiedConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = self.root / "x-tunnel.yaml"
        self.project = self.root / "project.env"
        self.bot = self.root / "speedtest-bot.env"
        self.config.write_text(OLD_YAML, encoding="utf-8")
        os.chmod(self.config, 0o600)

        for target, value in [
            (project_settings, "CONFIG_FILE"),
            (migrate_config, "CONFIG_FILE"),
            (cfip_speedtest, "CONFIG_FILE"),
        ]:
            patcher = mock.patch.object(target, value, self.config)
            patcher.start()
            self.addCleanup(patcher.stop)
        for target, name, value in [
            (migrate_config, "BASE_DIR", self.root),
            (migrate_config, "LEGACY_PROJECT", self.project),
            (migrate_config, "LEGACY_BOT", self.bot),
        ]:
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def write_legacy(self):
        self.project.write_text(
            "X_TUNNEL_LISTEN_HOSTS=127.0.0.1,192.168.68.11\n"
            "X_TUNNEL_TARGET_MBPS=450\n"
            "X_TUNNEL_PROCESS_UID=29002\n"
            "X_TUNNEL_PROVIDER_PATH=runtime/custom-provider.json\n"
            "X_TUNNEL_TG_PROXY=http://192.168.68.11:7890\n",
            encoding="utf-8",
        )
        self.bot.write_text(
            "TG_TOKEN='123456:private-token-test'\n"
            "TG_CHATID='123456789'\n",
            encoding="utf-8",
        )
        os.chmod(self.project, 0o644)
        os.chmod(self.bot, 0o600)

    def test_migrate_three_files_without_dropping_secrets_or_node_format(self):
        self.write_legacy()
        log = io.StringIO()
        with contextlib.redirect_stdout(log):
            self.assertTrue(migrate_config.migrate())
        self.assertNotIn("private-token-test", log.getvalue())
        self.assertFalse(self.project.exists())
        self.assertFalse(self.bot.exists())
        self.assertEqual(stat.S_IMODE(self.config.stat().st_mode), 0o600)
        updated = self.config.read_text(encoding="utf-8")
        self.assertIn('# preserve this comment', updated)
        self.assertIn('  - "hk|wss://example.org/about:443||||"', updated)
        doc = yaml.safe_load(updated)
        self.assertEqual(doc["defaults"]["listen_hosts"], ["127.0.0.1", "192.168.68.11"])
        self.assertEqual(doc["settings"]["target_mbps"], 450)
        self.assertEqual(doc["settings"]["provider_path"], "runtime/custom-provider.json")
        self.assertEqual(doc["settings"]["process_uid"], 29002)
        self.assertEqual(doc["telegram"]["token"], "123456:private-token-test")
        self.assertEqual(doc["telegram"]["chat_id"], "123456789")
        self.assertEqual(project_settings.get_target_mbps(), 450)
        self.assertEqual(telegram_speedtest_bot.read_config(),
                         ("123456:private-token-test", "123456789"))
        self.assertEqual(project_settings.telegram_proxy_chain()[0],
                         "http://192.168.68.11:7890")
        backups = list((self.root / "cfip-data/config-backups").glob("legacy-env-*"))
        self.assertEqual(len(backups), 1)
        for file_name in ("project.env", "speedtest-bot.env", "x-tunnel.yaml.before-migration"):
            backup = backups[0] / file_name
            self.assertTrue(backup.exists())
            self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(migrate_config.migrate())

    def test_speed_target_and_cf_ip_writer_do_not_overwrite_each_other(self):
        self.write_legacy()
        with contextlib.redirect_stdout(io.StringIO()):
            migrate_config.migrate()
        project_settings.set_target_mbps(500)
        self.assertEqual(project_settings.get_target_mbps(), 500)
        self.assertIn('  - "hk|wss://example.org/about:443||||"',
                      self.config.read_text(encoding="utf-8"))
        cfip_speedtest.apply_ips({"hk": "162.159.2.2,162.159.3.3"})
        doc = yaml.safe_load(self.config.read_text(encoding="utf-8"))
        self.assertEqual(doc["settings"]["target_mbps"], 500)
        self.assertEqual(doc["telegram"]["token"], "123456:private-token-test")
        self.assertIn("162.159.2.2", doc["nodes"][0])
        self.assertIn("162.159.3.3", doc["nodes"][0])

    def test_existing_yaml_keys_and_custom_lan_listener_win_over_old_env(self):
        self.write_legacy()
        self.config.write_text(
            OLD_YAML.replace('"127.0.0.1"\n', '"192.168.68.99"\n')
            + '\nsettings:\n  target_mbps: 600\n',
            encoding="utf-8",
        )
        with contextlib.redirect_stdout(io.StringIO()):
            migrate_config.migrate()
        document = project_settings.read_config()
        self.assertEqual(document["defaults"]["listen_hosts"], ["192.168.68.99"])
        self.assertEqual(document["settings"]["target_mbps"], 600)
        self.assertEqual(document["telegram"]["chat_id"], "123456789")

    def test_malformed_env_aborts_without_removing_originals(self):
        self.write_legacy()
        self.project.write_text("X_TUNNEL_PROCESS_UID='unterminated\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "malformed shell quoting"):
            migrate_config.migrate()
        self.assertTrue(self.bot.exists())
        self.assertTrue(self.project.exists())
        self.assertEqual(self.config.read_text(encoding="utf-8"), OLD_YAML)

    def test_unified_yaml_must_be_private_for_telegram(self):
        self.config.write_text(
            OLD_YAML + '\ntelegram:\n  token: "secret"\n  chat_id: "123"\n',
            encoding="utf-8",
        )
        os.chmod(self.config, 0o644)
        with self.assertRaises(PermissionError):
            telegram_speedtest_bot.read_config()

    def test_invalid_target_does_not_modify_yaml(self):
        original = self.config.read_bytes()
        with self.assertRaises(ValueError):
            project_settings.set_target_mbps(50)
        self.assertEqual(original, self.config.read_bytes())


if __name__ == "__main__":
    unittest.main()
