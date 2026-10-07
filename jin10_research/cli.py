from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional

from .ai import build_ai_brief
from .collector import collect_research_bundle
from .config import load_settings
from .mcp import Jin10MCPClient
from .report import render_research_report, write_report
from .storage import ResearchStore
from .alerts import append_alert, evaluate_alert
from .backtest import run_trend_backtest, validation_gate
from .webhook import serve_webhook
from .option_dashboard import serve_option_radar
from .option_radar import AKShareOptionRadar


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Jin10 scheduled research pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)

    report = subparsers.add_parser("report", help="Generate an oil research report")
    report.add_argument("--hours", type=int, default=12)
    report.add_argument("--output", default="")
    report.add_argument("--print", action="store_true", dest="print_report")
    report.add_argument("--no-store", action="store_true")

    doctor = subparsers.add_parser("doctor", help="Verify MCP initialization and capabilities")
    doctor.add_argument("--json", action="store_true")
    stats = subparsers.add_parser("stats", help="Show stored research and strategy statistics")
    stats.add_argument("--json", action="store_true")
    backtest = subparsers.add_parser("backtest", help="Backtest the stored trend strategy")
    backtest.add_argument("--code", default="USOIL")
    backtest.add_argument("--json", action="store_true")
    monitor = subparsers.add_parser("monitor", help="Evaluate conditional research alerts")
    monitor.add_argument("--hours", type=int, default=4)
    monitor.add_argument("--allow-unvalidated", action="store_true")
    monitor.add_argument("--json", action="store_true")

    ai_brief = subparsers.add_parser(
        "ai-brief", help="Build a Jin10 + AI risk brief for oil, gold, or silver"
    )
    ai_brief.add_argument("--asset", required=True, help="oil/gold/silver or a symbol")
    ai_brief.add_argument("--hours", type=int, default=12)
    ai_brief.add_argument("--no-ai", action="store_true")
    ai_brief.add_argument("--json", action="store_true")

    webhook = subparsers.add_parser(
        "webhook", help="Receive TradingView signals and analyze them asynchronously"
    )
    webhook.add_argument("--host", default="127.0.0.1")
    webhook.add_argument("--port", type=int, default=8787)
    webhook.add_argument("--no-ai", action="store_true")

    option_radar = subparsers.add_parser(
        "option-radar",
        help="Scan and rank live oil, gold, and silver option chains",
    )
    option_radar.add_argument("--host", default="127.0.0.1")
    option_radar.add_argument("--port", type=int, default=8790)
    option_radar.add_argument("--refresh", type=float, default=5.0)
    option_radar.add_argument("--risk-free-rate", type=float, default=0.015)
    option_radar.add_argument("--once", action="store_true")
    option_radar.add_argument("--json", action="store_true")
    return parser


def main(argv: Optional[list] = None) -> None:
    args = build_parser().parse_args(argv)
    needs_mcp = args.command in ("doctor", "report", "monitor", "ai-brief", "webhook")
    settings = load_settings(require_token=needs_mcp)
    client = (
        Jin10MCPClient(settings.server_url, settings.bearer_token, settings.protocol_version)
        if needs_mcp
        else None
    )
    if args.command == "stats":
        with ResearchStore(settings.data_dir / "research.sqlite3") as store:
            result = store.statistics()
        print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else result)
        return
    if args.command == "backtest":
        with ResearchStore(settings.data_dir / "research.sqlite3") as store:
            result = run_trend_backtest(store.kline_history(args.code))
            result["gate"] = validation_gate(store.statistics(), result)
        print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else result)
        return
    if args.command == "monitor":
        bundle = collect_research_bundle(client, args.hours)
        with ResearchStore(settings.data_dir / "research.sqlite3") as store:
            run_id = store.record_run(bundle, Path(""))
            alert = evaluate_alert(
                bundle, store, allow_unvalidated=args.allow_unvalidated
            )
        alert["run_id"] = run_id
        if alert["emitted"]:
            append_alert(settings.data_dir / "alerts.jsonl", alert)
        print(json.dumps(alert, ensure_ascii=False, indent=2) if args.json else alert)
        return
    if args.command == "ai-brief":
        if args.hours <= 0 or args.hours > 168:
            raise SystemExit("--hours must be between 1 and 168")
        result = build_ai_brief(
            client, args.asset, hours=args.hours, use_ai=not args.no_ai
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.command == "webhook":
        if args.port <= 0 or args.port > 65535:
            raise SystemExit("--port must be between 1 and 65535")
        serve_webhook(
            settings, host=args.host, port=args.port, use_ai=not args.no_ai
        )
        return
    if args.command == "option-radar":
        if args.port <= 0 or args.port > 65535:
            raise SystemExit("--port must be between 1 and 65535")
        if args.refresh < 2 or args.refresh > 300:
            raise SystemExit("--refresh must be between 2 and 300 seconds")
        if args.risk_free_rate < -0.05 or args.risk_free_rate > 0.25:
            raise SystemExit("--risk-free-rate must be between -0.05 and 0.25")
        if args.once:
            snapshot = AKShareOptionRadar(
                risk_free_rate=args.risk_free_rate
            ).snapshot()
            if args.json:
                print(json.dumps(snapshot, ensure_ascii=False, indent=2))
            else:
                print("{} eligible options from {} fetched quotes".format(
                    snapshot["counts"]["eligible"], snapshot["counts"]["fetched"]
                ))
                for row in snapshot["options"][:20]:
                    print(
                        "{instrument:16s} {recommendation:11s} buyer={score_buyer:5.1f} "
                        "signals={signal_count}/6 bid={bid} ask={ask} iv={iv_pct} delta={delta}".format(
                            **row
                        )
                    )
            return
        serve_option_radar(
            host=args.host,
            port=args.port,
            refresh_seconds=args.refresh,
            risk_free_rate=args.risk_free_rate,
        )
        return
    if args.command == "doctor":
        tools = client.list_tools().get("tools", [])
        resources = client.list_resources().get("resources", [])
        result = {
            "protocol_version": client.protocol_version,
            "server_info": client.server_info,
            "tools": [tool.get("name") for tool in tools],
            "resources": [resource.get("uri") for resource in resources],
        }
        print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else result)
        return

    if args.hours <= 0 or args.hours > 168:
        raise SystemExit("--hours must be between 1 and 168")
    bundle = collect_research_bundle(client, args.hours)
    content = render_research_report(bundle)
    path = write_report(content, settings.report_dir, args.output)
    run_id = None
    if not args.no_store:
        with ResearchStore(settings.data_dir / "research.sqlite3") as store:
            run_id = store.record_run(bundle, path)
    if args.print_report:
        print(content)
    print("Report written to {}".format(path))
    if run_id is not None:
        print("Research run stored as #{}".format(run_id))
