#!/bin/sh
# X-Tunnel single-command bootstrap for OpenWrt/procd and systemd Linux.
set -eu

REPO_ARCHIVE='https://github.com/fsyllkn/x-tunnel-cf-ip-autotest/archive/refs/heads/main.tar.gz'
REPO_NAME=x-tunnel-cf-ip-autotest

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

install_dependencies() {
    if [ "$PLATFORM" = openwrt ]; then
        missing=''
        command -v python3 >/dev/null 2>&1 || missing="$missing python3-light"
        command -v ip >/dev/null 2>&1 || missing="$missing ip-full"
        command -v curl >/dev/null 2>&1 || missing="$missing curl"
        if command -v python3 >/dev/null 2>&1; then
            python3 -c 'import yaml' >/dev/null 2>&1 || missing="$missing python3-yaml"
            python3 -c 'import codecs; codecs.lookup("idna")' >/dev/null 2>&1 || missing="$missing python3-codecs"
            python3 -c 'import urllib.request' >/dev/null 2>&1 || missing="$missing python3-urllib"
            python3 -c 'import ssl' >/dev/null 2>&1 || missing="$missing python3-openssl"
            python3 -c 'import logging' >/dev/null 2>&1 || missing="$missing python3-logging"
        else
            missing="$missing python3-yaml python3-codecs python3-urllib python3-openssl python3-logging"
        fi
        if [ -n "$missing" ]; then
            if command -v apk >/dev/null 2>&1; then
                info "Installing OpenWrt dependencies with apk:$missing"
                apk update && apk add $missing ca-bundle || fail 'apk dependency installation failed (check package DB lock and feeds).'
            elif command -v opkg >/dev/null 2>&1; then
                info "Installing OpenWrt dependencies with opkg:$missing"
                opkg update && opkg install $missing ca-bundle || fail 'opkg dependency installation failed.'
            else
                fail 'Missing dependencies and neither apk nor opkg was found.'
            fi
        fi
    else
        missing=0
        command -v python3 >/dev/null 2>&1 || missing=1
        command -v ip >/dev/null 2>&1 || missing=1
        command -v curl >/dev/null 2>&1 || missing=1
        if command -v python3 >/dev/null 2>&1; then
            python3 -c 'import yaml, ssl, urllib.request, logging, codecs; codecs.lookup("idna")' >/dev/null 2>&1 || missing=1
        fi
        if [ "$missing" -eq 1 ]; then
            command -v apt-get >/dev/null 2>&1 || fail 'Install python3, PyYAML, iproute2, curl and CA certificates manually.'
            info 'Installing Linux dependencies with apt-get.'
            apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-yaml iproute2 curl ca-certificates || fail 'apt-get failed.'
        fi
    fi
    command -v tar >/dev/null 2>&1 || fail 'tar is required.'
    command -v python3 >/dev/null 2>&1 || fail 'python3 is required.'
    python3 -c 'import yaml, ssl, urllib.request, logging, codecs; codecs.lookup("idna")' >/dev/null 2>&1 || fail 'Python dependencies are incomplete.'
    command -v ip >/dev/null 2>&1 || fail 'iproute2/ip-full is required.'
}

install_dependencies

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
SOURCE_DIR=$TMP_DIR/$REPO_NAME-main
[ -f "$SOURCE_DIR/install.sh" ] || fail 'Source archive is missing install.sh.'

if [ -d "$PROJECT_DIR/.git" ] && command -v git >/dev/null 2>&1; then
    info 'Existing Git checkout found: updating with git pull --ff-only.'
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
if [ ! -f "$PROJECT_DIR/speedtest-bot.env" ]; then
    (umask 077; cp "$PROJECT_DIR/speedtest-bot.env.example" "$PROJECT_DIR/speedtest-bot.env")
    created=1
fi
if [ ! -f "$PROJECT_DIR/project.env" ]; then
    cp "$PROJECT_DIR/project.env.example" "$PROJECT_DIR/project.env"
fi
chmod 0600 "$PROJECT_DIR/x-tunnel.yaml" "$PROJECT_DIR/speedtest-bot.env"
chmod 0644 "$PROJECT_DIR/project.env"

if [ "$created" -eq 1 ] || grep -q 'wss://example.com\|替换为你的' "$PROJECT_DIR/x-tunnel.yaml" || grep -q '替换为BotFather\|替换为你的数字' "$PROJECT_DIR/speedtest-bot.env"; then
    info 'Configuration templates are ready, but real node and Telegram credentials are required before services can start.'
    printf '\nEdit these files on your device:\n  %s/x-tunnel.yaml\n  %s/speedtest-bot.env\n  %s/project.env (check the Telegram proxy address)\n\n' "$PROJECT_DIR" "$PROJECT_DIR" "$PROJECT_DIR"
    printf 'Then rerun the same one-click installation command, or:\n  cd "%s" && sh install.sh\n' "$PROJECT_DIR"
    exit 0
fi

info 'Installing and enabling X-Tunnel services.'
(cd "$PROJECT_DIR" && sh ./install.sh) || fail 'Service installation failed; configs and downloaded source were kept.'
info 'Installation completed. Check services:'
if [ "$PLATFORM" = openwrt ]; then
    /etc/init.d/x-tunnel status || true
    /etc/init.d/x-tunnel-speedtest-bot status || true
else
    systemctl --no-pager status x-tunnel.service x-tunnel-speedtest-bot.service || true
fi
