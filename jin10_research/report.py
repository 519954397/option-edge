from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable

from .analysis import analyze_klines, score_events
from .strategy import build_strategy_card, future_important_calendar


def _fmt_quote(quote: Dict[str, Any]) -> str:
    if not quote:
        return "暂无数据"
    return "{name} {close}（{change}%），高 {high} / 低 {low}，时间 {time}".format(
        name=quote.get("name", quote.get("code", "-")),
        close=quote.get("close", "-"),
        change=quote.get("ups_percent", "-"),
        high=quote.get("high", "-"),
        low=quote.get("low", "-"),
        time=quote.get("time", "-"),
    )


def _event_lines(items: Iterable[Dict[str, Any]]) -> str:
    rows = []
    for item in items:
        content = item.get("title") or item.get("content") or item.get("introduction") or ""
        content = " ".join(content.split())
        if len(content) > 240:
            content = content[:237] + "..."
        source = item.get("url")
        label = "[原文]({})".format(source) if source else ""
        rows.append("- `{}` {} {}".format(item.get("time", "-"), content, label).rstrip())
    return "\n".join(rows) if rows else "- 本时间窗口没有检索到直接相关消息。"


def render_stage1_report(bundle: Dict[str, Any]) -> str:
    return """# 原油{hours}小时研究报告

生成时间：`{generated_at}`  
研究窗口：`{cutoff}` 至 `{generated_at}`

## 行情快照

- WTI：{wti}
- 布伦特：{brent}

## 重要快讯

{events}

## 深度资讯

{news}

## 数据说明

- 快讯总数：{flash_count}
- 原油相关快讯：{oil_count}
- 资讯文章：{news_count}
- MCP 响应优先使用 `result.structuredContent`
- 本报告仅用于研究，不构成投资建议
""".format(
        hours=bundle["hours"],
        generated_at=bundle["generated_at"],
        cutoff=bundle["cutoff"],
        wti=_fmt_quote(bundle["quotes"].get("USOIL", {})),
        brent=_fmt_quote(bundle["quotes"].get("UKOIL", {})),
        events=_event_lines(bundle["oil_flashes"]),
        news=_event_lines(bundle["news"]),
        flash_count=len(bundle["flashes"]),
        oil_count=len(bundle["oil_flashes"]),
        news_count=len(bundle["news"]),
    )


def _percent(value: Any) -> str:
    try:
        return "{:+.3f}%".format(float(value))
    except (TypeError, ValueError):
        return "-"


def _kline_line(item: Dict[str, Any]) -> str:
    if item.get("status") != "ok":
        return "数据不足（{}根）".format(item.get("bars", 0))
    return (
        "状态 `{regime}`；15/30/60分钟 {c15} / {c30} / {c60}；"
        "MA20 {ma20:.3f}，MA60 {ma60:.3f}；ATR14 {atr:.3f}；"
        "20分钟区间 {low:.3f}—{high:.3f}；突破 `{breakout}`；"
        "数据年龄 {age:.1f} 分钟{stale}"
    ).format(
        regime=item["regime"],
        c15=_percent(item.get("change_15m")),
        c30=_percent(item.get("change_30m")),
        c60=_percent(item.get("change_60m")),
        ma20=item["sma20"],
        ma60=item["sma60"],
        atr=item["atr14"],
        low=item["low20"],
        high=item["high20"],
        breakout=item["breakout"],
        age=item["age_minutes"],
        stale="（已过期）" if item["stale"] else "",
    )


def _scored_event_lines(events: Iterable[Dict[str, Any]]) -> str:
    rows = []
    direction_label = {"bullish": "偏多", "bearish": "偏空", "neutral": "中性"}
    for item in list(events)[:12]:
        raw = item["raw"]
        content = raw.get("title") or raw.get("content") or ""
        content = " ".join(content.split())
        if len(content) > 180:
            content = content[:177] + "..."
        link = "[原文]({})".format(item["url"]) if item.get("url") else ""
        rows.append(
            "- **{level}级 / {score}分 / {direction}** `{time}` {content} {link}".format(
                level=item["level"],
                score=item["score"],
                direction=direction_label[item["direction"]],
                time=item["time"],
                content=content,
                link=link,
            ).rstrip()
        )
    return "\n".join(rows) if rows else "- 没有相关事件。"


def render_research_report(bundle: Dict[str, Any]) -> str:
    generated_at = datetime.fromisoformat(bundle["generated_at"])
    events = score_events(bundle["oil_flashes"], generated_at)
    analyses = {
        code: analyze_klines(code, rows, generated_at)
        for code, rows in bundle.get("klines", {}).items()
    }
    strategy = build_strategy_card(
        events, analyses, bundle.get("calendar", []), generated_at=generated_at
    )
    calendar_rows = future_important_calendar(bundle.get("calendar", []), generated_at)
    calendar_text = "\n".join(
        "- `{}` {}（{}星，前值 {} / 预期 {} / 公布 {}）".format(
            item.get("pub_time", "-"),
            item.get("title", "-"),
            item.get("star", "-"),
            item.get("previous", "-"),
            item.get("consensus", "-"),
            item.get("actual", "-"),
        )
        for item in calendar_rows[:12]
    ) or "- 本周没有返回三星及以上日历事件。"

    return """# 原油{hours}小时研究与策略报告

生成时间：`{generated_at}`  
研究窗口：`{cutoff}` 至 `{generated_at}`

## 行情快照

- WTI：{wti}
- 布伦特：{brent}

## K线结构

- WTI：{wti_kline}
- 布伦特：{brent_kline}

## 事件评分

{events}

## 条件式策略卡

- 策略类型：`{strategy_type}`
- 市场倾向：`{bias}`
- 置信度：`{confidence}`
- 形成原因：{reason}
- 触发条件：{trigger}
- 失效条件：{invalidation}
- 高影响事件：{high_event_count} 条
- 高重要性日历事件：{calendar_count} 条

风险约束：

{risk_notes}

## 重要财经日历

{calendar}

## 深度资讯

{news}

## 数据质量

- 快讯总数：{flash_count}
- 原油相关快讯：{oil_count}
- K线分析使用分钟数据；过期数据自动阻止可执行策略
- MCP 响应优先使用 `result.structuredContent`
- 本报告仅用于研究和模拟验证，不构成投资建议
""".format(
        hours=bundle["hours"],
        generated_at=bundle["generated_at"],
        cutoff=bundle["cutoff"],
        wti=_fmt_quote(bundle["quotes"].get("USOIL", {})),
        brent=_fmt_quote(bundle["quotes"].get("UKOIL", {})),
        wti_kline=_kline_line(analyses.get("USOIL", {})),
        brent_kline=_kline_line(analyses.get("UKOIL", {})),
        events=_scored_event_lines(events),
        strategy_type=strategy["type"],
        bias=strategy["bias"],
        confidence=strategy["confidence"],
        reason=strategy["reason"],
        trigger=strategy["trigger"],
        invalidation=strategy["invalidation"],
        high_event_count=strategy["high_event_count"],
        calendar_count=strategy["important_calendar_count"],
        risk_notes="\n".join("- " + note for note in strategy["risk_notes"]),
        calendar=calendar_text,
        news=_event_lines(bundle["news"]),
        flash_count=len(bundle["flashes"]),
        oil_count=len(bundle["oil_flashes"]),
    )


def write_report(content: str, report_dir: Path, output: str = "") -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    if output:
        path = Path(output)
        if not path.is_absolute():
            path = report_dir / path
    else:
        stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
        path = report_dir / "oil-research-{}.md".format(stamp)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path.resolve()
