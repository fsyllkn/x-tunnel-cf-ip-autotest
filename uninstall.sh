#!/bin/sh
# Uninstall X-Tunnel services without touching ShellCrash or shared binaries.
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
PURGE=0
case "${1:-}" in
    '') ;;
    --purge) PURGE=1 ;;
    *) printf 'Usage: sh uninstall.sh [--purge]\n' >&2; exit 2 ;;
esac
[ "$(id -u)" -eq 0 ] || { echo 'Run as root.' >&2; exit 1; }

if [ -f /etc/openwrt_release ]; then
    for service in x-tunnel-speedtest-bot x-tunnel; do
        svc=/etc/init.d/$service
        if [ -f "$svc" ]; then
            "$svc" stop >/dev/null 2>&1 || true
            "$svc" disable >/dev/null 2>&1 || true
            rm -f "$svc"
        fi
    done
    if [ -f /etc/crontabs/root ]; then
        tmp=/etc/crontabs/root.xtunnel-tmp.$$
        awk '
            $0 == "# x-tunnel-cf-ip-autotest daily speedtest" { skip=1; next }
            skip == 1 && /openwrt\/run-speedtest\.sh/ { skip=0; next }
            { skip=0; print }
        ' /etc/crontabs/root > "$tmp"
        cat "$tmp" > /etc/crontabs/root
        rm -f "$tmp"
        if [ -x /etc/init.d/cron ]; then /etc/init.d/cron restart >/dev/null 2>&1 || true; fi
    fi
else
    command -v systemctl >/dev/null 2>&1 || { echo 'No supported service manager found.' >&2; exit 1; }
    for unit in x-tunnel-speedtest-bot.service x-tunnel.service x-tunnel-speedtest.timer x-tunnel-speedtest.service x-tunnel-speedtest-single@.service; do
        systemctl disable --now "$unit" >/dev/null 2>&1 || true
        rm -f "/etc/systemd/system/$unit"
    done
    for unit in x-tunnel.service x-tunnel-speedtest.service x-tunnel-speedtest-single@.service; do
        dropin_dir=/etc/systemd/system/$unit.d
        rm -f "$dropin_dir/shellcrash-bypass.conf"
        rmdir "$dropin_dir" >/dev/null 2>&1 || true
    done
    systemctl daemon-reload
fi

for launcher in /usr/bin/xtunnel-menu /usr/local/bin/xtunnel-menu; do
    if [ -f "$launcher" ] && grep -Fq "$PROJECT_DIR/menu.sh" "$launcher"; then
        rm -f "$launcher"
    fi
done

printf 'X-Tunnel services and managed schedule removed.\n'
printf 'Project and private configs retained at: %s\n' "$PROJECT_DIR"
printf 'Shared /usr/bin/x-tunnel binary and ShellCrash settings are not removed.\n'
if [ "$PURGE" -eq 1 ]; then
    case "$PROJECT_DIR" in
        /|/etc|/usr|/bin|/sbin|/opt|/root|/overlay|/tmp) echo 'Refusing to delete an unsafe path.' >&2; exit 1 ;;
    esac
    printf 'This also deletes private configuration and speed-test history under %s.\n' "$PROJECT_DIR"
    printf 'Type DELETE to confirm: '
    IFS= read -r answer </dev/tty || exit 1
    [ "$answer" = DELETE ] || { echo 'Purge cancelled.'; exit 1; }
    cd /
    rm -rf "$PROJECT_DIR"
    echo 'Project directory removed.'
fi
