"""Tests for Telegram native rich table report delivery, no network needed."""
import json
import threading
import unittest
from unittest import mock

from cfip_speedtest import TelegramProgress
from telegram_report import MAX_ROWS_PER_PAGE, rich_report_pages


def sample_scan(count=3):
    rows = []
    for index in range(count):
        ip = "104.16.1.%d" % (index + 1)
        rows.append({
            "ip": ip, "colo": "HKG", "latency_ms": 35 + index,
            "status": "measured", "stable": True,
            "stable_mbps": 340.0 + index, "average_mbps": 340.0 + index,
            "selected": index == 0,
        })
    return {
        "tiers_mbps": [300, 250, 200, 150, 100],
        "selected_tier_mbps": 300,
        "met_target_count_at_tier": True,
        "selected_ips": ["104.16.1.1"],
        "candidates": [
            {"ip": row["ip"], "latency_ms": row["latency_ms"],
             "speed_test_attempts": [row]}
            for row in rows
        ],
        "speed_test_rounds": [{"round": 1, "results": rows}],
        "regional_assignments": {
            "🇭🇰HK<&>": {
                "ip": "104.16.1.1", "colo": "HKG", "direct_region_match": True,
                "fallback_ips": [{"ip": "104.16.1.2", "colo": "HKG"}],
            },
        },
        "applied_nodes": {"🇭🇰HK<&>": "104.16.1.1,104.16.1.2"},
    }


class RichTableTests(unittest.TestCase):
    def test_full_scan_contains_real_bordered_tables_and_escaped_values(self):
        result = rich_report_pages(sample_scan())
        self.assertEqual(len(result), 1)
        text = result[0]
        self.assertIn("<table bordered compact>", text)
        self.assertIn("<th>节点</th>", text)
        self.assertIn("<th>均速 · RTT / 状态</th>", text)
        self.assertIn("🇭🇰HK&lt;&amp;&gt;", text)
        self.assertNotIn("🇭🇰HK<&>", text)
        self.assertIn("<code>104.16.1.1</code>", text)
        self.assertIn("340.0 Mbps", text)
        self.assertIn("✅ 已写入", text)

    def test_large_scan_paginated_with_no_lost_rows(self):
        result = rich_report_pages(sample_scan(MAX_ROWS_PER_PAGE * 2 + 3))
        self.assertEqual(len(result), 3)
        self.assertIn("104.16.1.31", result[-1])
        self.assertEqual(
            sum(page.count("Mbps</b>") for page in result),
            MAX_ROWS_PER_PAGE * 2 + 3,
        )

    def test_failed_and_single_scan_tables(self):
        failed = rich_report_pages(None, "网络连接失败 <403>")
        self.assertIn("<table bordered compact>", failed[0])
        self.assertIn("网络连接失败 &lt;403&gt;", failed[0])
        single = rich_report_pages({
            "mode": "single-ip", "ip": "1.1.1.1",
            "result": {
                "average_mbps": 265.2, "median_mbps": 270.2,
                "variation_coefficient": 0.04, "stable": True,
                "tcp_ms": 32, "colo": "NRT",
            },
            "applied_nodes": {"jp1": "1.1.1.1"},
        })
        self.assertIn("<table bordered compact>", single[0])
        self.assertIn("<th>新 CF IP</th>", single[0])
        self.assertIn("265.20 Mbps", single[0])

    @staticmethod
    def progress(message_id=None):
        progress = TelegramProgress.__new__(TelegramProgress)
        progress.token = "dummy"
        progress.chat_id = "123"
        progress.message_id = message_id
        progress.thread = None
        progress.stop_event = threading.Event()
        progress.last_sent_text = None
        return progress

    def test_finish_edits_progress_using_rich_message(self):
        progress = self.progress(message_id=77)
        with mock.patch.object(progress, "_request", return_value=True) as req:
            progress.finish(sample_scan())
        self.assertEqual(req.call_count, 1)
        args, kwargs = req.call_args
        self.assertEqual(args, ("editMessageText",))
        self.assertIn("<table bordered compact>", kwargs["rich_html"])
        self.assertNotIn("parse_mode", kwargs)

    def test_finish_sends_native_message_without_progress(self):
        progress = self.progress()
        with mock.patch.object(progress, "_request", return_value=True) as req:
            progress.finish(sample_scan())
        self.assertEqual(req.call_args.args, ("sendRichMessage",))

    def test_rejected_rich_api_falls_back_to_plain_message(self):
        progress = self.progress(message_id=23)
        calls = []
        def fake_request(method, text=None, parse_mode=None, rich_html=None):
            calls.append((method, text, rich_html))
            return rich_html is None
        with mock.patch.object(progress, "_request", side_effect=fake_request):
            progress.finish(sample_scan())
        self.assertEqual(calls[0][0], "editMessageText")
        self.assertIsNotNone(calls[0][2])
        self.assertEqual(calls[1][0], "editMessageText")
        self.assertIsNone(calls[1][2])
        self.assertIn("CF测速完成", calls[1][1])

    def test_rich_api_request_shape(self):
        progress = self.progress(message_id=23)
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = (
            b'{"ok":true,"result":{"message_id":23}}'
        )
        opener = mock.MagicMock()
        opener.open.return_value = response
        progress.proxies = ["http://127.0.0.1:7890"]
        progress.proxy = None
        with mock.patch("urllib.request.build_opener", return_value=opener):
            self.assertTrue(progress._request(
                "editMessageText", rich_html="<table bordered></table>",
            ))
        sent = opener.open.call_args.args[0]
        payload = json.loads(sent.data)
        self.assertEqual(payload["message_id"], 23)
        self.assertEqual(payload["rich_message"]["html"], "<table bordered></table>")
        self.assertNotIn("text", payload)
        self.assertNotIn("parse_mode", payload)


if __name__ == "__main__":
    unittest.main()
