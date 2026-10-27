"""Supervisor restart lifecycle regression tests (no real Go/tunnel processes)."""
import fcntl
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import supervisor


class SupervisorLifecycleTests(unittest.TestCase):
    def test_singleton_lock_prevents_duplicate_supervisor(self):
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory) / "x-tunnel-supervisor.lock"
            for _ in range(3):
                with supervisor.supervisor_instance_lock(lock):
                    with self.assertRaisesRegex(RuntimeError, "another X-Tunnel supervisor"):
                        with supervisor.supervisor_instance_lock(lock):
                            pass
                    self.assertEqual(lock.read_text().strip(), str(os.getpid()))
                with supervisor.supervisor_instance_lock(lock):
                    pass

    def test_lock_released_after_exception_without_unlink(self):
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory) / "x-tunnel-supervisor.lock"
            with self.assertRaisesRegex(ValueError, "startup failed"):
                with supervisor.supervisor_instance_lock(lock):
                    raise ValueError("startup failed")
            self.assertTrue(lock.exists())
            with supervisor.supervisor_instance_lock(lock):
                pass

    def test_supervisor_reaps_managed_children_if_reconcile_crashes(self):
        child = {
            "spec": {"name": "node-1"},
            "process": mock.Mock(),
            "restart_at": None,
        }

        def interrupted_reconcile(specs, children):
            children["node-1"] = child
            raise RuntimeError("unexpected reconciliation failure")

        with mock.patch.object(supervisor, "stopping", False), \
             mock.patch.object(supervisor.signal, "signal"), \
             mock.patch.object(supervisor, "signature", return_value=(1,)), \
             mock.patch.object(supervisor, "read_specs", return_value=[]), \
             mock.patch.object(supervisor.subprocess, "run",
                               return_value=subprocess.CompletedProcess([], 0, stdout="")), \
             mock.patch.object(supervisor, "reconcile", side_effect=interrupted_reconcile), \
             mock.patch.object(supervisor, "stop_child") as stop_child:
            with self.assertRaisesRegex(RuntimeError, "unexpected reconciliation failure"):
                supervisor.run_supervisor()
        stop_child.assert_called_once_with(child)

    def test_unit_kills_service_cgroup_not_just_parent(self):
        service = (
            Path(__file__).resolve().parents[1] / "systemd" / "x-tunnel.service"
        ).read_text()
        self.assertIn("KillMode=control-group", service)
        self.assertIn("SendSIGKILL=yes", service)
        self.assertIn("TimeoutStopSec=45s", service)
        self.assertNotIn("KillMode=process", service.split("[Service]")[1].split("# Do not")[0])


if __name__ == "__main__":
    unittest.main()
