#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
cd "$PROJECT_DIR"
exec /usr/bin/python3 "$PROJECT_DIR/cfip_speedtest.py"
