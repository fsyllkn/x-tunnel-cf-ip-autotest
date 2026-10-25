#!/usr/bin/env python3
"""Check whether the LXC systemd core may start, without changing state.

Exit codes: 0 = configured, 10 = installation allowed but not ready to
start, 2 = invalid configuration. Only print field names/reasons, never secrets.
Systemd's ExecCondition treats non-zero exit codes below 255 as a skipped start.
"""
import sys
from urllib.parse import urlsplit

import yaml

import supervisor
from project_settings import get_target_mbps, read_telegram_credentials


def is_placeholder(value):
    value = str(value or "").strip()
    upper = value.upper()
    return (
        not value
        or "替换为" in value
        or upper.startswith(("YOUR_", "REPLACE_", "CHANGE_ME", "CHANGEME"))
    )


def is_example_host(host):
    host = host.lower().rstrip(".")
    return host in ("example.com", "example.org", "example.net") or host.endswith(
        (".example.com", ".example.org", ".example.net", ".example", ".invalid")
    )


def readiness():
    """Return (ready, reason) for the resolved node configuration.

    Placeholder defaults are legal when every active node explicitly
    overrides them. A remaining example node is NOT silently started or
    discarded: the whole core stays inactive until configuration is complete.
    """
    get_target_mbps()
    read_telegram_credentials(require_chat_id=False)
    try:
        specs = supervisor.read_specs(check_port_availability=False)
    except ValueError as error:
        if "missing token or cfip defaults" in str(error):
            return False, "node token or CF IP is missing"
        raise
    if not specs:
        return False, "no X-Tunnel nodes configured"
    for spec in specs:
        name = spec["name"]
        server = str(spec["server"]).strip()
        address = urlsplit(server)
        host = address.hostname or ""
        if address.scheme not in ("ws", "wss") or not host or is_example_host(host):
            return False, "node %s: replace the example WSS server" % name
        if is_placeholder(spec["token"]):
            return False, "node %s: replace the example token" % name
        if is_placeholder(spec["cfip"]):
            return False, "node %s: replace the example CF IP" % name
    return True, "%d configured X-Tunnel node(s)" % len(specs)


def main():
    try:
        ready, reason = readiness()
    except (yaml.YAMLError, ValueError, OSError, UnicodeError) as error:
        # YAML parser errors can echo source lines (possibly containing tokens).
        # Never echo raw errors from untrusted configuration to the journal.
        print("INVALID: x-tunnel.yaml failed validation (%s)" % type(error).__name__,
              file=sys.stderr)
        return 2
    print(("%s: %s" % ("READY" if ready else "PENDING", reason)))
    return 0 if ready else 10


if __name__ == "__main__":
    sys.exit(main())
