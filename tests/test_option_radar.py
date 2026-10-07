import unittest

from jin10_research.option_radar import (
    black76_greeks,
    black76_price,
    black76_theta_per_day,
    implied_volatility,
    score_option_rows,
)


class OptionRadarTests(unittest.TestCase):
    def test_implied_volatility_round_trip(self):
        expected = 0.32
        price = black76_price(550.0, 560.0, 30 / 365, expected, 0.015, "CALL")
        actual = implied_volatility(price, 550.0, 560.0, 30 / 365, 0.015, "CALL")
        self.assertIsNotNone(actual)
        self.assertAlmostEqual(actual, expected, places=5)

    def test_call_and_put_deltas_have_expected_sign(self):
        call_delta, call_gamma, _ = black76_greeks(
            950.0, 950.0, 30 / 365, 0.25, 0.015, "CALL"
        )
        put_delta, put_gamma, _ = black76_greeks(
            950.0, 950.0, 30 / 365, 0.25, 0.015, "PUT"
        )
        self.assertGreater(call_delta, 0)
        self.assertLess(put_delta, 0)
        self.assertAlmostEqual(call_gamma, put_gamma)

    def test_long_option_theta_is_negative(self):
        theta = black76_theta_per_day(
            950.0, 950.0, 30 / 365, 0.25, 0.015, "CALL"
        )
        self.assertIsNotNone(theta)
        self.assertLess(theta, 0)

    def test_scoring_prefers_tight_liquid_quote(self):
        base = {
            "asset": "gold",
            "days_to_expiry": 20,
            "moneyness_pct": 0.5,
            "delta": 0.50,
            "gamma": 0.002,
            "valid_quote": True,
            "iv_pct": 25.0,
        }
        rows = score_option_rows(
            [
                dict(base, instrument="good", spread_fraction=0.01, open_interest=5000, depth=50),
                dict(base, instrument="poor", spread_fraction=0.20, open_interest=5, depth=1),
            ]
        )
        by_id = {row["instrument"]: row for row in rows}
        self.assertGreater(by_id["good"]["score_balanced"], by_id["poor"]["score_balanced"])
        self.assertTrue(by_id["good"]["tradable"])

    def test_buyer_recommendation_requires_hard_gates_and_core_signals(self):
        row = {
            "asset": "gold",
            "asset_name": "黄金",
            "underlying": "au-test",
            "instrument": "au-test-c950",
            "option_type": "CALL",
            "days_to_expiry": 30,
            "moneyness_pct": 0.2,
            "delta": 0.50,
            "gamma": 0.002,
            "valid_quote": True,
            "bid": 9.9,
            "ask": 10.0,
            "mid": 9.95,
            "spread_fraction": 0.01,
            "iv_pct": 20.0,
            "theta_burden_pct": 1.0,
            "open_interest": 3000,
            "depth": 30,
            "forecast_rv_pct": 30.0,
            "rv_ratio": 1.20,
            "trend_z": 1.5,
            "day_move_z": 0.5,
            "breakout_direction": 1.0,
            "scenario_roi_pct": 40.0,
        }
        result = score_option_rows([row])[0]
        self.assertTrue(result["buyer_eligible"])
        self.assertEqual(result["recommendation"], "RECOMMENDED")

        rejected = score_option_rows([dict(row, instrument="far", moneyness_pct=18.0)])[0]
        self.assertFalse(rejected["buyer_eligible"])
        self.assertIn("行权价偏离标的超过12%", rejected["hard_blockers"])


if __name__ == "__main__":
    unittest.main()
