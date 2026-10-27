#!/usr/bin/env python3
"""Manage X-Tunnel's daily systemd timer without restarting the SOCKS core.

HH:MM is interpreted in the machine's local timezone, not a hardcoded zone.
Only the project's own timer drop-in is modified.
"""
import argparse
import os
import re
import subprocess
import tempfile
from pathlib import Path

TIMER = "x-tunnel-speedtest.timer"
DROPIN_DIR = Path(os.environ.get(
    "X_TUNNEL_TIMER_DROPIN_DIR",
    "/etc/systemd/system/x-tunnel-speedtest.timer.d",
))
DROPIN_NAME = "50-xtunnel-schedule.conf"
SYSTEMCTL = os.environ.get("X_TUNNEL_SYSTEMCTL", "systemctl")
TIME_RE = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]\Z")
CALENDAR_RE = re.compile(
    r"(?m)^OnCalendar=\*-\*-\* ((?:[01][0-9]|2[0-3]):[0-5][0-9]):00\s*$"
)
DEFAULT_TIME = "18:00"


def valid_time(value):
    return bool(TIME_RE.fullmatch(value))


def saved_time():
    try:
        content = (DROPIN_DIR / DROPIN_NAME).read_text(encoding="utf-8")
    except FileNotFoundError:
        return DEFAULT_TIME
    match = CALENDAR_RE.search(content)
    return match.group(1) if match else DEFAULT_TIME


def systemctl(*args, check=True):
    return subprocess.run(
        [SYSTEMCTL, *args],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        check=check,
    )


def is_enabled():
    return systemctl("is-enabled", "--quiet", TIMER, check=False).returncode == 0


def is_active():
    return systemctl("is-active", "--quiet", TIMER, check=False).returncode == 0


def write_schedule(value):
    path = DROPIN_DIR / DROPIN_NAME
    content = (
        "[Timer]\n"
        "OnCalendar=\n"
        "OnCalendar=*-*-* %s:00\n"
        "Persistent=false\n" % value
    )
    if path.is_file() and path.read_text(encoding="utf-8") == content:
        return False
    DROPIN_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".xtunnel-timer-", dir=str(DROPIN_DIR))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return True


def main():
    parser = argparse.ArgumentParser(description="Daily X-Tunnel systemd timer in local HH:MM")
    parser.add_argument("action", choices=("status", "saved", "validate", "enable", "disable"))
    parser.add_argument("time", nargs="?")
    args = parser.parse_args()
    if args.action in ("validate", "enable"):
        value = args.time if args.time is not None else (
            saved_time() if args.action == "enable" else ""
        )
        if not valid_time(value):
            parser.error("time must be 24-hour HH:MM in 00:00..23:59")
    elif args.time is not None:
        parser.error("this action does not accept a time")
    if args.action == "validate":
        return 0
    if args.action == "saved":
        print(saved_time())
        return 0
    if args.action == "status":
        print(saved_time() if is_enabled() else "disabled")
        return 0
    if os.geteuid() != 0:
        parser.error("root privileges required to modify systemd timers")
    if args.action == "disable":
        systemctl("disable", "--now", TIMER)
        print("Daily CF speedtest disabled; saved time remains %s." % saved_time())
        return 0
    # Never start x-tunnel-speedtest.service here. Only arm/rearm its timer.
    # Persistent=false in the timer definition avoids catch-up executions.
    active_before = is_active()
    changed = write_schedule(value)
    if changed:
        systemctl("daemon-reload")
    systemctl("enable", TIMER)  # boot enablement only; never implies a scan
    if not active_before:
        systemctl("start", TIMER)  # arm the future schedule
    elif changed:
        systemctl("restart", TIMER)  # rearm calendar only if time changed
    print(
        "Daily CF speedtest schedule armed at %s (host local time); "
        "no speedtest launched or X-Tunnel core restarted." % value
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
