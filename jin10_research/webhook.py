from __future__ import annotations

import json
import queue
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from .ai import build_ai_brief
from .assets import resolve_asset
from .config import Settings
from .mcp import Jin10MCPClient


MAX_BODY_BYTES = 64 * 1024


def append_jsonl(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


class SignalProcessor:
    def __init__(self, settings: Settings, *, use_ai: bool = True):
        self.settings = settings
        self.use_ai = use_ai
        self.jobs: "queue.Queue[Optional[Tuple[str, Dict[str, Any]]]]" = queue.Queue()
        self.latest: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name="jin10-ai-worker", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def submit(self, signal: Dict[str, Any]) -> str:
        job_id = uuid.uuid4().hex
        received = {
            "job_id": job_id,
            "received_at": datetime.now(timezone.utc).astimezone().isoformat(),
            "signal": signal,
        }
        append_jsonl(self.settings.data_dir / "tradingview_signals.jsonl", received)
        self.jobs.put((job_id, signal))
        return job_id

    def stop(self) -> None:
        self.jobs.put(None)

    def get_latest(self, asset: str = "") -> Optional[Dict[str, Any]]:
        with self._lock:
            if asset:
                return self.latest.get(asset)
            if not self.latest:
                return None
            return max(self.latest.values(), key=lambda row: row.get("completed_at", ""))

    def _run(self) -> None:
        client = Jin10MCPClient(
            self.settings.server_url,
            self.settings.bearer_token,
            self.settings.protocol_version,
        )
        while True:
            job = self.jobs.get()
            if job is None:
                return
            job_id, signal = job
            try:
                profile = resolve_asset(str(signal.get("asset") or signal.get("symbol") or ""))
                result = build_ai_brief(
                    client,
                    profile.key,
                    hours=12,
                    signal=signal,
                    use_ai=self.use_ai,
                )
                record = {
                    "job_id": job_id,
                    "completed_at": datetime.now(timezone.utc).astimezone().isoformat(),
                    "status": "ok",
                    **result,
                }
            except Exception as exc:  # Worker errors must not terminate the webhook server.
                profile = None
                record = {
                    "job_id": job_id,
                    "completed_at": datetime.now(timezone.utc).astimezone().isoformat(),
                    "status": "error",
                    "error": str(exc),
                    "signal": signal,
                }
            append_jsonl(self.settings.data_dir / "ai_decisions.jsonl", record)
            key = profile.key if profile else str(signal.get("asset") or "unknown")
            with self._lock:
                self.latest[key] = record


def make_handler(processor: SignalProcessor):
    class Handler(BaseHTTPRequestHandler):
        server_version = "Jin10TradingView/0.2"

        def _json(self, status: int, payload: Dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/health":
                self._json(200, {"status": "ok", "queue_size": processor.jobs.qsize()})
                return
            if parsed.path == "/latest":
                asset = (parse_qs(parsed.query).get("asset") or [""])[0]
                try:
                    key = resolve_asset(asset).key if asset else ""
                except ValueError as exc:
                    self._json(400, {"error": str(exc)})
                    return
                latest = processor.get_latest(key)
                self._json(200 if latest else 404, latest or {"error": "no decision yet"})
                return
            self._json(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            if urlparse(self.path).path != "/webhook/tradingview":
                self._json(404, {"error": "not found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._json(400, {"error": "invalid Content-Length"})
                return
            if length <= 0 or length > MAX_BODY_BYTES:
                self._json(413, {"error": "body must be between 1 and {} bytes".format(MAX_BODY_BYTES)})
                return
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("JSON body must be an object")
                resolve_asset(str(payload.get("asset") or payload.get("symbol") or ""))
                if payload.get("side") not in ("long", "short", "exit"):
                    raise ValueError("side must be long, short, or exit")
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                self._json(400, {"error": str(exc)})
                return
            job_id = processor.submit(payload)
            # Return immediately: TradingView cancels webhook requests that take too long.
            self._json(202, {"status": "accepted", "job_id": job_id})

        def log_message(self, fmt: str, *args: Any) -> None:
            print("{} - {}".format(self.address_string(), fmt % args))

    return Handler


def serve_webhook(
    settings: Settings,
    *,
    host: str = "127.0.0.1",
    port: int = 8787,
    use_ai: bool = True,
) -> None:
    processor = SignalProcessor(settings, use_ai=use_ai)
    processor.start()
    server = ThreadingHTTPServer((host, port), make_handler(processor))
    print("TradingView webhook listening on http://{}:{}".format(host, port))
    print("POST /webhook/tradingview, GET /health, GET /latest?asset=gold")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        processor.stop()
        server.server_close()
