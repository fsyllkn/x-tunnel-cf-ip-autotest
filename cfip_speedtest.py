#!/usr/bin/env python3
import argparse
import concurrent.futures
import datetime
import fcntl
import html
import http.client
import ipaddress
import json
import os
import random
import re
import shlex
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from project_settings import (
    build_speed_tiers, configured_process_gid, get_target_mbps,
    load_project_env, telegram_proxy_chain,
)


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "cfip-data"
CONFIG_FILE = BASE_DIR / "x-tunnel.yaml"
SCAN_LOCK_FILE = "/run/lock/x-tunnel-cfip-speedtest.lock"
ROUTE_STATE_FILE = Path("/run/lock/x-tunnel-cfip-speedtest.routes.json")
SINGBOX_GATEWAY_CONFIG = Path("/opt/sing-box/config/config.json")
SINGBOX_GATEWAY_RULE = re.compile(
    r"^\s*15360:\s+from all fwmark 0x1536(?:/0xffffffff)? lookup 53136\s*$",
    re.I,
)
REPORT_FILE = DATA_DIR / "latest-results.json"
BOT_CONFIG_FILE = Path(os.environ.get(
    "X_TUNNEL_BOT_CONFIG",
    str(BASE_DIR / "speedtest-bot.env"),
))
URL_LIST = "https://www.baipiao.eu.org/cloudflare/url"
IP_LIST_URLS = {
    4: "https://www.baipiao.eu.org/cloudflare/ips-v4",
    6: "https://www.baipiao.eu.org/cloudflare/ips-v6",
}
USER_AGENT = "Mozilla/5.0"
MIN_RETRY_SPEED_MBPS = 100
MIN_APPLY_SPEED_MBPS = 50
MAX_ACCEPTABLE_VARIATION = 0.25
MAX_REGIONAL_ROUNDS = 3
IPS_PER_REGIONAL_ROUND = 2
MAX_NODE_CF_IPS = 2
EARLY_DISCARD_SECONDS = 3.0
DEFAULT_SPEED_DURATION = 30.0

# OpenWrt python3-light may not bundle decimal/fractions, which the standard
# library's statistics module imports. These three numeric operations are all
# we need for RTT and speed-window aggregation; avoid adding python3-decimal.
def arithmetic_mean(values):
    values = list(values)
    if not values:
        raise ValueError("mean requires at least one value")
    return sum(values) / len(values)


def numeric_median(values):
    ordered = sorted(values)
    if not ordered:
        raise ValueError("median requires at least one value")
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def population_stddev(values, mean_value=None):
    if not values:
        raise ValueError("population standard deviation requires values")
    if mean_value is None:
        mean_value = arithmetic_mean(values)
    return (sum((value - mean_value) ** 2 for value in values) / len(values)) ** 0.5



def direct_read(url, timeout=8):
    import urllib.request

    sources = [("direct", None)]
    sources.extend(("configured proxy", proxy) for proxy in telegram_proxy_chain())
    failures = []
    for source_name, proxy in sources:
        if proxy is None:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        else:
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": proxy, "https": proxy})
            )
        for attempt in range(1, 4):
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            try:
                with opener.open(request, timeout=timeout) as response:
                    content = response.read(32 * 1024 * 1024).decode("utf-8").strip()
                if not content:
                    raise ValueError("empty response")
                return content
            except (OSError, TimeoutError, ValueError) as error:
                failures.append("%s attempt %d: %s" % (source_name, attempt, error))
                if attempt < 3:
                    time.sleep(attempt)
    raise RuntimeError(
        "could not download %s after direct and configured-proxy retries (%s)"
        % (url, "; ".join(failures[-6:]))
    )


def load_cached(name, url, refresh=False):
    DATA_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = DATA_DIR / name
    if refresh or not path.is_file():
        try:
            content = direct_read(url)
        except Exception as error:
            raise RuntimeError("failed to refresh %s from %s: %s" % (name, url, error)) from error
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(content + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    return path.read_text(encoding="utf-8").strip()


def make_candidates(ip_version, limit, supplied_ips, refresh):
    if supplied_ips:
        candidates = [str(ipaddress.ip_address(value)) for value in supplied_ips]
        if any(ipaddress.ip_address(value).version != ip_version for value in candidates):
            raise ValueError("candidate IP family does not match --family")
        return list(dict.fromkeys(candidates))

    raw_list = load_cached(
        "ips-v%d.txt" % ip_version,
        IP_LIST_URLS[ip_version],
        refresh,
    )
    networks = []
    for line in raw_list.splitlines():
        value = line.strip().split("#", 1)[0].strip()
        if not value:
            continue
        network = ipaddress.ip_network(value, strict=False)
        if network.version == ip_version:
            networks.append(network)
    if not networks:
        raise ValueError("IP range list contains no IPv%d networks" % ip_version)

    sample = random.sample(networks, min(limit, len(networks)))
    candidates = []
    for network in sample:
        first = int(network.network_address)
        last = int(network.broadcast_address)
        if ip_version == 4 and network.num_addresses > 2:
            first += 1
            last -= 1
        candidates.append(str(ipaddress.ip_address(random.randint(first, last))))
    return list(dict.fromkeys(candidates))


def parse_speed_url(value, refresh):
    if value:
        parsed = value
    else:
        parsed = load_cached("url.txt", URL_LIST, refresh)
    from urllib.parse import urlsplit

    parts = urlsplit(parsed if "://" in parsed else "//" + parsed)
    if not parts.hostname:
        raise ValueError("invalid speed-test URL: %s" % parsed)
    request_path = parts.path or "/"
    if parts.query:
        request_path += "?" + parts.query
    return parts.scheme, parts.hostname, request_path


def one_rtt(ip, port, use_tls):
    started = time.monotonic()
    raw_socket = None
    try:
        raw_socket = socket.create_connection((ip, port), timeout=1)
        tcp_ms = int((time.monotonic() - started) * 1000)
        raw_socket.settimeout(1)
        connection = raw_socket
        if use_tls:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            connection = context.wrap_socket(raw_socket, server_hostname="cloudflare.com")
            raw_socket = None
        request = (
            "GET / HTTP/1.1\r\n"
            "Host: cloudflare.com\r\n"
            "User-Agent: %s\r\n"
            "Connection: close\r\n\r\n" % USER_AGENT
        ).encode("ascii")
        connection.sendall(request)
        response = http.client.HTTPResponse(connection)
        response.begin()
        ray = response.getheader("CF-RAY", "")
        response.close()
        if not ray:
            return None
        colo = ray.rsplit("-", 1)[-1].strip().upper()
        return {"latency_ms": tcp_ms, "colo": colo if re.fullmatch(r"[A-Z0-9]{3,4}", colo) else ""}
    except (OSError, ssl.SSLError, http.client.HTTPException):
        return None
    finally:
        if raw_socket is not None:
            raw_socket.close()


def test_rtt(ip, port, use_tls):
    measurements = []
    for _ in range(3):
        measured = one_rtt(ip, port, use_tls)
        if measured is None:
            return None
        measurements.append(measured)
    colos = [item["colo"] for item in measurements if item["colo"]]
    colo = max(set(colos), key=colos.count) if colos else ""
    return {
        "latency_ms": round(arithmetic_mean(item["latency_ms"] for item in measurements)),
        "colo": colo,
    }


def route_link_capacity_mbps(ip):
    try:
        route = subprocess.run(
            ["ip", "-o", "route", "get", ip],
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        ).stdout
        match = re.search(r"\bdev\s+(\S+)", route)
        if match:
            speed_file = Path("/sys/class/net/%s/speed" % match.group(1))
            speed = int(speed_file.read_text(encoding="ascii").strip())
            if speed > 0:
                return float(speed)
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return 1000.0


def best_case_average_mbps(bytes_received, elapsed_seconds, duration_seconds, link_capacity_mbps):
    remaining = max(0.0, duration_seconds - elapsed_seconds)
    return (bytes_received * 8 / 1_000_000 + remaining * link_capacity_mbps) / duration_seconds


def download_speed(
    ip,
    scheme,
    domain,
    request_path,
    use_tls,
    duration=20.0,
    discard_after=5.0,
    minimum_speed_mbps=100.0,
    early_discard_after=EARLY_DISCARD_SECONDS,
    progress_callback=None,
    progress_label="",
    allow_early_discard=True,
    require_discard_window=False,
):
    port = 443 if scheme == "https" else 80
    windows = []
    window_bytes = 0
    response_started = None
    window_started = None
    deadline = None
    tcp_ms = None
    colo = ""
    empty_responses = 0
    response_bytes = 0
    total_bytes = 0
    request_count = 0
    early_rejected = False
    early_speed_mbps = None
    best_case_speed_mbps = None
    link_capacity_mbps = route_link_capacity_mbps(ip)
    connection = None
    response = None
    last_progress = 0.0

    def next_response(timeout=3.0):
        nonlocal request_count
        request_count += 1
        started = time.monotonic()
        raw_socket = socket.create_connection((ip, port), timeout=timeout)
        raw_socket.settimeout(min(1.0, timeout))
        active_connection = raw_socket
        try:
            if use_tls:
                context = ssl.create_default_context()
                active_connection = context.wrap_socket(raw_socket, server_hostname=domain)
                raw_socket = None
            request = (
                "GET %s HTTP/1.1\r\n"
                "Host: %s\r\n"
                "User-Agent: %s\r\n"
                "Accept-Encoding: identity\r\n"
                "Connection: close\r\n\r\n"
            ) % (request_path, domain, USER_AGENT)
            active_connection.sendall(request.encode("ascii"))
            active_response = http.client.HTTPResponse(active_connection)
            active_response.begin()
            ray = active_response.getheader("CF-RAY", "")
            if active_response.status < 200 or active_response.status >= 300 or not ray:
                active_response.close()
                active_connection.close()
                return None, None, None, None
            return active_connection, active_response, int((time.monotonic() - started) * 1000), ray
        except Exception:
            active_connection.close()
            if raw_socket is not None:
                raw_socket.close()
            raise

    try:
        connection, response, tcp_ms, ray = next_response()
        if response is None:
            return None
        colo = ray.rsplit("-", 1)[-1]
        response_started = time.monotonic()
        window_started = response_started
        deadline = response_started + duration

        def record_window(now):
            nonlocal window_bytes, window_started, early_rejected, early_speed_mbps
            nonlocal best_case_speed_mbps
            elapsed = now - window_started
            if elapsed >= 1:
                window_end = now - response_started
                window_begin = window_started - response_started
                windows.append((window_begin, window_end, window_bytes / 1024 / elapsed))
                window_bytes = 0
                window_started = now
            elapsed_overall = now - response_started
            if elapsed_overall >= early_discard_after and early_speed_mbps is None:
                early_speed_mbps = total_bytes * 8 / elapsed_overall / 1_000_000
            if allow_early_discard and elapsed_overall >= early_discard_after:
                best_case_speed_mbps = best_case_average_mbps(
                    total_bytes,
                    elapsed_overall,
                    duration,
                    link_capacity_mbps,
                )
                if best_case_speed_mbps < minimum_speed_mbps:
                    early_rejected = True

        while time.monotonic() < deadline:
            now = time.monotonic()
            if progress_callback and now - last_progress >= 3:
                elapsed = now - response_started
                current_mbps = total_bytes * 8 / max(elapsed, 0.1) / 1_000_000
                progress_callback("%s：已测 %.0f/%.0f 秒，当前累计均速 %.1f Mbps" % (
                    progress_label, min(elapsed, duration), duration, current_mbps,
                ))
                last_progress = now
            try:
                chunk = response.read1(8 * 1024)
            except socket.timeout:
                record_window(time.monotonic())
                if early_rejected:
                    break
                continue
            except (OSError, http.client.HTTPException, TimeoutError):
                break
            if not chunk:
                if response_bytes:
                    empty_responses = 0
                else:
                    empty_responses += 1
                response.close()
                connection.close()
                remaining = deadline - time.monotonic()
                if empty_responses >= 3 or remaining <= 0:
                    break
                connection, response, _, ray = next_response(min(3.0, remaining))
                if response is None:
                    break
                response_bytes = 0
                continue
            now = time.monotonic()
            response_bytes += len(chunk)
            total_bytes += len(chunk)
            window_bytes += len(chunk)
            record_window(now)
            if early_rejected:
                break

        if early_rejected:
            return {
                "ip": ip,
                "status": "early_discard",
                "early_speed_mbps": round(early_speed_mbps, 2),
                "best_case_average_mbps": round(best_case_speed_mbps, 2),
                "sampled_seconds": round(time.monotonic() - response_started, 2),
                "request_count": request_count,
                "colo": colo,
            }

        elapsed = time.monotonic() - window_started
        if elapsed >= 0.5:
            windows.append((window_started - response_started, time.monotonic() - response_started,
                            window_bytes / 1024 / elapsed))
        sampled_seconds = time.monotonic() - response_started
        if sampled_seconds < duration - 1.0:
            return {
                "ip": ip,
                "status": "incomplete",
                "sampled_seconds": round(sampled_seconds, 2),
                "request_count": request_count,
                "colo": colo,
                "tcp_ms": tcp_ms,
            }
        if not windows:
            return None
        stable_windows = [rate for start, _, rate in windows if start >= discard_after]
        if not stable_windows:
            if require_discard_window:
                return None
            stable_windows = [rate for _, _, rate in windows]
        average_kib = arithmetic_mean(stable_windows)
        median_kib = numeric_median(stable_windows)
        variation = population_stddev(stable_windows, average_kib) / average_kib if average_kib else 1.0
        return {
            "ip": ip,
            "tcp_ms": tcp_ms,
            "stable_kib": int(average_kib),
            "average_kib": int(average_kib),
            "median_kib": int(median_kib),
            "variation_coefficient": round(variation, 3),
            "stable": variation <= MAX_ACCEPTABLE_VARIATION,
            "windows_kib": [int(value) for _, _, value in windows],
            "sampled_seconds": round(sampled_seconds, 2),
            "request_count": request_count,
            "colo": colo,
        }
    except (OSError, ssl.SSLError, http.client.HTTPException, TimeoutError):
        return None
    finally:
        if response is not None:
            response.close()
        if connection is not None:
            connection.close()


def _worker_emit(message):
    sys.stdout.write(json.dumps(message, ensure_ascii=True) + "\n")
    sys.stdout.flush()


def isolated_network_worker(action):
    """Run candidate TCP sockets in a non-root UID excluded from sing-box local eBPF."""
    if os.geteuid() == 0:
        raise RuntimeError("network test worker must not run as root")
    payload = json.load(sys.stdin)
    signal.alarm(240)
    if action == "rtt":
        candidates = payload["candidates"]
        port = int(payload["port"])
        tls = bool(payload["tls"])
        workers = max(1, min(int(payload["workers"]), 50))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            future_to_ip = {
                pool.submit(test_rtt, ip, port, tls): ip for ip in candidates
            }
            for future in concurrent.futures.as_completed(future_to_ip):
                _worker_emit({
                    "type": "rtt",
                    "ip": future_to_ip[future],
                    "measurement": future.result(),
                })
    elif action == "speed":
        params = payload["params"]
        params["progress_callback"] = lambda message: _worker_emit({
            "type": "progress", "message": message,
        })
        measurement = download_speed(**params)
        _worker_emit({"type": "result", "measurement": measurement})
    else:
        raise ValueError("unknown network worker operation")
    return 0


def isolated_worker_messages(uid, action, payload):
    """Stream IPC from an eBPF-exempt UID without changing kernel routing tables."""
    if os.geteuid() != 0 or uid <= 0:
        raise RuntimeError("isolated CF tests require a root controller and non-root worker")
    # The subprocess primary GID overrides systemd's Group= setting. Keep
    # ShellCrash's verified socket bypass for isolated RTT/download workers.
    gid = configured_process_gid(uid)
    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--network-worker", action],
        cwd=str(BASE_DIR),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        user=uid,
        group=gid,
        extra_groups=(),
    )
    try:
        process.stdin.write(json.dumps(payload))
        process.stdin.close()
        for line in process.stdout:
            if line.strip():
                yield json.loads(line)
        error_text = process.stderr.read().strip()
        if process.wait() != 0:
            raise RuntimeError(
                "isolated CF network worker failed (UID %d, GID %d): %s"
                % (uid, gid, error_text[-900:] or "no error text")
            )
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def measure_rtt_candidates(candidates, port, use_tls, workers, network_uid=None):
    if network_uid is not None:
        payload = {
            "candidates": candidates, "port": port,
            "tls": use_tls, "workers": workers,
        }
        received = 0
        for message in isolated_worker_messages(network_uid, "rtt", payload):
            if message.get("type") != "rtt":
                raise RuntimeError("invalid RTT worker response")
            received += 1
            yield message["ip"], message["measurement"]
        if received != len(candidates):
            raise RuntimeError("isolated RTT worker returned incomplete candidate results")
        return
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        future_to_ip = {
            pool.submit(test_rtt, ip, port, use_tls): ip for ip in candidates
        }
        for future in concurrent.futures.as_completed(future_to_ip):
            yield future_to_ip[future], future.result()


def measure_download(routes, ip, scheme, domain, request_path, use_tls, **kwargs):
    if routes.network_uid is None:
        return download_speed(ip, scheme, domain, request_path, use_tls, **kwargs)
    callback = kwargs.pop("progress_callback", None)
    params = {
        "ip": ip, "scheme": scheme, "domain": domain,
        "request_path": request_path, "use_tls": use_tls,
        **kwargs,
    }
    measurement_seen = False
    measurement = None
    for message in isolated_worker_messages(
        routes.network_uid, "speed", {"params": params}
    ):
        if message.get("type") == "progress":
            if callback:
                callback(message["message"])
        elif message.get("type") == "result":
            measurement_seen = True
            measurement = message["measurement"]
        else:
            raise RuntimeError("invalid bandwidth worker response")
    if not measurement_seen:
        raise RuntimeError("isolated bandwidth worker returned no measurement")
    return measurement


class DirectCandidateRoutes:
    def __init__(self, candidates):
        self.candidates = list(dict.fromkeys(candidates))
        self.routes = []
        self.old_signal_handlers = {}
        self.route_modes = {}
        self.network_uid = None

    @staticmethod
    def routing_mode(family, table):
        """Accept a known marked policy or an unmodified Linux default rule set."""
        rules = subprocess.run(
            ["ip", family, "rule", "show"],
            capture_output=True, text=True, timeout=3, check=True,
        ).stdout.splitlines()
        for line in rules:
            match = re.search(
                r"\bfwmark\s+(0x[\da-fA-F]+)(?:/\S+)?\s+lookup\s+%s\b" % table,
                line,
            )
            if match:
                return "marked-policy", match.group(1)

        # When the kernel only has the default rules, traffic uses main and
        # no proxy-specific routing table needs to be created for a speedtest.
        standard = {
            "from all lookup local",
            "from all lookup main",
            "from all lookup default",
        }
        observed = []
        singbox_gateway = False
        for line in rules:
            match = re.match(r"^\s*\d+:\s*(.*?)\s*$", line)
            if family == "-4" and SINGBOX_GATEWAY_RULE.fullmatch(line):
                singbox_gateway = True
            elif not match or match.group(1) not in standard:
                raise RuntimeError(
                    "Unrecognized %s policy rules without fwmark/table %s; "
                    "refusing to assume direct routing" % (family, table)
                )
            else:
                observed.append(match.group(1))
        if "from all lookup main" not in observed:
            raise RuntimeError("No normal main routing rule for %s" % family)
        return ("singbox-gateway", None) if singbox_gateway else ("native-direct", None)

    @staticmethod
    def active_singbox_local_ebpf():
        """Detect host-only sing-box eBPF, where ip rule still looks native."""
        if not SINGBOX_GATEWAY_CONFIG.is_file() or not Path("/run/systemd/system").is_dir():
            return False
        service = subprocess.run(
            ["systemctl", "is-active", "--quiet", "sing-box.service"],
            capture_output=True, text=True, timeout=3,
        )
        if service.returncode != 0:
            return False
        try:
            config = json.loads(SINGBOX_GATEWAY_CONFIG.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise RuntimeError("running sing-box config unreadable; refusing direct benchmark") from error
        return any(
            inbound.get("type") == "ebpf" and inbound.get("local", {}).get("enabled") is True
            for inbound in config.get("inbounds", [])
        )

    @staticmethod
    def verify_singbox_network_uid(require_gateway=True):
        """Avoid proxy-contaminated CF measurements from root under local eBPF."""
        if require_gateway:
            table_route = subprocess.run(
                ["ip", "-4", "route", "show", "table", "53136"],
                capture_output=True, text=True, timeout=3, check=True,
            ).stdout
            if not re.search(r"(?m)^local (?:default|0\.0\.0\.0/0) dev lo\b", table_route):
                raise RuntimeError("sing-box table 53136 is not a local TPROXY route")
            nft = subprocess.run(
                ["nft", "list", "table", "inet", "singbox_one_arm"],
                capture_output=True, text=True, timeout=3,
            )
            if nft.returncode:
                raise RuntimeError("sing-box single-NIC nftables table is not active")
        load_project_env()
        raw_uid = os.environ.get("X_TUNNEL_PROCESS_UID", "29002")
        try:
            uid = int(raw_uid)
        except ValueError as error:
            raise RuntimeError("invalid X_TUNNEL_PROCESS_UID") from error
        if uid < 1000:
            raise RuntimeError("CF worker needs an isolated non-root X-Tunnel UID")
        try:
            data = json.loads(SINGBOX_GATEWAY_CONFIG.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise RuntimeError("cannot inspect sing-box eBPF UID exclusions") from error
        ebpf_inbounds = [
            inbound for inbound in data.get("inbounds", [])
            if inbound.get("type") == "ebpf"
            and inbound.get("local", {}).get("enabled") is True
        ]
        if not ebpf_inbounds and require_gateway:
            # LXC standalone gateway deliberately uses nft PREROUTING only;
            # locally generated CF sockets are NOT transparently redirected.
            # Require all of the dedicated LXC markers before allowing this
            # no-eBPF path; avoid accidentally accepting another proxy.
            virt = subprocess.run(
                ["systemd-detect-virt", "--container"], capture_output=True,
                text=True, timeout=3,
            )
            guard = subprocess.run(
                ["nft", "list", "table", "inet", "singbox_lxc_guard"],
                capture_output=True, text=True, timeout=3,
            )
            service = subprocess.run(
                ["systemctl", "is-active", "--quiet", "sing-box.service"],
                capture_output=True, text=True, timeout=3,
            )
            if (
                virt.returncode == 0 and virt.stdout.strip() == "lxc"
                and guard.returncode == 0 and service.returncode == 0
                and any(inbound.get("type") == "tproxy"
                        and inbound.get("tag") == "lan-tproxy"
                        for inbound in data.get("inbounds", []))
                and not any(inbound.get("type") == "ebpf"
                            for inbound in data.get("inbounds", []))
            ):
                return None
            raise RuntimeError(
                "sing-box without eBPF was detected but dedicated LXC gateway "
                "markers or active service are missing; refusing CF benchmark"
            )
        if not any(
            uid in inbound.get("local", {}).get("exclude_uid", [])
            for inbound in ebpf_inbounds
        ):
            raise RuntimeError(
                "X-Tunnel UID %d is not excluded by sing-box local eBPF; "
                "refusing CF benchmark through a proxy" % uid
            )
        return uid

    @staticmethod
    def verify_native_route(value, family):
        """Check route via the main table, without changing any kernel routes."""
        result = subprocess.run(
            ["ip", family, "route", "get", value],
            capture_output=True, text=True, timeout=3, check=True,
        )
        fields = result.stdout.split()
        if not fields or fields[0] in (
            "local", "broadcast", "unreachable", "blackhole", "prohibit", "throw"
        ):
            raise RuntimeError("Route to %s is not ordinary unicast traffic" % value)
        if "dev" not in fields or fields.index("dev") + 1 >= len(fields):
            raise RuntimeError("Cannot determine outgoing interface for %s" % value)
        device = fields[fields.index("dev") + 1]
        if re.match(r"^(?:tun|tap|wg|utun|tailscale|zt|ppp|ipsec)\w*", device, re.I):
            raise RuntimeError(
                "Route to %s uses tunnel interface %s; refusing unsafe direct test"
                % (value, device)
            )
        if "table" in fields:
            index = fields.index("table") + 1
            if index >= len(fields) or fields[index] not in ("main", "254"):
                raise RuntimeError("Route to %s does not use the main table" % value)
        print("Native route to %s via %s; no policy route changes" % (value, device), flush=True)

    @staticmethod
    def route_command(route, operation):
        network = "%s/%d" % (route["ip"], route["prefix"])
        command = ["ip", route["family"], "route", operation, network]
        if operation == "add" and route.get("gateway"):
            command.extend(["via", route["gateway"]])
        if operation == "add":
            command.extend(["dev", route["device"]])
        command.extend(["table", route["table"]])
        return command

    @classmethod
    def remove_recorded_route(cls, route):
        try:
            result = subprocess.run(
                ["ip", route["family"], "route", "show", "exact",
                 "%s/%d" % (route["ip"], route["prefix"]), "table", route["table"]],
                capture_output=True, text=True, timeout=3,
            )
            if result.returncode:
                return False
            matching_route = False
            for line in result.stdout.splitlines():
                fields = line.split()
                device = fields[fields.index("dev") + 1] if "dev" in fields else None
                gateway = fields[fields.index("via") + 1] if "via" in fields else None
                if device == route["device"] and gateway == route.get("gateway"):
                    matching_route = True
                    deleted = subprocess.run(
                        cls.route_command(route, "del"),
                        capture_output=True, text=True, timeout=3,
                    )
                    if deleted.returncode:
                        return False
            return True
        except (OSError, subprocess.SubprocessError, KeyError, ValueError):
            return False

    @classmethod
    def cleanup_stale_routes(cls):
        if not ROUTE_STATE_FILE.exists():
            return
        try:
            routes = json.loads(ROUTE_STATE_FILE.read_text(encoding="utf-8"))
            if not isinstance(routes, list):
                raise ValueError("route-state file must contain a list")
        except (OSError, ValueError) as error:
            raise RuntimeError("cannot read stale direct-route state; refusing to start a scan") from error
        if any(not isinstance(route, dict) for route in routes):
            raise RuntimeError("stale direct-route state is malformed; refusing to start a scan")
        if not all(cls.remove_recorded_route(route) for route in routes):
            raise RuntimeError("could not remove stale direct routes; refusing to start a scan")
        ROUTE_STATE_FILE.unlink(missing_ok=True)

    def save_route_state(self):
        descriptor, temporary = tempfile.mkstemp(
            prefix=".x-tunnel-routes-", dir=ROUTE_STATE_FILE.parent,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as route_file:
                json.dump(self.routes, route_file)
                route_file.write("\n")
                route_file.flush()
                os.fsync(route_file.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, ROUTE_STATE_FILE)
        except Exception:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise

    def signal_handler(self, signum, frame):
        raise SystemExit(128 + signum)

    def install_route(self, value):
        address = ipaddress.ip_address(value)
        family = "-6" if address.version == 6 else "-4"
        table = "101" if address.version == 6 else "100"
        if family not in self.route_modes:
            self.route_modes[family] = self.routing_mode(family, table)
            print("Speedtest route mode for %s: %s" %
                  (family, self.route_modes[family][0]), flush=True)
        mode, mark = self.route_modes[family]
        if mode in ("native-direct", "singbox-gateway"):
            self.verify_native_route(value, family)
            return
        route = subprocess.run(
            ["ip", family, "route", "get", value],
            capture_output=True, text=True, timeout=3, check=True,
        )
        fields = route.stdout.split()
        device = fields[fields.index("dev") + 1] if "dev" in fields else None
        gateway = fields[fields.index("via") + 1] if "via" in fields else None
        if not device:
            raise RuntimeError("cannot determine direct interface for candidate %s" % value)
        entry = {
            "family": family,
            "ip": value,
            "prefix": address.max_prefixlen,
            "table": table,
            "device": device,
            "gateway": gateway,
        }
        existing = subprocess.run(
            ["ip", family, "route", "show", "exact",
             "%s/%d" % (value, address.max_prefixlen), "table", table],
            capture_output=True, text=True, timeout=3, check=True,
        ).stdout.strip()
        if existing:
            raise RuntimeError("a route already exists for candidate %s; refusing to override it" % value)
        self.routes.append(entry)
        self.save_route_state()
        result = subprocess.run(self.route_command(entry, "add"), capture_output=True, text=True)
        if result.returncode:
            self.routes.remove(entry)
            self.save_route_state()
            raise RuntimeError("could not install direct bypass route for %s: %s" % (
                value, result.stderr.strip(),
            ))
        verified = subprocess.run(
            ["ip", family, "route", "get", value, "mark", mark],
            capture_output=True, text=True, timeout=3, check=True,
        ).stdout.split()
        verified_device = verified[verified.index("dev") + 1] if "dev" in verified else None
        verified_table = verified[verified.index("table") + 1] if "table" in verified else None
        if verified_device != device or verified_table != table or "local" in verified:
            raise RuntimeError("marked traffic for %s is not confirmed direct; refusing to measure" % value)

    def __enter__(self):
        if os.geteuid() != 0:
            raise PermissionError("run as root to add temporary direct routes for candidate IPs")
        self.cleanup_stale_routes()
        for signum in (signal.SIGTERM, signal.SIGINT):
            self.old_signal_handlers[signum] = signal.signal(signum, self.signal_handler)
        try:
            for value in self.candidates:
                self.install_route(value)
            if any(mode == "singbox-gateway" for mode, _ in self.route_modes.values()):
                self.network_uid = self.verify_singbox_network_uid(require_gateway=True)
                if self.network_uid is None:
                    print(
                        "LXC pure-TPROXY gateway detected; CF probes use ordinary "
                        "root OUTPUT (not LAN PREROUTING); no route changes",
                        flush=True,
                    )
                else:
                    print(
                        "sing-box gateway detected; CF probes run under eBPF-excluded "
                        "UID %d without changing policy routes" % self.network_uid,
                        flush=True,
                    )
            elif any(mode == "native-direct" for mode, _ in self.route_modes.values()) and (
                self.active_singbox_local_ebpf()
            ):
                self.network_uid = self.verify_singbox_network_uid(require_gateway=False)
                print(
                    "Host-only sing-box eBPF detected; CF probes run under excluded "
                    "UID %d using ordinary main-table routes" % self.network_uid,
                    flush=True,
                )
        except BaseException:
            self.__exit__(None, None, None)
            raise
        if self.routes:
            print("Installed temporary direct bypass routes for %d candidate IP(s)" % len(self.routes), flush=True)
        else:
            print("Native routing validated for %d candidate IP(s); no routes added" %
                  len(self.candidates), flush=True)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            remaining = [
                route for route in reversed(self.routes)
                if not self.remove_recorded_route(route)
            ]
            self.routes = list(reversed(remaining))
            if self.routes:
                try:
                    self.save_route_state()
                    print("WARNING: some temporary bypass routes remain; saved cleanup state", file=sys.stderr)
                except OSError as error:
                    print("WARNING: unable to save bypass cleanup state: %s" % error, file=sys.stderr)
            else:
                ROUTE_STATE_FILE.unlink(missing_ok=True)
        finally:
            for signum, handler in self.old_signal_handlers.items():
                signal.signal(signum, handler)
            self.old_signal_handlers.clear()


class TelegramProgress:
    def __init__(self):
        load_project_env()
        credentials = {}
        try:
            credentials = read_shell_config_values(BOT_CONFIG_FILE, {"TG_TOKEN", "TG_CHATID"})
        except (OSError, ValueError):
            credentials = {}
        self.token = credentials.get("TG_TOKEN", "")
        self.chat_id = credentials.get("TG_CHATID", "")
        self.message_id = None
        self.proxy = None
        self.proxies = telegram_proxy_chain()
        self.current_text = None
        self.last_sent_text = None
        self.stop_event = threading.Event()
        self.thread = None
        if self.token and self.chat_id:
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()

    def _request(self, method, text, parse_mode=None):
        import urllib.error
        import urllib.request

        endpoint = "https://api.telegram.org/bot%s/%s" % (self.token, method)
        payload = {"chat_id": self.chat_id, "text": text}
        if self.message_id is not None:
            payload["message_id"] = self.message_id
        if parse_mode:
            payload["parse_mode"] = parse_mode
        proxies = [self.proxy] if self.proxy else []
        proxies.extend(proxy for proxy in self.proxies if proxy not in proxies)
        for proxy in proxies:
            request = urllib.request.Request(
                endpoint, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json"}, method="POST",
            )
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
            try:
                with opener.open(request, timeout=12) as response:
                    result = json.loads(response.read(64 * 1024).decode("utf-8"))
                if result.get("ok"):
                    self.proxy = proxy
                    if method == "sendMessage":
                        self.message_id = result["result"]["message_id"]
                    return True
            except (OSError, TimeoutError, ValueError, urllib.error.URLError, http.client.HTTPException):
                pass
        return False

    def update(self, text, force=False):
        if not self.token or not self.chat_id:
            return
        self.current_text = text

    def _send_edit(self, text, parse_mode=None):
        method = "editMessageText" if self.message_id is not None else "sendMessage"
        return self._request(method, text, parse_mode=parse_mode)

    def _run(self):
        while not self.stop_event.wait(3):
            text = self.current_text
            if text and text != self.last_sent_text and self._send_edit(text):
                self.last_sent_text = text

    def finish(self, report, error=None):
        if not self.token or not self.chat_id:
            return
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=25)
        body = html.escape(notification_text(report, error))
        if (report or {}).get("mode") == "single-ip" and report.get("ip"):
            ip = report["ip"]
            body = body.replace(html.escape(ip), "<code>%s</code>" % html.escape(ip))
        for record in (report or {}).get("candidates", []):
            ip = record.get("ip")
            if ip:
                body = body.replace(html.escape(ip), "<code>%s</code>" % html.escape(ip))
        chunks = split_telegram_message(body)
        if chunks:
            if self._send_edit(chunks[0], parse_mode="HTML"):
                self.last_sent_text = chunks[0]
            for chunk in chunks[1:]:
                self._request("sendMessage", chunk, parse_mode="HTML")

def apply_ips(assignments):
    import yaml

    content = CONFIG_FILE.read_text(encoding="utf-8")
    document = yaml.safe_load(content) or {}
    defaults = document.get("defaults") or {}
    default_ips = [
        value.strip()
        for value in str(defaults.get("cfip", "")).split(",")
        if value.strip()
    ]
    known_names = {
        row.split("|", 1)[0].strip()
        for row in document.get("nodes", [])
        if isinstance(row, str) and "|" in row
    }
    missing = set(assignments) - known_names
    if missing:
        raise ValueError("node(s) not found in x-tunnel.yaml: %s" % ", ".join(sorted(missing)))

    updated_lines = []
    changed = False
    for line in content.splitlines(keepends=True):
        match = re.fullmatch(r'(\s*-\s*)((?:"(?:\\.|[^"\\])*"))(\s*(?:#.*)?\s*)(\r?\n)?', line)
        if not match:
            updated_lines.append(line)
            continue
        row = json.loads(match.group(2))
        fields = [field.strip() for field in row.split("|")]
        if fields[0] not in assignments:
            updated_lines.append(line)
            continue
        while len(fields) < 4:
            fields.append("")
        assigned_ips = assignments[fields[0]]
        if isinstance(assigned_ips, (list, tuple)):
            assigned_ips = [str(value).strip() for value in assigned_ips if str(value).strip()]
        else:
            assigned_ips = [str(assigned_ips).strip()] if str(assigned_ips).strip() else []
        existing_ips = [
            value.strip()
            for value in fields[3].split(",")
            if value.strip()
        ]
        # Keep the known configuration as fallback: HTTP speed alone does not
        # prove that a CF edge will carry this node's WebSocket tunnel reliably.
        fields[3] = ",".join(dict.fromkeys(assigned_ips + existing_ips + default_ips))
        new_row = "|".join(fields)
        newline = match.group(4) or ""
        updated_lines.append(
            match.group(1) + json.dumps(new_row, ensure_ascii=False) + match.group(3) + newline
        )
        changed = True

    if not changed:
        raise ValueError("could not locate quoted node rows in x-tunnel.yaml")
    mode = CONFIG_FILE.stat().st_mode & 0o777
    descriptor, temporary = tempfile.mkstemp(prefix=".x-tunnel-config-", dir=CONFIG_FILE.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write("".join(updated_lines))
        os.chmod(temporary, mode)
        os.replace(temporary, CONFIG_FILE)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    for node_name in assignments:
        print(
            "updated %s CF IP in x-tunnel.yaml with configured fallback IPs; "
            "x-tunnel will reload automatically" % node_name
        )


def configured_node_names():
    import yaml

    document = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8")) or {}
    nodes = document.get("nodes") or []
    if not isinstance(nodes, list):
        raise ValueError("nodes must be a list of pipe-delimited strings")
    max_nodes = document.get("max_nodes", 5)
    if isinstance(max_nodes, bool) or not isinstance(max_nodes, int) or max_nodes < 1:
        raise ValueError("max_nodes must be a positive integer")
    return [
        row.split("|", 1)[0].strip()
        for row in nodes[:max_nodes]
        if isinstance(row, str) and "|" in row and row.split("|", 1)[0].strip()
    ]


FLAG_RE = re.compile(r"([\U0001F1E6-\U0001F1FF]{2})")
COLO_REGIONS = {
    "HKG": "HK", "NRT": "JP", "HND": "JP", "KIX": "JP", "NGO": "JP",
    "TPE": "TW", "KHH": "TW", "ICN": "KR", "SIN": "SG",
    "LAX": "US", "SJC": "US", "SFO": "US", "SEA": "US", "PDX": "US",
    "LAS": "US", "SAN": "US", "PHX": "US", "DEN": "US", "DFW": "US",
    "IAH": "US", "ORD": "US", "ATL": "US", "MIA": "US", "EWR": "US",
    "IAD": "US", "JFK": "US", "BOS": "US", "YYZ": "CA", "YVR": "CA",
    "LHR": "GB", "MAN": "GB", "FRA": "DE", "MUC": "DE", "AMS": "NL",
    "CDG": "FR", "MAD": "ES", "MXP": "IT", "ZRH": "CH", "VIE": "AT",
    "WAW": "PL", "ARN": "SE", "HEL": "FI", "CPH": "DK", "DUB": "IE",
    "GRU": "BR", "EZE": "AR", "SYD": "AU", "MEL": "AU", "BOM": "IN",
    "DEL": "IN", "JNB": "ZA",
}
REGION_FALLBACKS = {
    "HK": ("HK", "JP", "TW", "SG", "KR", "US"),
    "JP": ("JP", "HK", "KR", "TW", "SG", "US"),
    "US": ("US", "CA", "JP", "HK", "SG", "TW", "KR"),
    "SG": ("SG", "HK", "TW", "JP", "KR", "US"),
    "KR": ("KR", "JP", "HK", "TW", "SG", "US"),
    "TW": ("TW", "HK", "JP", "SG", "KR", "US"),
    "CA": ("CA", "US", "JP", "HK", "SG"),
}
CITY_COLOS = {
    "sanjose": ("SJC", "SFO", "LAX"),
    "san jose": ("SJC", "SFO", "LAX"),
    "圣何塞": ("SJC", "SFO", "LAX"),
    "los angeles": ("LAX", "SJC", "SFO"),
    "losangeles": ("LAX", "SJC", "SFO"),
    "洛杉矶": ("LAX", "SJC", "SFO"),
    "tokyo": ("NRT", "HND"),
    "东京": ("NRT", "HND"),
    "osaka": ("KIX", "NGO", "NRT"),
    "大阪": ("KIX", "NGO", "NRT"),
}
REGION_ALIASES = {"UM": "US"}


def node_region(name):
    flag = FLAG_RE.search(name)
    if flag:
        value = "".join(chr(ord(character) - 0x1F1E6 + ord("A")) for character in flag.group(1))
        return REGION_ALIASES.get(value, value)
    lowered = name.lower()
    text_regions = (
        ("HK", ("hong kong", "hongkong", "香港", "hkg")),
        ("JP", ("japan", "日本", "tokyo", "东京", "osaka", "大阪", "nrt", "kix")),
        ("US", ("united states", "usa", "美国", "sanjose", "san jose", "圣何塞", "lax", "sjc")),
        ("SG", ("singapore", "新加坡", "sin")),
        ("KR", ("south korea", "韩国", "首尔", "seoul", "icn")),
        ("TW", ("taiwan", "台湾", "台灣", "tpe")),
    )
    return next((region for region, markers in text_regions if any(marker in lowered for marker in markers)), None)


def candidate_region_rank(node_name, candidate):
    colo = str(candidate.get("colo", "")).upper()
    region = node_region(node_name)
    city_colos = next(
        (preferred for marker, preferred in CITY_COLOS.items() if marker in node_name.lower()),
        (),
    )
    if city_colos:
        if colo in city_colos:
            return city_colos.index(colo)
    candidate_country = COLO_REGIONS.get(colo)
    if region and candidate_country:
        nearby = REGION_FALLBACKS.get(region, (region,))
        if candidate_country in nearby:
            country_rank = nearby.index(candidate_country)
        else:
            country_rank = len(nearby) + 1
        if city_colos:
            return len(city_colos) + 1 + country_rank
        return country_rank
    return 100


def candidate_allowed_for_node(node_name, candidate):
    region = node_region(node_name)
    colo = str(candidate.get("colo", "")).upper()
    if region == "HK":
        return colo == "HKG"
    candidate_macro_region = colo_macro_region(colo)
    if region in {"JP", "TW", "SG", "KR"}:
        return candidate_macro_region == "asia"
    if region in {"US", "CA"}:
        return candidate_macro_region == "north_america"
    return True


def supplemental_candidate_ips(ip_version, node_names, history_days=30, per_node=MAX_NODE_CF_IPS + 1):
    import yaml

    candidates = []
    try:
        document = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8")) or {}
        max_nodes = document.get("max_nodes", 5)
        for row in (document.get("nodes") or [])[:max_nodes]:
            if not isinstance(row, str):
                continue
            fields = [field.strip() for field in row.split("|")]
            if len(fields) < 4:
                continue
            for value in fields[3].split(","):
                try:
                    address = ipaddress.ip_address(value.strip())
                except ValueError:
                    continue
                if address.version == ip_version:
                    candidates.append(str(address))
    except (OSError, TypeError, ValueError):
        pass

    cutoff = time.time() - history_days * 86400
    historical = {}
    for path in sorted(DATA_DIR.glob("scan-*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            modified = path.stat().st_mtime
            if modified < cutoff:
                continue
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        records = list(report.get("candidates", []))
        records.extend(
            attempt
            for test_round in report.get("speed_test_rounds", [])
            for attempt in test_round.get("results", [])
        )
        for record in records:
            if not isinstance(record, dict) or not record.get("ip"):
                continue
            try:
                address = ipaddress.ip_address(record["ip"])
            except ValueError:
                continue
            if address.version != ip_version:
                continue
            colo = str(record.get("colo") or record.get("rtt_colo") or "").upper()
            stable_kib = record.get("stable_kib_per_second")
            speed_mbps = record.get("stable_mbps")
            if speed_mbps is None and stable_kib is not None:
                speed_mbps = float(stable_kib) * 1024 * 8 / 1_000_000
            try:
                speed_mbps = float(speed_mbps)
                variation = float(record.get("variation_coefficient", 0))
            except (TypeError, ValueError):
                continue
            if (not colo or record.get("stable") is not True
                    or speed_mbps < MIN_APPLY_SPEED_MBPS
                    or variation > MAX_ACCEPTABLE_VARIATION):
                continue
            existing = historical.get(str(address))
            if existing is None or modified > existing["modified"]:
                historical[str(address)] = {
                    "ip": str(address),
                    "colo": colo,
                    "stable_mbps": speed_mbps,
                    "variation_coefficient": variation,
                    "modified": modified,
                }

    for name in node_names:
        eligible = [
            item for item in historical.values()
            if candidate_allowed_for_node(name, item)
        ]
        eligible.sort(key=lambda item: (
            candidate_region_rank(name, item),
            -item["stable_mbps"],
            -item["modified"],
        ))
        candidates.extend(item["ip"] for item in eligible[:per_node])
    return list(dict.fromkeys(candidates))


def colo_macro_region(colo):
    country = COLO_REGIONS.get(str(colo or "").upper())
    if country in {"HK", "JP", "SG", "KR", "TW"}:
        return "asia"
    if country in {"US", "CA"}:
        return "north_america"
    return None


def select_rtt_candidates(results, node_names, limit):
    ranked = sorted(results, key=lambda item: item["latency_ms"])
    active_regions = set()
    for name in node_names:
        region = node_region(name)
        if region in {"HK", "JP", "SG", "KR", "TW", "US", "CA"}:
            active_regions.add(region)

    selected = []
    selected_ips = set()
    region_labels = {
        "HK": "香港",
        "JP": "日本",
        "SG": "新加坡",
        "KR": "韩国",
        "TW": "台湾",
        "US": "美国",
        "CA": "加拿大",
    }
    for region in ("HK", "JP", "SG", "KR", "TW", "US", "CA"):
        if region not in active_regions:
            continue
        regional = min(
            (
                item for item in ranked
                if item["ip"] not in selected_ips
                and candidate_allowed_for_node(region_labels[region], item)
            ),
            key=lambda item: (
                candidate_region_rank(region_labels[region], item),
                item["latency_ms"],
            ),
            default=None,
        )
        if regional is not None and regional["ip"] not in selected_ips:
            selected.append(regional)
            selected_ips.add(regional["ip"])
        if len(selected) >= limit:
            return selected[:limit]
    selected.extend(item for item in ranked if item["ip"] not in selected_ips)
    return selected[:limit]


def prioritized_target_nodes(node_names, count):
    priority = {"HK": 0, "US": 1, "JP": 2, "SG": 3, "KR": 4, "TW": 5, "CA": 6}
    indexed = list(enumerate(node_names))
    indexed.sort(key=lambda pair: (priority.get(node_region(pair[1]), 20), pair[0]))
    return [name for _, name in indexed[:count]]


def candidate_matches_node_region(node_name, candidate):
    region = node_region(node_name)
    colo_country = COLO_REGIONS.get(str(candidate.get("colo", "")).upper())
    return bool(region and colo_country == region)


def matching_nodes_for_colo(colo, node_names):
    normalized_colo = str(colo or "").upper()
    candidate = {"colo": normalized_colo}
    candidate_country = COLO_REGIONS.get(normalized_colo)
    candidate_macro_region = colo_macro_region(normalized_colo)
    if not candidate_country or not candidate_macro_region:
        return []

    node_macro_regions = {
        "asia": {"HK", "JP", "TW", "SG", "KR"},
        "north_america": {"US", "CA"},
    }
    return [
        name for name in node_names
        if candidate_allowed_for_node(name, candidate)
        and node_region(name) in REGION_FALLBACKS
        and candidate_country in REGION_FALLBACKS[node_region(name)]
        and node_region(name) in node_macro_regions[candidate_macro_region]
    ]


def next_speedtest_batch(candidates, attempted_ips, target_nodes, limit=IPS_PER_REGIONAL_ROUND):
    available = [item for item in candidates if item["ip"] not in attempted_ips]
    pairs = []
    for node_index, name in enumerate(target_nodes):
        for candidate in available:
            if not candidate_allowed_for_node(name, candidate):
                continue
            pairs.append((
                not candidate_matches_node_region(name, candidate),
                node_index,
                candidate_region_rank(name, candidate),
                candidate["latency_ms"],
                candidate["ip"],
                candidate,
            ))
    pairs.sort(key=lambda pair: pair[:-1])

    batch = []
    assigned_nodes = set()
    assigned_ips = set()
    for _, node_index, _, _, candidate_ip, candidate in pairs:
        if node_index in assigned_nodes or candidate_ip in assigned_ips:
            continue
        batch.append(candidate)
        assigned_nodes.add(node_index)
        assigned_ips.add(candidate_ip)
        if len(batch) >= limit:
            break
    if len(batch) < limit:
        batch.extend(
            item for item in available
            if item["ip"] not in assigned_ips
        )
        batch = batch[:limit]
    return batch


def exact_region_coverage(assignments, target_nodes):
    return all(
        name in assignments and candidate_matches_node_region(name, assignments[name])
        for name in target_nodes
    )


def regionally_select(candidates, node_names, count):
    ranked = sorted(
        candidates,
        key=lambda item: (
            not item.get("stable", True),
            -item["stable_kib"],
            -item.get("median_kib", item.get("average_kib", item["stable_kib"])),
            item.get("variation_coefficient", 0),
            item["latency_ms"],
        ),
    )
    if not node_names:
        return ranked[:count], {}

    pairs = [
        (candidate_region_rank(name, candidate), candidate_index, node_index)
        for candidate_index, candidate in enumerate(ranked)
        for node_index, name in enumerate(node_names)
        if candidate_allowed_for_node(name, candidate)
        and candidate_matches_node_region(name, candidate)
    ]
    pairs.sort()
    assigned_nodes = set()
    assigned_candidates = set()
    assignments = {}
    for _, candidate_index, node_index in pairs:
        if node_index in assigned_nodes or candidate_index in assigned_candidates:
            continue
        name = node_names[node_index]
        assignments[name] = ranked[candidate_index]
        assigned_nodes.add(node_index)
        assigned_candidates.add(candidate_index)
        if len(assignments) >= min(count, len(node_names), len(ranked)):
            break

    for node_index, name in enumerate(node_names):
        if node_index in assigned_nodes:
            continue
        eligible = [
            (candidate_region_rank(name, candidate), candidate_index, candidate)
            for candidate_index, candidate in enumerate(ranked)
            if candidate_allowed_for_node(name, candidate)
        ]
        if not eligible:
            continue
        _, _, candidate = min(eligible, key=lambda item: (item[0], item[1]))
        assignments[name] = candidate
        assigned_nodes.add(node_index)

    selected = []
    assigned_ids = set()
    for name in node_names:
        candidate = assignments.get(name)
        if candidate is not None and id(candidate) not in assigned_ids:
            selected.append(candidate)
            assigned_ids.add(id(candidate))
    selected.extend(
        candidate for candidate in ranked
        if id(candidate) not in assigned_ids
    )
    selected = selected[:count]
    return selected, assignments


def select_candidates(measured, tiers_mbps, count, node_names=None):
    ranked = sorted(
        measured,
        key=lambda item: (
            not item.get("stable", True),
            -item["stable_kib"],
            -item.get("median_kib", item.get("average_kib", item["stable_kib"])),
            item.get("variation_coefficient", 0),
            item["latency_ms"],
        ),
    )
    writeable = [
        item for item in ranked
        if item.get("stable", False)
        and item["stable_kib"] * 1024 * 8 / 1_000_000 >= MIN_APPLY_SPEED_MBPS
    ]
    for tier in tiers_mbps:
        threshold_kib = max(tier, MIN_APPLY_SPEED_MBPS) * 1_000_000 / 8 / 1024
        qualified = [item for item in writeable if item["stable_kib"] >= threshold_kib]
        if len(qualified) >= count:
            selected, assignments = regionally_select(qualified, node_names or [], count)
            return selected, tier, True, assignments
    selected, assignments = regionally_select(writeable, node_names or [], count)
    return selected, tiers_mbps[-1], False, assignments


def writeable_candidates(measured):
    return [
        item for item in measured
        if item.get("stable", False)
        and item["stable_kib"] * 1024 * 8 / 1_000_000 >= MIN_APPLY_SPEED_MBPS
    ]


def has_node_ip_failover_coverage(measured, node_names, count=MAX_NODE_CF_IPS):
    eligible_candidates = writeable_candidates(measured)
    return bool(node_names) and all(
        len({item["ip"] for item in eligible_candidates if candidate_allowed_for_node(name, item)}) >= count
        for name in node_names
    )


def build_node_ip_assignments(measured, primary_assignments, max_ips=MAX_NODE_CF_IPS):
    eligible_candidates = writeable_candidates(measured)
    assignments = {}
    for name, primary in primary_assignments.items():
        eligible = [
            item for item in eligible_candidates
            if candidate_allowed_for_node(name, item)
        ]
        eligible.sort(key=lambda item: (
            candidate_region_rank(name, item),
            not item.get("stable", True),
            -item["stable_kib"],
            -item.get("median_kib", item.get("average_kib", item["stable_kib"])),
            item.get("variation_coefficient", 0),
            item["latency_ms"],
        ))
        ordered = [primary] + [item for item in eligible if item["ip"] != primary["ip"]]
        assigned_ips = []
        for item in ordered:
            if item["ip"] not in assigned_ips:
                assigned_ips.append(item["ip"])
            if len(assigned_ips) >= max_ips:
                break
        if assigned_ips:
            assignments[name] = assigned_ips
    return assignments


def save_report(report):
    DATA_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = DATA_DIR / ("scan-%s.json" % timestamp)
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    for path in (report_path, REPORT_FILE):
        descriptor, temporary_path = tempfile.mkstemp(prefix=".cfip-report-", dir=DATA_DIR)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output_file:
                output_file.write(payload)
            os.chmod(temporary_path, 0o600)
            os.replace(temporary_path, path)
        except Exception:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass
            raise
    return report_path


def read_shell_config_values(path, keys):
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$", line)
        if not match or match.group(1) not in keys:
            continue
        try:
            parsed = shlex.split(match.group(2), comments=True)
        except ValueError:
            continue
        values[match.group(1)] = parsed[0] if parsed else ""
    return values


def notification_text(report, error=None):
    if not report:
        return "❌ X-Tunnel CF测速失败\n原因：%s" % (error or "未生成测速报告")

    if report.get("mode") == "single-ip":
        result = report.get("result") or {}
        ip = report.get("ip", "未知")
        if error or not result:
            reason = report.get("measurement_error") or error or "未收到有效下载数据"
            sampled = report.get("sampled_seconds")
            suffix = "\n有效采样：%.1f/30 秒" % sampled if sampled is not None else ""
            return "❌ CF 单 IP 测速无效\nIP：%s\n原因：%s%s" % (ip, reason, suffix)
        stability = "稳定" if result.get("stable") else "波动较大"
        lines = [
            "✅ CF 单 IP 测速完成",
            "IP：%s" % ip,
            "30 秒测速；取第 5–30 秒窗口均速：%.2f Mbps" % result["average_mbps"],
            "窗口中位数：%.2f Mbps；波动系数：%.1f%%（%s）" % (
                result["median_mbps"], result["variation_coefficient"] * 100, stability,
            ),
            "Cloudflare Colo：%s；TCP 建连：%s ms" % (
                result.get("colo") or "未知", result.get("tcp_ms", "未知"),
            ),
        ]
        applied = report.get("applied_nodes", {})
        if applied:
            lines.append("已写入匹配地区节点：")
            lines.extend("  %s → %s" % (name, address) for name, address in applied.items())
        else:
            lines.append("未修改节点配置：%s" % report.get(
                "apply_reason", "未达到写入条件，保留原 CF IP。",
            ))
        if report.get("apply_error"):
            lines.append("配置写入失败：%s" % report["apply_error"])
        if report.get("report_path"):
            lines.append("报告：%s" % report["report_path"])
        return "\n".join(lines)

    candidate_rows = report.get("candidates", [])
    rtt_passed = sum(row.get("latency_ms") is not None for row in candidate_rows)
    speed_attempt_count = sum(len(row.get("speed_test_attempts", [])) for row in candidate_rows)
    speed_rows = [
        row for row in candidate_rows
        if row.get("speed_test_attempts") or row.get("status") in (
            "measured", "speed_test_failed", "early_discard", "incomplete",
        )
    ]
    selected_tier = report.get("selected_tier_mbps")
    nodes_missing_fallback = report.get("nodes_missing_fallback_ip", [])
    reached = report.get("met_target_count_at_tier", False) and not nodes_missing_fallback
    if error:
        title = "❌ X-Tunnel CF测速/写入失败"
    elif reached:
        title = "✅ X-Tunnel CF测速完成"
    elif report.get("met_target_count_at_tier", False):
        title = "⚠️ 测速达到速度目标，但部分节点缺少备用 IP"
    else:
        title = "⚠️ X-Tunnel CF测速完成，未满足目标数量/档位"

    lines = [
        title,
        "扫描候选：%d，RTT有效：%d，下载测速尝试：%d" % (
            len(candidate_rows), rtt_passed, speed_attempt_count,
        ),
    ]
    if selected_tier is not None:
        lines.append("目标档：%s Mbps；本次选取：%s" % (
            selected_tier,
            ", ".join(report.get("selected_ips", [])) or "无",
        ))
    regional_assignments = report.get("regional_assignments", {})
    if regional_assignments:
        lines.append("地区分配：")
        for name, assignment in regional_assignments.items():
            match_type = "地区直配" if assignment.get("direct_region_match") else "邻近回退"
            fallbacks = assignment.get("fallback_ips", [])
            fallback_summary = "；备用 " + ", ".join(
                "%s[%s]" % (item["ip"], item.get("colo") or "未知")
                for item in fallbacks
            ) if fallbacks else ""
            lines.append("  %s → %s[%s]（%s）" % (
                name,
                assignment.get("ip", ""),
                assignment.get("colo", "未知"),
                match_type,
            ) + fallback_summary)
    if nodes_missing_fallback:
        lines.append("备用 IP 不足（仅写入已测合格地址）：%s" % ", ".join(nodes_missing_fallback))
    preserved_nodes = report.get("preserved_nodes", [])
    if preserved_nodes:
        reasons = report.get("preserved_node_reasons", {})
        lines.append("未写入（保留原 CF IP）：")
        lines.extend("  %s：%s" % (name, reasons.get(name, "无可用匹配 IP")) for name in preserved_nodes)
    applied = report.get("applied_nodes", {})
    apply_mode = report.get("apply_mode")
    if applied:
        lines.append("写入 x-tunnel.yaml：成功")
        lines.extend("  %s → %s" % (name, ip) for name, ip in applied.items())
    elif apply_mode == "disabled":
        lines.append("写入 x-tunnel.yaml：未执行（--no-apply）")
    else:
        lines.append("写入 x-tunnel.yaml：未写入节点")
    if error:
        lines.append("错误：%s" % error)
    lines.append("测速IP明细：")
    rounds = report.get("speed_test_rounds", [])
    if rounds:
        for test_round in rounds:
            lines.append("第%d轮：%s" % (test_round["round"], test_round.get("result", "完成")))
            for attempt in test_round.get("results", []):
                if attempt.get("stable_mbps") is not None:
                    marker = "★" if attempt.get("selected") else "·"
                    stability = "稳定" if attempt.get("stable") else "波动CV %.0f%%" % (
                        attempt.get("variation_coefficient", 0) * 100,
                    )
                    lines.append("%s %s 均速%.1f/中位%.1fMbps RTT %sms %s %s" % (
                        marker,
                        attempt["ip"],
                        attempt.get("average_mbps", attempt["stable_mbps"]),
                        attempt.get("median_mbps", attempt["stable_mbps"]),
                        attempt.get("latency_ms", "?"),
                        attempt.get("colo", ""),
                        stability,
                    ))
                elif attempt.get("status") == "early_discard":
                    lines.append("⏩ %s 前3秒%.1fMbps；最终上限估算%.1fMbps，提前淘汰" % (
                        attempt["ip"],
                        attempt.get("early_speed_mbps", 0),
                        attempt.get("best_case_average_mbps", 0),
                    ))
                elif attempt.get("status") == "incomplete":
                    lines.append("× %s 采样不完整（%.1f秒），不计入候选" % (
                        attempt["ip"], attempt.get("sampled_seconds", 0),
                    ))
                else:
                    lines.append("× %s 下载测速失败" % attempt["ip"])
    else:
        for row in speed_rows:
            if row.get("status") == "measured":
                marker = "★" if row.get("selected") else "·"
                lines.append("%s %s %.1fMbps RTT %sms %s" % (
                    marker,
                    row["ip"],
                    row.get("stable_mbps", 0),
                    row.get("latency_ms", "?"),
                    row.get("colo", ""),
                ))
            else:
                lines.append("× %s 下载测速失败" % row["ip"])
    if report.get("report_path"):
        lines.append("报告：%s" % report["report_path"])
    return "\n".join(lines)


def split_telegram_message(text, limit=3500):
    chunks = []
    current = ""
    for line in text.splitlines() or [text]:
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        addition = line if not current else "\n" + line
        if len(current) + len(addition) > limit:
            chunks.append(current)
            current = line
        else:
            current += addition
    if current:
        chunks.append(current)
    return chunks


def run_single_ip(ip, progress, use_tls=False):
    address = ipaddress.ip_address(ip)
    ip = str(address)
    report = {
        "mode": "single-ip",
        "ip": ip,
        "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "duration_seconds": DEFAULT_SPEED_DURATION,
        "warmup_seconds": 5,
        "apply_mode": "matching-region-if-at-least-100-mbps",
        "apply_threshold_mbps": MIN_RETRY_SPEED_MBPS,
        "maximum_apply_variation_coefficient": MAX_ACCEPTABLE_VARIATION,
    }
    lock_path = Path(SCAN_LOCK_FILE)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w", encoding="utf-8") as scan_lock:
        try:
            fcntl.flock(scan_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("已有 CF 自动测速或单 IP 测速正在运行") from error

        progress.update("🔎 CF 单 IP 测速\nIP：%s\n准备直连路由；测速 30 秒，前 5 秒作为热身。" % ip, force=True)
        with DirectCandidateRoutes([ip]) as routes:
            _, domain, request_path = parse_speed_url(None, False)
            scheme = "https" if use_tls else "http"
            report.update({
                "scheme": scheme,
                "speed_test_host": domain,
                "speed_test_path": request_path,
            })
            measurement = measure_download(
                routes,
                ip,
                scheme,
                domain,
                request_path,
                scheme == "https",
                duration=DEFAULT_SPEED_DURATION,
                discard_after=5.0,
                minimum_speed_mbps=0.0,
                early_discard_after=30.0,
                progress_callback=lambda text: progress.update("🔎 CF 单 IP 测速\n" + text),
                progress_label=ip,
                allow_early_discard=False,
                require_discard_window=True,
            )
        if measurement and measurement.get("status") == "incomplete":
            report["status"] = "incomplete"
            report["sampled_seconds"] = measurement["sampled_seconds"]
            report["measurement_error"] = "连接提前中断，未完成 30 秒采样；结果无效且不会写入节点。"
            report["apply_reason"] = "采样不完整，保留原 CF IP。"
        elif measurement:
            result = {
                "average_mbps": round(measurement["average_kib"] * 1024 * 8 / 1_000_000, 2),
                "median_mbps": round(measurement["median_kib"] * 1024 * 8 / 1_000_000, 2),
                "variation_coefficient": measurement["variation_coefficient"],
                "stable": measurement["stable"],
                "windows_mbps": [round(value * 1024 * 8 / 1_000_000, 2) for value in measurement["windows_kib"]],
                "sampled_seconds": measurement["sampled_seconds"],
                "request_count": measurement["request_count"],
                "colo": measurement["colo"],
                "tcp_ms": measurement["tcp_ms"],
            }
            report["result"] = result
            report["status"] = "measured"
            apply_reasons = []
            if result["average_mbps"] < MIN_RETRY_SPEED_MBPS:
                apply_reasons.append("均速未达到 %.0f Mbps" % MIN_RETRY_SPEED_MBPS)
            if not result["stable"]:
                apply_reasons.append("波动系数 %.1f%% 超过 %.0f%% 稳定性上限" % (
                    result["variation_coefficient"] * 100,
                    MAX_ACCEPTABLE_VARIATION * 100,
                ))
            if apply_reasons:
                report["apply_reason"] = "；".join(apply_reasons) + "，保留原 CF IP。"
            else:
                try:
                    matching_nodes = matching_nodes_for_colo(
                        result["colo"], configured_node_names(),
                    )
                    if not matching_nodes:
                        report["apply_reason"] = "没有与 %s colo 匹配的已配置节点，保留原 CF IP。" % (
                            result["colo"] or "未知",
                        )
                    else:
                        assignments = {name: ip for name in matching_nodes}
                        apply_ips(assignments)
                        report["applied_nodes"] = assignments
                        report["apply_status"] = "applied"
                except Exception as error:
                    report["apply_error"] = str(error)
                    report["apply_reason"] = "节点配置未更改，保留原 CF IP。"
        else:
            report["status"] = "failed"
            report["measurement_error"] = "连接或下载失败，未获得有效测速窗口。"
            report["apply_reason"] = "测速失败，保留原 CF IP。"
        report["finished_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        DATA_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        report_path = DATA_DIR / ("single-%s-%s.json" % (ip.replace(":", "_"), stamp))
        report["report_path"] = str(report_path)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.chmod(report_path, 0o600)
    return report


def main():
    load_project_env()
    parser = argparse.ArgumentParser(
        description="Benchmark Cloudflare IPs, apply the best results to x-tunnel.yaml, and record a full report."
    )
    parser.add_argument("--family", type=int, choices=(4, 6), default=4)
    parser.add_argument("--ip", action="append", help="test one candidate IP; repeat to add candidates")
    parser.add_argument("--limit", type=int, default=100, help="random candidate IPs from the cached Cloudflare ranges")
    parser.add_argument("--top", type=int, default=20, help="lowest-latency candidates to bandwidth-test")
    parser.add_argument("--workers", type=int, default=50, help="parallel TCP/TLS latency probes")
    parser.add_argument("--target-mbps", type=int, default=get_target_mbps(), help="primary speed target in Mbps; fallback tiers descend by 50 Mbps to 100")
    parser.add_argument("--tiers-mbps", help="legacy override: comma-separated descending speed tiers")
    parser.add_argument(
        "--target-count", type=int, default=None,
        help="number of IPs to find (default: up to three, based on active node count)",
    )
    parser.add_argument(
        "--speed-duration", type=float, default=DEFAULT_SPEED_DURATION,
        help="maximum download test seconds per IP (15-30; default 30)",
    )
    parser.add_argument("--discard-after", type=float, default=5.0, help="initial seconds excluded from stable-speed windows")
    parser.add_argument("--tls", action="store_true", help="probe Cloudflare HTTPS on port 443 instead of HTTP on port 80")
    parser.add_argument("--speed-url", help="override the APK's cached speed-test URL")
    parser.add_argument("--refresh-data", action="store_true", help="also refresh the speed-test URL; CF IP ranges refresh every run")
    parser.add_argument("--single-ip", metavar="IP", help="test one IP for 30 seconds using the same URL and --tls setting; apply it to matching-region nodes if mean speed is at least 100 Mbps")
    apply_options = parser.add_mutually_exclusive_group()
    apply_options.add_argument("--apply", action="append", metavar="NODE_NAME", help="target specific node(s); repeat to select multiple")
    apply_options.add_argument("--no-apply", action="store_true", help="do not write selected IPs to x-tunnel.yaml")
    args = parser.parse_args()

    if args.single_ip:
        progress = TelegramProgress()
        report = None
        error_text = None
        exit_code = 0
        try:
            report = run_single_ip(args.single_ip, progress, args.tls)
            if report["status"] != "measured":
                error_text = report.get(
                    "measurement_error",
                    "连接或下载失败；请检查 IP、网络与 ShellCrash 直连路由。",
                )
                exit_code = 1
        except Exception as error:
            error_text = str(error)
            exit_code = 1
            report = {"mode": "single-ip", "ip": args.single_ip}
            print("cfip_speedtest: %s" % error, file=sys.stderr)
        progress.finish(report, error_text)
        if report:
            print(notification_text(report, error_text), flush=True)
            if report.get("report_path"):
                print("report: %s" % report["report_path"], flush=True)
        return exit_code

    try:
        node_names = args.apply if args.apply else configured_node_names()
    except (OSError, ValueError) as error:
        parser.error("could not read active x-tunnel nodes: %s" % error)
    if args.target_count is None:
        if args.apply:
            args.target_count = len(args.apply)
        else:
            args.target_count = min(3, len(node_names)) if node_names else 2
    target_nodes = prioritized_target_nodes(node_names, args.target_count)

    if args.tiers_mbps:
        try:
            tiers_mbps = [float(value.strip()) for value in args.tiers_mbps.split(",")]
        except ValueError:
            parser.error("tiers-mbps must be a comma-separated list of positive numbers")
        if args.target_mbps != get_target_mbps():
            parser.error("do not combine --tiers-mbps with --target-mbps")
    else:
        try:
            tiers_mbps = build_speed_tiers(args.target_mbps)
        except ValueError as error:
            parser.error(str(error))
    if (args.limit < 1 or args.top < 1 or args.workers < 1 or args.target_count < 1
            or args.speed_duration < 15 or args.speed_duration > 30
            or args.discard_after < 3 or args.discard_after >= args.speed_duration
            or not tiers_mbps or any(value <= 0 for value in tiers_mbps)
            or any(left <= right for left, right in zip(tiers_mbps, tiers_mbps[1:]))):
        parser.error("use valid positive settings, speed-duration 15-30s, warm-up 3s or more and below the duration, and descending tiers")

    notification_report = None
    scan_error = None
    exit_code = 0
    progress = TelegramProgress()
    try:
        with open(SCAN_LOCK_FILE, "w", encoding="utf-8") as scan_lock:
            try:
                fcntl.flock(scan_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeError("another CF IP speed test is already running") from error

            progress.update("🔄 正在更新 IPv%d Cloudflare IP 段数据…" % args.family, force=True)
            candidates = make_candidates(args.family, args.limit, args.ip or [], True)
            supplemental_ips = [] if args.ip else supplemental_candidate_ips(args.family, node_names)
            candidates = list(dict.fromkeys(candidates + supplemental_ips))
            with DirectCandidateRoutes(candidates) as routes:
                report = {
                    "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "ip_data_refreshed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "ip_data_source": "manual --ip list" if args.ip else IP_LIST_URLS[args.family],
                    "supplemental_candidate_ips": supplemental_ips,
                    "family": args.family,
                    "tiers_mbps": tiers_mbps,
                    "target_count": args.target_count,
                    "speed_duration_seconds": args.speed_duration,
                    "warmup_seconds": args.discard_after,
                    "apply_mode": "disabled" if args.no_apply else ("manual" if args.apply else "automatic"),
                    "candidates": [
                        {"ip": ip, "status": "rtt_pending"}
                        for ip in candidates
                    ],
                }
                notification_report = report
                progress.update("🔎 CF IP 测速开始\n目标：先找 %s Mbps；不足时逐档回退\n候选 IP：%d，测速窗口：%d 秒" % (
                    tiers_mbps[0], len(candidates), args.speed_duration,
                ), force=True)
                records_by_ip = {record["ip"]: record for record in report["candidates"]}
                _, domain, request_path = parse_speed_url(args.speed_url, args.refresh_data)
                scheme = "https" if args.tls else "http"
                report.update({
                    "scheme": scheme,
                    "speed_test_host": domain,
                    "speed_test_path": request_path,
                    "rtt_candidates_for_download": args.top,
                    "rtt_workers": args.workers,
                    "minimum_retry_speed_mbps": MIN_RETRY_SPEED_MBPS,
                    "minimum_apply_speed_mbps": MIN_APPLY_SPEED_MBPS,
                    "early_discard_speed_mbps": MIN_APPLY_SPEED_MBPS,
                    "early_discard_seconds": EARLY_DISCARD_SECONDS,
                    "stable_window_policy": "mean of 1-second windows after warmup; CV <= 0.25 required for applying; stable candidates rank by mean, median, CV, latency",
                    "maximum_apply_variation_coefficient": MAX_ACCEPTABLE_VARIATION,
                    "maximum_regional_rounds": MAX_REGIONAL_ROUNDS,
                    "ips_per_regional_round": IPS_PER_REGIONAL_ROUND,
                })
                port = 443 if args.tls else 80
                print("RTT probing %d IPv%d candidate(s) on TCP/%d..." % (len(candidates), args.family, port), flush=True)
                results = []
                rtt_completed = 0
                for ip, measurement in measure_rtt_candidates(
                    candidates, port, args.tls, args.workers, routes.network_uid
                ):
                    rtt_completed += 1
                    if measurement is not None:
                        result = {
                            "ip": ip,
                            "latency_ms": measurement["latency_ms"],
                            "colo": measurement["colo"],
                        }
                        results.append(result)
                        records_by_ip[ip].update({
                            "status": "rtt_passed",
                            "latency_ms": measurement["latency_ms"],
                            "rtt_colo": measurement["colo"],
                        })
                    else:
                        records_by_ip[ip]["status"] = "rtt_failed"
                    progress.update("📡 RTT 筛选中：%d/%d\n有效：%d，正在保留低延迟候选" % (
                        rtt_completed, len(candidates), len(results),
                    ))
                ranked_results = sorted(results, key=lambda result: result["latency_ms"])
                results = select_rtt_candidates(ranked_results, target_nodes, args.top)
                selected_rtt_ips = {result["ip"] for result in results}
                for result in ranked_results:
                    if result["ip"] not in selected_rtt_ips:
                        records_by_ip[result["ip"]]["status"] = "rtt_passed_not_speed_tested"
                report["rtt_region_counts"] = {
                    region: sum(colo_macro_region(item.get("colo")) == region for item in ranked_results)
                    for region in ("asia", "north_america")
                }
                report["rtt_selected_region_counts"] = {
                    region: sum(colo_macro_region(item.get("colo")) == region for item in results)
                    for region in ("asia", "north_america")
                }
                report["rtt_selection_policy"] = (
                    "prioritize observed colo regions for target nodes, with HK then North America preferred; "
                    "remaining candidates are ordered by RTT"
                )
                if not results:
                    report["finished_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
                    report_path = save_report(report)
                    print("full candidate report: %s" % report_path, flush=True)
                    raise RuntimeError("no candidate passed three CF-RAY RTT probes")

                print("%d candidate(s) passed RTT; testing up to %d new IPs per round for %d rounds, %.0fs each" % (
                    len(results), IPS_PER_REGIONAL_ROUND, MAX_REGIONAL_ROUNDS, args.speed_duration,
                ), flush=True)
                best_by_ip = {}
                attempted_ips = set()
                report["speed_test_rounds"] = []
                for round_number in range(1, MAX_REGIONAL_ROUNDS + 1):
                    batch = next_speedtest_batch(results, attempted_ips, target_nodes)
                    if not batch:
                        break
                    round_report = {"round": round_number, "results": []}
                    round_measured = []
                    round_report["candidate_ips"] = [item["ip"] for item in batch]
                    print("Bandwidth test round %d/%d (%d new IPs)..." % (
                        round_number, MAX_REGIONAL_ROUNDS, len(batch),
                    ), flush=True)
                    for index, result in enumerate(batch, 1):
                        attempted_ips.add(result["ip"])
                        record = records_by_ip[result["ip"]]
                        record["status"] = "speed_test_running"
                        measurement = measure_download(
                            routes, result["ip"], scheme, domain, request_path, scheme == "https",
                            duration=args.speed_duration,
                            discard_after=args.discard_after,
                            minimum_speed_mbps=MIN_APPLY_SPEED_MBPS,
                            progress_callback=progress.update,
                            progress_label="第%d轮 %d/%d · %s" % (
                                round_number, index, len(batch), result["ip"],
                            ),
                        )
                        progress.update("⚡ 第%d轮测速：完成 %d/%d 个候选\n当前测速：%s" % (
                            round_number, index, len(batch), result["ip"],
                        ), force=True)
                        attempt = {"ip": result["ip"], "latency_ms": result["latency_ms"]}
                        if measurement is None:
                            attempt["status"] = "speed_test_failed"
                            round_report["results"].append(attempt)
                            record.setdefault("speed_test_attempts", []).append(attempt.copy())
                            if result["ip"] not in best_by_ip:
                                record["status"] = "speed_test_failed"
                            print("[%d/%d] %s: download test failed" % (index, len(batch), result["ip"]), flush=True)
                            continue
                        if measurement.get("status") == "incomplete":
                            attempt.update({
                                "status": "incomplete",
                                "sampled_seconds": measurement["sampled_seconds"],
                                "request_count": measurement["request_count"],
                                "colo": measurement.get("colo", ""),
                            })
                            round_report["results"].append(attempt)
                            record.setdefault("speed_test_attempts", []).append(attempt.copy())
                            if result["ip"] not in best_by_ip:
                                record["status"] = "incomplete"
                            print("[%d/%d] %s: incomplete sample %.1fs; rejected" % (
                                index, len(batch), result["ip"], measurement["sampled_seconds"],
                            ), flush=True)
                            continue
                        if measurement.get("status") == "early_discard":
                            attempt.update({
                                "status": "early_discard",
                                "early_speed_mbps": measurement["early_speed_mbps"],
                                "best_case_average_mbps": measurement["best_case_average_mbps"],
                                "sampled_seconds": measurement["sampled_seconds"],
                                "request_count": measurement["request_count"],
                                "colo": measurement["colo"],
                            })
                            round_report["results"].append(attempt)
                            record.setdefault("speed_test_attempts", []).append(attempt.copy())
                            if result["ip"] not in best_by_ip:
                                record["status"] = "early_discard"
                            print("[%d/%d] %s: early discard; best-case final average %.1fMbps after %.1fs" % (
                                index,
                                len(batch),
                                result["ip"],
                                measurement["best_case_average_mbps"],
                                measurement["sampled_seconds"],
                            ), flush=True)
                            continue
                        measurement["latency_ms"] = result["latency_ms"]
                        measured_mbps = round(measurement["stable_kib"] * 1024 * 8 / 1_000_000, 2)
                        attempt.update({
                            "status": "measured",
                            "stable_mbps": measured_mbps,
                            "average_mbps": round(measurement["average_kib"] * 1024 * 8 / 1_000_000, 2),
                            "variation_coefficient": measurement["variation_coefficient"],
                            "stable": measurement["stable"],
                            "stable_kib_per_second": measurement["stable_kib"],
                            "median_mbps": round(measurement["median_kib"] * 1024 * 8 / 1_000_000, 2),
                            "tcp_ms": measurement["tcp_ms"],
                            "sampled_seconds": measurement["sampled_seconds"],
                            "request_count": measurement["request_count"],
                            "window_kib_per_second": measurement["windows_kib"],
                            "colo": measurement["colo"],
                        })
                        round_report["results"].append(attempt)
                        record.setdefault("speed_test_attempts", []).append(attempt.copy())
                        round_measured.append(measurement)
                        previous = best_by_ip.get(measurement["ip"])
                        measurement_rank = (
                            measurement["stable"],
                            measurement["stable_kib"],
                            measurement["median_kib"],
                            -measurement["variation_coefficient"],
                        )
                        previous_rank = (
                            previous["stable"],
                            previous["stable_kib"],
                            previous["median_kib"],
                            -previous["variation_coefficient"],
                        ) if previous is not None else None
                        if previous_rank is None or measurement_rank > previous_rank:
                            best_by_ip[measurement["ip"]] = measurement
                            record.update({
                                "status": "measured",
                                "latency_ms": result["latency_ms"],
                                "tcp_ms": measurement["tcp_ms"],
                                "sampled_seconds": measurement["sampled_seconds"],
                                "request_count": measurement["request_count"],
                                "stable_kib_per_second": measurement["stable_kib"],
                                "stable_mbps": measured_mbps,
                                "average_mbps": round(measurement["average_kib"] * 1024 * 8 / 1_000_000, 2),
                                "variation_coefficient": measurement["variation_coefficient"],
                                "stable": measurement["stable"],
                                "median_mbps": round(measurement["median_kib"] * 1024 * 8 / 1_000_000, 2),
                                "window_kib_per_second": measurement["windows_kib"],
                                "colo": measurement["colo"],
                            })
                        print(
                            "[%d/%d] %s latency=%dms stable=%dKiB/s (~%.1fMbps) sampled=%.1fs windows=%s colo=%s" % (
                                index,
                                len(batch),
                                measurement["ip"],
                                measurement["latency_ms"],
                                measurement["stable_kib"],
                                measured_mbps,
                                measurement["sampled_seconds"],
                                "/".join(map(str, measurement["windows_kib"])),
                                measurement["colo"],
                            ),
                            flush=True,
                        )
                    round_report["measured_count"] = len(round_measured)
                    qualified = sum(
                        item["stable"]
                        and item["stable_kib"] * 1024 * 8 / 1_000_000 >= MIN_RETRY_SPEED_MBPS
                        for item in round_measured
                    )
                    round_report["qualified_count"] = qualified
                    _, interim_tier, interim_met, interim_assignments = select_candidates(
                        list(best_by_ip.values()), tiers_mbps, args.target_count, target_nodes,
                    )
                    region_complete = exact_region_coverage(interim_assignments, target_nodes)
                    failover_complete = has_node_ip_failover_coverage(
                        list(best_by_ip.values()), node_names,
                    )
                    round_report["selected_tier_mbps"] = interim_tier
                    round_report["met_target_count"] = interim_met
                    round_report["reached_primary_tier"] = interim_met and interim_tier == tiers_mbps[0]
                    round_report["exact_region_coverage"] = region_complete
                    round_report["failover_ip_coverage"] = failover_complete
                    round_report["result"] = (
                        "目标速度、地区直配和备用 IP 覆盖满足"
                        if interim_met and interim_tier == tiers_mbps[0] and region_complete and failover_complete
                        else "继续补测地区/速度候选"
                    )
                    report["speed_test_rounds"].append(round_report)
                    if interim_met and interim_tier == tiers_mbps[0] and region_complete and failover_complete:
                        break
                    if round_number < MAX_REGIONAL_ROUNDS:
                        missing_regions = [
                            name for name in target_nodes
                            if name not in interim_assignments
                            or not candidate_matches_node_region(name, interim_assignments[name])
                        ]
                        print("Continuing regional/speed search; missing direct-region matches: %s" % (
                            ", ".join(missing_regions) or "speed target",
                        ), flush=True)
                measured = list(best_by_ip.values())
                if not measured:
                    report["finished_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
                    report_path = save_report(report)
                    print("full candidate report: %s" % report_path, flush=True)
                    raise RuntimeError("all bandwidth tests failed")
                selected, tier, met_tier, regional_candidates = select_candidates(
                    measured, tiers_mbps, args.target_count, node_names,
                )
                node_ip_assignments = build_node_ip_assignments(measured, regional_candidates)
                report["selected_tier_mbps"] = tier
                unassigned_nodes = [name for name in node_names if name not in node_ip_assignments]
                report["preserved_nodes"] = unassigned_nodes
                report["preserved_node_reasons"] = {}
                for name in unassigned_nodes:
                    compatible = [item for item in measured if candidate_allowed_for_node(name, item)]
                    if compatible:
                        fastest = max(compatible, key=lambda item: item["stable_kib"])
                        speed_mbps = fastest["stable_kib"] * 1024 * 8 / 1_000_000
                        reasons = []
                        if not fastest.get("stable", False):
                            reasons.append("波动 CV %.0f%% 超过 %.0f%% 稳定性上限" % (
                                fastest.get("variation_coefficient", 0) * 100,
                                MAX_ACCEPTABLE_VARIATION * 100,
                            ))
                        if speed_mbps < MIN_APPLY_SPEED_MBPS:
                            reasons.append("最高均速 %.1f Mbps，低于 %.0f Mbps 写入下限" % (
                                speed_mbps, MIN_APPLY_SPEED_MBPS,
                            ))
                        if reasons:
                            reason = "；".join(reasons)
                        else:
                            reason = "没有满足目标数量/地区分配条件的候选"
                    elif node_region(name) == "HK":
                        reason = "没有 HKG colo 候选；香港节点仅接受 HKG"
                    else:
                        reason = "没有可用的地区匹配候选"
                    report["preserved_node_reasons"][name] = reason
                nodes_missing_fallback = list(unassigned_nodes) + [
                    name for name, addresses in node_ip_assignments.items()
                    if len(addresses) < MAX_NODE_CF_IPS
                ]
                report["nodes_missing_fallback_ip"] = nodes_missing_fallback
                report["met_target_count_at_tier"] = met_tier and not unassigned_nodes
                report["regional_assignments"] = {
                    name: {
                        "ip": item["ip"],
                        "fallback_ips": [
                            {
                                "ip": fallback_ip,
                                "colo": next(
                                    candidate.get("colo", "")
                                    for candidate in measured if candidate["ip"] == fallback_ip
                                ),
                            }
                            for fallback_ip in node_ip_assignments.get(name, [])[1:]
                        ],
                        "colo": item.get("colo", ""),
                        "node_region": node_region(name),
                        "direct_region_match": candidate_matches_node_region(name, item),
                    }
                    for name, item in regional_candidates.items()
                }
                tier_result = "达到" if tier == tiers_mbps[0] and report["met_target_count_at_tier"] else "回退到"
                chosen_summary = "；".join(
                    "%s[%s] %.1fMbps" % (
                        item["ip"], item.get("colo") or "未知",
                        item["stable_kib"] * 1024 * 8 / 1_000_000,
                    )
                    for item in selected
                ) or "无可用 IP"
                regional_summary = "；".join(
                    "%s → %s[%s]%s" % (
                        name,
                        item["ip"],
                        item.get("colo") or "未知",
                        "；备用 " + ", ".join(node_ip_assignments.get(name, [])[1:])
                        if len(node_ip_assignments.get(name, [])) > 1 else "",
                    )
                    for name, item in regional_candidates.items()
                ) or "无可匹配节点"
                progress.update("📊 阶梯筛选完成：%s %.0f Mbps\n候选结果：%s\n地区匹配：%s\n正在写入节点配置" % (
                    tier_result, tier, chosen_summary, regional_summary,
                ))
                assigned_nodes_by_ip = {}
                for node_name, addresses in node_ip_assignments.items():
                    for address in addresses:
                        assigned_nodes_by_ip.setdefault(address, []).append(node_name)
                for item in selected:
                    records_by_ip[item["ip"]]["selected_tier_mbps"] = tier
                for address, assigned_node_names in assigned_nodes_by_ip.items():
                    records_by_ip[address]["selected"] = True
                    records_by_ip[address]["assigned_nodes"] = assigned_node_names
                report["selected_ips"] = [item["ip"] for item in selected]
                selected_ip_set = set(report["selected_ips"]) | set(assigned_nodes_by_ip)
                for test_round in report.get("speed_test_rounds", []):
                    for attempt in test_round.get("results", []):
                        attempt["selected"] = attempt["ip"] in selected_ip_set
                if met_tier:
                    print("selected %d IP(s) at >= %.0f Mbps" % (len(selected), tier), flush=True)
                else:
                    print("No tier produced %d IPs; returning the %d fastest available candidate(s) (below %.0f Mbps may occur)" % (
                        args.target_count, len(selected), tiers_mbps[-1]
                    ), flush=True)
                for item in selected:
                    print("selected: %s (~%.1fMbps, %dms, %s)" % (
                        item["ip"], item["stable_kib"] * 1024 * 8 / 1_000_000,
                        item["latency_ms"], item["colo"],
                    ), flush=True)
                assignments = {
                    node_name: ",".join(addresses)
                    for node_name, addresses in node_ip_assignments.items()
                } if not args.no_apply else {}
                if assignments:
                    try:
                        apply_ips(assignments)
                    except Exception as error:
                        report["apply_error"] = str(error)
                        report["finished_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
                        report_path = save_report(report)
                        print("full candidate report: %s" % report_path, flush=True)
                        raise
                report["applied_nodes"] = assignments
                report["finished_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
                report_path = save_report(report)
                report["report_path"] = str(report_path)
                save_report(report)
                print("full candidate report: %s" % report_path, flush=True)
    except Exception as error:
        exit_code = 1
        scan_error = str(error)
        if notification_report is not None:
            notification_report["error"] = scan_error
            notification_report.setdefault("finished_at", datetime.datetime.now(datetime.timezone.utc).isoformat())
            try:
                report_path = save_report(notification_report)
                notification_report["report_path"] = str(report_path)
                save_report(notification_report)
            except OSError as report_error:
                print("WARNING: could not save failed scan report: %s" % report_error, file=sys.stderr)
        print("cfip_speedtest: %s" % error, file=sys.stderr)
    progress.finish(notification_report, scan_error)
    return exit_code

if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--network-worker":
        try:
            sys.exit(isolated_network_worker(sys.argv[2]))
        except Exception as error:
            print("cfip_speedtest worker: %s" % error, file=sys.stderr)
            sys.exit(1)
    sys.exit(main())
