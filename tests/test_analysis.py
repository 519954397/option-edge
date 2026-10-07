from datetime import datetime, timezone
import unittest

from jin10_research.analysis import analyze_klines, score_event
from jin10_research.strategy import build_strategy_card, future_important_calendar


class AnalysisTests(unittest.TestCase):
    def test_event_scoring_direction(self):
        event = score_event(
            {
                "time": "2026-08-09T10:00:00+00:00",
                "content": "霍尔木兹海峡未重新开放，原油供应中断风险上升",
                "url": "https://example.com/event",
            },
            datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(event["direction"], "bullish")
        self.assertGreaterEqual(event["score"], 70)

    def test_kline_trend_and_staleness(self):
        generated_at = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)
        base = int(generated_at.timestamp()) - 99 * 60
        rows = [
            {
                "time": base + index * 60,
                "open": 70 + index * 0.05,
                "high": 70.2 + index * 0.05,
                "low": 69.8 + index * 0.05,
                "close": 70.1 + index * 0.05,
                "volume": 100,
            }
            for index in range(100)
        ]
        result = analyze_klines("USOIL", rows, generated_at)
        self.assertEqual(result["regime"], "uptrend")
        self.assertFalse(result["stale"])
        self.assertGreater(result["change_60m"], 0)

    def test_stale_data_forces_observe(self):
        card = build_strategy_card(
            [{"score": 90, "direction": "bullish"}],
            {
                "USOIL": {"status": "ok", "stale": True, "regime": "uptrend", "breakout": "up"},
                "UKOIL": {"status": "ok", "stale": True, "regime": "uptrend", "breakout": "up"},
            },
            [],
        )
        self.assertEqual(card["type"], "observe")

    def test_calendar_only_keeps_next_24_hours(self):
        generated = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)
        rows = future_important_calendar(
            [
                {"pub_time": "2026-08-09 13:00", "star": 4, "title": "future"},
                {"pub_time": "2026-08-08 13:00", "star": 5, "title": "past"},
                {"pub_time": "2026-08-09 14:00", "star": 2, "title": "low"},
            ],
            generated,
        )
        self.assertEqual([item["title"] for item in rows], ["future"])


if __name__ == "__main__":
    unittest.main()
