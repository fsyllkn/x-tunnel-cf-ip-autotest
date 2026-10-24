#!/usr/bin/env python3
"""One authoritative X-Tunnel YAML; no project.env reads at runtime.

Keep load_project_env() as a compatibility API for existing internal imports.
It derives the old variable names from x-tunnel.yaml only; external/legacy
environment values do not override the canonical configuration.
"""
import contextlib
import fcntl
import os
import re
import stat
import tempfile
from pathlib import Path

import yaml

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "x-tunnel.yaml"
DEFAULT_LOCAL_PROXY = "http://127.0.0.1:7890"
DEFAULT_TARGET_MBPS = 300
SHELLCRASH_GID = 7890
SHELLCRASH_GROUP_FILE = Path("/etc/group")
MIN_TARGET_MBPS = 100
MAX_TARGET_MBPS = 10000
# Legacy variable names retained only in process memory for callers that
# still call load_project_env(); no shell .env file is loaded.
LEGACY_SETTINGS = {
    "X_TUNNEL_PROVIDER_PATH": "provider_path",
    "X_TUNNEL_PROCESS_UID": "process_uid",
    "X_TUNNEL_PROCESS_GID": "process_gid",
    "X_TUNNEL_TG_PROXY": "telegram_proxy",
    "X_TUNNEL_LOCAL_PROXY": "local_proxy",
    "X_TUNNEL_TARGET_MBPS": "target_mbps",
}
PROJECT_KEYS = set(LEGACY_SETTINGS) | {"X_TUNNEL_LISTEN_HOSTS"}


def read_config():
    try:
        document = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return {}
    if not isinstance(document, dict):
        raise ValueError("x-tunnel.yaml must be a YAML mapping")
    for section in ("defaults", "settings", "telegram"):
        if section in document and not isinstance(document[section], dict):
            raise ValueError("x-tunnel.yaml %s must be a mapping" % section)
    return document


def project_settings():
    return read_config().get("settings") or {}


def load_project_env():
    """Compatibility shim: populate old internal names FROM YAML, not .env.

    Intentionally never synthesize X_TUNNEL_LISTEN_HOSTS: the only listener
    configuration lives at defaults.listen_hosts.
    """
    settings = project_settings()
    for key in PROJECT_KEYS:
        os.environ.pop(key, None)
    for name, field in LEGACY_SETTINGS.items():
        if field in settings and settings[field] is not None:
            os.environ[name] = str(settings[field])


def shellcrash_compat_gid():
    """Return 7890 only when the system group is exactly shellcrash:7890.

    Do not assume that an unrelated group with numeric GID 7890 carries
    ShellCrash's bypass rule. This is a group-based compatibility hint, not
    proof that a running firewall rule has been installed.
    """
    try:
        for line in SHELLCRASH_GROUP_FILE.read_text(encoding="utf-8").splitlines():
            fields = line.split(":")
            if (len(fields) >= 4 and fields[0] == "shellcrash"
                    and fields[2].isdecimal() and int(fields[2]) == SHELLCRASH_GID):
                return SHELLCRASH_GID
    except OSError:
        pass
    return None


def configured_process_gid(uid):
    """Keep isolated UID while optionally selecting ShellCrash bypass GID.

    X_TUNNEL_PROCESS_GID:
      auto (default): shellcrash primary GID 7890, if group verified; else UID
      uid:            always use GID matching the dedicated UID
      7890:           require the verified shellcrash group or fail closed
    The canonical source is x-tunnel.yaml settings.process_gid only.
    """
    if not isinstance(uid, int) or isinstance(uid, bool) or not 1000 <= uid <= 60000:
        raise ValueError("dedicated X-Tunnel UID must be in 1000..60000")
    mode = str(project_settings().get("process_gid", "auto") or "auto").strip().lower()
    if mode == "uid":
        return uid
    if mode in ("", "auto", str(SHELLCRASH_GID)):
        compatible = shellcrash_compat_gid()
        if mode == str(SHELLCRASH_GID) and compatible is None:
            raise ValueError(
                "X_TUNNEL_PROCESS_GID=7890 requires /etc/group entry shellcrash:x:7890"
            )
        return compatible if compatible is not None else uid
    raise ValueError("X_TUNNEL_PROCESS_GID must be auto, uid, or 7890")



def normalize_proxy(value):
    proxy = str(value or "").strip()
    if not proxy:
        return ""
    if "://" not in proxy:
        proxy = "http://" + proxy
    return proxy


def telegram_proxy_chain():
    settings = project_settings()
    local_proxy = normalize_proxy(settings.get("local_proxy", "")) or DEFAULT_LOCAL_PROXY
    custom_proxy = normalize_proxy(settings.get("telegram_proxy", ""))
    candidates = [custom_proxy, local_proxy] if custom_proxy else [local_proxy]
    return list(dict.fromkeys(candidates))


def read_telegram_credentials(require_chat_id=False):
    doc = read_config()
    telegram = doc.get("telegram") or {}
    token = str(telegram.get("token") or "").strip()
    chat_id = str(telegram.get("chat_id") or "").strip()
    if token and "替换为" in token:
        token = ""
    if chat_id and "替换为" in chat_id:
        chat_id = ""
    if token or chat_id or require_chat_id:
        if CONFIG_FILE.is_symlink():
            raise PermissionError("x-tunnel.yaml must not be a symlink when storing Bot credentials")
        mode = stat.S_IMODE(CONFIG_FILE.stat().st_mode)
        if mode & 0o077:
            raise PermissionError("x-tunnel.yaml must have mode 0600 or stricter (contains Bot secrets)")
    if require_chat_id and not token:
        raise ValueError("telegram.token is missing from x-tunnel.yaml")
    if require_chat_id and not chat_id:
        raise ValueError("telegram.chat_id is missing from x-tunnel.yaml")
    if chat_id and not chat_id.isdecimal():
        raise ValueError("telegram.chat_id must be the numeric ID of your private chat")
    return token, chat_id


def get_process_uid():
    raw = project_settings().get("process_uid", 29002)
    if isinstance(raw, bool) or not str(raw).isdecimal():
        raise ValueError("settings.process_uid must be a numeric UID")
    return int(raw)


def get_target_mbps():
    raw = project_settings().get("target_mbps", DEFAULT_TARGET_MBPS)
    if isinstance(raw, bool) or not str(raw).isdecimal():
        raise ValueError("settings.target_mbps must be an integer Mbps value")
    target = int(raw)
    if not MIN_TARGET_MBPS <= target <= MAX_TARGET_MBPS:
        raise ValueError(
            "settings.target_mbps must be between %d and %d Mbps"
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


@contextlib.contextmanager
def config_lock():
    """Serialize all programmatic YAML writes, including CF IP updates."""
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    lock_path = CONFIG_FILE.with_name(".x-tunnel.yaml.lock")
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "a+") as locked:
            fcntl.flock(locked.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(locked.fileno(), fcntl.LOCK_UN)
    except BaseException:
        raise


def write_config_atomic(content):
    """Commit a validated YAML document without exposing Bot credentials."""
    parsed = yaml.safe_load(content)
    if not isinstance(parsed, dict):
        raise ValueError("x-tunnel.yaml must be a YAML mapping")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".x-tunnel-yaml.", dir=str(CONFIG_FILE.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, CONFIG_FILE)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def set_target_mbps(target):
    """Preserve comments and exact double-quoted node rows used by CF updater."""
    if not isinstance(target, int) or not MIN_TARGET_MBPS <= target <= MAX_TARGET_MBPS:
        raise ValueError(
            "target must be between %d and %d Mbps"
            % (MIN_TARGET_MBPS, MAX_TARGET_MBPS)
        )
    with config_lock():
        original = CONFIG_FILE.read_text(encoding="utf-8")
        lines = original.splitlines(keepends=True)
        # Only accept block-mapping settings:, never corrupt inline YAML.
        start = next((i for i, line in enumerate(lines)
                      if re.match(r"^settings:\s*(?:#.*)?$", line.rstrip("\r\n"))), None)
        if start is None:
            if re.search(r"(?m)^settings:", original):
                raise ValueError("settings must use a YAML block mapping")
            updated = original.rstrip("\r\n") + "\n\nsettings:\n  target_mbps: %d\n" % target
        else:
            end = next((i for i in range(start + 1, len(lines))
                        if re.match(r"^[^\s#][^:]*:", lines[i])), len(lines))
            for i in range(start + 1, end):
                if re.match(r"^  target_mbps\s*:", lines[i]):
                    suffix = "\n" if lines[i].endswith("\n") else ""
                    lines[i] = "  target_mbps: %d" % target + suffix
                    break
            else:
                lines.insert(end, "  target_mbps: %d\n" % target)
            updated = "".join(lines)
        write_config_atomic(updated)
