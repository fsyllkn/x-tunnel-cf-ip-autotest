#!/usr/bin/env python3
"""Read-only ShellCrash socket-owner bypass for X-Tunnel child processes.

ShellCrash's nftables OUTPUT and DNS rules exempt socket GID 7890. A systemd
Group=7890 override on the root supervisor is not sufficient: Python Popen
explicitly sets group=uid on the Go child, replacing the supervisor's GID.
Keep the dedicated UID for sing-box eBPF exclusions and use the proven
ShellCrash group as the *primary* GID of the network process only.
"""
from pathlib import Path

SHELLCRASH_GROUP = "shellcrash"
SHELLCRASH_GID = 7890
GROUP_FILE = Path("/etc/group")


def shellcrash_bypass_gid():
    """Return 7890 only if the local group DB unambiguously owns that GID.

    This does not add firewall rules, change routing, or change configuration.
    Do not grant an arbitrary preexisting GID based only on its numeric value.
    """
    try:
        lines = GROUP_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    owners = set()
    for line in lines:
        fields = line.split(":")
        if len(fields) >= 3 and fields[2].isdecimal() and int(fields[2]) == SHELLCRASH_GID:
            owners.add(fields[0])
    return SHELLCRASH_GID if owners == {SHELLCRASH_GROUP} else None


def outbound_process_gid(uid):
    """Keep the dedicated UID; select a socket-owner-exempt primary GID."""
    return shellcrash_bypass_gid() or uid
