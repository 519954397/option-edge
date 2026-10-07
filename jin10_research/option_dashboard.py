from __future__ import annotations

import base64
import hmac
import json
import os
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from .option_radar import AKShareOptionRadar


class SnapshotCache:
    def __init__(self, radar: AKShareOptionRadar, refresh_seconds: float):
        self.radar = radar
        self.refresh_seconds = refresh_seconds
        self._lock = threading.RLock()
        self._snapshot: Dict[str, Any] = {
            "status": "starting",
            "generated_at": None,
            "options": [],
            "summaries": [],
            "counts": {
                "fetched": 0,
                "eligible": 0,
                "buyer_eligible": 0,
                "recommended": 0,
                "watch": 0,
                "contracts": 0,
                "errors": 0,
            },
        }
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def refresh(self) -> None:
        try:
            snapshot = self.radar.snapshot()
            snapshot["refresh_seconds"] = self.refresh_seconds
            with self._lock:
                self._snapshot = snapshot
        except Exception as exc:
            with self._lock:
                previous = dict(self._snapshot)
                previous["status"] = "error" if not previous.get("options") else "stale"
                previous["last_error"] = str(exc)
                previous["refresh_seconds"] = self.refresh_seconds
                self._snapshot = previous

    def get(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._snapshot)

    def start(self) -> None:
        self.refresh()
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="option-radar-refresh", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(2.0, self.refresh_seconds + 1.0))

    def _run(self) -> None:
        while not self._stop.wait(self.refresh_seconds):
            self.refresh()


def _dashboard_html() -> bytes:
    return resources.files("jin10_research").joinpath("option_dashboard.html").read_bytes()


def _basic_auth_matches(
    authorization: Optional[str], username: str, password: str
) -> bool:
    """Validate an HTTP Basic credential without leaking timing information."""
    if not username and not password:
        return True
    if not username or not password or not authorization:
        return False
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.lower() != "basic" or not token:
        return False
    try:
        supplied = base64.b64decode(token, validate=True)
    except (ValueError, TypeError):
        return False
    expected = "{}:{}".format(username, password).encode("utf-8")
    return hmac.compare_digest(supplied, expected)


def _handler(cache: SnapshotCache, username: str = "", password: str = ""):
    class Handler(BaseHTTPRequestHandler):
        server_version = "OptionRadar/0.1"

        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def _send(self, content: bytes, content_type: str, status: HTTPStatus = HTTPStatus.OK) -> None:
            self.send_response(status.value)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(content)

        def _require_authorization(self) -> bool:
            if _basic_auth_matches(
                self.headers.get("Authorization"), username, password
            ):
                return True
            content = "需要登录后访问".encode("utf-8")
            self.send_response(HTTPStatus.UNAUTHORIZED.value)
            self.send_header("WWW-Authenticate", 'Basic realm="Option Edge", charset="UTF-8"')
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(content)
            return False

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            # Keep liveness/readiness checks public so platforms can monitor the service.
            if path not in ("/live", "/health") and not self._require_authorization():
                return
            if path == "/live":
                self._send(b'{"status":"alive"}', "application/json; charset=utf-8")
                return
            if path == "/":
                self._send(_dashboard_html(), "text/html; charset=utf-8")
                return
            if path == "/api/snapshot":
                payload = json.dumps(cache.get(), ensure_ascii=False, allow_nan=False).encode("utf-8")
                self._send(payload, "application/json; charset=utf-8")
                return
            if path == "/health":
                snapshot = cache.get()
                payload = json.dumps(
                    {
                        "status": snapshot.get("status"),
                        "generated_at": snapshot.get("generated_at"),
                        "counts": snapshot.get("counts"),
                    },
                    ensure_ascii=False,
                ).encode("utf-8")
                status = (
                    HTTPStatus.OK
                    if snapshot.get("status") in ("ok", "partial", "stale")
                    else HTTPStatus.SERVICE_UNAVAILABLE
                )
                self._send(payload, "application/json; charset=utf-8", status)
                return
            self._send(b"not found", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)

    return Handler


def serve_option_radar(
    host: str = "127.0.0.1",
    port: int = 8790,
    refresh_seconds: float = 5.0,
    risk_free_rate: float = 0.015,
) -> None:
    username = os.environ.get("OPTION_RADAR_USERNAME", "").strip()
    password = os.environ.get("OPTION_RADAR_PASSWORD", "")
    if bool(username) != bool(password):
        raise RuntimeError(
            "OPTION_RADAR_USERNAME and OPTION_RADAR_PASSWORD must be set together"
        )
    radar = AKShareOptionRadar(risk_free_rate=risk_free_rate)
    cache = SnapshotCache(radar, refresh_seconds)
    cache.start()
    server = ThreadingHTTPServer((host, port), _handler(cache, username, password))
    print("Option radar listening on http://{}:{}".format(host, port))
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
        cache.stop()
