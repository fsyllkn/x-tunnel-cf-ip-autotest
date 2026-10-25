"""Regression checks for installing the LXC core before node configuration.

No systemctl calls or real binaries are invoked.
"""
import unittest
from pathlib import Path
from unittest import mock
import config_ready

ROOT = Path(__file__).resolve().parents[1]


def spec(**kwargs):
    result = dict(name="HK", server="wss://hk.domain.test/about:443",
                  token="secret", cfip="162.159.1.1")
    result.update(kwargs)
    return result


class LxcDeferredInstallTest(unittest.TestCase):
    def check(self, nodes):
        with mock.patch.object(config_ready.supervisor, "read_specs", return_value=nodes), \
             mock.patch.object(config_ready, "get_target_mbps"), \
             mock.patch.object(config_ready, "read_telegram_credentials"):
            return config_ready.readiness()

    def test_no_nodes_is_pending(self):
        self.assertEqual(self.check([])[0], False)

    def test_example_nodes_are_pending(self):
        self.assertFalse(self.check([spec(server="wss://example.com/about:443")])[0])
        self.assertFalse(self.check([spec(token="替换为你的默认密钥")])[0])
        self.assertFalse(self.check([spec(cfip="替换为优选 CF IP")])[0])

    def test_real_node_is_ready_with_template_defaults_overridden(self):
        self.assertTrue(self.check([spec()])[0])

    def test_mixed_real_and_example_nodes_stays_pending(self):
        self.assertFalse(self.check([
            spec(), spec(name="example", server="wss://example.com/path:443")
        ])[0])

    def test_installer_does_not_gate_registration_on_nodes(self):
        install = (ROOT / "install.sh").read_text(encoding="utf-8")
        menu = (ROOT / "menu.sh").read_text(encoding="utf-8")
        quick = (ROOT / "quick-install.sh").read_text(encoding="utf-8")
        unit = (ROOT / "systemd/x-tunnel.service").read_text(encoding="utf-8")
        self.assertIn("CORE_READY=0", install)
        self.assertIn('systemctl disable --now x-tunnel.service', install)
        self.assertIn('systemctl enable x-tunnel.service', install)
        self.assertNotIn("if grep -q 'example.com", menu[menu.index("install_services() {"):menu.index("update_code() {")])
        self.assertNotIn('info "Edit $PROJECT_DIR/x-tunnel.yaml, then rerun."', quick)
        self.assertIn("ExecCondition=/usr/bin/python3 @PROJECT_DIR@/config_ready.py", unit)


if __name__ == "__main__":
    unittest.main()
