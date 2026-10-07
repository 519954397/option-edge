from datetime import datetime, timezone
import unittest

from jin10_research.ai import deterministic_gate
from jin10_research.assets import resolve_asset


class AssetTests(unittest.TestCase):
    def test_resolve_common_symbols(self):
        self.assertEqual(resolve_asset("OANDA:XAUUSD").key, "gold")
        self.assertEqual(resolve_asset("TVC:USOIL").key, "oil")
        self.assertEqual(resolve_asset("COMEX:SI1!").key, "silver")

    def test_imminent_high_impact_event_pauses(self):
        now = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)
        context = {
            "generated_at": now.isoformat(),
            "technical": {"status": "ok", "stale": False, "regime": "uptrend"},
            "calendar": [
                {
                    "title": "美国CPI",
                    "star": 5,
                    "parsed_time": "2026-08-09T12:30:00+00:00",
                }
            ],
        }
        decision = deterministic_gate(context)
        self.assertEqual(decision["allowed_direction"], "none")
        self.assertEqual(decision["risk_multiplier"], 0)
        self.assertEqual(decision["blocking_event"], "美国CPI")

    def test_trend_gate_uses_half_risk_without_ai(self):
        context = {
            "generated_at": "2026-08-09T12:00:00+00:00",
            "technical": {"status": "ok", "stale": False, "regime": "downtrend"},
            "calendar": [],
        }
        decision = deterministic_gate(context)
        self.assertEqual(decision["allowed_direction"], "short_only")
        self.assertEqual(decision["risk_multiplier"], 0.5)
        self.assertFalse(decision["ai_used"])


if __name__ == "__main__":
    unittest.main()
