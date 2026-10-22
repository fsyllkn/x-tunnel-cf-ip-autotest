#!/usr/bin/env python3
"""Read-only SOCKS5 health check against the generated local provider."""
import json
import socket
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sync_singbox import provider_path


def socks5_greeting(server, port):
    try:
        with socket.create_connection((server, port), timeout=0.5) as conn:
            conn.settimeout(0.5)
            conn.sendall(b"\x05\x01\x00")
            return conn.recv(2) == b"\x05\x00"
    except (OSError, TimeoutError):
        return False


def main():
    path = provider_path()
    if not path.is_file():
        raise SystemExit("ERROR: provider missing: %s" % path)
    nodes = json.loads(path.read_text(encoding="utf-8")).get("outbounds") or []
    if not nodes:
        raise SystemExit("ERROR: no X-Tunnel SOCKS5 nodes in provider")
    for _ in range(30):
        failed = [
            node for node in nodes
            if node.get("type") != "socks"
            or not socks5_greeting(node["server"], int(node["server_port"]))
        ]
        if not failed:
            print("PASS: %d/%d SOCKS5 endpoints accept handshakes" % (len(nodes), len(nodes)))
            return
        time.sleep(0.5)
    for node in failed:
        print("ERROR: %s %s:%s is not accepting SOCKS5 connections" % (
            node.get("tag", "?"), node.get("server", "?"), node.get("server_port", "?")
        ))
    raise SystemExit(1)


if __name__ == "__main__":
    main()
