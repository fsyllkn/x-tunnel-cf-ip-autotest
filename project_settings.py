#!/usr/bin/env python3
import os
import shlex
import stat
import tempfile
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
PROJECT_ENV_FILE = BASE_DIR / "project.env"
PROJECT_KEYS = {
    "X_TUNNEL_LISTEN_HOSTS",
    "X_TUNNEL_PROVIDER_PATH",
    "X_TUNNEL_TG_PROXY",
    "X_TUNNEL_LOCAL_PROXY",
    "X_TUNNEL_TARGET_MBPS",
}
DEFAULT_LOCAL_PROXY = "http://127.0.0.1:7890"
DEFAULT_TARGET_MBPS = 300
MIN_TARGET_MBPS = 100
MAX_TARGET_MBPS = 10000


def load_project_env():
    for key in PROJECT_KEYS:
        os.environ.pop(key, None)
    try:
        lines = PROJECT_ENV_FILE.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return
    for line in lines:
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or key not in PROJECT_KEYS:
            continue
        parsed = shlex.split(value, comments=True)
        os.environ[key] = parsed[0] if parsed else ""


def normalize_proxy(value):
    proxy = value.strip()
    if not proxy:
        return ""
    if "://" not in proxy:
        proxy = "http://" + proxy
    return proxy


def telegram_proxy_chain():
    load_project_env()
    local_proxy = normalize_proxy(os.environ.get("X_TUNNEL_LOCAL_PROXY", "")) or DEFAULT_LOCAL_PROXY
    custom_proxy = normalize_proxy(os.environ.get("X_TUNNEL_TG_PROXY", ""))
    candidates = [custom_proxy, local_proxy] if custom_proxy else [local_proxy]
    return list(dict.fromkeys(candidates))


def get_target_mbps():
    load_project_env()
    raw = os.environ.get("X_TUNNEL_TARGET_MBPS", str(DEFAULT_TARGET_MBPS))
    if not raw.isdecimal():
        raise ValueError("X_TUNNEL_TARGET_MBPS must be an integer Mbps value")
    target = int(raw)
    if not MIN_TARGET_MBPS <= target <= MAX_TARGET_MBPS:
        raise ValueError(
            "X_TUNNEL_TARGET_MBPS must be between %d and %d Mbps"
            % (MIN_TARGET_MBPS, MAX_TARGET_MBPS)
        )
    return target


def build_speed_tiers(target, step=50, floor=MIN_TARGET_MBPS):
    if not MIN_TARGET_MBPS <= target <= MAX_TARGET_MBPS:
        raise ValueError(
            "target must be between %d and %d Mbps"
            % (MIN_TARGET_MBPS, MAX_TARGET_MBPS)
        )
    if step < 1 or floor < MIN_TARGET_MBPS or floor > target:
        raise ValueError("invalid speed tier step or floor")
    tiers = []
    current = target
    while current > floor:
        tiers.append(float(current))
        current -= step
    tiers.append(float(floor))
    return tiers


def set_target_mbps(target):
    if not MIN_TARGET_MBPS <= target <= MAX_TARGET_MBPS:
        raise ValueError(
            "target must be between %d and %d Mbps"
            % (MIN_TARGET_MBPS, MAX_TARGET_MBPS)
        )
    try:
        original = PROJECT_ENV_FILE.read_text(encoding="utf-8")
        mode = stat.S_IMODE(PROJECT_ENV_FILE.stat().st_mode)
    except FileNotFoundError:
        original = ""
        mode = 0o644

    lines = original.splitlines()
    assignment = "X_TUNNEL_TARGET_MBPS=%d" % target
    for index, line in enumerate(lines):
        if line.partition("=")[0].strip() == "X_TUNNEL_TARGET_MBPS":
            lines[index] = assignment
            break
    else:
        lines.append(assignment)
    content = "\n".join(lines).rstrip() + "\n"

    PROJECT_ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".project.env.", dir=str(PROJECT_ENV_FILE.parent), text=True
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.chmod(temporary_name, mode)
        os.replace(temporary_name, PROJECT_ENV_FILE)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
