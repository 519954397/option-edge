from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .mcp import Jin10MCPClient, MCPError


OIL_TERMS = (
    "原油",
    "石油",
    "油价",
    "WTI",
    "布伦特",
    "欧佩克",
    "OPEC",
    "霍尔木兹",
    "油轮",
    "炼油",
    "汽油",
    "柴油",
    "燃料油",
    "油田",
    "输油",
    "油气",
)


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def is_oil_related(item: Dict[str, Any]) -> bool:
    text = "{}\n{}".format(item.get("title", ""), item.get("content", ""))
    lowered = text.lower()
    return any(term.lower() in lowered for term in OIL_TERMS)


def collect_window(
    client: Jin10MCPClient,
    tool_name: str,
    cutoff: datetime,
    *,
    max_pages: int = 100,
) -> List[Dict[str, Any]]:
    cursor: Optional[str] = None
    collected: List[Dict[str, Any]] = []
    seen = set()

    for _ in range(max_pages):
        arguments = {"cursor": cursor} if cursor else {}
        payload = client.call_tool(tool_name, arguments)
        data = payload.get("data")
        if not isinstance(data, dict):
            raise MCPError("{} returned invalid data".format(tool_name))
        items = data.get("items") or []
        reached_cutoff = False
        for item in items:
            if not isinstance(item, dict) or not item.get("time"):
                continue
            published = parse_time(item["time"])
            if published < cutoff:
                reached_cutoff = True
                continue
            identity = item.get("url") or item.get("id") or (
                item.get("time"), item.get("title"), item.get("content")
            )
            if identity not in seen:
                seen.add(identity)
                collected.append(item)
        if reached_cutoff or not data.get("has_more"):
            break
        next_cursor = data.get("next_cursor")
        if not next_cursor:
            raise MCPError("{} has_more=true without data.next_cursor".format(tool_name))
        cursor = str(next_cursor)

    collected.sort(key=lambda item: item.get("time", ""), reverse=True)
    return collected


def collect_research_bundle(
    client: Jin10MCPClient,
    hours: int,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc).astimezone()
    cutoff = now - timedelta(hours=hours)
    flashes = collect_window(client, "list_flash", cutoff)
    try:
        news = collect_window(client, "list_news", cutoff)
    except MCPError:
        news = []
    quotes = {}
    klines = {}
    for code in ("USOIL", "UKOIL"):
        payload = client.call_tool("get_quote", {"code": code})
        quotes[code] = payload.get("data") or {}
        kline_payload = client.call_tool("get_kline", {"code": code, "count": 100})
        klines[code] = (kline_payload.get("data") or {}).get("klines") or []
    calendar_payload = client.call_tool("list_calendar", {})
    return {
        "generated_at": now.isoformat(),
        "cutoff": cutoff.isoformat(),
        "hours": hours,
        "flashes": flashes,
        "oil_flashes": [item for item in flashes if is_oil_related(item)],
        "news": news,
        "quotes": quotes,
        "klines": klines,
        "calendar": calendar_payload.get("data") or [],
    }
