#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
ENABLE_TIMER=0
WITH_BOT=0
WITH_SPEEDTEST=0
DISABLE_BOT=0
OPENWRT=0
PLATFORM=openwrt
KERNEL_PATH=/usr/bin/x-tunnel
KERNEL_REPOSITORY=caichengle666/x-tunnel

if [ -f /etc/openwrt_release ] && [ -f /etc/rc.common ] && [ -x /sbin/procd ]; then
    OPENWRT=1
else
    echo "OpenWrt/procd required. Debian and Ubuntu should use the main branch." >&2
    exit 1
fi

for argument in "$@"; do
    case "$argument" in
        --enable-timer) ENABLE_TIMER=1; WITH_SPEEDTEST=1 ;;
        --with-bot) WITH_BOT=1; WITH_SPEEDTEST=1 ;;
        --with-speedtest) WITH_SPEEDTEST=1 ;;
        --disable-bot) DISABLE_BOT=1 ;;
        --help|-h) echo "Usage: $0 [--with-bot] [--with-speedtest] [--enable-timer] [--disable-bot]"; exit 0 ;;
        *) echo "Invalid option. Use --help." >&2; exit 2 ;;
    esac
done

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this installer as root." >&2
    exit 1
fi

[ "$WITH_BOT" -eq 0 ] || [ "$DISABLE_BOT" -eq 0 ] || { echo "Cannot enable and disable Bot together." >&2; exit 2; }
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

if [ -e "$KERNEL_PATH" ] || [ -L "$KERNEL_PATH" ]; then
    [ -f "$KERNEL_PATH" ] || {
        echo "$KERNEL_PATH exists but is not a regular file; move it aside before installing." >&2
        exit 1
    }
    validate_kernel "$KERNEL_PATH" || {
        echo "Existing $KERNEL_PATH is invalid or for a different architecture; it was not replaced." >&2
        exit 1
    }
    chmod 0755 "$KERNEL_PATH"
    echo "Using existing x-tunnel kernel ($machine); permissions set to 0755."
else
    release=${X_TUNNEL_KERNEL_VERSION:-latest}
    asset="x-tunnel-linux-$kernel_arch"
    case "$release" in
        latest) download_url="https://github.com/$KERNEL_REPOSITORY/releases/latest/download/$asset" ;;
        *[!A-Za-z0-9._-]*|"")
            echo "Invalid X_TUNNEL_KERNEL_VERSION: $release" >&2
            exit 1
            ;;
        *) download_url="https://github.com/$KERNEL_REPOSITORY/releases/download/$release/$asset" ;;
    esac
    download_dir=$(mktemp -d "${TMPDIR:-/tmp}/x-tunnel-kernel.XXXXXX")
    trap 'rm -rf "$download_dir"' EXIT HUP INT TERM
    downloaded_kernel="$download_dir/$asset"
    if command -v curl >/dev/null 2>&1; then
        curl --fail --location --retry 3 --connect-timeout 20 "$download_url" --output "$downloaded_kernel"
    elif command -v wget >/dev/null 2>&1; then
        wget -T 20 -O "$downloaded_kernel" "$download_url"
    else
        echo "Install curl or wget to download the x-tunnel kernel." >&2
        exit 1
    fi
    validate_kernel "$downloaded_kernel"
    chmod 0755 "$downloaded_kernel"
    "$downloaded_kernel" -h >/dev/null 2>&1 || {
        echo "Downloaded x-tunnel kernel failed its startup check; it was not installed." >&2
        exit 1
    }
    copy_with_mode "$downloaded_kernel" "$KERNEL_PATH.new" 0755
    mv -f "$KERNEL_PATH.new" "$KERNEL_PATH"
    echo "Installed x-tunnel kernel for $machine from $KERNEL_REPOSITORY ($release)."
    rm -rf "$download_dir"
    trap - EXIT HUP INT TERM
fi
[ -f "$PROJECT_DIR/x-tunnel.yaml" ] || {
    echo "Missing $PROJECT_DIR/x-tunnel.yaml; copy x-tunnel.example.yaml and configure it." >&2
    exit 1
}
if [ "$WITH_BOT" -eq 1 ]; then
 if [ ! -f "$PROJECT_DIR/speedtest-bot.env" ]; then
  copy_with_mode "$PROJECT_DIR/speedtest-bot.env.example" "$PROJECT_DIR/speedtest-bot.env" 0600
  echo 'Edit speedtest-bot.env then rerun --with-bot.' >&2
  exit 1
 fi
 chmod 0600 "$PROJECT_DIR/speedtest-bot.env"
fi
if [ ! -f "$PROJECT_DIR/project.env" ]; then
    copy_with_mode "$PROJECT_DIR/project.env.example" "$PROJECT_DIR/project.env" 0644
fi
chmod 0600 "$PROJECT_DIR/x-tunnel.yaml"
chmod 0644 "$PROJECT_DIR/project.env"

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
    echo "[ERROR] X-Tunnel configuration preflight failed; inspect x-tunnel.yaml and project.env." >&2
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
python3 - "$PROJECT_DIR/speedtest-bot.env" <<'PY'
import pathlib
import shlex
import sys

values = {}
for line in pathlib.Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    key, separator, value = line.partition("=")
    if separator:
        parsed = shlex.split(value, comments=True)
        values[key.strip()] = parsed[0] if parsed else ""
if not values.get("TG_TOKEN") or "替换为" in values["TG_TOKEN"]:
    raise SystemExit("Set a real TG_TOKEN in speedtest-bot.env first.")
if not values.get("TG_CHATID", "").isdecimal():
    raise SystemExit("Set the numeric private TG_CHATID in speedtest-bot.env first.")
PY
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
    for service in $services; do
        temporary="/etc/init.d/$service.tmp"
        sed "s|@PROJECT_DIR@|$PROJECT_DIR|g" "$PROJECT_DIR/openwrt/$service.init" > "$temporary"
        chmod 0755 "$temporary"
        mv -f "$temporary" "/etc/init.d/$service"
        "/etc/init.d/$service" enable
        "/etc/init.d/$service" stop >/dev/null 2>&1 || true
        "/etc/init.d/$service" start
    done
    if [ "$DISABLE_BOT" -eq 1 ] && [ -f /etc/init.d/x-tunnel-speedtest-bot ]; then
        /etc/init.d/x-tunnel-speedtest-bot stop >/dev/null 2>&1 || true
        /etc/init.d/x-tunnel-speedtest-bot disable >/dev/null 2>&1 || true
    fi
    if [ "$ENABLE_TIMER" -eq 1 ]; then
        cron_entry="0 18 * * * /bin/sh '$PROJECT_DIR/openwrt/run-speedtest.sh' >> '$PROJECT_DIR/cfip-data/cron.log' 2>&1"
        cron_marker="# x-tunnel-cf-ip-autotest daily speedtest"
        mkdir -p /etc/crontabs
        touch /etc/crontabs/root
        if ! grep -Fq "$cron_marker" /etc/crontabs/root 2>/dev/null; then
            printf '%s\n%s\n' "$cron_marker" "$cron_entry" >> /etc/crontabs/root
        fi
        /etc/init.d/cron enable
        /etc/init.d/cron start
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
        printf 'Daily 18:00 speed test unchanged; install with --enable-timer to add the cron job.\n'
    fi
fi
