"""LXC lifecycle regression checks; never start system services."""
import unittest
from pathlib import Path
from unittest import mock

import bot_ready

ROOT = Path(__file__).resolve().parents[1]

class LifecycleGuardTests(unittest.TestCase):
    def test_bot_missing_credentials_skips_start(self):
        with mock.patch.object(bot_ready, "read_telegram_credentials", side_effect=ValueError("missing")):
            self.assertEqual(bot_ready.main(), 10)

    def test_bot_present_credentials_allows_start(self):
        with mock.patch.object(bot_ready, "read_telegram_credentials", return_value=("fake-token", "123456789")):
            self.assertEqual(bot_ready.main(), 0)

    def test_unit_readiness_guards(self):
        for name, script in [
            ("x-tunnel.service", "config_ready.py"),
            ("x-tunnel-speedtest.service", "config_ready.py"),
            ("x-tunnel-speedtest-single@.service", "config_ready.py"),
            ("x-tunnel-speedtest-bot.service", "bot_ready.py"),
        ]:
            with self.subTest(unit=name):
                content = (ROOT / "systemd" / name).read_text(encoding="utf-8")
                self.assertIn("ExecCondition=/usr/bin/python3 @PROJECT_DIR@/" + script, content)

    def test_install_and_menu_keep_optional_dependencies_separate(self):
        install = (ROOT / "install.sh").read_text(encoding="utf-8")
        menu = (ROOT / "menu.sh").read_text(encoding="utf-8")
        self.assertIn('if [ "$CORE_READY" -eq 1 ]; then', install)
        self.assertIn('if [ "$WITH_BOT" -eq 1 ]; then', install)
        self.assertIn('systemctl disable --now x-tunnel.service', install)
        self.assertIn("Telegram Bot 连接诊断", menu)
        self.assertIn("单 IP 测速日志", menu)
        self.assertIn('if ! python3 "$PROJECT_DIR/config_ready.py"; then', menu)
        self.assertIn("systemctl start --no-block x-tunnel-speedtest.service", menu)

if __name__ == "__main__":
    unittest.main()
