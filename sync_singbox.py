#!/usr/bin/env python3
import json
import os
import sys
import tempfile
from pathlib import Path

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


def main():
    reusable_bindings = set()
    if sys.argv[1:2] == ["--reuse-bindings-json"]:
        if len(sys.argv) != 3:
            raise ValueError("--reuse-bindings-json requires one JSON argument")
        reusable_bindings = {
            (str(host), int(port)) for host, port in json.loads(sys.argv[2])
        }
    elif sys.argv[1:]:
        raise ValueError("unknown argument: %s" % " ".join(sys.argv[1:]))

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

    changed = atomic_write_json(provider_path(), {"outbounds": outbounds})
    print("provider-updated" if changed else "unchanged")


if __name__ == "__main__":
    main()
