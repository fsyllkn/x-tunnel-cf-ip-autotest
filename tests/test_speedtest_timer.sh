#!/bin/sh
# Non-destructive regression tests for the OpenWrt daily CF speedtest timer.
# Overrides the crontab and cron service; never touches the host's real cron.
set -eu

REPO=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
TIMER="$REPO/openwrt/speedtest-timer.sh"
TMP=$(mktemp -d "${TMPDIR:-/tmp}/xtunnel-timer-tests.XXXXXX") || exit 1
trap 'rm -rf "$TMP"' EXIT HUP INT TERM
mkdir -p "$TMP/bin"
cat > "$TMP/bin/id" <<'SH'
#!/bin/sh
if [ "${1:-}" = -u ]; then printf '0\n'; else /usr/bin/id "$@"; fi
SH
cat > "$TMP/bin/mock-cron" <<'SH'
#!/bin/sh
printf '%s\n' "$1" >> "$X_TUNNEL_CRON_LOG"
SH
chmod +x "$TMP/bin/id" "$TMP/bin/mock-cron"

CRONFILE="$TMP/root"
CRONLOG="$TMP/cron-actions"
export PATH="$TMP/bin:$PATH"
export X_TUNNEL_CRONTAB_FILE="$CRONFILE"
export X_TUNNEL_CRON_SERVICE="$TMP/bin/mock-cron"
export X_TUNNEL_CRON_LOG="$CRONLOG"

fail() { echo "Timer regression failure: $*" >&2; exit 1; }
timer() { sh "$TIMER" "$@"; }
status() { timer status; }
job_count() { grep -Fc '# x-tunnel-cf-ip-autotest daily speedtest' "$CRONFILE" || true; }

for time in 00:00 06:45 18:00 23:59; do timer validate "$time"; done
for time in 24:00 18:60 08:7 8:07 18:00:00 1a:00 99:99; do
    if timer validate "$time" >/dev/null 2>&1; then fail "accepted $time"; fi
done

printf '5 3 * * * /bin/echo keep-other-job\n' > "$CRONFILE"
timer enable >/dev/null
[ "$(status)" = '18:00' ] || fail 'default is not 18:00'
grep -Fqx "0 18 * * * /bin/sh '$REPO/openwrt/run-speedtest.sh' >> '$REPO/cfip-data/cron.log' 2>&1" "$CRONFILE" ||
    fail 'default cron expression is wrong'
[ "$(job_count)" = 1 ] || fail 'default task duplicated'
grep -Fqx '5 3 * * * /bin/echo keep-other-job' "$CRONFILE" || fail 'unrelated cron task removed'

timer enable 06:45 >/dev/null
[ "$(status)" = '06:45' ] || fail 'custom time was not persisted'
grep -Fqx "45 6 * * * /bin/sh '$REPO/openwrt/run-speedtest.sh' >> '$REPO/cfip-data/cron.log' 2>&1" "$CRONFILE" ||
    fail '06:45 cron expression wrong'
[ "$(job_count)" = 1 ] || fail 'old cron task not replaced'

if timer enable 25:00 >/dev/null 2>&1; then fail 'invalid time accepted'; fi
[ "$(status)" = '06:45' ] || fail 'invalid input modified schedule'
timer enable 23:59 >/dev/null
[ "$(status)" = '23:59' ] || fail '23:59 schedule failed'
timer enable 00:00 >/dev/null
[ "$(status)" = '00:00' ] || fail 'midnight schedule failed'
[ "$(job_count)" = 1 ] || fail 'task was duplicated'

# No restart needed if nothing changed; repeated enable is idempotent.
before=$(wc -l < "$CRONLOG")
timer enable 00:00 >/dev/null
after=$(wc -l < "$CRONLOG")
[ "$before" -eq "$after" ] || fail 'unchanged cron task was restarted'

timer disable >/dev/null
[ "$(status)" = disabled ] || fail 'disabled timer still reported active'
! grep -Fq 'openwrt/run-speedtest.sh' "$CRONFILE" || fail 'timer job survived disable'
grep -Fqx '5 3 * * * /bin/echo keep-other-job' "$CRONFILE" || fail 'unrelated job removed on disable'
timer disable >/dev/null
[ "$(status)" = disabled ] || fail 'repeated disable failed'

echo 'OpenWrt speedtest HH:MM timer regression tests passed.'
