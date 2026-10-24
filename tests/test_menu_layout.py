"""Regression checks for the OpenWrt X-Tunnel numeric SSH menu.

Read-only checks; never start services or change network configuration.
"""
import re
import unittest
from pathlib import Path


MENU = (Path(__file__).resolve().parents[1] / "menu.sh").read_text(encoding="utf-8")

EXPECTED_ACTIONS = {
    1: "install_services",
    2: "update_code",
    3: "restart_service core",
    4: "show_logs",
    5: "manage_svc core",
    6: "stop_core_only",
    7: "edit_file x-tunnel.yaml x-tunnel.example.yaml",
    8: "if [ ! -f x-tunnel.yaml ]; then",
    9: "config_help",
    10: "backup_unified_config",
    11: "run_test",
    12: "manage_timer",
    13: 'sh "$PROJECT_DIR/install.sh" --with-bot',
    14: "manage_svc bot",
    15: "restart_service bot",
    16: "/etc/init.d/x-tunnel-speedtest-bot disable",
    17: "export_socks_providers",
    18: "repair_kernel",
    19: 'sh "$PROJECT_DIR/uninstall.sh"',
}


def parts():
    menu_start = MENU.index("show_menu() {\n")
    menu_end = MENU.index("\n}\nwhile :; do\n  show_menu", menu_start)
    screen = MENU[menu_start:menu_end]
    dispatcher = MENU.index('  case "$option" in\n', menu_end)
    switch_end = MENU.index('    0|q|Q) break;;', dispatcher)
    body = MENU[dispatcher:switch_end]
    entries = list(re.finditer(r"(?m)^    (\d+)\)", body))
    return screen, body, {
        int(match.group(1)): body[match.start():(entries[index + 1].start()
                                 if index + 1 < len(entries) else len(body))]
        for index, match in enumerate(entries)
    }, [int(match.group(1)) for match in entries]


class OpenWrtMenuLayoutTest(unittest.TestCase):
    def test_all_19_choices_are_shown_once_and_in_order(self):
        screen, _, _, _ = parts()
        numbers = [int(value) for value in re.findall(r"(?<!\d)(\d{1,2})\)", screen)]
        self.assertEqual(numbers, list(EXPECTED_ACTIONS) + [0])
        self.assertIn("【安装与常用】", screen)
        self.assertIn("【配置管理】", screen)
        self.assertIn("【CF IP 测速】", screen)
        self.assertIn("【Telegram Bot】", screen)
        self.assertIn("【导出与维护】", screen)

    def test_display_and_dispatch_agree_for_every_choice(self):
        _, _, actions, order = parts()
        self.assertEqual(order, list(EXPECTED_ACTIONS))
        for number, marker in EXPECTED_ACTIONS.items():
            with self.subTest(option=number):
                self.assertIn(marker, actions[number])

    def test_core_restart_and_logs_are_three_and_four(self):
        _, _, actions, _ = parts()
        self.assertIn("restart_service core", actions[3])
        self.assertIn("show_logs", actions[4])
        self.assertNotIn("restart_service bot", actions[3])
        self.assertIn("restart_service bot", actions[15])
        self.assertIn("manage_svc bot", actions[14])
        self.assertIn("选项 7 编辑统一 YAML", actions[13])


if __name__ == "__main__":
    unittest.main()
