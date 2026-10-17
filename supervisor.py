#!/usr/bin/env python3
import logging
import ipaddress
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import yaml
from project_settings import PROJECT_ENV_FILE, load_project_env


load_project_env()
BASE_DIR = Path(__file__).resolve().parent
CONFIG = BASE_DIR / "x-tunnel.yaml"
BINARY = "/usr/bin/x-tunnel"
SINGBOX_SYNC = BASE_DIR / "sync_singbox.py"
PROVIDER_PATH = Path(os.environ.get(
    "X_TUNNEL_PROVIDER_PATH",
    str(BASE_DIR / "runtime/x-tunnel-provider.json"),
))
if not PROVIDER_PATH.is_absolute():
    PROVIDER_PATH = BASE_DIR / PROVIDER_PATH
WATCHED_FILES = (
    PROVIDER_PATH,
    BASE_DIR / "x-tunnel-singbox-template.json",
    PROJECT_ENV_FILE,
)
DEFAULT_MAX_NODES = 5
RESTART_DELAY = 5
POLL_INTERVAL = 2

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

stopping = False


def stop_handler(signum, frame):
    global stopping
    stopping = True


def port_available(port, listen_hosts, reusable_bindings):
    for host in listen_hosts:
        if (host, port) in reusable_bindings:
            continue
        address = ipaddress.ip_address(host)
        family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
        with socket.socket(family, socket.SOCK_STREAM) as probe:
            try:
                probe.bind((host, port))
            except OSError:
                return False
    return True


def allocate_port(preferred_port, listen_hosts, used_ports, reusable_bindings, node_name):
    for port in range(preferred_port, 65536):
        if port in used_ports:
            continue
        if port_available(port, listen_hosts, reusable_bindings):
            if port != preferred_port:
                logging.warning(
                    "SOCKS5 port %d for node %s is occupied; assigned port %d",
                    preferred_port, node_name, port,
                )
            used_ports.add(port)
            return port
    raise ValueError(
        "no available SOCKS5 port at or above %d for node %s"
        % (preferred_port, node_name)
    )


def read_specs(reusable_bindings=None):
    load_project_env()
    reusable_bindings = set(reusable_bindings or ())
    with open(CONFIG, "r", encoding="utf-8") as config_file:
        document = yaml.safe_load(config_file) or {}

    defaults = document.get("defaults") or {}
    nodes = document.get("nodes") or []
    if not isinstance(nodes, list):
        raise ValueError("nodes must be a list of pipe-delimited strings")
    max_nodes = document.get("max_nodes", DEFAULT_MAX_NODES)
    if isinstance(max_nodes, bool) or not isinstance(max_nodes, int) or max_nodes < 1:
        raise ValueError("max_nodes must be a positive integer")
    if len(nodes) > max_nodes:
        logging.warning(
            "configured node count is %d; max_nodes=%d, ignoring %d extra nodes",
            len(nodes), max_nodes, len(nodes) - max_nodes,
        )
        nodes = nodes[:max_nodes]

    first_port = int(defaults.get("first_port", 32322))
    configured_hosts = os.environ.get("X_TUNNEL_LISTEN_HOSTS")
    if configured_hosts is None:
        listen_hosts = defaults.get("listen_hosts", ["127.0.0.1"])
    else:
        listen_hosts = [host.strip() for host in configured_hosts.split(",") if host.strip()]
        if not listen_hosts:
            listen_hosts = ["127.0.0.1"]
    if not isinstance(listen_hosts, list) or not listen_hosts:
        raise ValueError("defaults.listen_hosts must be a non-empty list")
    try:
        listen_hosts = [str(ipaddress.ip_address(str(host))) for host in listen_hosts]
    except ValueError as error:
        raise ValueError("listen hosts must be IP addresses; configure X_TUNNEL_LISTEN_HOSTS") from error
    if first_port < 1 or first_port > 65535:
        raise ValueError("defaults.first_port must be between 1 and 65535")

    specs = []
    names = set()
    used_ports = set()
    for index, row in enumerate(nodes):
        if not isinstance(row, str):
            raise ValueError("each node must be a pipe-delimited string")
        fields = [field.strip() for field in row.split("|")]
        if len(fields) < 2 or len(fields) > 6 or not fields[0] or not fields[1]:
            raise ValueError("node rows require name|server and at most six fields")
        name, server = fields[:2]
        if name in names:
            raise ValueError("duplicate node name: %s" % name)
        names.add(name)

        token = fields[2] if len(fields) > 2 and fields[2] else defaults.get("token")
        cfip = fields[3] if len(fields) > 3 and fields[3] else defaults.get("cfip")
        connections = fields[4] if len(fields) > 4 and fields[4] else defaults.get("connections", 3)
        preferred_port = fields[5] if len(fields) > 5 and fields[5] else first_port + index
        if not token or not cfip:
            raise ValueError("node %s is missing token or cfip defaults" % name)
        connections = int(connections)
        if connections < 1:
            raise ValueError("node %s connections must be positive" % name)
        preferred_port = int(preferred_port)
        if preferred_port < 1 or preferred_port > 65535:
            raise ValueError("node %s port is out of range" % name)
        port = allocate_port(
            preferred_port,
            listen_hosts,
            used_ports,
            reusable_bindings,
            name,
        )

        specs.append({
            "name": name,
            "server": server,
            "token": str(token),
            "cfip": str(cfip),
            "connections": connections,
            "ech": str(defaults.get("ech", "cloudflare-ech.com")),
            "dns": str(defaults.get("dns", "https://doh.pub/dns-query")),
            "listen_hosts": [str(host) for host in listen_hosts],
            "port": port,
        })
    return specs


def command_for(spec):
    listeners = ",".join(
        "socks5://%s:%d" % (host, spec["port"])
        for host in spec["listen_hosts"]
    )
    return [
        BINARY,
        "-f", spec["server"],
        "-token", spec["token"],
        "-ech", spec["ech"],
        "-dns", spec["dns"],
        "-ip", spec["cfip"],
        "-n", str(spec["connections"]),
        "-l", listeners,
    ]


def stop_child(entry):
    process = entry.get("process")
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def start_child(entry):
    spec = entry["spec"]
    try:
        entry["process"] = subprocess.Popen(command_for(spec))
        entry["restart_at"] = None
        logging.info(
            "started node %s on SOCKS5 port %d (pid %d)",
            spec["name"], spec["port"], entry["process"].pid,
        )
    except OSError:
        logging.exception("failed to start node %s", spec["name"])
        entry["process"] = None
        entry["restart_at"] = time.monotonic() + RESTART_DELAY


def reconcile(specs, children):
    wanted = {spec["name"]: spec for spec in specs}
    for name in list(children):
        if name not in wanted or children[name]["spec"] != wanted[name]:
            stop_child(children[name])
            del children[name]
    for name, spec in wanted.items():
        if name not in children:
            children[name] = {"spec": spec, "process": None, "restart_at": None}
            start_child(children[name])


def reusable_bindings(children):
    return {
        (host, entry["spec"]["port"])
        for entry in children.values()
        if entry["process"] is not None and entry["process"].poll() is None
        for host in entry["spec"]["listen_hosts"]
    }


def signature():
    info = os.stat(CONFIG)
    watched = [(CONFIG, info.st_ino, info.st_mtime_ns, info.st_size)]
    for path in WATCHED_FILES:
        try:
            info = os.stat(path)
            watched.append((path, info.st_ino, info.st_mtime_ns, info.st_size))
        except FileNotFoundError:
            watched.append((path, None, None, None))
    return tuple(watched)


def main():
    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)
    children = {}
    last_signature = None

    while not stopping:
        try:
            current_signature = signature()
            if current_signature != last_signature:
                try:
                    specs = read_specs(reusable_bindings(children))
                except Exception:
                    logging.exception("invalid configuration; keeping current nodes")
                else:
                    try:
                        sync_result = subprocess.run(
                            [
                                sys.executable,
                                SINGBOX_SYNC,
                                "--reuse-bindings-json",
                                json.dumps(sorted(reusable_bindings(children))),
                            ],
                            check=True,
                            capture_output=True,
                            text=True,
                        )
                        if sync_result.stdout.strip() == "provider-updated":
                            logging.info("updated local X-Tunnel node provider")
                    except (OSError, subprocess.CalledProcessError):
                        logging.exception("failed to write local X-Tunnel node provider")
                    reconcile(specs, children)
                    logging.info("loaded %d node(s)", len(specs))
                last_signature = current_signature
        except OSError:
            if last_signature is None:
                raise
            logging.exception("cannot access configuration; keeping current nodes")

        now = time.monotonic()
        for entry in children.values():
            process = entry["process"]
            if process is not None and process.poll() is not None:
                logging.warning(
                    "node %s exited with status %d; restarting in %d seconds",
                    entry["spec"]["name"], process.returncode, RESTART_DELAY,
                )
                entry["process"] = None
                entry["restart_at"] = now + RESTART_DELAY
            if entry["process"] is None and entry["restart_at"] is not None and now >= entry["restart_at"]:
                start_child(entry)
        time.sleep(POLL_INTERVAL)

    for entry in children.values():
        stop_child(entry)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        logging.error("supervisor stopped: %s", error)
        sys.exit(1)
