from __future__ import annotations

from statistics import mean
from typing import Any, Dict, Iterable, List


def run_trend_backtest(
    bars: Iterable[Dict[str, Any]],
    *,
    horizon_bars: int = 30,
    step_bars: int = 15,
) -> Dict[str, Any]:
    rows = sorted(list(bars), key=lambda item: int(item["bar_time"]))
    samples: List[float] = []
    for index in range(60, len(rows) - horizon_bars, max(1, step_bars)):
        window = rows[index - 60 : index]
        closes = [float(item["close"]) for item in window if item.get("close") is not None]
        if len(closes) != 60 or not closes[-1]:
            continue
        sma20 = mean(closes[-20:])
        sma60 = mean(closes)
        current = closes[-1]
        direction = 1 if current > sma20 > sma60 else -1 if current < sma20 < sma60 else 0
        if not direction:
            continue
        future = float(rows[index + horizon_bars]["close"])
        signed_return = (future / current - 1) * 100 * direction
        samples.append(signed_return)

    wins = sum(1 for value in samples if value > 0)
    return {
        "strategy": "sma20_sma60_trend",
        "horizon_bars": horizon_bars,
        "samples": len(samples),
        "wins": wins,
        "win_rate": wins / len(samples) if samples else None,
        "average_signed_return_pct": mean(samples) if samples else None,
        "maximum_gain_pct": max(samples) if samples else None,
        "maximum_loss_pct": min(samples) if samples else None,
    }


def validation_gate(
    strategy_stats: Dict[str, Any],
    backtest: Dict[str, Any],
    *,
    min_completed: int = 20,
    min_backtest_samples: int = 30,
    min_win_rate: float = 0.52,
) -> Dict[str, Any]:
    reasons = []
    completed = int(strategy_stats.get("completed") or 0)
    live_win_rate = strategy_stats.get("win_rate")
    live_avg = strategy_stats.get("average_signed_return_pct")
    if completed < min_completed:
        reasons.append("已完成策略样本不足：{} < {}".format(completed, min_completed))
    if completed and (live_win_rate is None or live_win_rate < min_win_rate):
        reasons.append("模拟策略胜率未达到 {:.0%}".format(min_win_rate))
    if completed and (live_avg is None or live_avg <= 0):
        reasons.append("模拟策略平均方向收益尚未为正")

    samples = int(backtest.get("samples") or 0)
    backtest_win_rate = backtest.get("win_rate")
    backtest_avg = backtest.get("average_signed_return_pct")
    if samples < min_backtest_samples:
        reasons.append("历史回测样本不足：{} < {}".format(samples, min_backtest_samples))
    if samples and (backtest_win_rate is None or backtest_win_rate < min_win_rate):
        reasons.append("历史回测胜率未达到 {:.0%}".format(min_win_rate))
    if samples and (backtest_avg is None or backtest_avg <= 0):
        reasons.append("历史回测平均方向收益尚未为正")
    return {
        "ready": not reasons,
        "reasons": reasons,
        "requirements": {
            "min_completed_strategies": min_completed,
            "min_backtest_samples": min_backtest_samples,
            "min_win_rate": min_win_rate,
            "positive_average_return": True,
        },
    }

