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
    "X_TUNNEL_PROCESS_UID",
    "X_TUNNEL_PROCESS_GID",
    "X_TUNNEL_TG_PROXY",
    "X_TUNNEL_LOCAL_PROXY",
    "X_TUNNEL_TARGET_MBPS",
}
DEFAULT_LOCAL_PROXY = "http://127.0.0.1:7890"
# ShellCrash's socket-group bypass uses the PRIMARY GID of the process.
# Never use a supplementary group or UID 0 to achieve compatibility.
SHELLCRASH_GID = 7890
SHELLCRASH_GROUP_FILE = Path("/etc/group")
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
    The caller must have loaded project.env before invoking this function.
    """
    if not isinstance(uid, int) or isinstance(uid, bool) or not 1000 <= uid <= 60000:
        raise ValueError("dedicated X-Tunnel UID must be in 1000..60000")
    mode = os.environ.get("X_TUNNEL_PROCESS_GID", "auto").strip().lower()
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
