"""Regression tests for actionable SOCKS5 listener bind errors."""
import errno
import unittest
from unittest import mock

import supervisor


class FakeSocket:
    def __init__(self, failure):
        self.failure = failure
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def setsockopt(self, *args):
        pass
    def bind(self, address):
        if self.failure is not None:
            raise self.failure


class ListenerBindErrorsTest(unittest.TestCase):
    def test_unassigned_lan_ip_fails_with_address_and_hint(self):
        failure = OSError(errno.EADDRNOTAVAIL, "Cannot assign requested address")
        with mock.patch.object(supervisor.socket, "socket", return_value=FakeSocket(failure)):
            with self.assertRaisesRegex(ValueError, "192\\.168\\.68\\.11") as raised:
                supervisor.port_available(32322, ["192.168.68.11"], set())
        message = str(raised.exception)
        self.assertIn("EADDRNOTAVAIL", message)
        self.assertIn("ip -br addr", message)
        self.assertIn("defaults.listen_hosts", message)

    def test_unassigned_ip_does_not_scan_next_ports(self):
        failure = OSError(errno.EADDRNOTAVAIL, "Cannot assign requested address")
        with mock.patch.object(supervisor.socket, "socket", return_value=FakeSocket(failure)), \
             mock.patch.object(supervisor, "port_listener_owners") as owners:
            with self.assertRaisesRegex(ValueError, "192\\.168\\.68\\.11"):
                supervisor.allocate_port(32322, ["192.168.68.11"], set(), set(), "hk")
        owners.assert_not_called()

    def test_actual_port_conflict_still_falls_forward(self):
        calls = []
        def bind_check(port, hosts, reusable):
            calls.append(port)
            return port == 32323
        with mock.patch.object(supervisor, "port_available", side_effect=bind_check), \
             mock.patch.object(supervisor, "port_listener_owners", return_value=[{
                 "name": "other", "pid": 123, "is_x_tunnel": False
             }]):
            self.assertEqual(supervisor.allocate_port(
                32322, ["127.0.0.1"], set(), set(), "hk"
            ), 32323)
        self.assertEqual(calls, [32322, 32323])

    def test_permission_error_is_not_misreported_as_port_conflict(self):
        failure = OSError(errno.EACCES, "Permission denied")
        with mock.patch.object(supervisor.socket, "socket", return_value=FakeSocket(failure)):
            with self.assertRaisesRegex(ValueError, "Errno 13") as raised:
                supervisor.port_available(32322, ["127.0.0.1"], set())
        self.assertIn("127.0.0.1:32322", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
