"""Regression coverage for the Debian/Ubuntu SSH menu's systemd timer path.

Shell functions and timer CLI are mocked; host systemd is never changed.
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "menu.sh").read_text(encoding="utf-8")


def function_source(name, next_name):
    start = SOURCE.index(name + "() {")
    end = SOURCE.index("\n" + next_name + "() {", start)
    return SOURCE[start:end]


class LinuxTimerMenuTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.log = Path(self.dir.name) / "actions.log"

    def menu_shell(self):
        # Detect the missing variable that previously terminated menu 12 -> 1.
        header = SOURCE.split("say() {", 1)[0]
        declaration = 'SYSTEMD_TIMER_CLI="$PROJECT_DIR/systemd/speedtest-timer.py"'
        self.assertIn(declaration, header)
        self.assertIn("12) manage_timer;;", SOURCE)
        return "\n".join((
            "set -u",
            'PROJECT_DIR="%s"' % ROOT,
            "PLATFORM=systemd",
            declaration,
            "root_only() { :; }",
            "say() { printf '%s\\n' \"$*\"; }",
            "timer_status() { printf '已开启 (18:00)'; }",
            """python3() {
  if [ "$1" = "-c" ]; then return 0; fi
  if [ "$1" != "$SYSTEMD_TIMER_CLI" ]; then return 127; fi
  case "$2" in
    saved) printf '18:00\\n' ;;
    validate) case "$3" in 06:45|18:00) return 0 ;; *) return 2 ;; esac ;;
    enable) printf 'enable:%s\\n' "$3" >> "$LOGFILE" ;;
    disable) printf 'disable:\\n' >> "$LOGFILE" ;;
    *) return 5 ;;
  esac
}
sh() { printf 'install:%s\\n' "$*" >> "$LOGFILE"; }""",
            function_source("manage_timer", "run_test"),
            "manage_timer",
            "exit 0",
        ))

    def invoke(self, input_text):
        p = subprocess.run(
            ["/bin/sh", "-c", self.menu_shell()],
            input=input_text, capture_output=True, text=True,
            env={**os.environ, "LOGFILE": str(self.log)},
            timeout=10, check=False,
        )
        self.assertEqual(p.returncode, 0, p.stderr + "\n" + p.stdout)
        self.assertNotIn("parameter not set", p.stderr)
        return p, self.log.read_text() if self.log.exists() else ""

    def test_enable_custom_daily_schedule(self):
        result, calls = self.invoke("1\n06:45\n")
        self.assertIn("每日测速时间", result.stdout)
        self.assertTrue(
            "enable:06:45" in calls or "--timer-at=06:45" in calls, calls,
        )

    def test_default_schedule_on_blank_entry(self):
        _, calls = self.invoke("1\n\n")
        self.assertTrue(
            "enable:18:00" in calls or "--timer-at=18:00" in calls, calls,
        )

    def test_reject_invalid_time_without_changes(self):
        _, calls = self.invoke("1\n24:99\n")
        self.assertEqual(calls, "")

    def test_disable_schedule(self):
        _, calls = self.invoke("2\n")
        self.assertIn("disable:", calls)

    def test_status_displays_saved_hhmm(self):
        declaration = 'SYSTEMD_TIMER_CLI="$PROJECT_DIR/systemd/speedtest-timer.py"'
        body = "\n".join((
            "set -u",
            'PROJECT_DIR="%s"' % ROOT,
            "PLATFORM=systemd",
            declaration,
            "systemctl() { return 0; }",
            """python3() {
  if [ "$2" = "status" ]; then printf '06:45\\n'; else return 2; fi
}""",
            function_source("timer_status", "prepare"),
            "timer_status",
        ))
        p = subprocess.run(
            ["/bin/sh", "-c", body],
            capture_output=True, text=True, timeout=10, check=False,
        )
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout, "已开启 (06:45)")


if __name__ == "__main__":
    unittest.main()
