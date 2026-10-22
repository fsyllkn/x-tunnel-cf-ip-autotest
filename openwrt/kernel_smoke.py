#!/usr/bin/env python3
"""Validate that a candidate X-Tunnel Linux binary actually serves SOCKS5.

No external network is used. This must run before replacing /usr/bin/x-tunnel.
"""
import socket
import subprocess
import sys
import time


def check(binary):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tmp:
        tmp.bind(("127.0.0.1", 0))
        port = tmp.getsockname()[1]
    args = [
        binary, "-f", "ws://127.0.0.1:9/tunnel", "-token", "local-preflight",
        "-n", "1", "-web", "", "-l", "socks5://127.0.0.1:%d" % port,
    ]
    process = subprocess.Popen(
        args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    healthy = False
    try:
        for _ in range(80):
            if process.poll() is not None:
                break
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2) as conn:
                    conn.settimeout(0.4)
                    conn.sendall(b"\x05\x01\x00")
                    if conn.recv(2) == b"\x05\x00":
                        healthy = True
                        break
            except (OSError, TimeoutError):
                pass
            time.sleep(0.1)
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    if not healthy:
        raise RuntimeError("SOCKS5 listener did not accept a valid greeting")
    print("PASS: candidate binary created a local SOCKS5 listener")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: kernel_smoke.py /path/to/x-tunnel-binary")
    try:
        check(sys.argv[1])
    except (OSError, RuntimeError) as error:
        raise SystemExit("FAIL: X-Tunnel kernel smoke test: %s" % error)
