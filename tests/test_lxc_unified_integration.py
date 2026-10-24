"""Static integration checks: unified YAML on systemd and private LXC updates.

Never executes an installer, changes users/groups, or modifies a route.
"""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class LXCUnifiedIntegrationTest(unittest.TestCase):
    def test_all_systemd_units_stop_injecting_legacy_env_files(self):
        for unit in (
            "x-tunnel.service",
            "x-tunnel-speedtest.service",
            "x-tunnel-speedtest-single@.service",
            "x-tunnel-speedtest-bot.service",
        ):
            with self.subTest(unit=unit):
                content = (ROOT / "systemd" / unit).read_text(encoding="utf-8")
                self.assertNotIn("EnvironmentFile=", content)
                self.assertNotIn("X_TUNNEL_BOT_CONFIG", content)
                self.assertIn("ExecStart=", content)

    def test_installer_migrates_before_any_systemd_reload_or_restart(self):
        content = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn("python3 migrate_config.py", content)
        self.assertLess(content.index("python3 migrate_config.py"),
                        content.index("systemctl daemon-reload"))
        self.assertNotIn("project.env.example", content)
        self.assertNotIn("speedtest-bot.env.example", content)
        self.assertIn("configured_process_gid(get_process_uid())", content)
        self.assertIn("BOT_WAS_RUNNING", content)

    def test_private_bootstrap_never_uses_main_or_openwrt_branch(self):
        content = (ROOT / "quick-install.sh").read_text(encoding="utf-8")
        self.assertIn("BRANCH=lxc", content)
        self.assertIn("refs/heads/lxc.tar.gz", content)
        self.assertIn("--archive=*", content)
        self.assertIn('git -C "$PROJECT_DIR" pull --ff-only origin "$BRANCH"', content)
        self.assertNotIn("BRANCH=main", content)
        self.assertNotIn("project.env.example", content)
        self.assertIn("python3 migrate_config.py", content)

    def test_menu_uses_yaml_and_systemd_without_reintroducing_old_env(self):
        content = (ROOT / "menu.sh").read_text(encoding="utf-8")
        self.assertIn("GitHub lxc 分支", content)
        self.assertIn("edit_file x-tunnel.yaml x-tunnel.example.yaml", content)
        self.assertIn('python3 "$SYSTEMD_TIMER_CLI" enable', content)
        self.assertIn("systemctl disable --now x-tunnel-speedtest-bot.service", content)
        self.assertNotIn("edit_file project.env", content)
        self.assertNotIn("edit_file speedtest-bot.env", content)

    def test_lxc_route_safeguards_and_child_primary_gid_are_preserved(self):
        speedtest = (ROOT / "cfip_speedtest.py").read_text(encoding="utf-8")
        supervisor = (ROOT / "supervisor.py").read_text(encoding="utf-8")
        self.assertIn("singbox_lxc_guard", speedtest)
        self.assertIn("systemd-detect-virt", speedtest)
        self.assertIn("active_singbox_local_ebpf", speedtest)
        self.assertIn("gid = configured_process_gid(uid)", speedtest)
        self.assertIn("group=gid", speedtest)
        self.assertIn("process_gid", supervisor)
        self.assertIn("group=gid", supervisor)

    def test_legacy_templates_are_absent_and_migrator_is_present(self):
        self.assertFalse((ROOT / "project.env.example").exists())
        self.assertFalse((ROOT / "speedtest-bot.env.example").exists())
        self.assertTrue((ROOT / "migrate_config.py").is_file())
        example = (ROOT / "x-tunnel.example.yaml").read_text(encoding="utf-8")
        for field in ("settings:", "process_uid:", "process_gid:", "target_mbps:",
                      "provider_path:", "local_proxy:", "telegram:", "listen_hosts:"):
            with self.subTest(field=field):
                self.assertIn(field, example)

if __name__ == "__main__":
    unittest.main()
