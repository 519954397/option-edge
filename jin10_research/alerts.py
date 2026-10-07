from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from .analysis import analyze_klines, score_events
from .backtest import run_trend_backtest, validation_gate
from .storage import ResearchStore
from .strategy import build_strategy_card


def evaluate_alert(
    bundle: Dict[str, Any],
    store: ResearchStore,
    *,
    allow_unvalidated: bool = False,
) -> Dict[str, Any]:
    generated_at = datetime.fromisoformat(bundle["generated_at"])
    events = score_events(bundle.get("oil_flashes", []), generated_at)
    analyses = {
        code: analyze_klines(code, rows, generated_at)
        for code, rows in bundle.get("klines", {}).items()
    }
    strategy = build_strategy_card(
        events, analyses, bundle.get("calendar", []), generated_at=generated_at
    )
    historical_bars = store.kline_history("USOIL")
    backtest = run_trend_backtest(historical_bars)
    gate = validation_gate(store.statistics(), backtest)
    candidate = strategy["type"] != "observe" and strategy["confidence"] in ("medium", "high")
    emitted = candidate and (gate["ready"] or allow_unvalidated)
    return {
        "generated_at": bundle["generated_at"],
        "candidate": candidate,
        "emitted": emitted,
        "validation_bypassed": bool(emitted and not gate["ready"]),
        "strategy": strategy,
        "gate": gate,
        "backtest": backtest,
        "top_events": [
            {
                "time": item["time"],
                "level": item["level"],
                "score": item["score"],
                "direction": item["direction"],
                "title": item["title"],
                "url": item["url"],
            }
            for item in events[:5]
        ],
    }


def append_alert(path: Path, alert: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(alert, ensure_ascii=False) + "\n")
