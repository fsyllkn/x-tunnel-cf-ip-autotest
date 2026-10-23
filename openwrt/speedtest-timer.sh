#!/bin/sh
# Manage only the X-Tunnel daily CF speedtest entry in OpenWrt root's crontab.
# Times are 24-hour HH:MM in the router's LOCAL timezone.
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
CRONTAB_FILE=${X_TUNNEL_CRONTAB_FILE:-/etc/crontabs/root}
CRON_SERVICE=${X_TUNNEL_CRON_SERVICE:-/etc/init.d/cron}
MARKER='# x-tunnel-cf-ip-autotest daily speedtest'
DEFAULT_TIME='18:00'

valid_time() {
    case "$1" in
        [0-1][0-9]:[0-5][0-9]|2[0-3]:[0-5][0-9]) return 0 ;;
        *) return 1 ;;
    esac
}

current_time() {
    [ -f "$CRONTAB_FILE" ] || return 0
    awk -v marker="$MARKER" '
        $0 == marker { next_is_job=1; next }
        next_is_job {
            next_is_job=0
            if (index($0, "/openwrt/run-speedtest.sh") &&
                $1 ~ /^[0-9]+$/ && $2 ~ /^[0-9]+$/ &&
                $3=="*" && $4=="*" && $5=="*" &&
                ($1+0)>=0 && ($1+0)<=59 && ($2+0)>=0 && ($2+0)<=23) {
                printf "%02d:%02d\n", $2, $1
            }
            exit
        }
    ' "$CRONTAB_FILE"
}

strip_managed_job() {
    awk -v marker="$MARKER" '
        $0 == marker { next_is_job=1; next }
        next_is_job {
            next_is_job=0
            if (index($0, "/openwrt/run-speedtest.sh")) next
        }
        { print }
    ' "$CRONTAB_FILE"
}

change_schedule() {
    operation=$1
    time=$2
    [ "$(id -u)" -eq 0 ] || { echo 'Run as root.' >&2; exit 1; }
    if [ "$operation" = enable ] && ! valid_time "$time"; then
        echo "Invalid time '$time'; use 24-hour HH:MM (00:00-23:59)." >&2
        exit 2
    fi
    if [ "$operation" = disable ] && [ ! -f "$CRONTAB_FILE" ]; then
        echo 'Daily CF speedtest already disabled.'
        return 0
    fi
    directory=$(dirname -- "$CRONTAB_FILE")
    mkdir -p "$directory"
    tmp=$(mktemp "$directory/.xtunnel-cron.XXXXXX") || exit 1
    trap 'rm -f "$tmp"' EXIT HUP INT TERM
    if [ -f "$CRONTAB_FILE" ]; then
        strip_managed_job > "$tmp"
    else
        : > "$tmp"
    fi
    if [ "$operation" = enable ]; then
        hour=${time%:*}
        minute=${time#*:}
        # POSIX sh / BusyBox ash: convert 06 -> 6, 00 -> 0 (no bash arithmetic).
        case "$hour" in 0*) hour=${hour#0} ;; esac
        case "$minute" in 0*) minute=${minute#0} ;; esac
        printf '%s\n' "$MARKER" >> "$tmp"
        printf "%s %s * * * /bin/sh '%s/openwrt/run-speedtest.sh' >> '%s/cfip-data/cron.log' 2>&1\n" \
            "$minute" "$hour" "$PROJECT_DIR" "$PROJECT_DIR" >> "$tmp"
    fi
    chmod 0600 "$tmp"
    if [ -f "$CRONTAB_FILE" ] && cmp -s "$CRONTAB_FILE" "$tmp"; then
        rm -f "$tmp"
        trap - EXIT HUP INT TERM
        if [ "$operation" = enable ]; then
            printf 'Daily CF speedtest already scheduled for %s (router local time).\n' "$time"
        else
            echo 'Daily CF speedtest already disabled.'
        fi
        return 0
    fi
    mv -f "$tmp" "$CRONTAB_FILE"
    trap - EXIT HUP INT TERM
    if [ -x "$CRON_SERVICE" ]; then
        if [ "$operation" = enable ]; then
            "$CRON_SERVICE" enable || { echo 'Unable to enable cron service.' >&2; return 1; }
        fi
        "$CRON_SERVICE" restart || { echo 'Crontab updated, but cron service reload failed.' >&2; return 1; }
    else
        echo 'WARNING: cron service unavailable; crontab saved but scheduling not confirmed.' >&2
    fi
    if [ "$operation" = enable ]; then
        printf 'Daily CF speedtest scheduled for %s (router local time).\n' "$time"
    else
        echo 'Daily CF speedtest disabled (other cron jobs preserved).'
    fi
}

case "${1:-}" in
    validate)
        [ "$#" -eq 2 ] && valid_time "$2" ||
            { echo 'Expected HH:MM in 24-hour format (00:00-23:59).' >&2; exit 2; }
        ;;
    status)
        current=$(current_time)
        printf '%s\n' "${current:-disabled}"
        ;;
    enable)
        [ "$#" -le 2 ] || { echo 'Usage: speedtest-timer.sh enable [HH:MM]' >&2; exit 2; }
        change_schedule enable "${2:-$DEFAULT_TIME}"
        ;;
    disable)
        change_schedule disable ''
        ;;
    *)
        echo 'Usage: speedtest-timer.sh {status|validate HH:MM|enable [HH:MM]|disable}' >&2
        exit 2
        ;;
esac
