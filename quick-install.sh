#!/bin/sh
# X-Tunnel single-command bootstrap for OpenWrt/procd and systemd Linux.
set -eu

REPO_ARCHIVE='https://github.com/fsyllkn/x-tunnel-cf-ip-autotest/archive/refs/heads/openwrt.tar.gz'
REPO_NAME=x-tunnel-cf-ip-autotest
BRANCH=openwrt
INSTALL_ARGS=""
for arg in "$@"; do
 case "$arg" in --with-bot|--with-speedtest|--enable-timer|--disable-bot) INSTALL_ARGS="$INSTALL_ARGS $arg";; *) echo "Unknown option: $arg" >&2; exit 2;; esac
done

fail() { printf '[ERROR] %s\n' "$*" >&2; exit 1; }
info() { printf '[X-Tunnel] %s\n' "$*"; }

[ "$(id -u)" -eq 0 ] || fail 'Run as root.'
if [ -f /etc/openwrt_release ]; then
    PLATFORM=openwrt
    DEFAULT_DIR=/overlay/$REPO_NAME
    [ -d /overlay ] || DEFAULT_DIR=/root/$REPO_NAME
elif [ -d /run/systemd/system ] && command -v systemctl >/dev/null 2>&1; then
    PLATFORM=systemd
    DEFAULT_DIR=/opt/$REPO_NAME
else
    fail 'Only OpenWrt/procd and systemd Linux are supported.'
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

# The dedicated OpenWrt installer installs only dependencies for selected features.
[ "$PLATFORM" = openwrt ] || fail 'Use the main branch for systemd Linux.'
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
    [ "$current_branch" = "$BRANCH" ] || fail "Existing Git checkout uses branch $current_branch; switch explicitly to openwrt after backing up configs."
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
if [ ! -f "$PROJECT_DIR/project.env" ]; then cp "$PROJECT_DIR/project.env.example" "$PROJECT_DIR/project.env"; fi
chmod 600 "$PROJECT_DIR/x-tunnel.yaml"
if [ "$created" -eq 1 ] || grep -q 'example.com\|替换为' "$PROJECT_DIR/x-tunnel.yaml"; then
 info "Edit $PROJECT_DIR/x-tunnel.yaml, then rerun."
 exit 0
fi

info 'Installing and enabling X-Tunnel services.'
(cd "$PROJECT_DIR" && sh ./install.sh $INSTALL_ARGS) || fail 'Service installation failed; configs and downloaded source were kept.'
info 'Installation completed. Check services:'
if [ "$PLATFORM" = openwrt ]; then
    /etc/init.d/x-tunnel status || true
    /etc/init.d/x-tunnel-speedtest-bot status || true
else
    systemctl --no-pager status x-tunnel.service x-tunnel-speedtest-bot.service || true
fi
