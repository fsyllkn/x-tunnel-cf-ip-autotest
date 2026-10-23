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


    def test_singbox_one_nic_gateway_is_recognized_without_adding_routes(self):
        rules = (
            DEFAULT_IPV4_RULES
            + "15360: from all fwmark 0x1536 lookup 53136\n"
        )
        seen = []

        def fake_ip(command, **kwargs):
            seen.append(command)
            if command == ["ip", "-4", "rule", "show"]:
                return completed(command, rules)
            if command == ["ip", "-4", "route", "get", "1.1.1.1"]:
                return completed(command, SAMPLE_LAN_ROUTE)
            raise AssertionError("unexpected routing mutation: %r" % command)

        with mock.patch("cfip_speedtest.subprocess.run", side_effect=fake_ip), \
                mock.patch.object(DirectCandidateRoutes, "cleanup_stale_routes"), \
                mock.patch("cfip_speedtest.os.geteuid", return_value=0), \
                mock.patch.object(
                    DirectCandidateRoutes, "verify_singbox_network_uid",
                    return_value=29002,
                ):
            self.assertEqual(
                DirectCandidateRoutes.routing_mode("-4", "100"),
                ("singbox-gateway", None),
            )
            with DirectCandidateRoutes(["1.1.1.1"]) as route_guard:
                self.assertEqual(route_guard.network_uid, 29002)
                self.assertEqual(route_guard.routes, [])

        self.assertFalse(any("add" in command or "del" in command for command in seen))

    def test_host_only_local_ebpf_uses_nonroot_worker(self):
        seen = []
        def fake_ip(command, **kwargs):
            seen.append(command)
            if command == ["ip", "-4", "rule", "show"]:
                return completed(command, DEFAULT_IPV4_RULES)
            if command == ["ip", "-4", "route", "get", "1.1.1.1"]:
                return completed(command, SAMPLE_LAN_ROUTE)
            raise AssertionError("unexpected route mutation: %r" % command)

        with mock.patch("cfip_speedtest.subprocess.run", side_effect=fake_ip), \
                mock.patch("cfip_speedtest.os.geteuid", return_value=0), \
                mock.patch.object(DirectCandidateRoutes, "cleanup_stale_routes"), \
                mock.patch.object(DirectCandidateRoutes, "active_singbox_local_ebpf",
                                  return_value=True), \
                mock.patch.object(DirectCandidateRoutes, "verify_singbox_network_uid",
                                  return_value=29002) as check_uid:
            with DirectCandidateRoutes(["1.1.1.1"]) as guard:
                self.assertEqual(guard.network_uid, 29002)
                self.assertEqual(guard.routes, [])
            check_uid.assert_called_once_with(require_gateway=False)
        self.assertEqual(len(seen), 2)

    def test_host_only_native_route_without_singbox_needs_no_isolation(self):
        def fake_ip(command, **kwargs):
            if command == ["ip", "-4", "rule", "show"]:
                return completed(command, DEFAULT_IPV4_RULES)
            if command == ["ip", "-4", "route", "get", "1.1.1.1"]:
                return completed(command, SAMPLE_LAN_ROUTE)
            raise AssertionError("unexpected route mutation: %r" % command)

        with mock.patch("cfip_speedtest.subprocess.run", side_effect=fake_ip), \
                mock.patch("cfip_speedtest.os.geteuid", return_value=0), \
                mock.patch.object(DirectCandidateRoutes, "cleanup_stale_routes"), \
                mock.patch.object(DirectCandidateRoutes, "active_singbox_local_ebpf",
                                  return_value=False), \
                mock.patch.object(DirectCandidateRoutes, "verify_singbox_network_uid"
                                  ) as check_uid:
            with DirectCandidateRoutes(["1.1.1.1"]) as guard:
                self.assertIsNone(guard.network_uid)
                self.assertEqual(guard.routes, [])
            check_uid.assert_not_called()

    def test_unknown_gateway_mark_is_still_rejected(self):
        rules = DEFAULT_IPV4_RULES + "15360: from all fwmark 0x1537 lookup 53136\n"
        with mock.patch("cfip_speedtest.subprocess.run", return_value=completed([], rules)):
            with self.assertRaises(RuntimeError):
                DirectCandidateRoutes.routing_mode("-4", "100")

    def test_singbox_gateway_requires_ebpf_exclusion(self):
        def fake_run(command, **kwargs):
            if command == ["ip", "-4", "route", "show", "table", "53136"]:
                return completed(command, "local default dev lo scope host\n")
            if command == ["nft", "list", "table", "inet", "singbox_one_arm"]:
                return completed(command, "table inet singbox_one_arm { }\n")
            raise AssertionError("unexpected command: %r" % command)

        wrong_config = '{"inbounds":[{"type":"ebpf","local":{"enabled":true,"exclude_uid":[]}}]}'
        with mock.patch("cfip_speedtest.subprocess.run", side_effect=fake_run), \
                mock.patch("cfip_speedtest.load_project_env"), \
                mock.patch.dict("os.environ", {"X_TUNNEL_PROCESS_UID": "29002"}), \
                mock.patch("cfip_speedtest.SINGBOX_GATEWAY_CONFIG") as config_path:
            config_path.read_text.return_value = wrong_config
            with self.assertRaisesRegex(RuntimeError, "not excluded"):
                DirectCandidateRoutes.verify_singbox_network_uid()
            config_path.read_text.return_value = wrong_config.replace(
                '"exclude_uid":[]', '"exclude_uid":[29002]'
            )
            self.assertEqual(DirectCandidateRoutes.verify_singbox_network_uid(), 29002)

    def test_rtt_delegates_to_excluded_network_worker(self):
        mocked = iter([{"type": "rtt", "ip": "1.1.1.1",
                        "measurement": {"latency_ms": 18, "colo": "NRT"}}])
        with mock.patch("cfip_speedtest.isolated_worker_messages", return_value=mocked) as spawn:
            self.assertEqual(
                list(__import__("cfip_speedtest").measure_rtt_candidates(
                    ["1.1.1.1"], 443, True, 10, network_uid=29002
                )),
                [("1.1.1.1", {"latency_ms": 18, "colo": "NRT"})],
            )
            self.assertEqual(spawn.call_args.args[:2], (29002, "rtt"))

    def test_speed_worker_progress_and_result(self):
        updates = []
        responses = iter([
            {"type": "progress", "message": "download started"},
            {"type": "result", "measurement": {"stable_kib": 1000}},
        ])
        with mock.patch("cfip_speedtest.isolated_worker_messages", return_value=responses) as spawn:
            result = __import__("cfip_speedtest").measure_download(
                mock.Mock(network_uid=29002),
                "1.1.1.1", "https", "example.org", "/file", True,
                duration=15, progress_callback=updates.append,
            )
        self.assertEqual(result, {"stable_kib": 1000})
        self.assertEqual(updates, ["download started"])
        self.assertEqual(spawn.call_args.args[:2], (29002, "speed"))

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
