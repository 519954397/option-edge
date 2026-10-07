from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional


def _fmt(value: Any, digits: int = 3) -> str:
    try:
        return ("{:.%df}" % digits).format(float(value))
    except (TypeError, ValueError):
        return "-"


def future_important_calendar(
    calendar: Iterable[Dict[str, Any]],
    generated_at: datetime,
    horizon_hours: int = 24,
) -> List[Dict[str, Any]]:
    end = generated_at + timedelta(hours=horizon_hours)
    rows = []
    for item in calendar:
        if int(item.get("star") or 0) < 3 or not item.get("pub_time"):
            continue
        try:
            published = datetime.fromisoformat(str(item["pub_time"]).replace("Z", "+00:00"))
            if published.tzinfo is None:
                published = published.replace(tzinfo=generated_at.tzinfo)
        except ValueError:
            continue
        if generated_at <= published <= end:
            rows.append(item)
    return rows


def build_strategy_card(
    events: Iterable[Dict[str, Any]],
    kline_analysis: Dict[str, Dict[str, Any]],
    calendar: Iterable[Dict[str, Any]],
    generated_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    events = list(events)
    analyses = [item for item in kline_analysis.values() if item.get("status") == "ok"]
    high_events = [item for item in events if item.get("score", 0) >= 70]
    bullish_score = sum(item["score"] for item in high_events if item["direction"] == "bullish")
    bearish_score = sum(item["score"] for item in high_events if item["direction"] == "bearish")
    event_bias = "bullish" if bullish_score > bearish_score else "bearish" if bearish_score > bullish_score else "neutral"
    stale = not analyses or any(item.get("stale") for item in analyses)
    regimes = {item.get("regime") for item in analyses}
    breakouts = {item.get("breakout") for item in analyses}
    # The caller's event and K-line times share the report generation timezone.
    generated_at = generated_at or datetime.now().astimezone()
    important_calendar = future_important_calendar(calendar, generated_at)

    strategy_type = "observe"
    reason = "新闻与价格尚未形成一致确认"
    if stale:
        reason = "行情或K线已经过期，禁止生成可执行策略"
    elif high_events and ("event_shock" in regimes or breakouts.intersection({"up", "down"})):
        strategy_type = "event_breakout"
        reason = "高影响事件与突破/波动放大同时出现"
    elif regimes == {"uptrend"} or regimes == {"downtrend"}:
        strategy_type = "trend_following"
        reason = "WTI与布伦特短周期趋势一致"
    elif regimes == {"range"} and not high_events:
        strategy_type = "mean_reversion"
        reason = "行情处于震荡状态且没有高影响事件"

    if strategy_type == "observe":
        bias = event_bias if event_bias != "neutral" else "neutral"
        trigger = "等待实时K线恢复，并确认WTI与布伦特方向一致"
        invalidation = "数据继续过期或新闻与价格保持冲突"
        confidence = "low"
    else:
        direction_from_regime = (
            "bullish"
            if regimes.intersection({"uptrend"}) or "up" in breakouts
            else "bearish"
            if regimes.intersection({"downtrend"}) or "down" in breakouts
            else event_bias
        )
        bias = direction_from_regime if direction_from_regime != "neutral" else event_bias
        wti = kline_analysis.get("USOIL", {})
        trigger_level = wti.get("high20") if bias == "bullish" else wti.get("low20")
        opposite_level = wti.get("low20") if bias == "bullish" else wti.get("high20")
        trigger = "WTI有效突破 {}，且布伦特同步确认".format(_fmt(trigger_level))
        invalidation = "WTI重新穿越 {}，或高影响事件被可靠来源否定".format(_fmt(opposite_level))
        confidence = "high" if len(high_events) >= 2 and len(regimes) == 1 else "medium"

    return {
        "type": strategy_type,
        "bias": bias,
        "confidence": confidence,
        "reason": reason,
        "trigger": trigger,
        "invalidation": invalidation,
        "high_event_count": len(high_events),
        "event_bias": event_bias,
        "important_calendar_count": len(important_calendar),
        "risk_notes": [
            "策略只在数据新鲜且价格确认后有效",
            "财经日历高重要性事件前后暂停普通策略",
            "初期仅记录模拟信号，不连接交易账户",
        ],
    }
