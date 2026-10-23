#!/usr/bin/env python3
import json
import os
import sys
import tempfile
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import quote

import yaml

from project_settings import load_project_env
from supervisor import read_specs


BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_FILE = BASE_DIR / "x-tunnel-singbox-template.json"
DEFAULT_PROVIDER_PATH = BASE_DIR / "runtime/x-tunnel-provider.json"
NODE_TEMPLATE_TAG = "__XTUNNEL_NODE_TEMPLATE__"


def provider_path():
    load_project_env()
    configured = os.environ.get("X_TUNNEL_PROVIDER_PATH", "").strip()
    if not configured:
        return DEFAULT_PROVIDER_PATH
    path = Path(os.path.expanduser(configured))
    return path if path.is_absolute() else BASE_DIR / path


def atomic_write_json(path, value):
    payload = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8") == payload:
        return False
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    descriptor, temporary = tempfile.mkstemp(prefix=".x-tunnel-provider-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(payload)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        return True
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


# These companion files describe the SAME local SOCKS5 endpoints. They are
# provider fragments, not a VPN/TUN setup or a Shadowsocks server.
CLASH_FILE = "x-tunnel-clash.yaml"
V2RAY_FILE = "x-tunnel-v2ray.json"
SOCKS_LINKS_FILE = "x-tunnel-socks5.txt"


def atomic_write_text(path, payload):
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8") == payload:
        return False
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    descriptor, temporary = tempfile.mkstemp(prefix=".x-tunnel-export-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(payload)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        return True
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def validate_local_socks(outbounds):
    if not isinstance(outbounds, list):
        raise ValueError("provider outbounds must be a list")
    endpoints = []
    seen = set()
    for node in outbounds:
        if not isinstance(node, dict) or node.get("type") != "socks":
            raise ValueError("multi-format export only accepts SOCKS5 outbounds")
        name, host, port = node.get("tag"), node.get("server"), node.get("server_port")
        if not isinstance(name, str) or not name.strip() or name in seen:
            raise ValueError("SOCKS5 tags must be nonempty and unique")
        if not isinstance(host, str) or not host.strip():
            raise ValueError("SOCKS5 server must be a nonempty host")
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError("SOCKS5 port must be 1..65535")
        seen.add(name)
        endpoints.append((name, host, port))
    return endpoints


def export_companion_formats(outbounds, target):
    endpoints = validate_local_socks(outbounds)
    # Mihomo/Clash proxy-provider YAML: no route rules or global proxy control.
    # Do not advertise UDP support before the X-Tunnel SOCKS5 kernel is tested.
    clash = {
        "proxies": [
            {"name": name, "type": "socks5", "server": host, "port": port}
            for name, host, port in endpoints
        ]
    }
    # Import/merge these SOCKS outbounds into an existing Xray/V2Ray config.
    v2ray = {
        "outbounds": [
            {
                "tag": name,
                "protocol": "socks",
                "settings": {"servers": [{"address": host, "port": port}]},
            }
            for name, host, port in endpoints
        ]
    }
    links = []
    for name, host, port in endpoints:
        try:
            if ip_address(host).version == 6:
                host = "[" + host + "]"
        except ValueError:
            pass
        links.append("socks5://%s:%d#%s" % (host, port, quote(name, safe="")))
    dest = target.parent
    results = [
        atomic_write_text(
            dest / CLASH_FILE,
            yaml.safe_dump(clash, allow_unicode=True, sort_keys=False),
        ),
        atomic_write_text(
            dest / V2RAY_FILE,
            json.dumps(v2ray, ensure_ascii=False, indent=2) + "\n",
        ),
        atomic_write_text(
            dest / SOCKS_LINKS_FILE,
            "\n".join(links) + ("\n" if links else ""),
        ),
    ]
    return any(results)


def main():
    args = sys.argv[1:]
    manual = args == ["--export-only"]
    reusable_bindings = set()
    if not manual and args[:1] == ["--reuse-bindings-json"]:
        if len(args) != 2:
            raise ValueError("--reuse-bindings-json requires one JSON argument")
        reusable_bindings = {
            (str(host), int(port)) for host, port in json.loads(args[1])
        }
    elif args and not manual:
        raise ValueError("unknown argument: %s" % " ".join(args))

    target = provider_path()
    if manual:
        # Refresh only companion exports from the last provider snapshot.
        # Never reallocate ports or rewrite the live SOCKS5 provider.
        if not target.is_file():
            raise FileNotFoundError(
                "X-Tunnel provider does not exist; start core service first: %s" % target
            )
        source = json.loads(target.read_text(encoding="utf-8"))
        changed = export_companion_formats(source.get("outbounds"), target)
    else:
        specs = read_specs(reusable_bindings=reusable_bindings)
        template_data = json.loads(TEMPLATE_FILE.read_text(encoding="utf-8"))
        node_template = next(
            outbound for outbound in template_data.get("outbounds", [])
            if outbound.get("tag") == NODE_TEMPLATE_TAG
        )
        outbounds = []
        for spec in specs:
            outbound = json.loads(json.dumps(node_template))
            outbound["tag"] = spec["name"]
            outbound["server"] = (
                "127.0.0.1" if "127.0.0.1" in spec["listen_hosts"]
                else spec["listen_hosts"][0]
            )
            outbound["server_port"] = spec["port"]
            outbounds.append(outbound)
        # Validate all three output formats before touching the live provider.
        validate_local_socks(outbounds)
        changed = atomic_write_json(target, {"outbounds": outbounds})
        changed = export_companion_formats(outbounds, target) or changed

    print("provider-updated" if changed else "unchanged")


if __name__ == "__main__":
    main()
