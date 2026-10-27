#!/bin/sh
# Interactive X-Tunnel bootstrap for Debian/Ubuntu systemd.
set -eu

REPO_ARCHIVE='https://github.com/fsyllkn/x-tunnel-cf-ip-autotest/archive/refs/heads/main.tar.gz'
REPO_NAME=x-tunnel-cf-ip-autotest
BRANCH=main
# No arguments opens an interactive SSH installation and management menu.
# Explicit flags keep noninteractive installation available for automation.
INSTALL_ARGS=""
MENU_MODE=1
for arg in "$@"; do
 case "$arg" in
   --menu) MENU_MODE=1 ;;
   --core) MENU_MODE=0 ;;
   --with-bot|--with-speedtest|--enable-timer|--disable-bot|--repair-kernel|--timer-at=*)
     MENU_MODE=0
     INSTALL_ARGS="$INSTALL_ARGS $arg" ;;
   -h|--help)
     echo "Usage: sh quick-install.sh [--menu|--core|--with-bot|--with-speedtest|--enable-timer|--timer-at=HH:MM|--disable-bot|--repair-kernel]"
     echo "No arguments: interactive installation/management menu (SSH terminal required)."
     exit 0 ;;
   *) echo "Unknown option: $arg" >&2; exit 2 ;;
 esac
done
if [ "$MENU_MODE" -eq 1 ] && [ ! -t 0 ]; then
 echo "[ERROR] Interactive mode requires a terminal; use --core or --with-bot for noninteractive installation." >&2
 exit 2
fi

fail() { printf '[ERROR] %s\n' "$*" >&2; exit 1; }
info() { printf '[X-Tunnel] %s\n' "$*"; }

[ "$(id -u)" -eq 0 ] || fail 'Run as root.'
if [ -f /etc/openwrt_release ]; then
    fail 'Use the openwrt branch for OpenWrt / iStoreOS.'
elif [ -d /run/systemd/system ] && command -v systemctl >/dev/null 2>&1; then
    PLATFORM=systemd
    DEFAULT_DIR=/opt/$REPO_NAME
else
    fail 'Debian/Ubuntu with systemd required.'
fi
PROJECT_DIR=${X_TUNNEL_INSTALL_DIR:-$DEFAULT_DIR}
case "$PROJECT_DIR" in
    /*) ;;
    *) fail 'X_TUNNEL_INSTALL_DIR must be an absolute path.' ;;
esac
case "$PROJECT_DIR" in
    /|/etc|/usr|/bin|/sbin|/opt|/root|/overlay|/tmp) fail 'Unsafe installation directory.' ;;
esac
info "Platform: $PLATFORM; directory: $PROJECT_DIR"

# Main is for systemd Linux; no OpenWrt networking settings are changed.
command -v tar >/dev/null 2>&1 || fail 'tar required.'
command -v curl >/dev/null 2>&1 || command -v wget >/dev/null 2>&1 || fail 'curl or wget required.'

TMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/xtunnel-setup.XXXXXX") || fail 'Cannot create temporary directory.'
trap 'rm -rf "$TMP_DIR"' EXIT HUP INT TERM
ARCHIVE=$TMP_DIR/source.tar.gz
if command -v curl >/dev/null 2>&1; then
    curl -fL --retry 3 --connect-timeout 20 -o "$ARCHIVE" "$REPO_ARCHIVE" || fail 'Failed to download source archive.'
elif command -v wget >/dev/null 2>&1; then
    wget -T 20 -O "$ARCHIVE" "$REPO_ARCHIVE" || fail 'Failed to download source archive.'
else
    fail 'Install curl or wget.'
fi
tar -xzf "$ARCHIVE" -C "$TMP_DIR" || fail 'Invalid source archive.'
SOURCE_DIR=$TMP_DIR/$REPO_NAME-$BRANCH
[ -f "$SOURCE_DIR/install.sh" ] || fail 'Source archive is missing install.sh.'

if [ -d "$PROJECT_DIR/.git" ] && command -v git >/dev/null 2>&1; then
    info 'Existing Git checkout found: updating with git pull --ff-only.'
    current_branch=$(git -C "$PROJECT_DIR" symbolic-ref --quiet --short HEAD 2>/dev/null || true)
    [ "$current_branch" = "$BRANCH" ] || fail "Existing Git checkout uses branch $current_branch; switch explicitly to main after backing up configs."
    git -C "$PROJECT_DIR" pull --ff-only || fail 'Git update failed. Fix local changes or remote access before retrying.'
else
    mkdir -p "$PROJECT_DIR"
    cp -R "$SOURCE_DIR"/. "$PROJECT_DIR"/ || fail 'Failed to copy project files.'
fi

created=0
if [ ! -f "$PROJECT_DIR/x-tunnel.yaml" ]; then
 (umask 077; cp "$PROJECT_DIR/x-tunnel.example.yaml" "$PROJECT_DIR/x-tunnel.yaml")
 created=1
fi
# Legacy .env files are migrated during install.sh preflight; never recreate them.
chmod 600 "$PROJECT_DIR/x-tunnel.yaml"
# Make the menu available even before the core service has been installed.
if [ -f "$PROJECT_DIR/menu.sh" ]; then
    mkdir -p /usr/local/bin
    for launcher in /usr/local/bin/x-t /usr/local/bin/xtunnel-menu; do
        if [ -L "$launcher" ]; then
            echo "$launcher is a symlink; refusing to overwrite." >&2
            exit 1
        fi
        if [ -e "$launcher" ] &&
           ! grep -Fq "$PROJECT_DIR/menu.sh" "$launcher" 2>/dev/null; then
            fail "$launcher belongs to another program."
        fi
        printf '#!/bin/sh\nexec /bin/sh "%s/menu.sh" "$@"\n' "$PROJECT_DIR" > "$launcher"
        chmod 0755 "$launcher"
    done
fi
if [ "$MENU_MODE" -eq 1 ]; then
 info 'Opening Debian/Ubuntu setup and management menu (Bot/timer optional).'
 # Remove the downloaded temporary files before replacing this shell.
 rm -rf "$TMP_DIR"
 trap - EXIT HUP INT TERM
 exec /bin/sh "$PROJECT_DIR/menu.sh"
fi
if [ "$created" -eq 1 ] || grep -q 'example.com' "$PROJECT_DIR/x-tunnel.yaml"; then
 info "Edit $PROJECT_DIR/x-tunnel.yaml, then rerun."
 exit 0
fi

if [ "$MENU_MODE" -eq 0 ]; then
    if ! command -v python3 >/dev/null 2>&1 ||
       ! python3 -c 'import yaml, ssl, urllib.request; import sys; assert sys.version_info >= (3,9)' >/dev/null 2>&1 ||
       ! command -v ip >/dev/null 2>&1; then
        command -v apt-get >/dev/null 2>&1 || fail 'apt-get required to install Python/iproute2.'
        info 'Installing Linux dependencies: python3 python3-yaml iproute2 ca-certificates.'
        apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-yaml iproute2 ca-certificates ||
            fail 'Linux package installation failed (Python >=3.9 required).'
    fi
fi
info 'Installing requested X-Tunnel services.'
(cd "$PROJECT_DIR" && sh ./install.sh $INSTALL_ARGS) || fail 'Service installation failed; configs and downloaded source were kept.'
info 'Installation completed. Check services:'
systemctl --no-pager status x-tunnel.service || true
if systemctl is-enabled --quiet x-tunnel-speedtest-bot.service 2>/dev/null; then
    systemctl --no-pager status x-tunnel-speedtest-bot.service || true
fi
