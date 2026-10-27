"""Unit tests for local-time systemd CF scheduler (never touches host systemd)."""
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

MODULE_FILE = Path(__file__).resolve().parents[1] / "systemd/speedtest-timer.py"
spec = importlib.util.spec_from_file_location("xtunnel_systemd_timer", MODULE_FILE)
timer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(timer)


class SystemdTimerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.dropin = Path(self.directory.name) / "x-tunnel-speedtest.timer.d"
        self.mock_dir = mock.patch.object(timer, "DROPIN_DIR", self.dropin)
        self.mock_dir.start()
        self.addCleanup(self.mock_dir.stop)

    def test_validate_strict_24_hour_hhmm(self):
        for good in ("00:00", "06:45", "18:00", "23:59"):
            self.assertTrue(timer.valid_time(good))
        for bad in ("", "8:00", "24:00", "18:60", "18:00:00", "99:99"):
            self.assertFalse(timer.valid_time(bad))

    def test_atomic_dropin_overrides_original_calendar_without_timezone(self):
        self.assertEqual(timer.saved_time(), "18:00")
        self.assertTrue(timer.write_schedule("06:45"))
        self.assertEqual(timer.saved_time(), "06:45")
        drop = (self.dropin / timer.DROPIN_NAME).read_text()
        self.assertIn("OnCalendar=\nOnCalendar=*-*-* 06:45:00", drop)
        self.assertIn("Persistent=false", drop)
        self.assertNotIn("Asia/Shanghai", drop)
        self.assertFalse(timer.write_schedule("06:45"))
        self.assertTrue(timer.write_schedule("23:59"))
        self.assertEqual(timer.saved_time(), "23:59")

    def test_enable_only_changes_own_timer_not_core_service(self):
        commands = []

        def fake_systemctl(*args, **kwargs):
            commands.append(args)
            ret = 1 if args[0] in ("is-enabled", "is-active") else 0
            return subprocess.CompletedProcess(list(args), ret)

        with mock.patch.object(timer.os, "geteuid", return_value=0), \
                mock.patch.object(timer, "systemctl", side_effect=fake_systemctl), \
                mock.patch.object(sys, "argv", ["speedtest-timer.py", "enable", "21:30"]):
            self.assertEqual(timer.main(), 0)
        self.assertEqual(timer.saved_time(), "21:30")
        self.assertIn(("daemon-reload",), commands)
        self.assertIn(("enable", timer.TIMER), commands)
        self.assertIn(("start", timer.TIMER), commands)
        self.assertNotIn(("restart", timer.TIMER), commands)
        self.assertFalse(any("--now" in cmd for cmd in commands))
        self.assertFalse(any("x-tunnel-speedtest.service" in cmd for cmd in commands))
        self.assertTrue(all("x-tunnel.service" not in item for cmd in commands for item in cmd))

    def test_reschedule_enabled_timer_without_core_restart(self):
        commands = []

        def fake_systemctl(*args, **kwargs):
            commands.append(args)
            return subprocess.CompletedProcess(list(args), 0)

        with mock.patch.object(timer.os, "geteuid", return_value=0), \
                mock.patch.object(timer, "systemctl", side_effect=fake_systemctl), \
                mock.patch.object(sys, "argv", ["speedtest-timer.py", "enable", "08:10"]):
            self.assertEqual(timer.main(), 0)
        self.assertIn(("restart", timer.TIMER), commands)
        self.assertNotIn(("start", timer.TIMER), commands)
        self.assertFalse(any("x-tunnel-speedtest.service" in cmd for cmd in commands))
        self.assertEqual(timer.saved_time(), "08:10")


    def test_repeat_enable_same_time_leaves_running_timer_untouched(self):
        self.assertTrue(timer.write_schedule("09:30"))
        commands = []

        def fake_systemctl(*args, **kwargs):
            commands.append(args)
            return subprocess.CompletedProcess(list(args), 0)

        with mock.patch.object(timer.os, "geteuid", return_value=0), \
                mock.patch.object(timer, "systemctl", side_effect=fake_systemctl), \
                mock.patch.object(sys, "argv", ["speedtest-timer.py", "enable", "09:30"]):
            self.assertEqual(timer.main(), 0)
        self.assertIn(("enable", timer.TIMER), commands)
        self.assertNotIn(("restart", timer.TIMER), commands)
        self.assertNotIn(("start", timer.TIMER), commands)
        self.assertFalse(any("x-tunnel-speedtest.service" in cmd for cmd in commands))

    def test_disable_retains_time_and_never_touches_core(self):
        self.assertTrue(timer.write_schedule("06:45"))
        commands = []

        def fake_systemctl(*args, **kwargs):
            commands.append(args)
            result = 1 if args[0] == "is-enabled" else 0
            return subprocess.CompletedProcess(args, result)

        with mock.patch.object(timer.os, "geteuid", return_value=0), \
                mock.patch.object(timer, "systemctl", side_effect=fake_systemctl), \
                mock.patch.object(sys, "argv", ["speedtest-timer.py", "disable"]):
            self.assertEqual(timer.main(), 0)
        self.assertEqual(timer.saved_time(), "06:45")
        self.assertIn(("disable", "--now", timer.TIMER), commands)
        self.assertTrue(all("x-tunnel.service" not in a for cmd in commands for a in cmd))

    def test_status_tracks_enabled_state_and_saved_time(self):
        self.assertTrue(timer.write_schedule("07:15"))
        output = []
        with mock.patch.object(timer, "is_enabled", side_effect=[True, False]), \
                mock.patch("builtins.print", side_effect=output.append), \
                mock.patch.object(sys, "argv", ["speedtest-timer.py", "status"]):
            self.assertEqual(timer.main(), 0)
            self.assertEqual(timer.main(), 0)
        self.assertEqual(output, ["07:15", "disabled"])


if __name__ == "__main__":
    unittest.main()
