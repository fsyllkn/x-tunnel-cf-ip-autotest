#!/usr/bin/env python3
"""Local SOCKS5 handshake preflight for the ORIGINAL single-file X-Tunnel core.

Only flags supported by the stable x-tunnel.go are used. No external
network is needed and the candidate binary is never installed by this test.
"""
import socket
import subprocess
import sys
import tempfile
import time


def check(binary):
    # Use an ephemeral local port to avoid existing production SOCKS5 nodes.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    args = [
        binary,
        "-f", "ws://127.0.0.1:9/tunnel",
        "-token", "local-preflight",
        "-n", "1",
        "-l", "socks5://127.0.0.1:%d" % port,
    ]
    # IMPORTANT: The original stable Go core DOES NOT support the -web flag.
    # Capture stderr so a startup/flag error is never misreported as an
    # unexplained listener failure.
    with tempfile.TemporaryFile(mode="w+b") as log:
        process = subprocess.Popen(
            args, stdout=log, stderr=subprocess.STDOUT,
        )
        healthy = False
        try:
            for _ in range(120):
                if process.poll() is not None:
                    break
                try:
                    with socket.create_connection(
                        ("127.0.0.1", port), timeout=0.2
                    ) as conn:
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
            log.flush()
            log.seek(0)
            detail = log.read(32768).decode("utf-8", errors="replace")
            detail = "\n".join(detail.splitlines()[-15:])
            raise RuntimeError(
                "SOCKS5 listener did not accept a greeting "
                "(Go exit code %s). Startup log:\n%s"
                % (process.returncode, detail or "(empty)")
            )
    print("PASS: candidate binary created a local SOCKS5 listener")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: kernel_smoke.py /path/to/x-tunnel-binary")
    try:
        check(sys.argv[1])
    except (OSError, RuntimeError) as error:
        raise SystemExit("FAIL: X-Tunnel kernel smoke test: %s" % error)
