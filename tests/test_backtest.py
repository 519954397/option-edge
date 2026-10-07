import unittest

from jin10_research.backtest import run_trend_backtest, validation_gate


class BacktestTests(unittest.TestCase):
    def test_trend_backtest_and_gate(self):
        bars = [
            {
                "bar_time": index * 60,
                "close": 70 + index * 0.05,
                "open": 70 + index * 0.05,
                "high": 70.2 + index * 0.05,
                "low": 69.8 + index * 0.05,
                "volume": 100,
            }
            for index in range(800)
        ]
        result = run_trend_backtest(bars)
        self.assertGreaterEqual(result["samples"], 30)
        self.assertEqual(result["win_rate"], 1.0)
        gate = validation_gate(
            {
                "completed": 25,
                "win_rate": 0.6,
                "average_signed_return_pct": 0.2,
            },
            result,
        )
        self.assertTrue(gate["ready"])

    def test_empty_data_is_not_ready(self):
        result = run_trend_backtest([])
        gate = validation_gate({"completed": 0}, result)
        self.assertFalse(gate["ready"])
        self.assertGreaterEqual(len(gate["reasons"]), 2)


if __name__ == "__main__":
    unittest.main()
