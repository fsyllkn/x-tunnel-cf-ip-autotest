"""Regression tests for OpenWrt speedtest route selection.

Run on a development machine with: python3 -m unittest discover -s tests
Mocks all ip commands; never changes the host routing table.
"""
import subprocess
import unittest
from unittest import mock

from cfip_speedtest import DirectCandidateRoutes


DEFAULT_IPV4_RULES = (
    "0:      from all lookup local\n"
    "32766:  from all lookup main\n"
    "32767:  from all lookup default\n"
)
SAMPLE_LAN_ROUTE = (
    "1.1.1.1 via 192.168.68.1 dev br-lan src 192.168.68.11 uid 0\n    cache\n"
)


def completed(command, stdout):
    return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")


class NativeRoutesTest(unittest.TestCase):
    def test_without_shellcrash_selects_native_mode(self):
        with mock.patch("cfip_speedtest.subprocess.run", return_value=completed([], DEFAULT_IPV4_RULES)):
            self.assertEqual(
                DirectCandidateRoutes.routing_mode("-4", "100"),
                ("native-direct", None),
            )

    def test_policy_mode_is_still_supported(self):
        rules = DEFAULT_IPV4_RULES + "100: from all fwmark 0x1 lookup 100\n"
        with mock.patch("cfip_speedtest.subprocess.run", return_value=completed([], rules)):
            self.assertEqual(
                DirectCandidateRoutes.routing_mode("-4", "100"),
                ("marked-policy", "0x1"),
            )

    def test_unknown_policy_is_rejected(self):
        rules = DEFAULT_IPV4_RULES + "200: from all fwmark 0x3 lookup 200\n"
        with mock.patch("cfip_speedtest.subprocess.run", return_value=completed([], rules)):
            with self.assertRaises(RuntimeError):
                DirectCandidateRoutes.routing_mode("-4", "100")

    def test_native_mode_never_adds_routes(self):
        seen = []

        def fake_ip(command, **kwargs):
            seen.append(command)
            if command == ["ip", "-4", "rule", "show"]:
                return completed(command, DEFAULT_IPV4_RULES)
            if command == ["ip", "-4", "route", "get", "1.1.1.1"]:
                return completed(command, SAMPLE_LAN_ROUTE)
            raise AssertionError("unexpected routing operation: %r" % command)

        with mock.patch("cfip_speedtest.subprocess.run", side_effect=fake_ip):
            routes = DirectCandidateRoutes(["1.1.1.1"])
            routes.install_route("1.1.1.1")
            self.assertEqual(routes.routes, [])
            self.assertEqual(len(seen), 2)

    def test_native_mode_rejects_tunnel(self):
        route = "1.1.1.1 dev tun0 src 10.0.0.2\n"
        with mock.patch("cfip_speedtest.subprocess.run", return_value=completed([], route)):
            with self.assertRaises(RuntimeError):
                DirectCandidateRoutes.verify_native_route("1.1.1.1", "-4")

    def test_native_mode_rejects_non_main_table(self):
        route = "1.1.1.1 via 192.168.68.1 dev br-lan table 500 src 192.168.68.11\n"
        with mock.patch("cfip_speedtest.subprocess.run", return_value=completed([], route)):
            with self.assertRaises(RuntimeError):
                DirectCandidateRoutes.verify_native_route("1.1.1.1", "-4")


if __name__ == "__main__":
    unittest.main()
