#!/bin/sh
# PVE LXC / Debian Ubuntu systemd bootstrap; Git checkout or private local archive.
set -eu

REPO_ARCHIVE='https://github.com/fsyllkn/x-tunnel-cf-ip-autotest/archive/refs/heads/lxc.tar.gz'
REPO_NAME=x-tunnel-cf-ip-autotest
BRANCH=lxc
ARCHIVE_FILE=''
# No arguments opens an interactive SSH installation and management menu.
# Explicit flags keep noninteractive installation available for automation.
INSTALL_ARGS=""
MENU_MODE=1
for arg in "$@"; do
 case "$arg" in
   --menu) MENU_MODE=1 ;;
   --archive=*) ARCHIVE_FILE=${arg#*=}; [ -n "$ARCHIVE_FILE" ] || { echo "Empty --archive= path" >&2; exit 2; } ;;
   --core) MENU_MODE=0 ;;
   --with-bot|--with-speedtest|--enable-timer|--disable-bot|--repair-kernel|--timer-at=*)
     MENU_MODE=0
     INSTALL_ARGS="$INSTALL_ARGS $arg" ;;
   -h|--help)
     echo "Usage: sh quick-install.sh [--menu|--core|--archive=/path/lxc.zip|--with-bot|--with-speedtest|--enable-timer|--timer-at=HH:MM|--disable-bot|--repair-kernel]"
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

# Existing Git checkouts update with git; private archives can be supplied
# locally without GitHub credentials. Never switch branches automatically.
TMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/xtunnel-setup.XXXXXX") || fail 'Cannot create staging directory.'
trap 'rm -rf "$TMP_DIR"' EXIT HUP INT TERM
if [ -d "$PROJECT_DIR/.git" ]; then
    [ -z "$ARCHIVE_FILE" ] || fail 'Git checkout detected; update with git pull instead of --archive.'
    command -v git >/dev/null 2>&1 || fail 'git required for updating a Git checkout.'
    current_branch=$(git -C "$PROJECT_DIR" symbolic-ref --quiet --short HEAD 2>/dev/null || true)
    [ "$current_branch" = "$BRANCH" ] ||
        fail "Existing Git checkout uses $current_branch (expected lxc); refusing branch switch."
    git -C "$PROJECT_DIR" pull --ff-only origin "$BRANCH" ||
        fail 'Git pull failed; check private repo permissions and local modifications.'
else
    command -v tar >/dev/null 2>&1 || fail 'tar required.'
    if [ -n "$ARCHIVE_FILE" ]; then
        [ -f "$ARCHIVE_FILE" ] || fail 'Local archive not found.'
        ARCHIVE=$ARCHIVE_FILE
        info "Using private branch archive: $ARCHIVE"
    else
        command -v curl >/dev/null 2>&1 || command -v wget >/dev/null 2>&1 ||
            fail 'Download an lxc ZIP or install curl/wget.'
        ARCHIVE="$TMP_DIR/source.tar.gz"
        info 'Fetching lxc branch. For a private repository use --archive=/path/lxc.zip.'
        if command -v curl >/dev/null 2>&1; then
            curl -fL --retry 3 --connect-timeout 20 -o "$ARCHIVE" "$REPO_ARCHIVE" ||
                fail 'Private branch download failed; supply --archive=.'
        else
            wget -T 20 -O "$ARCHIVE" "$REPO_ARCHIVE" ||
                fail 'Private branch download failed; supply --archive=.'
        fi
    fi
    case "$ARCHIVE" in
        *.zip)
            command -v unzip >/dev/null 2>&1 || fail 'unzip required for ZIP source.'
            unzip -q "$ARCHIVE" -d "$TMP_DIR" || fail 'ZIP extraction failed.' ;;
        *.tar.gz|*.tgz)
            tar -xzf "$ARCHIVE" -C "$TMP_DIR" || fail 'Tar extraction failed.' ;;
        *) fail 'Archive must have .zip, .tar.gz or .tgz suffix.' ;;
    esac
    SOURCE_DIR="$TMP_DIR/$REPO_NAME-$BRANCH"
    [ -f "$SOURCE_DIR/install.sh" ] && [ -f "$SOURCE_DIR/migrate_config.py" ] ||
        fail 'Source archive is not the lxc branch (missing install.sh/migrate_config.py).'
    mkdir -p "$PROJECT_DIR"
    cp -R "$SOURCE_DIR"/. "$PROJECT_DIR"/ ||
        fail 'Source copy failed; services were not restarted.'
fi

created=0
if [ ! -f "$PROJECT_DIR/x-tunnel.yaml" ]; then
 (umask 077; cp "$PROJECT_DIR/x-tunnel.example.yaml" "$PROJECT_DIR/x-tunnel.yaml")
 created=1
fi
chmod 600 "$PROJECT_DIR/x-tunnel.yaml"
# Migrate in interactive mode when dependencies exist; install.sh will
# revalidate and migrate before deploying any services.
if command -v python3 >/dev/null 2>&1 &&
   python3 -c 'import yaml, fcntl' >/dev/null 2>&1; then
    (cd "$PROJECT_DIR" && python3 migrate_config.py) ||
        fail 'Unified YAML migration failed; services remain unchanged.'
else
    info 'YAML migration deferred until Python/PyYAML become available.'
fi
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
 info 'Opening LXC systemd management menu with unified YAML.'
 # Remove the downloaded temporary files before replacing this shell.
 rm -rf "$TMP_DIR"
 trap - EXIT HUP INT TERM
 exec /bin/sh "$PROJECT_DIR/menu.sh"
fi
if [ "$created" -eq 1 ] || grep -q 'example.com\|替换为' "$PROJECT_DIR/x-tunnel.yaml"; then
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
