#!/bin/sh
# Safe mock-only end-to-end test of X-Tunnel uninstaller.
# Uses a private temp unit tree, mock systemctl and mock id (no real service
# is stopped, no systemd files outside the temp tree are read or removed).
set -eu
BASE=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
TMP=$(mktemp -d "$HOME/xtunnel-lxc-uninstall.XXXXXX")
trap 'rm -rf "$TMP"' EXIT HUP INT TERM
PROJECT="$TMP/project"
UNITS="$TMP/units"
LOCAL="$TMP/localbin"
USR="$TMP/usrbin"
MOCK="$TMP/mocks"
mkdir -p "$PROJECT/systemd" "$PROJECT/runtime" "$UNITS" "$LOCAL" "$USR" "$MOCK"
cp "$BASE/uninstall.sh" "$PROJECT/uninstall.sh"
cp "$BASE/systemd/ownership.sh" "$PROJECT/systemd/ownership.sh"
for unit in x-tunnel.service x-tunnel-speedtest.service x-tunnel-speedtest-single@.service x-tunnel-speedtest-bot.service x-tunnel-speedtest.timer; do
    sed "s|@PROJECT_DIR@|$PROJECT|g" "$BASE/systemd/$unit" > "$UNITS/$unit"
done
printf 'Important private configuration\n' > "$PROJECT/x-tunnel.yaml"
printf 'Shared binary should remain\n' > "$TMP/shared-kernel"
printf 'Other project service\n' > "$UNITS/unrelated.service"
for unit in x-tunnel.service x-tunnel-speedtest.service x-tunnel-speedtest-single@.service; do
    mkdir -p "$UNITS/$unit.d"
    printf '[Service]\nGroup=7890\n' > "$UNITS/$unit.d/shellcrash-bypass.conf"
done
mkdir -p "$UNITS/x-tunnel-speedtest.timer.d"
printf '[Timer]\nOnCalendar=\nOnCalendar=*-*-* 06:45:00\nPersistent=false\n' > "$UNITS/x-tunnel-speedtest.timer.d/50-xtunnel-schedule.conf"
printf '#!/bin/sh\nexec /bin/sh "%s/menu.sh" "$@"\n' "$PROJECT" > "$LOCAL/x-t"
printf '#!/bin/sh\nexec /bin/sh "%s/menu.sh" "$@"\n' "$PROJECT" > "$LOCAL/xtunnel-menu"
printf '#!/bin/sh\nexec /bin/sh "%s/menu.sh" "$@"\n' "$PROJECT" > "$USR/xtunnel-menu"
printf 'stale lock\n' > "$TMP/lock"

cat > "$MOCK/systemctl" <<'MOCKSYSTEMCTL'
#!/bin/sh
printf '%s\n' "$*" >> "$XT_TEST_LOG"
case "$1" in
 list-units)
   printf '%s\n' 'x-tunnel-speedtest-single@104.17.0.1.service loaded active running example'
   exit 0 ;;
 is-active)
   exit 1 ;;
 *) exit 0 ;;
esac
MOCKSYSTEMCTL
cat > "$MOCK/id" <<'MOCKID'
#!/bin/sh
if [ "$1" = -u ]; then printf '0\n'; exit 0; fi
exec /usr/bin/id "$@"
MOCKID
chmod 755 "$MOCK/systemctl" "$MOCK/id"
export XTUNNEL_UNIT_DIR="$UNITS" XTUNNEL_LOCAL_BIN_DIR="$LOCAL"
export XTUNNEL_USR_BIN_DIR="$USR" XTUNNEL_KERNEL_PATH="$TMP/shared-kernel"
export XTUNNEL_LOCK_FILE="$TMP/lock" XT_TEST_LOG="$TMP/systemctl.log"
PATH="$MOCK:$PATH"; export PATH

echo 'TEST unrecognised foreign unit is protected BEFORE calling systemctl'
cp "$UNITS/x-tunnel.service" "$TMP/genuine-service"
printf '[Unit]\nDescription=Foreign service\n' > "$UNITS/x-tunnel.service"
if sh "$PROJECT/uninstall.sh" > "$TMP/result" 2>&1; then
    echo "Foreign unit accidentally accepted" >&2; exit 1
fi
grep -q 'Unknown or foreign systemd unit' "$TMP/result"
[ ! -e "$TMP/systemctl.log" ]
[ -f "$UNITS/x-tunnel.service" ]
cp "$TMP/genuine-service" "$UNITS/x-tunnel.service"

echo 'TEST dry run lists files but does not modify or call systemctl'
sh "$PROJECT/uninstall.sh" --dry-run > "$TMP/result"
grep -q 'DRY RUN' "$TMP/result"
[ -f "$UNITS/x-tunnel.service" ]
[ -f "$PROJECT/x-tunnel.yaml" ]
[ -f "$LOCAL/x-t" ]
[ ! -e "$TMP/systemctl.log" ]

echo 'TEST kernel cannot be removed without project install provenance'
if sh "$PROJECT/uninstall.sh" --purge --remove-kernel > "$TMP/result" 2>&1; then
    echo 'Unmanaged shared kernel removal erroneously accepted' >&2; exit 1
fi
grep -q 'No kernel ownership marker' "$TMP/result"
[ ! -e "$TMP/systemctl.log" ]
[ -f "$TMP/shared-kernel" ]

echo 'TEST verified shared kernel can be dry-run, never removed implicitly'
sha=$(sha256sum "$TMP/shared-kernel" | awk '{print $1}')
printf '%s  %s\n' "$sha" "$TMP/shared-kernel" > "$PROJECT/runtime/managed-kernel.sha256"
chmod 600 "$PROJECT/runtime/managed-kernel.sha256"
sh "$PROJECT/uninstall.sh" --dry-run --purge --remove-kernel > "$TMP/result"
grep -q 'Would remove installer-owned X-Tunnel binary' "$TMP/result"
[ -f "$TMP/shared-kernel" ]
[ -f "$PROJECT/x-tunnel.yaml" ]

echo 'TEST legacy timer drop-in and own shellcrash group are removed'
sh "$PROJECT/uninstall.sh" > "$TMP/result"
for unit in x-tunnel.service x-tunnel-speedtest.service x-tunnel-speedtest-single@.service x-tunnel-speedtest-bot.service x-tunnel-speedtest.timer; do
    [ ! -f "$UNITS/$unit" ]
done
[ ! -f "$UNITS/x-tunnel.service.d/shellcrash-bypass.conf" ]
[ ! -f "$UNITS/x-tunnel-speedtest.timer.d/50-xtunnel-schedule.conf" ]
[ ! -f "$LOCAL/x-t" ]
[ ! -f "$LOCAL/xtunnel-menu" ]
[ ! -f "$USR/xtunnel-menu" ]
[ ! -f "$TMP/lock" ]
[ -f "$UNITS/unrelated.service" ]
[ -f "$PROJECT/x-tunnel.yaml" ]
[ -f "$TMP/shared-kernel" ]
grep -q '^stop x-tunnel-speedtest.timer$' "$XT_TEST_LOG"
grep -q '^stop x-tunnel-speedtest-single@104.17.0.1.service$' "$XT_TEST_LOG"
grep -q '^daemon-reload$' "$XT_TEST_LOG"

echo 'TEST repeated uninstall is idempotent'
sh "$PROJECT/uninstall.sh" > "$TMP/result"
[ -f "$PROJECT/x-tunnel.yaml" ]
[ -f "$UNITS/unrelated.service" ]
echo 'TEST --purge asks for confirmation before changing anything'
python3 - "$PROJECT/uninstall.sh" NO --purge <<'PY'
import os, pty, sys
pid, tty = pty.fork()
if pid == 0:
    os.execv("/bin/sh", ["sh", sys.argv[1], *sys.argv[3:]])
os.write(tty, sys.argv[2].encode() + b"\n")
_, status = os.waitpid(pid, 0)
os.close(tty)
assert os.waitstatus_to_exitcode(status) == 1, "refused purge should fail"
PY
[ -f "$PROJECT/x-tunnel.yaml" ]
[ -f "$TMP/shared-kernel" ]

echo 'TEST confirmed --purge removes the entire project but retains shared binary'
python3 - "$PROJECT/uninstall.sh" DELETE --purge <<'PY'
import os, pty, sys
pid, tty = pty.fork()
if pid == 0:
    os.execv("/bin/sh", ["sh", sys.argv[1], *sys.argv[3:]])
os.write(tty, sys.argv[2].encode() + b"\n")
_, status = os.waitpid(pid, 0)
os.close(tty)
assert os.waitstatus_to_exitcode(status) == 0, "confirmed purge failed"
PY
[ ! -d "$PROJECT" ]
[ -f "$TMP/shared-kernel" ]
[ -f "$UNITS/unrelated.service" ]

echo 'TEST --purge --remove-kernel removes only recorded SHA256 owned binary'
mkdir -p "$PROJECT/systemd" "$PROJECT/runtime"
cp "$BASE/uninstall.sh" "$PROJECT/uninstall.sh"
cp "$BASE/systemd/ownership.sh" "$PROJECT/systemd/ownership.sh"
sha=$(sha256sum "$TMP/shared-kernel" | awk '{print $1}')
printf '%s  %s\n' "$sha" "$TMP/shared-kernel" > "$PROJECT/runtime/managed-kernel.sha256"
python3 - "$PROJECT/uninstall.sh" DELETE --purge --remove-kernel <<'PY'
import os, pty, sys
pid, tty = pty.fork()
if pid == 0:
    os.execv("/bin/sh", ["sh", sys.argv[1], *sys.argv[3:]])
os.write(tty, sys.argv[2].encode() + b"\n")
_, status = os.waitpid(pid, 0)
os.close(tty)
assert os.waitstatus_to_exitcode(status) == 0, "verified kernel purge failed"
PY
[ ! -d "$PROJECT" ]
[ ! -f "$TMP/shared-kernel" ]
[ -f "$UNITS/unrelated.service" ]
echo 'Mock-only LXC uninstall regression passed.'
