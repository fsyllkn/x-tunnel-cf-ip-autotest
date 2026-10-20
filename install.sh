#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
ENABLE_TIMER=0
OPENWRT=0
PLATFORM=systemd
KERNEL_PATH=/usr/bin/x-tunnel
KERNEL_REPOSITORY=caichengle666/x-tunnel

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
        --enable-timer) ENABLE_TIMER=1 ;;
        *) echo "Usage: $0 [--enable-timer]" >&2; exit 2 ;;
    esac
done

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this installer as root." >&2
    exit 1
fi

printf 'Detected platform: %s\n' "$PLATFORM"

required_commands="ip sed install python3 uname"
if [ "$OPENWRT" -eq 0 ]; then
    required_commands="$required_commands systemctl"
fi
for command in $required_commands; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Required command not found: $command" >&2
        exit 1
    }
done

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
        wget --tries=3 --timeout=20 --output-document="$downloaded_kernel" "$download_url"
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
    install -m 0755 "$downloaded_kernel" "$KERNEL_PATH.new"
    mv -f "$KERNEL_PATH.new" "$KERNEL_PATH"
    echo "Installed x-tunnel kernel for $machine from $KERNEL_REPOSITORY ($release)."
    rm -rf "$download_dir"
    trap - EXIT HUP INT TERM
fi
[ -f "$PROJECT_DIR/x-tunnel.yaml" ] || {
    echo "Missing $PROJECT_DIR/x-tunnel.yaml; copy x-tunnel.example.yaml and configure it." >&2
    exit 1
}
[ -f "$PROJECT_DIR/speedtest-bot.env" ] || {
    install -m 0600 "$PROJECT_DIR/speedtest-bot.env.example" "$PROJECT_DIR/speedtest-bot.env"
    echo "Created speedtest-bot.env; set TG_TOKEN and TG_CHATID, then rerun." >&2
    exit 1
}
if [ ! -f "$PROJECT_DIR/project.env" ]; then
    install -m 0644 "$PROJECT_DIR/project.env.example" "$PROJECT_DIR/project.env"
fi
chmod 0600 "$PROJECT_DIR/speedtest-bot.env" "$PROJECT_DIR/x-tunnel.yaml"
chmod 0644 "$PROJECT_DIR/project.env"

python3 -c 'import yaml' >/dev/null 2>&1 || {
    if [ "$OPENWRT" -eq 1 ]; then
        echo "Python dependency PyYAML is missing (install python3-yaml with opkg/apk)." >&2
    else
        echo "Python dependency PyYAML is missing (install python3-yaml)." >&2
    fi
    exit 1
}
PYTHONPATH="$PROJECT_DIR" python3 -c 'import supervisor; supervisor.read_specs(check_port_availability=False)' || {
    echo "Invalid x-tunnel.yaml configuration." >&2
    exit 1
}
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
            install -d -m 0755 "$dropin_dir"
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
    systemctl enable x-tunnel.service x-tunnel-speedtest-bot.service
    for unit in x-tunnel.service x-tunnel-speedtest-bot.service; do
        if systemctl is-active --quiet "$unit"; then
            systemctl restart "$unit"
        else
            systemctl start "$unit"
        fi
    done

    if [ "$ENABLE_TIMER" -eq 1 ]; then
        systemctl enable --now x-tunnel-speedtest.timer
    fi

    printf 'Installed X-Tunnel services from %s\n' "$PROJECT_DIR"
    printf 'Status: systemctl status x-tunnel.service x-tunnel-speedtest-bot.service\n'
    if [ "$ENABLE_TIMER" -eq 0 ]; then
        printf 'Daily 18:00 (Asia/Shanghai) timer unchanged; enable with: systemctl enable --now x-tunnel-speedtest.timer\n'
    fi
fi
