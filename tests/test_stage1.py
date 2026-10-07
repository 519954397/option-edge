from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from jin10_research.collector import collect_research_bundle, collect_window
from jin10_research.report import render_stage1_report, write_report


class FakeClient:
    def __init__(self):
        self.calls = []

    def call_tool(self, name, arguments=None):
        arguments = arguments or {}
        self.calls.append((name, arguments))
        if name == "list_flash":
            if not arguments.get("cursor"):
                return {
                    "status": 200,
                    "data": {
                        "items": [
                            {
                                "time": "2026-08-09T11:00:00+00:00",
                                "content": "WTI原油上涨",
                                "url": "https://example.com/1",
                            }
                        ],
                        "has_more": True,
                        "next_cursor": "p2",
                    },
                }
            return {
                "status": 200,
                "data": {
                    "items": [
                        {
                            "time": "2026-08-08T20:00:00+00:00",
                            "content": "旧消息",
                            "url": "https://example.com/old",
                        }
                    ],
                    "has_more": False,
                    "next_cursor": "",
                },
            }
        if name == "list_news":
            return {"status": 200, "data": {"items": [], "has_more": False, "next_cursor": ""}}
        if name == "get_quote":
            code = arguments["code"]
            return {
                "status": 200,
                "data": {
                    "code": code,
                    "name": code,
                    "close": "80",
                    "ups_percent": "1",
                    "high": "81",
                    "low": "78",
                    "time": "2026-08-09T11:00:00+00:00",
                },
            }
        if name == "get_kline":
            code = arguments["code"]
            return {
                "status": 200,
                "data": {
                    "code": code,
                    "klines": [
                        {
                            "time": 1786273200 + index * 60,
                            "open": str(79 + index / 100),
                            "high": str(79.2 + index / 100),
                            "low": str(78.8 + index / 100),
                            "close": str(79.1 + index / 100),
                            "volume": 100 + index,
                        }
                        for index in range(100)
                    ],
                },
            }
        if name == "list_calendar":
            return {"status": 200, "data": []}
        raise AssertionError(name)


class StageOneTests(unittest.TestCase):
    def test_cursor_pagination_stops_at_cutoff(self):
        client = FakeClient()
        items = collect_window(
            client,
            "list_flash",
            datetime(2026, 8, 9, 0, 0, tzinfo=timezone.utc),
        )
        self.assertEqual([item["url"] for item in items], ["https://example.com/1"])
        self.assertEqual(client.calls[1], ("list_flash", {"cursor": "p2"}))

    def test_bundle_and_report(self):
        client = FakeClient()
        bundle = collect_research_bundle(
            client,
            12,
            now=datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc),
        )
        report = render_stage1_report(bundle)
        self.assertIn("WTI原油上涨", report)
        self.assertIn("USOIL 80", report)
        with TemporaryDirectory() as directory:
            path = write_report(report, Path(directory))
            self.assertTrue(path.is_file())


if __name__ == "__main__":
    unittest.main()
