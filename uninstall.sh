#!/bin/sh
# Remove only X-Tunnel-managed LXC/systemd resources, never ShellCrash,
# sing-box or unrelated unit files. See --dry-run before --purge.
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
UNIT_DIR="${XTUNNEL_UNIT_DIR:-/etc/systemd/system}"
LOCAL_BIN="${XTUNNEL_LOCAL_BIN_DIR:-/usr/local/bin}"
USR_BIN="${XTUNNEL_USR_BIN_DIR:-/usr/bin}"
KERNEL_PATH="${XTUNNEL_KERNEL_PATH:-/usr/bin/x-tunnel}"
LOCK_FILE="${XTUNNEL_LOCK_FILE:-/run/lock/x-tunnel-speedtest-bot.lock}"
MARKER="$PROJECT_DIR/runtime/managed-kernel.sha256"

PURGE=0
REMOVE_KERNEL=0
DRY_RUN=0
usage() {
    printf 'Usage: sh uninstall.sh [--dry-run] [--purge [--remove-kernel]]\n'
    printf '  default         Remove X-Tunnel services, timer, managed drop-ins and shortcuts.\n'
    printf '                  Keep private YAML, history, project and shared Go binary.\n'
    printf '  --dry-run       Preview changes without modifying service or files.\n'
    printf '  --purge         Also delete project directory, backups and history (confirm DELETE).\n'
    printf '  --remove-kernel With --purge only, require matching project-install SHA256 marker.\n'
}
fail() { printf 'UNINSTALL REFUSED: %s\n' "$*" >&2; exit 1; }
info() { printf '%s\n' "$*"; }

for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        --purge) PURGE=1 ;;
        --remove-kernel) REMOVE_KERNEL=1 ;;
        --help|-h) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done
[ "$REMOVE_KERNEL" -eq 0 ] || [ "$PURGE" -eq 1 ] ||
    fail '--remove-kernel requires explicit --purge'
[ "$DRY_RUN" -eq 1 ] || [ "$(id -u)" -eq 0 ] || fail 'Run as root.'
case "$PROJECT_DIR" in
    /|/etc|/usr|/bin|/sbin|/opt|/root|/overlay|/tmp|/var|/home|/srv)
        fail 'Refusing an unsafe project root directory.' ;;
esac
if [ "$PURGE" -eq 1 ]; then
    case "$PROJECT_DIR" in
        /opt/*|/root/*|/home/*|/srv/*) ;;
        *) fail '--purge only supports an identifiable project directory under /opt, /root, /home or /srv' ;;
    esac
fi

[ -f "$PROJECT_DIR/systemd/ownership.sh" ] ||
    fail 'Missing systemd/ownership.sh; use updated lxc branch uninstall script.'
. "$PROJECT_DIR/systemd/ownership.sh"

UNITS='x-tunnel-speedtest.timer x-tunnel-speedtest-bot.service x-tunnel-speedtest-single@.service x-tunnel-speedtest.service x-tunnel.service'
# Inspect ALL assets for ownership before changing a single service or file.
# Legacy unit files without the new marker are recognised by exact ExecStart
# and WorkingDirectory paths (or timer Unit+Description).
for unit in $UNITS; do
    file="$UNIT_DIR/$unit"
    if [ -e "$file" ] || [ -L "$file" ]; then
        xt_unit_owned "$PROJECT_DIR" "$UNIT_DIR" "$unit" ||
            fail "Unknown or foreign systemd unit $file; refusing to delete or stop it."
    fi
done
for unit in x-tunnel.service x-tunnel-speedtest.service x-tunnel-speedtest-single@.service; do
    file="$UNIT_DIR/$unit.d/shellcrash-bypass.conf"
    if [ -e "$file" ] || [ -L "$file" ]; then
        xt_bypass_owned "$file" ||
            fail "Unrecognised systemd drop-in $file; refusing to delete it."
    fi
done
TIMER_OVERRIDE="$UNIT_DIR/x-tunnel-speedtest.timer.d/50-xtunnel-schedule.conf"
if [ -e "$TIMER_OVERRIDE" ] || [ -L "$TIMER_OVERRIDE" ]; then
    xt_timer_override_owned "$TIMER_OVERRIDE" ||
        fail "Unrecognised timer override $TIMER_OVERRIDE; refusing to delete it."
fi

owned_kernel() {
    [ -f "$MARKER" ] && [ ! -L "$MARKER" ] ||
        fail 'No kernel ownership marker; refusing to delete shared /usr/bin/x-tunnel.'
    [ -f "$KERNEL_PATH" ] && [ ! -L "$KERNEL_PATH" ] ||
        fail 'Kernel binary is missing, a symlink or not a regular file; refusing removal.'
    command -v sha256sum >/dev/null 2>&1 ||
        fail 'sha256sum needed to verify kernel ownership.'
    recorded=$(cat "$MARKER")
    current=$(sha256sum "$KERNEL_PATH" | awk '{print $1}')
    [ "$recorded" = "$current  $KERNEL_PATH" ] ||
        fail 'Kernel binary SHA256 differs from installer record; refusing removal.'
    if command -v dpkg-query >/dev/null 2>&1 &&
       dpkg-query -S "$KERNEL_PATH" >/dev/null 2>&1; then
        fail 'Kernel binary is owned by a system package; refusing removal.'
    fi
}
if [ "$REMOVE_KERNEL" -eq 1 ]; then owned_kernel; fi

if [ "$DRY_RUN" -eq 1 ]; then
    info "DRY RUN: X-Tunnel project $PROJECT_DIR"
    for unit in $UNITS; do
        [ ! -f "$UNIT_DIR/$unit" ] || info "Would stop/disable and delete $UNIT_DIR/$unit"
    done
    for unit in x-tunnel.service x-tunnel-speedtest.service x-tunnel-speedtest-single@.service; do
        file="$UNIT_DIR/$unit.d/shellcrash-bypass.conf"
        [ ! -e "$file" ] || info "Would delete managed group drop-in $file"
    done
    [ ! -e "$TIMER_OVERRIDE" ] || info "Would delete managed timer override $TIMER_OVERRIDE"
    for launcher in "$LOCAL_BIN/x-t" "$LOCAL_BIN/xtunnel-menu" "$USR_BIN/xtunnel-menu"; do
        xt_launcher_owned "$PROJECT_DIR" "$launcher" &&
            info "Would delete owned shortcut $launcher" || true
    done
    [ "$PURGE" -eq 0 ] || info "Would permanently delete project tree $PROJECT_DIR"
    if [ "$REMOVE_KERNEL" -eq 1 ]; then
        info "Would remove installer-owned X-Tunnel binary $KERNEL_PATH"
    else
        info "Would retain shared X-Tunnel binary $KERNEL_PATH"
    fi
    info 'No changes performed.'
    exit 0
fi

# Confirm all destructive actions BEFORE stopping or disabling any service.
if [ "$PURGE" -eq 1 ]; then
    info "WARNING: this removes the entire $PROJECT_DIR directory, including"
    info 'YAML, private Token, cfip-data, database, backups and optional .git checkout.'
    info "Shared binary remains $KERNEL_PATH unless --remove-kernel was chosen."
    printf 'Type DELETE to confirm purge: '
    IFS= read -r answer </dev/tty || fail 'No interactive terminal for purge confirmation.'
    [ "$answer" = DELETE ] || fail 'Purge cancelled; nothing was changed.'
fi

command -v systemctl >/dev/null 2>&1 ||
    fail 'systemctl is required on Debian/Ubuntu LXC to remove managed services.'

# Disable/stop the timer BEFORE enumerating transient workers, otherwise
# the timer could schedule new tests while we remove existing instances.
if [ -f "$UNIT_DIR/x-tunnel-speedtest.timer" ]; then
    if ! systemctl stop x-tunnel-speedtest.timer >/dev/null 2>&1; then
        systemctl is-active --quiet x-tunnel-speedtest.timer &&
            fail 'Cannot stop scheduled speedtest timer.'
    fi
    systemctl disable x-tunnel-speedtest.timer >/dev/null 2>&1 || true
fi
# A template (@.service) is not an instance. NEVER pass it to
# systemctl stop/disable: systemd rejects that unit name. Enumerate real
# instances in both loaded units and unit-file registrations (the latter
# catches enabled-but-currently-inactive instances).
if [ -f "$UNIT_DIR/x-tunnel-speedtest-single@.service" ]; then
    loaded_instances=$(systemctl list-units --all --no-legend --no-pager \
        --type=service 'x-tunnel-speedtest-single@*.service') ||
        fail 'Cannot enumerate loaded single-IP workers; refusing incomplete uninstall.'
    registered_instances=$(systemctl list-unit-files --all --no-legend --no-pager \
        'x-tunnel-speedtest-single@*.service') ||
        fail 'Cannot enumerate registered single-IP workers; refusing incomplete uninstall.'
    instances=$(printf '%s\n%s\n' "$loaded_instances" "$registered_instances" |
        awk '{for(i=1;i<=NF;i++) if ($i ~ /^x-tunnel-speedtest-single@[^@[:space:]]+\.service$/) print $i}' |
        sort -u)
    for instance in $instances; do
        if ! systemctl stop "$instance" >/dev/null 2>&1; then
            systemctl is-active --quiet "$instance" &&
                fail "Cannot stop running $instance; refusing incomplete uninstall."
        fi
        if systemctl is-enabled --quiet "$instance" 2>/dev/null; then
            systemctl disable "$instance" ||
                fail "Cannot disable enabled $instance; refusing incomplete uninstall."
        fi
    done
fi
for unit in $UNITS; do
    case "$unit" in
        x-tunnel-speedtest.timer) continue ;; # already stopped and disabled
        x-tunnel-speedtest-single@.service) continue ;; # template; never stop/disable
    esac
    [ -f "$UNIT_DIR/$unit" ] || continue
    if ! systemctl stop "$unit" >/dev/null 2>&1; then
        if systemctl is-active --quiet "$unit"; then
            fail "Cannot stop $unit; refusing to remove its unit file."
        fi
    fi
    systemctl disable "$unit" >/dev/null 2>&1 || true
done
# Once all owned units are stopped, do not remove our core if some OTHER
# program is still using the shared Go binary.
if [ "$REMOVE_KERNEL" -eq 1 ]; then
    command -v pidof >/dev/null 2>&1 ||
        fail 'pidof is required to confirm no shared X-Tunnel process is running.'
    if pidof x-tunnel >/dev/null 2>&1; then
        fail 'Another x-tunnel process still runs; refusing to delete shared binary.'
    fi
    owned_kernel
fi

for unit in $UNITS; do
    [ ! -f "$UNIT_DIR/$unit" ] || rm -f "$UNIT_DIR/$unit"
done
for unit in x-tunnel.service x-tunnel-speedtest.service x-tunnel-speedtest-single@.service; do
    directory="$UNIT_DIR/$unit.d"
    file="$directory/shellcrash-bypass.conf"
    [ ! -e "$file" ] || rm -f "$file"
    rmdir "$directory" >/dev/null 2>&1 || true
done
[ ! -e "$TIMER_OVERRIDE" ] || rm -f "$TIMER_OVERRIDE"
rmdir "$UNIT_DIR/x-tunnel-speedtest.timer.d" >/dev/null 2>&1 || true

systemctl daemon-reload ||
    fail 'daemon-reload failed; inspect systemd and retry uninstall.'
for launcher in "$LOCAL_BIN/x-t" "$LOCAL_BIN/xtunnel-menu" "$USR_BIN/xtunnel-menu"; do
    xt_launcher_owned "$PROJECT_DIR" "$launcher" && rm -f "$launcher" || true
done
# The runtime lock file belongs to this Bot and is only removed after stop.
[ ! -f "$LOCK_FILE" ] || rm -f "$LOCK_FILE"

if [ "$REMOVE_KERNEL" -eq 1 ]; then
    rm -f "$KERNEL_PATH"
    info "Removed verified project-owned Go kernel $KERNEL_PATH"
fi
info 'X-Tunnel service units, timer, actual single-IP worker instances and owned shortcuts removed.'
if [ "$PURGE" -eq 1 ]; then
    cd /
    rm -rf "$PROJECT_DIR"
    info 'X-Tunnel project files, private YAML, backups and history purged.'
else
    info "Retained $PROJECT_DIR (x-tunnel.yaml, cfip-data and history)."
fi
if [ "$REMOVE_KERNEL" -eq 0 ]; then
    info "Retained shared binary $KERNEL_PATH (not proven exclusive to X-Tunnel)."
fi
info 'Did not modify ShellCrash, sing-box, other firewall rules or system packages.'
