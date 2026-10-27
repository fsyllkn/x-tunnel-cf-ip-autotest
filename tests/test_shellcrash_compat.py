"""ShellCrash nftables skgid bypass without touching firewall or routing.

Mocked /etc/group and process launches; no live sockets are opened.
"""
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cfip_speedtest
import shellcrash_compat
import supervisor


class ShellCrashCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.groups = Path(self.temp.name) / "group"
        self.patch_group = mock.patch.object(shellcrash_compat, "GROUP_FILE", self.groups)
        self.patch_group.start()
        self.addCleanup(self.patch_group.stop)

    def group_file(self, text):
        self.groups.write_text(text, encoding="utf-8")

    def test_valid_shellcrash_gid_enables_socket_bypass(self):
        self.group_file("root:x:0:\\nshellcrash:x:7890:\\n")
        self.assertEqual(shellcrash_compat.outbound_process_gid(29002), 7890)

    def test_missing_group_keeps_dedicated_gid(self):
        self.assertEqual(shellcrash_compat.outbound_process_gid(29002), 29002)
        self.group_file("other:x:7890:\\n")
        self.assertEqual(shellcrash_compat.outbound_process_gid(29002), 29002)
        self.group_file("shellcrash:x:7891:\\n")
        self.assertEqual(shellcrash_compat.outbound_process_gid(29002), 29002)

    def test_duplicate_gid_fails_closed(self):
        self.group_file("shellcrash:x:7890:\\nother:x:7890:\\n")
        self.assertEqual(shellcrash_compat.outbound_process_gid(29002), 29002)

    def test_go_child_keeps_uid_but_uses_shellcrash_gid(self):
        self.group_file("shellcrash:x:7890:\\n")
        spec = {
            "name": "hk",
            "server": "wss://example.org/about",
            "token": "test",
            "ech": "cloudflare-ech.com",
            "dns": "https://doh.pub/dns-query",
            "cfip": "1.1.1.1",
            "connections": 1,
            "listen_hosts": ["127.0.0.1"],
            "port": 32322,
            "process_uid": 29002,
        }
        entry = {"spec": spec, "process": None, "restart_at": None}
        with mock.patch.object(supervisor, "child_workdir", return_value=Path(self.temp.name)), \\
             mock.patch.object(supervisor.subprocess, "Popen") as launch:
            launch.return_value.pid = 12345
            supervisor.start_child(entry)
        options = launch.call_args.kwargs
        self.assertEqual(options["user"], 29002)
        self.assertEqual(options["group"], 7890)
        self.assertEqual(options["extra_groups"], ())

    def test_cf_network_worker_uses_same_group_without_root_or_routes(self):
        self.group_file("shellcrash:x:7890:\\n")
        fake = mock.Mock()
        fake.stdin = io.StringIO()
        fake.stdout = io.StringIO(json.dumps({"type": "result", "measurement": {"ok": True}}) + "\\n")
        fake.stderr = io.StringIO()
        fake.wait.return_value = 0
        fake.poll.return_value = 0
        with mock.patch.object(cfip_speedtest.os, "geteuid", return_value=0), \\
             mock.patch.object(cfip_speedtest.subprocess, "Popen", return_value=fake) as launch:
            messages = list(cfip_speedtest.isolated_worker_messages(29002, "speed", {"params": {}}))
        self.assertEqual(len(messages), 1)
        self.assertTrue(messages[0]["measurement"]["ok"])
        options = launch.call_args.kwargs
        self.assertEqual(options["user"], 29002)
        self.assertEqual(options["group"], 7890)
        self.assertEqual(options["extra_groups"], ())


if __name__ == "__main__":
    unittest.main()
