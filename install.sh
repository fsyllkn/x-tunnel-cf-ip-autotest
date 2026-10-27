#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
ENABLE_TIMER=0
TIMER_AT=''
WITH_BOT=0
WITH_SPEEDTEST=0
DISABLE_BOT=0
REPAIR_KERNEL=0
OPENWRT=0
PLATFORM=systemd
KERNEL_PATH=/usr/bin/x-tunnel
KERNEL_REPOSITORY=fsyllkn/x-tunnel
KERNEL_DEFAULT_VERSION=openwrt-stable-go126-v1

if [ -f /etc/openwrt_release ] || {
    [ -f /etc/rc.common ] && [ -x /sbin/procd ]
}; then
    OPENWRT=1
    PLATFORM=openwrt
elif [ ! -d /run/systemd/system ] || ! command -v systemctl >/dev/null 2>&1; then
    echo "Unsupported init system: expected OpenWrt procd or systemd." >&2
    exit 1
fi

for argument in "$@"; do
    case "$argument" in
        --enable-timer) ENABLE_TIMER=1; WITH_SPEEDTEST=1 ;;
        --timer-at=*) ENABLE_TIMER=1; WITH_SPEEDTEST=1; TIMER_AT=${argument#*=} ;;
        --with-bot) WITH_BOT=1; WITH_SPEEDTEST=1 ;;
        --with-speedtest) WITH_SPEEDTEST=1 ;;
        --disable-bot) DISABLE_BOT=1 ;;
        --repair-kernel) REPAIR_KERNEL=1 ;;
        --help|-h) echo "Usage: $0 [--with-bot] [--with-speedtest] [--enable-timer|--timer-at=HH:MM] [--disable-bot] [--repair-kernel]"; exit 0 ;;
        *) echo "Invalid option: $argument. Use --help." >&2; exit 2 ;;
    esac
done

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this installer as root." >&2
    exit 1
fi

[ "$WITH_BOT" -eq 0 ] || [ "$DISABLE_BOT" -eq 0 ] || { echo "Cannot enable and disable Bot together." >&2; exit 2; }
printf 'Detected platform: %s; Bot: %s; speedtest: %s\n' "$PLATFORM" "$WITH_BOT" "$WITH_SPEEDTEST"
if [ "$OPENWRT" -eq 0 ]; then
    if ! command -v python3 >/dev/null 2>&1 ||
       ! python3 -c 'import yaml, ssl, urllib.request, hashlib' >/dev/null 2>&1 ||
       ! command -v ip >/dev/null 2>&1 ||
       { ! command -v curl >/dev/null 2>&1 && ! command -v wget >/dev/null 2>&1; }; then
        command -v apt-get >/dev/null 2>&1 || {
            echo 'apt-get required to install Debian/Ubuntu dependencies.' >&2; exit 1;
        }
        apt-get update &&
            DEBIAN_FRONTEND=noninteractive apt-get install -y                 python3 python3-yaml iproute2 curl ca-certificates ||
            { echo 'Could not install required Debian/Ubuntu packages.' >&2; exit 1; }
    fi
fi
python3 -c 'import sys; assert sys.version_info >= (3, 9), "Python 3.9+ required for dedicated child UID isolation"' || exit 1

# Schedule validation needs Python; install apt dependencies before invoking it.
if [ "$OPENWRT" -eq 0 ] && [ "$ENABLE_TIMER" -eq 1 ]; then
    TIMER_CLI="$PROJECT_DIR/systemd/speedtest-timer.py"
    [ -f "$TIMER_CLI" ] || { echo "Missing systemd timer manager." >&2; exit 1; }
    if [ -n "$TIMER_AT" ]; then
        python3 "$TIMER_CLI" validate "$TIMER_AT" || exit 2
    else
        TIMER_AT=$(python3 "$TIMER_CLI" saved)
    fi
fi

required_commands="ip sed cp chmod mkdir mv python3 uname"
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

KERNEL_INSTALLED=0
if [ -e "$KERNEL_PATH" ] || [ -L "$KERNEL_PATH" ]; then
    [ -f "$KERNEL_PATH" ] && [ ! -L "$KERNEL_PATH" ] || {
        echo "$KERNEL_PATH is a symlink or nonregular file; refusing replacement." >&2
        exit 1
    }
    KERNEL_INSTALLED=1
fi
if [ "$KERNEL_INSTALLED" -eq 1 ] && [ "$REPAIR_KERNEL" -eq 0 ]; then
    validate_kernel "$KERNEL_PATH" || {
        echo "Existing $KERNEL_PATH is invalid; use explicit --repair-kernel to replace." >&2
        exit 1
    }
    chmod 0755 "$KERNEL_PATH"
    echo "Using existing X-Tunnel kernel ($machine); leaving binary unchanged."
else
    release=${X_TUNNEL_KERNEL_VERSION:-$KERNEL_DEFAULT_VERSION}
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
    if [ "$release" = "$KERNEL_DEFAULT_VERSION" ] && [ "$kernel_arch" = amd64 ]; then
        python3 - "$downloaded_kernel" <<'PY'
import hashlib, pathlib, sys
expected = "ae3a79a4d2a6a8d7e1377afb2e022c5ed75b0157a694fb6aead9d86ab873ccc2"
actual = hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest()
if actual != expected:
    raise SystemExit("X-Tunnel stable AMD64 SHA256 mismatch; binary not installed")
PY
    fi
    "$downloaded_kernel" -h >/dev/null 2>&1 || {
        echo "Downloaded x-tunnel kernel failed its startup check; it was not installed." >&2
        exit 1
    }
    if [ -f "$PROJECT_DIR/openwrt/kernel_smoke.py" ]; then
        python3 "$PROJECT_DIR/openwrt/kernel_smoke.py" "$downloaded_kernel" ||
            { echo "X-Tunnel SOCKS5 preflight failed; binary not installed." >&2; exit 1; }
    fi
    if [ "$KERNEL_INSTALLED" -eq 1 ]; then
        backup="$KERNEL_PATH.backup-$(date +%Y%m%d-%H%M%S)-$"
        cp -p "$KERNEL_PATH" "$backup" || { echo 'Kernel backup failed; original left unchanged.' >&2; exit 1; }
        echo "Saved previous X-Tunnel binary to $backup"
    fi
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
chmod 0600 "$PROJECT_DIR/x-tunnel.yaml"
if ! (cd "$PROJECT_DIR" && python3 migrate_config.py); then
    echo "[ERROR] YAML migration failed; running services were not restarted." >&2
    exit 1
fi

# OpenWrt's python3-light may omit standard library IDNA codecs.
# urllib's HTTPS proxy tunnel setup requires this codec.
if [ "$WITH_SPEEDTEST" -eq 1 ]; then
python3 -c 'import codecs; codecs.lookup("idna")' >/dev/null 2>&1 || {
    if [ "$OPENWRT" -eq 1 ]; then
        echo "Python IDNA codec is missing. Install python3-codecs using apk add or opkg install." >&2
    else
        echo "Python IDNA codec is missing. Install the complete Python 3 standard library." >&2
    fi
    exit 1
}

fi
python3 -c 'import yaml' >/dev/null 2>&1 || {
    if [ "$OPENWRT" -eq 1 ]; then
        echo "Python dependency PyYAML is missing (install python3-yaml with opkg/apk)." >&2
    else
        echo "Python dependency PyYAML is missing (install python3-yaml)." >&2
    fi
    exit 1
}
PYTHONPATH="$PROJECT_DIR" python3 -c 'import supervisor; supervisor.read_specs(check_port_availability=False); import project_settings; project_settings.get_target_mbps(); project_settings.read_telegram_credentials(require_chat_id=False)' || {
    echo "Invalid x-tunnel.yaml configuration." >&2
    exit 1
}
if [ "$WITH_SPEEDTEST" -eq 1 ]; then
    (cd "$PROJECT_DIR" && python3 -c 'import cfip_speedtest') || {
        echo "[ERROR] CF speedtest dependencies failed preflight; services unchanged." >&2
        exit 1
    }
fi
if [ "$WITH_BOT" -eq 1 ]; then
    (cd "$PROJECT_DIR" && python3 -c 'import telegram_speedtest_bot as b; b.read_config()') || {
        echo "[ERROR] Set telegram.token and telegram.chat_id in x-tunnel.yaml." >&2
        exit 1
    }
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
    for service in x-tunnel x-tunnel-speedtest-bot; do
        temporary="/etc/init.d/$service.tmp"
        sed "s|@PROJECT_DIR@|$PROJECT_DIR|g" "$PROJECT_DIR/openwrt/$service.init" > "$temporary"
        chmod 0755 "$temporary"
        mv -f "$temporary" "/etc/init.d/$service"
        "/etc/init.d/$service" enable
        "/etc/init.d/$service" stop >/dev/null 2>&1 || true
        "/etc/init.d/$service" start
    done
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
        printf '#!/bin/sh\nexec /bin/sh "%s/menu.sh" "$@"\n' "$PROJECT_DIR" > /usr/bin/xtunnel-menu
        chmod 0755 /usr/bin/xtunnel-menu
    fi
    printf 'Installed X-Tunnel procd services from %s\n' "$PROJECT_DIR"
    printf 'Status: /etc/init.d/x-tunnel status; logread -e x-tunnel\n'
    if [ "$ENABLE_TIMER" -eq 0 ]; then
        printf 'Daily 18:00 speed test unchanged; install with --enable-timer to add the cron job.\n'
    fi
else
    for unit in x-tunnel.service x-tunnel-speedtest.service x-tunnel-speedtest-bot.service x-tunnel-speedtest.timer x-tunnel-speedtest-single@.service; do
        temporary="/etc/systemd/system/$unit.tmp"
        sed "s|@PROJECT_DIR@|$PROJECT_DIR|g" "$PROJECT_DIR/systemd/$unit" > "$temporary"
        chmod 0644 "$temporary"
        mv -f "$temporary" "/etc/systemd/system/$unit"
    done

    bypass_units="x-tunnel.service x-tunnel-speedtest.service x-tunnel-speedtest-single@.service"
    if [ -r /etc/group ] && awk -F: '$1 == "shellcrash" && $3 == 7890 { found = 1 } END { exit !found }' /etc/group; then
        for unit in $bypass_units; do
            dropin_dir="/etc/systemd/system/$unit.d"
            temporary="$dropin_dir/shellcrash-bypass.conf.tmp"
            mkdir -p "$dropin_dir"
            chmod 0755 "$dropin_dir"
            printf '%s\n' '[Service]' 'Group=7890' > "$temporary"
            chmod 0644 "$temporary"
            mv -f "$temporary" "$dropin_dir/shellcrash-bypass.conf"
        done
        printf 'Detected ShellCrash bypass group (GID 7890); configured X-Tunnel and speed-test services to use it.\n'
    else
        for unit in $bypass_units; do
            dropin="/etc/systemd/system/$unit.d/shellcrash-bypass.conf"
            rm -f "$dropin" "$dropin.tmp"
        done
        printf 'ShellCrash bypass group (GID 7890) not found; X-Tunnel services keep the default group.\n'
    fi

    systemctl daemon-reload
    systemctl enable x-tunnel.service
    running_units="x-tunnel.service"
    if [ "$WITH_BOT" -eq 1 ]; then
        systemctl enable x-tunnel-speedtest-bot.service
        running_units="$running_units x-tunnel-speedtest-bot.service"
    fi
    for unit in $running_units; do
        if systemctl is-active --quiet "$unit"; then
            systemctl restart "$unit"
        else
            systemctl start "$unit"
        fi
    done

    if [ "$WITH_BOT" -eq 0 ] && [ "$DISABLE_BOT" -eq 0 ] &&
       systemctl is-active --quiet x-tunnel-speedtest-bot.service; then
        # Running Bot must reload YAML credentials after an .env migration.
        if ! (cd "$PROJECT_DIR" && python3 -c 'import telegram_speedtest_bot as b; b.read_config()'); then
            echo "[ERROR] Existing Bot credentials invalid; stop Bot or configure YAML." >&2
            exit 1
        fi
        systemctl restart x-tunnel-speedtest-bot.service
    fi
    if [ "$DISABLE_BOT" -eq 1 ]; then
        systemctl disable --now x-tunnel-speedtest-bot.service >/dev/null 2>&1 || true
    fi
    if [ "$ENABLE_TIMER" -eq 1 ]; then
        python3 "$TIMER_CLI" enable "$TIMER_AT"
    fi

    if [ -f "$PROJECT_DIR/menu.sh" ]; then
        mkdir -p /usr/local/bin
        printf '#!/bin/sh\nexec /bin/sh "%s/menu.sh" "$@"\n' "$PROJECT_DIR" > /usr/local/bin/xtunnel-menu
        chmod 0755 /usr/local/bin/xtunnel-menu
    fi
    printf 'Installed X-Tunnel services from %s\n' "$PROJECT_DIR"
    printf 'Status: systemctl status x-tunnel.service x-tunnel-speedtest-bot.service\n'
    if [ "$ENABLE_TIMER" -eq 0 ]; then
        printf 'Daily timer unchanged; run: python3 %s/systemd/speedtest-timer.py enable [HH:MM]\n' "$PROJECT_DIR"
    fi
fi
