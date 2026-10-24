#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
ENABLE_TIMER=0
TIMER_AT=''
TIMER_AT_EXPLICIT=0
WITH_BOT=0
WITH_SPEEDTEST=0
DISABLE_BOT=0
OPENWRT=0
PLATFORM=openwrt
KERNEL_PATH=/usr/bin/x-tunnel
KERNEL_REPOSITORY=fsyllkn/x-tunnel
KERNEL_DEFAULT_VERSION=openwrt-stable-go126-v1
REPAIR_KERNEL=0

if [ -f /etc/openwrt_release ] && [ -f /etc/rc.common ] && [ -x /sbin/procd ]; then
    OPENWRT=1
else
    echo "OpenWrt/procd required. Debian and Ubuntu should use the main branch." >&2
    exit 1
fi

for argument in "$@"; do
    case "$argument" in
        --enable-timer) ENABLE_TIMER=1; WITH_SPEEDTEST=1 ;;
        --timer-at=*) ENABLE_TIMER=1; WITH_SPEEDTEST=1; TIMER_AT_EXPLICIT=1; TIMER_AT=${argument#*=} ;;
        --with-bot) WITH_BOT=1; WITH_SPEEDTEST=1 ;;
        --with-speedtest) WITH_SPEEDTEST=1 ;;
        --disable-bot) DISABLE_BOT=1 ;;
        --repair-kernel) REPAIR_KERNEL=1 ;;
        --help|-h) echo "Usage: $0 [--with-bot] [--with-speedtest] [--enable-timer] [--timer-at=HH:MM] [--disable-bot] [--repair-kernel]"; exit 0 ;;
        *) echo "Invalid option. Use --help." >&2; exit 2 ;;
    esac
done

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this installer as root." >&2
    exit 1
fi

[ "$WITH_BOT" -eq 0 ] || [ "$DISABLE_BOT" -eq 0 ] || { echo "Cannot enable and disable Bot together." >&2; exit 2; }
if [ "$ENABLE_TIMER" -eq 1 ]; then
    TIMER_SCRIPT="$PROJECT_DIR/openwrt/speedtest-timer.sh"
    [ -f "$TIMER_SCRIPT" ] || { echo "Missing speedtest timer manager: $TIMER_SCRIPT" >&2; exit 1; }
    if [ "$TIMER_AT_EXPLICIT" -eq 1 ]; then
        sh "$TIMER_SCRIPT" validate "$TIMER_AT" || exit 2
    else
        TIMER_AT=$(sh "$TIMER_SCRIPT" status)
        [ "$TIMER_AT" != disabled ] || TIMER_AT=18:00
    fi
fi
mode=core-only
if [ "$WITH_BOT" -eq 1 ]; then
    mode=core-plus-telegram-bot
elif [ "$WITH_SPEEDTEST" -eq 1 ]; then
    mode=core-plus-speedtest
fi
printf 'Detected platform: %s; install mode: %s\n' "$PLATFORM" "$mode"

# Refresh package indexes only once. Third-party feed failures must not
# prevent installing dependencies from healthy feeds (OpenWrt 24.x opkg).
PKG_REFRESH_ATTEMPTED=0
install_missing() {
    [ "$#" -gt 0 ] || return 0
    if command -v apk >/dev/null 2>&1; then
        manager=apk
    elif command -v opkg >/dev/null 2>&1; then
        manager=opkg
    else
        echo "[ERROR] Cannot install $*: neither apk nor opkg is available." >&2
        return 1
    fi
    if [ "$PKG_REFRESH_ATTEMPTED" -eq 0 ]; then
        PKG_REFRESH_ATTEMPTED=1
        if ! "$manager" update; then
            echo "[WARN] $manager update was incomplete (some package feeds may be unavailable)." >&2
            echo "[WARN] Trying to install required packages from the available indexes." >&2
        fi
    fi
    if [ "$manager" = apk ]; then
        "$manager" add "$@" || {
            echo "[ERROR] apk could not install: $*" >&2
            return 1
        }
    else
        "$manager" install "$@" || {
            echo "[ERROR] opkg could not install: $*" >&2
            echo "[ERROR] Check feed URLs, architecture and availability; no feed configuration was changed." >&2
            return 1
        }
    fi
}
command -v python3 >/dev/null 2>&1 || install_missing python3-light
# OpenWrt splits Python's standard library into separate packages. Python 3.11
# pathlib imports urllib.parse even for local-file operations; the core
# supervisor therefore requires urllib in ALL installation modes.
if ! python3 -c 'import urllib.parse; from pathlib import Path' >/dev/null 2>&1; then
    echo "[INFO] Core Python urllib.parse/pathlib missing; installing python3-urllib."
    install_missing python3-urllib || exit 1
fi
python3 -c 'import urllib.parse; from pathlib import Path' >/dev/null 2>&1 || {
    echo "[ERROR] Core Python still cannot import urllib.parse/pathlib." >&2
    echo "[ERROR] Check OpenWrt package feeds and Python package version compatibility." >&2
    exit 1
}
# Dedicated-UID X-Tunnel nodes use hashlib for per-node cache directories.
if ! python3 -c 'import hashlib; hashlib.sha256(b"x-tunnel").hexdigest()' >/dev/null 2>&1; then
    echo "[INFO] Core Python hashlib missing; installing python3-hashlib."
    install_missing python3-hashlib || exit 1
fi
python3 -c 'import hashlib; hashlib.sha256(b"x-tunnel").hexdigest()' >/dev/null 2>&1 || {
    echo "[ERROR] Core Python hashlib is unavailable after package installation." >&2
    exit 1
}
python3 -c 'import yaml' >/dev/null 2>&1 || install_missing python3-yaml
python3 -c 'import logging' >/dev/null 2>&1 || install_missing python3-logging
if [ "$WITH_SPEEDTEST" -eq 1 ]; then
 missing=""
 command -v ip >/dev/null 2>&1 || missing="$missing ip-full"
 python3 -c 'import codecs; codecs.lookup("idna")' >/dev/null 2>&1 || missing="$missing python3-codecs"
 python3 -c 'import ssl' >/dev/null 2>&1 || missing="$missing python3-openssl"
 python3 -c 'import urllib.request' >/dev/null 2>&1 || missing="$missing python3-urllib"
 if [ -n "$missing" ]; then install_missing $missing ca-bundle; fi
fi
required_commands="sed cp chmod mkdir mv python3 uname"
if [ "$OPENWRT" -eq 0 ]; then
    required_commands="$required_commands systemctl"
fi
for command in $required_commands; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Required command not found: $command" >&2
        exit 1
    }
done

# BusyBox-based OpenWrt images may not provide GNU/coreutils 'install'.
# Copy with a restrictive umask first so private configuration templates are
# never briefly exposed before their final permissions are applied.
copy_with_mode() {
    (umask 077; cp "$1" "$2") || return 1
    chmod "$3" "$2"
}

machine=$(uname -m)
case "$machine" in
    x86_64|amd64) kernel_arch=amd64; expected_elf_machine=62 ;;
    aarch64|arm64) kernel_arch=arm64; expected_elf_machine=183 ;;
    *)
        echo "Unsupported CPU architecture: $machine (supported: x86_64/amd64, aarch64/arm64)." >&2
        exit 1
        ;;
esac

validate_kernel() {
    kernel_file=$1
    python3 - "$kernel_file" "$expected_elf_machine" <<'PY'
import struct
import sys

path, expected_machine = sys.argv[1], int(sys.argv[2])
with open(path, "rb") as binary:
    header = binary.read(20)
if len(header) < 20 or header[:4] != b"\x7fELF":
    raise SystemExit("Downloaded x-tunnel file is not a valid ELF executable.")
if header[4] != 2 or header[5] not in (1, 2):
    raise SystemExit("x-tunnel must be a 64-bit ELF executable.")
byte_order = "<" if header[5] == 1 else ">"
machine = struct.unpack(byte_order + "H", header[18:20])[0]
if machine != expected_machine:
    raise SystemExit("x-tunnel ELF architecture does not match this system.")
PY
}

# Normal updates preserve a user's binary; replacing a running Go executable
# is explicit (--repair-kernel), verified before installing and backed up.
KERNEL_ALREADY_INSTALLED=0
if [ -e "$KERNEL_PATH" ] || [ -L "$KERNEL_PATH" ]; then
    KERNEL_ALREADY_INSTALLED=1
    [ -f "$KERNEL_PATH" ] && [ ! -L "$KERNEL_PATH" ] || {
        echo "Existing $KERNEL_PATH is not a regular file." >&2; exit 1;
    }
    validate_kernel "$KERNEL_PATH" || exit 1
fi
if [ "$KERNEL_ALREADY_INSTALLED" -eq 1 ] && [ "$REPAIR_KERNEL" -eq 0 ]; then
    chmod 0755 "$KERNEL_PATH"
    echo "Using existing x-tunnel kernel ($machine); no binary change."
    echo "[INFO] To repair missing SOCKS5 listeners select x-t menu 19."
else
    release=${X_TUNNEL_KERNEL_VERSION:-$KERNEL_DEFAULT_VERSION}
    case "$release" in
        *[!A-Za-z0-9._-]*|"") echo "Invalid kernel version: $release" >&2; exit 1 ;;
    esac
    asset="x-tunnel-linux-$kernel_arch"
    download_dir=$(mktemp -d "/tmp/x-tunnel-kernel.XXXXXX")
    trap 'rm -rf "$download_dir"' EXIT HUP INT TERM
    downloaded_kernel="$download_dir/$asset"
    if [ -n "${X_TUNNEL_KERNEL_FILE:-}" ]; then
        echo "[INFO] Loading candidate from local file: $X_TUNNEL_KERNEL_FILE"
        cp "$X_TUNNEL_KERNEL_FILE" "$downloaded_kernel"
    else
        download_url="https://github.com/$KERNEL_REPOSITORY/releases/download/$release/$asset"
        echo "[INFO] Downloading OpenWrt fixed kernel ($release)"
        if command -v curl >/dev/null 2>&1; then
            curl -fL --retry 3 --connect-timeout 20 -o "$downloaded_kernel" "$download_url"
        elif command -v wget >/dev/null 2>&1; then
            wget -T 20 -O "$downloaded_kernel" "$download_url"
        else
            echo "curl or wget required for kernel download." >&2; exit 1
        fi
    fi
    validate_kernel "$downloaded_kernel" || exit 1
    # Pin the known-working AMD64 release to the exact user-verified ELF.
    # Explicit custom files remain allowed for controlled local testing,
    # but must still pass ELF checks and the SOCKS5 handshake smoke test.
    if [ -z "${X_TUNNEL_KERNEL_FILE:-}" ] &&
       [ "$release" = "openwrt-stable-go126-v1" ] &&
       [ "$kernel_arch" = "amd64" ]; then
        python3 - "$downloaded_kernel" <<'PY'
import hashlib
import pathlib
import sys
want = "ae3a79a4d2a6a8d7e1377afb2e022c5ed75b0157a694fb6aead9d86ab873ccc2"
digest = hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest()
if digest != want:
    raise SystemExit("[ERROR] Stable AMD64 ELF hash mismatch; refusing to replace existing kernel.")
print("[OK] Stable AMD64 core SHA256 matches original working binary.")
PY
    fi
    chmod 0755 "$downloaded_kernel"
    "$downloaded_kernel" -h >/dev/null 2>&1 || {
        echo "[ERROR] Candidate kernel CLI test failed; existing binary unchanged." >&2; exit 1;
    }
    python3 "$PROJECT_DIR/openwrt/kernel_smoke.py" "$downloaded_kernel" || {
        echo "[ERROR] Candidate SOCKS5 handshake failed; existing binary unchanged." >&2; exit 1;
    }
    if [ "$KERNEL_ALREADY_INSTALLED" -eq 1 ]; then
        backup="$KERNEL_PATH.backup-$(date +%Y%m%d-%H%M%S)-$$"
        cp -p "$KERNEL_PATH" "$backup"
        echo "[INFO] Kernel backup: $backup"
    fi
    copy_with_mode "$downloaded_kernel" "$KERNEL_PATH.new" 0755
    mv -f "$KERNEL_PATH.new" "$KERNEL_PATH"
    echo "[OK] Installed preflight-verified X-Tunnel kernel ($release)."
    rm -rf "$download_dir"
    trap - EXIT HUP INT TERM
fi

[ -f "$PROJECT_DIR/x-tunnel.yaml" ] || {
    echo "Missing x-tunnel.yaml; copy x-tunnel.example.yaml and configure it." >&2
    exit 1
}
chmod 0600 "$PROJECT_DIR/x-tunnel.yaml"
# Migrate legacy project.env and speedtest-bot.env before service restart.
# x-tunnel.yaml remains the only active configuration; sensitive backups are 0600.
if ! (cd "$PROJECT_DIR" && python3 migrate_config.py); then
    echo "[ERROR] Legacy settings migration failed; existing services were not restarted." >&2
    exit 1
fi

if [ "$WITH_SPEEDTEST" -eq 1 ]; then
 python3 -c 'import codecs, ssl, urllib.request; codecs.lookup("idna")' >/dev/null 2>&1 || { echo 'Missing HTTPS/IDNA modules.' >&2; exit 1; }
fi
python3 -c 'import yaml' >/dev/null 2>&1 || {
    if [ "$OPENWRT" -eq 1 ]; then
        echo "Python dependency PyYAML is missing (install python3-yaml with opkg/apk)." >&2
    else
        echo "Python dependency PyYAML is missing (install python3-yaml)." >&2
    fi
    exit 1
}
# Separate Python import failures from invalid node configuration.
# Neither preflight changes running nodes, routes, or Provider files.
if ! (cd "$PROJECT_DIR" && PYTHONPATH="$PROJECT_DIR" python3 -c 'import supervisor'); then
    echo "[ERROR] X-Tunnel supervisor import failed (missing Python dependency)." >&2
    echo "[ERROR] Existing X-Tunnel service was not restarted." >&2
    exit 1
fi
if ! (cd "$PROJECT_DIR" && PYTHONPATH="$PROJECT_DIR" python3 -c 'import supervisor; supervisor.read_specs(check_port_availability=False)'); then
    echo "[ERROR] X-Tunnel configuration preflight failed; inspect x-tunnel.yaml." >&2
    echo "[ERROR] Existing X-Tunnel service was not restarted." >&2
    exit 1
fi
# Fail during installation, not after a Telegram command launches a subprocess.
# Import-only preflight never changes routes, nodes or their CF IP configuration.
if [ "$WITH_SPEEDTEST" -eq 1 ]; then
    echo "Checking CF speedtest Python imports (without starting a test)..."
    if ! (cd "$PROJECT_DIR" && PYTHONPATH="$PROJECT_DIR" python3 -c 'import cfip_speedtest'); then
        echo "[ERROR] CF speedtest imports failed. Review the Python traceback above." >&2
        echo "[ERROR] No CF routes or speedtest processes were started." >&2
        exit 1
    fi
fi
if [ "$WITH_BOT" -eq 1 ]; then
    if ! (cd "$PROJECT_DIR" && python3 -c 'import telegram_speedtest_bot as b; b.read_config()'); then
        echo "[ERROR] Configure telegram.token and telegram.chat_id in x-tunnel.yaml (0600)." >&2
        echo "[ERROR] Existing X-Tunnel service was not restarted." >&2
        exit 1
    fi
fi

mkdir -p "$PROJECT_DIR/runtime" "$PROJECT_DIR/cfip-data"
mkdir -p /run/lock
chmod 0755 "$PROJECT_DIR/runtime"
chmod 0700 "$PROJECT_DIR/cfip-data"

if [ "$OPENWRT" -eq 1 ]; then
    [ -d /etc/init.d ] && [ -f /etc/rc.common ] || {
        echo "OpenWrt procd init system was not found." >&2
        exit 1
    }
    services="x-tunnel"
    [ "$WITH_BOT" -eq 0 ] || services="$services x-tunnel-speedtest-bot"
    # An already running Bot holds the old Python module in memory. After
    # migrating and archiving its .env file, restart that Bot so /topspeed and
    # other commands write to the new YAML rather than recreating project.env.
    BOT_WAS_RUNNING=0
    if [ "$WITH_BOT" -eq 0 ] && [ "$DISABLE_BOT" -eq 0 ] &&
       [ -f /etc/init.d/x-tunnel-speedtest-bot ] &&
       /etc/init.d/x-tunnel-speedtest-bot status >/dev/null 2>&1; then
        if ! (cd "$PROJECT_DIR" && python3 -c 'import telegram_speedtest_bot as b; b.read_config()'); then
            echo "[ERROR] A Bot is running, but unified YAML Bot credentials are invalid; not restarting services." >&2
            exit 1
        fi
        BOT_WAS_RUNNING=1
    fi
    for service in $services; do
        temporary="/etc/init.d/$service.tmp"
        sed "s|@PROJECT_DIR@|$PROJECT_DIR|g" "$PROJECT_DIR/openwrt/$service.init" > "$temporary"
        chmod 0755 "$temporary"
        mv -f "$temporary" "/etc/init.d/$service"
        "/etc/init.d/$service" enable
        "/etc/init.d/$service" stop >/dev/null 2>&1 || true
        "/etc/init.d/$service" start
    done
    if [ "$BOT_WAS_RUNNING" -eq 1 ]; then
        /etc/init.d/x-tunnel-speedtest-bot stop >/dev/null 2>&1 || true
        /etc/init.d/x-tunnel-speedtest-bot start ||
            { echo "[ERROR] Failed to restart existing Bot with migrated YAML credentials." >&2; exit 1; }
        echo "[OK] Existing Telegram Bot restarted with unified YAML settings."
    fi
    if [ "$DISABLE_BOT" -eq 1 ] && [ -f /etc/init.d/x-tunnel-speedtest-bot ]; then
        /etc/init.d/x-tunnel-speedtest-bot stop >/dev/null 2>&1 || true
        /etc/init.d/x-tunnel-speedtest-bot disable >/dev/null 2>&1 || true
    fi
    if [ "$ENABLE_TIMER" -eq 1 ]; then
        sh "$TIMER_SCRIPT" enable "$TIMER_AT"
    fi
    if [ -f "$PROJECT_DIR/menu.sh" ]; then
        if [ -L /usr/bin/x-t ]; then
    echo '/usr/bin/x-t is a symlink; refusing to overwrite it.' >&2
    exit 1
  fi
  if [ -e /usr/bin/x-t ]; then
            grep -Fq "$PROJECT_DIR/menu.sh" /usr/bin/x-t 2>/dev/null || {
                echo "Cannot register x-t: /usr/bin/x-t is already used by another program." >&2
                exit 1
            }
        fi
        printf '#!/bin/sh\nexec /bin/sh "%s/menu.sh" "$@"\n' "$PROJECT_DIR" > /usr/bin/x-t
        chmod 0755 /usr/bin/x-t
        if [ -f /usr/bin/xtunnel-menu ] &&
           grep -Fq "$PROJECT_DIR/menu.sh" /usr/bin/xtunnel-menu 2>/dev/null; then
            rm -f /usr/bin/xtunnel-menu
        fi
    fi
    printf 'Installed X-Tunnel procd services from %s\n' "$PROJECT_DIR"
    printf 'Status: /etc/init.d/x-tunnel status; logread -e x-tunnel\n'
    if [ "$ENABLE_TIMER" -eq 0 ]; then
        printf 'Daily CF speedtest schedule unchanged; use --enable-timer or --timer-at=HH:MM to configure.\n'
    fi
fi
