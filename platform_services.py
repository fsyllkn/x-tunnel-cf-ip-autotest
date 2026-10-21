#!/usr/bin/env python3
import fcntl
import os
import shutil
import subprocess
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
SCAN_LOCK_FILE = Path("/run/lock/x-tunnel-cfip-speedtest.lock")


def is_systemd():
    return Path("/run/systemd/system").is_dir() and shutil.which("systemctl") is not None


def is_openwrt():
    return Path("/etc/openwrt_release").is_file()


def speedtest_running():
    SCAN_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with SCAN_LOCK_FILE.open("a+") as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(lock_file, fcntl.LOCK_UN)
    return False


def launch_speedtest(ip=None):
    script = BASE_DIR / "cfip_speedtest.py"
    command = [sys.executable, str(script)]
    if ip:
        command.extend(["--single-ip", ip])
    log_path = BASE_DIR / "cfip-data" / "openwrt-speedtest.log"
    log_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log_file:
        process = subprocess.Popen(
            command,
            cwd=str(BASE_DIR),
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    return process


def restart_xtunnel():
    return subprocess.run(
        ["/etc/init.d/x-tunnel", "restart"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
