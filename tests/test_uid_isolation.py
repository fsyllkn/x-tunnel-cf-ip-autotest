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
import project_settings
import cfip_speedtest


class UIDIsolationTest(unittest.TestCase):
    def test_default_and_overridden_uid(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(supervisor.configured_process_uid(), 29002)
        with mock.patch.dict(os.environ, {"X_TUNNEL_PROCESS_UID": "0"}):
            with self.assertRaises(ValueError):
                supervisor.configured_process_uid()
        with mock.patch.dict(os.environ, {"X_TUNNEL_PROCESS_UID": "29003"}), \
             mock.patch.object(Path, "read_text", return_value="root:x:0:0:root:/root:/bin/sh\n"):
            self.assertEqual(supervisor.configured_process_uid(), 29003)

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

    def test_shellcrash_gid_auto_and_explicit_opt_out(self):
        with tempfile.TemporaryDirectory() as directory:
            group_file = Path(directory) / "group"
            with mock.patch.object(project_settings, "SHELLCRASH_GROUP_FILE", group_file):
                # Standalone LXC and unrelated GID 7890 must preserve UID/GID.
                group_file.write_text("root:x:0:\\nother:x:7890:\\n", encoding="utf-8")
                with mock.patch.dict(os.environ, {"X_TUNNEL_PROCESS_GID": "auto"}):
                    self.assertEqual(project_settings.configured_process_gid(29002), 29002)
                group_file.write_text("shellcrash:x:1234:\\n", encoding="utf-8")
                with mock.patch.dict(os.environ, {"X_TUNNEL_PROCESS_GID": "auto"}):
                    self.assertEqual(project_settings.configured_process_gid(29002), 29002)
                # Only the exact system group can supply ShellCrash's bypass GID.
                group_file.write_text("root:x:0:\\nshellcrash:x:7890:\\n", encoding="utf-8")
                with mock.patch.dict(os.environ, {"X_TUNNEL_PROCESS_GID": "auto"}):
                    self.assertEqual(project_settings.configured_process_gid(29002), 7890)
                with mock.patch.dict(os.environ, {"X_TUNNEL_PROCESS_GID": "uid"}):
                    self.assertEqual(project_settings.configured_process_gid(29002), 29002)
                with mock.patch.dict(os.environ, {"X_TUNNEL_PROCESS_GID": "7890"}):
                    self.assertEqual(project_settings.configured_process_gid(29002), 7890)

    def test_invalid_or_unverified_gid_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            group_file = Path(directory) / "group"
            group_file.write_text("other:x:7890:\\n", encoding="utf-8")
            with mock.patch.object(project_settings, "SHELLCRASH_GROUP_FILE", group_file):
                with mock.patch.dict(os.environ, {"X_TUNNEL_PROCESS_GID": "7890"}):
                    with self.assertRaisesRegex(ValueError, "requires /etc/group"):
                        project_settings.configured_process_gid(29002)
                with mock.patch.dict(os.environ, {"X_TUNNEL_PROCESS_GID": "0"}):
                    with self.assertRaisesRegex(ValueError, "must be auto, uid, or 7890"):
                        project_settings.configured_process_gid(29002)
                with mock.patch.dict(os.environ, {"X_TUNNEL_PROCESS_GID": "7890"}):
                    with self.assertRaises(ValueError):
                        project_settings.configured_process_gid(0)
                with mock.patch.dict(os.environ, {"X_TUNNEL_PROCESS_GID": "auto"}):
                    group_file.unlink()
                    self.assertEqual(project_settings.configured_process_gid(29002), 29002)

    def test_child_launch_uses_shellcrash_primary_gid_without_changing_uid(self):
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
            "process_gid": 7890,
        }
        entry = {"spec": spec, "process": None, "restart_at": None}
        with mock.patch.object(supervisor, "child_workdir", return_value=Path("/tmp/test-node")), \\
             mock.patch.object(supervisor.subprocess, "Popen") as launch:
            launch.return_value.pid = 45678
            supervisor.start_child(entry)
        self.assertEqual(launch.call_args.kwargs["user"], 29002)
        self.assertEqual(launch.call_args.kwargs["group"], 7890)
        self.assertEqual(launch.call_args.kwargs["extra_groups"], ())
        self.assertEqual(launch.call_args.kwargs["umask"], 0o077)

    def test_isolated_speedtest_worker_uses_the_same_primary_gid(self):
        fake_proc = mock.MagicMock()
        fake_proc.stdout = iter([
            '{"type": "rtt", "ip": "1.1.1.1", "measurement": null}\\n',
        ])
        fake_proc.stderr.read.return_value = ""
        fake_proc.wait.return_value = 0
        fake_proc.poll.return_value = 0
        with mock.patch("cfip_speedtest.os.geteuid", return_value=0), \\
             mock.patch("cfip_speedtest.configured_process_gid", return_value=7890), \\
             mock.patch("cfip_speedtest.subprocess.Popen", return_value=fake_proc) as launch:
            messages = list(cfip_speedtest.isolated_worker_messages(
                29002, "rtt", {"candidates": ["1.1.1.1"], "port": 443, "tls": True, "workers": 1},
            ))
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["ip"], "1.1.1.1")
        self.assertEqual(launch.call_args.kwargs["user"], 29002)
        self.assertEqual(launch.call_args.kwargs["group"], 7890)
        self.assertEqual(launch.call_args.kwargs["extra_groups"], ())



if __name__ == "__main__":
    unittest.main()
