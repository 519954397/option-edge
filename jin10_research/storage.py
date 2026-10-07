from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from .analysis import analyze_klines, score_events
from .strategy import build_strategy_card


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    generated_at TEXT NOT NULL,
    cutoff TEXT NOT NULL,
    hours INTEGER NOT NULL,
    report_path TEXT NOT NULL,
    flash_count INTEGER NOT NULL,
    oil_event_count INTEGER NOT NULL,
    data_fresh INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    identity TEXT NOT NULL,
    published_at TEXT,
    title TEXT,
    url TEXT,
    score INTEGER NOT NULL,
    level TEXT NOT NULL,
    direction TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    UNIQUE(run_id, identity)
);

CREATE TABLE IF NOT EXISTS quotes (
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    code TEXT NOT NULL,
    quote_time TEXT,
    open REAL,
    close REAL,
    high REAL,
    low REAL,
    ups_percent REAL,
    volume REAL,
    raw_json TEXT NOT NULL,
    PRIMARY KEY(run_id, code)
);

CREATE TABLE IF NOT EXISTS klines (
    code TEXT NOT NULL,
    bar_time INTEGER NOT NULL,
    open REAL,
    close REAL,
    high REAL,
    low REAL,
    volume REAL,
    collected_at TEXT NOT NULL,
    PRIMARY KEY(code, bar_time)
);

CREATE TABLE IF NOT EXISTS strategies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    strategy_type TEXT NOT NULL,
    bias TEXT NOT NULL,
    confidence TEXT NOT NULL,
    reason TEXT NOT NULL,
    trigger_text TEXT NOT NULL,
    invalidation_text TEXT NOT NULL,
    entry_price REAL,
    status TEXT NOT NULL,
    outcome_at TEXT,
    outcome_return_pct REAL,
    outcome_label TEXT,
    raw_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_published ON events(published_at);
CREATE INDEX IF NOT EXISTS idx_strategies_status ON strategies(status, created_at);
"""


def _number(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class ResearchStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(self.path))
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)

    def close(self) -> None:
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()

    def record_run(self, bundle: Dict[str, Any], report_path: Path) -> int:
        generated_at = datetime.fromisoformat(bundle["generated_at"])
        events = score_events(bundle.get("oil_flashes", []), generated_at)
        analyses = {
            code: analyze_klines(code, rows, generated_at)
            for code, rows in bundle.get("klines", {}).items()
        }
        strategy = build_strategy_card(
            events, analyses, bundle.get("calendar", []), generated_at=generated_at
        )
        data_fresh = bool(analyses) and all(
            item.get("status") == "ok" and not item.get("stale")
            for item in analyses.values()
        )

        cursor = self.connection.execute(
            """
            INSERT INTO runs(
                generated_at, cutoff, hours, report_path, flash_count,
                oil_event_count, data_fresh
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                bundle["generated_at"],
                bundle["cutoff"],
                bundle["hours"],
                str(report_path),
                len(bundle.get("flashes", [])),
                len(bundle.get("oil_flashes", [])),
                int(data_fresh),
            ),
        )
        run_id = int(cursor.lastrowid)

        for event in events:
            raw = event["raw"]
            identity = str(raw.get("url") or raw.get("id") or event["title"])
            self.connection.execute(
                """
                INSERT OR IGNORE INTO events(
                    run_id, identity, published_at, title, url, score,
                    level, direction, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    identity,
                    event.get("time"),
                    event.get("title"),
                    event.get("url"),
                    event["score"],
                    event["level"],
                    event["direction"],
                    json.dumps(raw, ensure_ascii=False),
                ),
            )

        for code, quote in bundle.get("quotes", {}).items():
            self.connection.execute(
                """
                INSERT INTO quotes(
                    run_id, code, quote_time, open, close, high, low,
                    ups_percent, volume, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    code,
                    quote.get("time"),
                    _number(quote.get("open")),
                    _number(quote.get("close")),
                    _number(quote.get("high")),
                    _number(quote.get("low")),
                    _number(quote.get("ups_percent")),
                    _number(quote.get("volume")),
                    json.dumps(quote, ensure_ascii=False),
                ),
            )

        for code, bars in bundle.get("klines", {}).items():
            for bar in bars:
                self.connection.execute(
                    """
                    INSERT INTO klines(
                        code, bar_time, open, close, high, low, volume, collected_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(code, bar_time) DO UPDATE SET
                        open=excluded.open, close=excluded.close, high=excluded.high,
                        low=excluded.low, volume=excluded.volume,
                        collected_at=excluded.collected_at
                    """,
                    (
                        code,
                        int(bar.get("time") or 0),
                        _number(bar.get("open")),
                        _number(bar.get("close")),
                        _number(bar.get("high")),
                        _number(bar.get("low")),
                        _number(bar.get("volume")),
                        bundle["generated_at"],
                    ),
                )

        entry_price = _number((bundle.get("quotes", {}).get("USOIL") or {}).get("close"))
        status = "pending" if strategy["type"] != "observe" and data_fresh and entry_price else "skipped"
        self.connection.execute(
            """
            INSERT INTO strategies(
                run_id, created_at, strategy_type, bias, confidence, reason,
                trigger_text, invalidation_text, entry_price, status, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                bundle["generated_at"],
                strategy["type"],
                strategy["bias"],
                strategy["confidence"],
                strategy["reason"],
                strategy["trigger"],
                strategy["invalidation"],
                entry_price,
                status,
                json.dumps(strategy, ensure_ascii=False),
            ),
        )
        self.evaluate_pending(bundle)
        self.connection.commit()
        return run_id

    def evaluate_pending(self, bundle: Dict[str, Any], horizon_hours: int = 24) -> int:
        current_quote = bundle.get("quotes", {}).get("USOIL") or {}
        current_price = _number(current_quote.get("close"))
        quote_time = current_quote.get("time")
        if current_price is None or not quote_time:
            return 0
        current_time = datetime.fromisoformat(str(quote_time).replace("Z", "+00:00"))
        updated = 0
        pending = self.connection.execute(
            "SELECT * FROM strategies WHERE status = 'pending'"
        ).fetchall()
        for row in pending:
            created = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
            if current_time < created + timedelta(hours=horizon_hours):
                continue
            entry = row["entry_price"]
            if not entry:
                continue
            raw_return = (current_price / entry - 1) * 100
            signed_return = -raw_return if row["bias"] == "bearish" else raw_return
            label = "win" if signed_return > 0 else "loss" if signed_return < 0 else "flat"
            self.connection.execute(
                """
                UPDATE strategies
                SET status='completed', outcome_at=?, outcome_return_pct=?, outcome_label=?
                WHERE id=?
                """,
                (current_time.isoformat(), signed_return, label, row["id"]),
            )
            updated += 1
        return updated

    def statistics(self) -> Dict[str, Any]:
        statuses = {
            row["status"]: row["count"]
            for row in self.connection.execute(
                "SELECT status, COUNT(*) AS count FROM strategies GROUP BY status"
            )
        }
        completed = self.connection.execute(
            """
            SELECT COUNT(*) AS count,
                   SUM(CASE WHEN outcome_label='win' THEN 1 ELSE 0 END) AS wins,
                   AVG(outcome_return_pct) AS avg_return
            FROM strategies WHERE status='completed'
            """
        ).fetchone()
        count = int(completed["count"] or 0)
        wins = int(completed["wins"] or 0)
        return {
            "database": str(self.path.resolve()),
            "runs": self.connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0],
            "events": self.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0],
            "kline_bars": self.connection.execute("SELECT COUNT(*) FROM klines").fetchone()[0],
            "strategies": statuses,
            "completed": count,
            "wins": wins,
            "win_rate": wins / count if count else None,
            "average_signed_return_pct": completed["avg_return"],
        }

    def kline_history(self, code: str) -> list:
        return [
            dict(row)
            for row in self.connection.execute(
                """
                SELECT code, bar_time, open, close, high, low, volume
                FROM klines WHERE code=? ORDER BY bar_time
                """,
                (code,),
            )
        ]
