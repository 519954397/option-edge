from __future__ import annotations

from datetime import datetime, timezone
from statistics import mean
from typing import Any, Dict, Iterable, List, Optional


BULLISH_TERMS = (
    "未重新开放",
    "不会重新开放",
    "保持对海峡控制",
    "关闭",
    "封锁",
    "供应中断",
    "出口骤降",
    "减产",
    "袭击",
    "扣押",
    "制裁",
    "库存减少",
    "库存下降",
    "火灾",
    "停产",
)

BEARISH_TERMS = (
    "重新开放",
    "恢复通航",
    "恢复装载",
    "恢复出口",
    "增产",
    "出口增加",
    "库存增加",
    "库存上升",
    "需求下降",
    "进口下降",
    "停火",
    "缓解",
    "火灾已被扑灭",
)

HIGH_IMPACT_TERMS = (
    "霍尔木兹",
    "欧佩克",
    "OPEC",
    "制裁",
    "战争",
    "袭击",
    "库存",
    "美联储",
    "非农",
    "CPI",
    "PPI",
    "减产",
    "停产",
)


def _text(item: Dict[str, Any]) -> str:
    return "{}\n{}\n{}".format(
        item.get("title", ""),
        item.get("content", ""),
        item.get("introduction", ""),
    )


def score_event(item: Dict[str, Any], generated_at: datetime) -> Dict[str, Any]:
    text = _text(item)
    published = datetime.fromisoformat(item["time"].replace("Z", "+00:00"))
    age_hours = max(0.0, (generated_at - published).total_seconds() / 3600.0)

    bullish_hits = [term for term in BULLISH_TERMS if term.lower() in text.lower()]
    bearish_hits = [term for term in BEARISH_TERMS if term.lower() in text.lower()]
    # Specific negated phrases should not also be counted as a generic reopening.
    if any(term in text for term in ("未重新开放", "不会重新开放", "保持对海峡控制")):
        bearish_hits = [term for term in bearish_hits if term != "重新开放"]

    direction_value = len(bullish_hits) - len(bearish_hits)
    direction = "bullish" if direction_value > 0 else "bearish" if direction_value < 0 else "neutral"
    impact_hits = [term for term in HIGH_IMPACT_TERMS if term.lower() in text.lower()]
    direct_asset_hits = sum(
        1
        for term in ("原油", "石油", "油价", "WTI", "布伦特", "炼油", "油轮")
        if term.lower() in text.lower()
    )
    importance = min(30, 18 + 7 * len(impact_hits))
    relevance = min(25, 12 + 7 * direct_asset_hits)
    surprise = min(15, 4 + 3 * (len(bullish_hits) + len(bearish_hits)))
    recency = max(0, round(10 * (1 - min(age_hours, 24) / 24)))
    credibility = 10 if any(term in text for term in ("官方", "能源部", "统计局", "CFTC", "海关")) else 7
    score = min(100, importance + relevance + surprise + recency + credibility)
    level = "S" if score >= 85 else "A" if score >= 70 else "B" if score >= 50 else "C"
    return {
        "time": item.get("time"),
        "title": item.get("title") or item.get("content", "")[:120],
        "url": item.get("url", ""),
        "score": score,
        "level": level,
        "direction": direction,
        "bullish_hits": bullish_hits,
        "bearish_hits": bearish_hits,
        "impact_hits": impact_hits,
        "raw": item,
    }


def score_events(items: Iterable[Dict[str, Any]], generated_at: datetime) -> List[Dict[str, Any]]:
    events = [score_event(item, generated_at) for item in items]
    events.sort(key=lambda item: (item["score"], item["time"] or ""), reverse=True)
    return events


def _float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def analyze_klines(
    code: str,
    raw_klines: Iterable[Dict[str, Any]],
    generated_at: datetime,
) -> Dict[str, Any]:
    rows = []
    for item in raw_klines:
        close = _float(item.get("close"))
        high = _float(item.get("high"))
        low = _float(item.get("low"))
        open_price = _float(item.get("open"))
        if None in (close, high, low, open_price):
            continue
        rows.append(
            {
                "time": int(item.get("time", 0)),
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": _float(item.get("volume")) or 0.0,
            }
        )
    rows.sort(key=lambda row: row["time"])
    if len(rows) < 20:
        return {"code": code, "status": "insufficient_data", "bars": len(rows)}

    closes = [row["close"] for row in rows]
    volumes = [row["volume"] for row in rows]
    last = rows[-1]

    def change(period: int) -> Optional[float]:
        if len(closes) <= period or not closes[-period - 1]:
            return None
        return (closes[-1] / closes[-period - 1] - 1) * 100

    sma20 = mean(closes[-20:])
    sma60 = mean(closes[-60:]) if len(closes) >= 60 else mean(closes)
    true_ranges = []
    for previous, current in zip(rows[-15:-1], rows[-14:]):
        true_ranges.append(
            max(
                current["high"] - current["low"],
                abs(current["high"] - previous["close"]),
                abs(current["low"] - previous["close"]),
            )
        )
    atr14 = mean(true_ranges) if true_ranges else 0.0
    prior20 = rows[-21:-1] if len(rows) >= 21 else rows[:-1]
    high20 = max(row["high"] for row in prior20)
    low20 = min(row["low"] for row in prior20)
    prior_volume = mean(volumes[-21:-1]) if len(volumes) >= 21 else mean(volumes[:-1])
    volume_ratio = last["volume"] / prior_volume if prior_volume else None
    range_position = (
        (last["close"] - low20) / (high20 - low20) if high20 > low20 else 0.5
    )
    breakout = "up" if last["close"] > high20 else "down" if last["close"] < low20 else "none"

    if abs(change(15) or 0) >= 1.5 or (volume_ratio or 0) >= 2.0:
        regime = "event_shock"
    elif last["close"] > sma20 > sma60:
        regime = "uptrend"
    elif last["close"] < sma20 < sma60:
        regime = "downtrend"
    elif abs(sma20 - sma60) / last["close"] < 0.0025:
        regime = "range"
    else:
        regime = "mixed"

    bar_time = datetime.fromtimestamp(last["time"], tz=timezone.utc)
    age_minutes = max(0.0, (generated_at.astimezone(timezone.utc) - bar_time).total_seconds() / 60)
    return {
        "code": code,
        "status": "ok",
        "bars": len(rows),
        "last_time": bar_time.isoformat(),
        "age_minutes": round(age_minutes, 1),
        "stale": age_minutes > 15,
        "last_close": last["close"],
        "change_15m": change(15),
        "change_30m": change(30),
        "change_60m": change(60),
        "sma20": sma20,
        "sma60": sma60,
        "atr14": atr14,
        "high20": high20,
        "low20": low20,
        "range_position": range_position,
        "volume_ratio": volume_ratio,
        "breakout": breakout,
        "regime": regime,
    }
