#!/bin/sh
# Read-only ownership tests for X-Tunnel's LXC/systemd assets.
# Source from install.sh and uninstall.sh. No systemctl or filesystem writes.
xt_unit_owned() {
    xt_project=$1 xt_unit_dir=$2 xt_unit=$3
    xt_file="$xt_unit_dir/$xt_unit"
    [ -f "$xt_file" ] && [ ! -L "$xt_file" ] || return 1
    case "$xt_unit" in
        x-tunnel.service)
            grep -Fxq "WorkingDirectory=$xt_project" "$xt_file" &&
            grep -Fxq "ExecStart=/usr/bin/python3 $xt_project/supervisor.py" "$xt_file" ;;
        x-tunnel-speedtest.service)
            grep -Fxq "WorkingDirectory=$xt_project" "$xt_file" &&
            grep -Fxq "ExecStart=/usr/bin/python3 $xt_project/cfip_speedtest.py" "$xt_file" ;;
        x-tunnel-speedtest-single@.service)
            grep -Fxq "WorkingDirectory=$xt_project" "$xt_file" &&
            grep -Fxq "ExecStart=/usr/bin/python3 $xt_project/cfip_speedtest.py --single-ip %I" "$xt_file" ;;
        x-tunnel-speedtest-bot.service)
            grep -Fxq "WorkingDirectory=$xt_project" "$xt_file" &&
            grep -Fxq "ExecStart=/usr/bin/python3 $xt_project/telegram_speedtest_bot.py" "$xt_file" ;;
        x-tunnel-speedtest.timer)
            grep -Fxq "Unit=x-tunnel-speedtest.service" "$xt_file" &&
            {
                grep -Fxq '# Managed-by: x-tunnel-cf-ip-autotest (lxc/systemd)' "$xt_file" ||
                grep -Fxq 'Description=Run X-Tunnel Cloudflare IP speedtest daily (local time; default 18:00)' "$xt_file"
            } ;;
        *) return 1 ;;
    esac
}

xt_launcher_owned() {
    xt_project=$1 xt_file=$2
    [ -f "$xt_file" ] && [ ! -L "$xt_file" ] &&
    grep -Fxq '#!/bin/sh' "$xt_file" &&
    grep -Fxq "exec /bin/sh \"$xt_project/menu.sh\" \"\$@\"" "$xt_file"
}

xt_bypass_owned() {
    xt_file=$1
    [ -f "$xt_file" ] && [ ! -L "$xt_file" ] &&
        [ "$(cat "$xt_file")" = "$(printf '[Service]\nGroup=7890')" ]
}

xt_timer_override_owned() {
    xt_file=$1
    [ -f "$xt_file" ] && [ ! -L "$xt_file" ] &&
    grep -Fxq '[Timer]' "$xt_file" &&
    grep -Fxq 'OnCalendar=' "$xt_file" &&
    grep -Fxq 'Persistent=false' "$xt_file" &&
    grep -Eq '^OnCalendar=\*-\*-\* ([01][0-9]|2[0-3]):[0-5][0-9]:00$' "$xt_file"
}
