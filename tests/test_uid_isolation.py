"""OpenWrt X-Tunnel kernel UID isolation regression tests.

Run after python3-yaml is installed:
    python3 -m unittest discover -s tests -p 'test_uid_isolation.py'

Mocks process creation; does not start a tunnel or change system UIDs.
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import supervisor


class UIDIsolationTest(unittest.TestCase):
    def test_default_and_overridden_uid(self):
        # UID is configured only in YAML, not inherited from environment.
        with mock.patch("project_settings.project_settings", return_value={}):
            with mock.patch.dict(os.environ, {"X_TUNNEL_PROCESS_UID": "0"}):
                self.assertEqual(supervisor.configured_process_uid(), 29002)
        with mock.patch("project_settings.project_settings", return_value={"process_uid": 0}):
            with self.assertRaises(ValueError):
                supervisor.configured_process_uid()
        with mock.patch("project_settings.project_settings", return_value={"process_uid": 29003}), \
             mock.patch.object(Path, "read_text", return_value="root:x:0:0:root:/root:/bin/sh\\n"):
            self.assertEqual(supervisor.configured_process_uid(), 29003)
        with mock.patch("project_settings.project_settings", return_value={"process_uid": 29003}), \
             mock.patch.object(Path, "read_text", return_value="other:x:29003:29003::/tmp:/bin/sh\\n"):
            with self.assertRaises(ValueError):
                supervisor.configured_process_uid()

    def test_workdirs_are_per_node_and_private(self):
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(supervisor, "NODE_RUNTIME", Path(directory)), \
             mock.patch.object(supervisor.os, "geteuid", return_value=0), \
             mock.patch.object(supervisor.os, "chown") as chown:
            hk = supervisor.child_workdir({"name": "hk", "port": 32322, "process_uid": 29002})
            sg = supervisor.child_workdir({"name": "sg", "port": 32323, "process_uid": 29002})
            self.assertNotEqual(hk, sg)
            self.assertEqual(hk.stat().st_mode & 0o777, 0o700)
            self.assertEqual(sg.stat().st_mode & 0o777, 0o700)
            self.assertEqual(chown.call_count, 2)
            chown.assert_any_call(hk, 29002, 29002)
            chown.assert_any_call(sg, 29002, 29002)

    def test_child_only_is_dropped_to_dedicated_uid(self):
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
        with mock.patch.object(supervisor, "child_workdir", return_value=Path("/tmp/x-tunnel-test-work")), \
             mock.patch.object(supervisor, "outbound_process_gid", return_value=29002), \
             mock.patch.object(supervisor.subprocess, "Popen") as launch:
            launch.return_value.pid = 12345
            supervisor.start_child(entry)
            self.assertEqual(entry["process"], launch.return_value)
            kwargs = launch.call_args.kwargs
            self.assertEqual(kwargs["user"], 29002)
            self.assertEqual(kwargs["group"], 29002)
            self.assertEqual(kwargs["extra_groups"], ())
            self.assertEqual(kwargs["umask"], 0o077)
            self.assertEqual(kwargs["cwd"], "/tmp/x-tunnel-test-work")
            self.assertEqual(kwargs["env"]["HOME"], "/tmp/x-tunnel-test-work")


if __name__ == "__main__":
    unittest.main()
