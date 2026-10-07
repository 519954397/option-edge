from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from .analysis import analyze_klines
from .assets import AssetProfile, contains_any, resolve_asset
from .collector import collect_window
from .mcp import Jin10MCPClient


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"


DECISION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "allowed_direction": {
            "type": "string",
            "enum": ["both", "long_only", "short_only", "none"],
        },
        "risk_multiplier": {"type": "number", "minimum": 0, "maximum": 1},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "market_regime": {
            "type": "string",
            "enum": ["trend", "range", "event_shock", "uncertain"],
        },
        "summary": {"type": "string"},
        "reasons": {"type": "array", "items": {"type": "string"}},
        "blocking_event": {"type": ["string", "null"]},
        "valid_minutes": {"type": "integer", "minimum": 5, "maximum": 240},
        "data_quality": {"type": "string", "enum": ["good", "limited", "stale"]},
    },
    "required": [
        "allowed_direction", "risk_multiplier", "confidence", "market_regime",
        "summary", "reasons", "blocking_event", "valid_minutes", "data_quality",
    ],
    "additionalProperties": False,
}


def _event_text(item: Dict[str, Any]) -> str:
    return "{}\n{}\n{}".format(
        item.get("title", ""), item.get("content", ""), item.get("introduction", "")
    )


def _parse_calendar_time(value: str, tzinfo: Any) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=tzinfo) if parsed.tzinfo is None else parsed


def _relevant_calendar(
    rows: Iterable[Dict[str, Any]], profile: AssetProfile, now: datetime
) -> List[Dict[str, Any]]:
    output = []
    for row in rows:
        title = str(row.get("title") or "")
        if int(row.get("star") or 0) < 3 or not contains_any(title, profile.calendar_terms):
            continue
        published = _parse_calendar_time(str(row.get("pub_time") or ""), now.tzinfo)
        if published and now - timedelta(hours=6) <= published <= now + timedelta(hours=24):
            copied = dict(row)
            copied["parsed_time"] = published.isoformat()
            output.append(copied)
    output.sort(key=lambda row: row.get("parsed_time", ""))
    return output


def collect_ai_context(
    client: Jin10MCPClient,
    asset: str,
    *,
    hours: int = 12,
    now: Optional[datetime] = None,
    max_events: int = 20,
) -> Dict[str, Any]:
    profile = resolve_asset(asset)
    now = now or datetime.now(timezone.utc).astimezone()
    cutoff = now - timedelta(hours=hours)
    flashes = collect_window(client, "list_flash", cutoff, max_pages=30)
    relevant = [item for item in flashes if contains_any(_event_text(item), profile.terms)]
    quote = client.call_tool("get_quote", {"code": profile.jin10_code}).get("data") or {}
    kline_payload = client.call_tool(
        "get_kline", {"code": profile.jin10_code, "count": 100}
    )
    klines = (kline_payload.get("data") or {}).get("klines") or []
    calendar = client.call_tool("list_calendar", {}).get("data") or []
    return {
        "generated_at": now.isoformat(),
        "asset": profile.key,
        "asset_name": profile.name,
        "jin10_code": profile.jin10_code,
        "quote": quote,
        "technical": analyze_klines(profile.jin10_code, klines, now),
        "events": relevant[:max_events],
        "calendar": _relevant_calendar(calendar, profile, now),
    }


def deterministic_gate(
    context: Dict[str, Any], signal: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    now = datetime.fromisoformat(context["generated_at"])
    technical = context.get("technical") or {}
    calendar = context.get("calendar") or []
    imminent = []
    for row in calendar:
        published = _parse_calendar_time(str(row.get("parsed_time") or ""), now.tzinfo)
        if published and -timedelta(minutes=15) <= published - now <= timedelta(minutes=45):
            imminent.append(row)

    stale = technical.get("status") != "ok" or bool(technical.get("stale"))
    if stale:
        direction, risk, quality = "none", 0.0, "stale"
        summary = "行情数据过期或不足，暂停新交易。"
    elif imminent:
        direction, risk, quality = "none", 0.0, "good"
        summary = "高影响数据临近公布，暂停新交易，等待波动稳定。"
    else:
        regime = technical.get("regime")
        direction, risk, quality = "both", 0.5, "good"
        summary = "未发现临近的高影响日历事件；以半风险等待技术信号确认。"
        if regime == "uptrend":
            direction = "long_only"
        elif regime == "downtrend":
            direction = "short_only"
        elif regime in ("event_shock", "mixed"):
            risk = 0.25

    regime_value = technical.get("regime")
    market_regime = (
        "event_shock" if regime_value == "event_shock"
        else "trend" if regime_value in ("uptrend", "downtrend")
        else "range" if regime_value == "range"
        else "uncertain"
    )
    blocking = imminent[0].get("title") if imminent else None
    result = {
        "allowed_direction": direction,
        "risk_multiplier": risk,
        "confidence": "low",
        "market_regime": market_regime,
        "summary": summary,
        "reasons": [
            "这是规则降级结果，未使用大模型判断。",
            "只依据金十行情新鲜度、分钟结构和高影响日历做风险过滤。",
        ],
        "blocking_event": blocking,
        "valid_minutes": 30,
        "data_quality": quality,
        "ai_used": False,
    }
    if signal:
        result["signal"] = signal
    return result


def _response_text(response: Dict[str, Any]) -> str:
    if isinstance(response.get("output_text"), str):
        return response["output_text"]
    for item in response.get("output") or []:
        if item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                return content["text"]
    raise RuntimeError("OpenAI response did not contain output_text")


def request_ai_decision(
    context: Dict[str, Any],
    signal: Optional[Dict[str, Any]] = None,
    *,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    timeout: int = 60,
) -> Dict[str, Any]:
    api_key = api_key or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Missing OPENAI_API_KEY")
    model = model or os.environ.get("OPENAI_MODEL", "gpt-5.6-terra")
    bounded_context = dict(context)
    # Raw minute bars are intentionally excluded. The model receives calculated
    # features and source-stamped events, not an unbounded market-data dump.
    user_payload = {"context": bounded_context, "tradingview_signal": signal or {}}
    prompt = (
        "你是商品交易的风险过滤器，不是收益预测器。基于给定的金十行情、分钟结构、"
        "财经日历和带时间戳的快讯，判断当前技术信号是否应被允许。"
        "高影响事件前后优先暂停或降风险；数据冲突、陈旧、来源不足时保守处理。"
        "不要虚构新闻、价格或因果关系。reasons 必须引用输入中的具体时间、指标标题"
        "或快讯摘要。输出只描述风险闸门，不给收益承诺。"
    )
    request_body = {
        "model": model,
        "reasoning": {"effort": "low"},
        "input": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "commodity_risk_gate",
                "strict": True,
                "schema": DECISION_SCHEMA,
            }
        },
    }
    request = urllib.request.Request(
        OPENAI_RESPONSES_URL,
        data=json.dumps(request_body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
            "User-Agent": "jin10-research/0.2",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError("OpenAI HTTP {}: {}".format(exc.code, body[:500])) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError("OpenAI network error: {}".format(exc.reason)) from exc
    decision = json.loads(_response_text(payload))
    decision["ai_used"] = True
    decision["model"] = model
    return decision


def build_ai_brief(
    client: Jin10MCPClient,
    asset: str,
    *,
    hours: int = 12,
    signal: Optional[Dict[str, Any]] = None,
    use_ai: bool = True,
) -> Dict[str, Any]:
    context = collect_ai_context(client, asset, hours=hours)
    generated_at = datetime.fromisoformat(context["generated_at"])
    if use_ai and os.environ.get("OPENAI_API_KEY"):
        decision = request_ai_decision(context, signal)
    else:
        decision = deterministic_gate(context, signal)
    valid_minutes = int(decision.get("valid_minutes") or 30)
    decision["valid_until"] = (generated_at + timedelta(minutes=valid_minutes)).isoformat()
    return {"context": context, "decision": decision}
