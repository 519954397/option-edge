from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, Iterable, Optional


class MCPError(RuntimeError):
    pass


class Jin10MCPClient:
    def __init__(self, server_url: str, bearer_token: str, protocol_version: str):
        self.server_url = server_url
        self.bearer_token = bearer_token
        self.protocol_version = protocol_version
        self.session_id: Optional[str] = None
        self._request_id = 0
        self.server_info: Dict[str, Any] = {}
        self.initialized = False

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    @staticmethod
    def _decode_sse(text: str) -> Iterable[Dict[str, Any]]:
        for block in text.replace("\r\n", "\n").split("\n\n"):
            data_lines = [
                line[5:].strip()
                for line in block.splitlines()
                if line.startswith("data:")
            ]
            if not data_lines:
                continue
            data = "\n".join(data_lines)
            if data and data != "[DONE]":
                parsed = json.loads(data)
                if isinstance(parsed, dict):
                    yield parsed

    def _post(
        self,
        payload: Dict[str, Any],
        *,
        include_protocol: bool = True,
    ) -> Optional[Dict[str, Any]]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": "Bearer " + self.bearer_token,
            "User-Agent": "jin10-research/0.1",
        }
        if include_protocol:
            headers["MCP-Protocol-Version"] = self.protocol_version
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id

        request = urllib.request.Request(
            self.server_url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                session_id = response.headers.get("Mcp-Session-Id")
                if session_id:
                    self.session_id = session_id
                if response.status == 202:
                    return None
                raw = response.read().decode("utf-8", errors="replace")
                content_type = response.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise MCPError("HTTP {}: {}".format(exc.code, body[:500])) from exc
        except urllib.error.URLError as exc:
            raise MCPError("Network error: {}".format(exc.reason)) from exc

        if not raw:
            return None
        messages = list(self._decode_sse(raw)) if "text/event-stream" in content_type else [json.loads(raw)]
        expected_id = payload.get("id")
        if expected_id is not None:
            for message in messages:
                if message.get("id") == expected_id:
                    return message
        return messages[-1] if messages else None

    @staticmethod
    def _result(message: Optional[Dict[str, Any]], operation: str) -> Dict[str, Any]:
        if not message:
            raise MCPError("{} returned no response".format(operation))
        if "error" in message:
            raise MCPError("{} protocol error: {}".format(operation, message["error"]))
        result = message.get("result")
        if not isinstance(result, dict):
            raise MCPError("{} returned an invalid result".format(operation))
        return result

    def initialize(self) -> None:
        if self.initialized:
            return
        request_id = self._next_id()
        message = self._post(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "initialize",
                "params": {
                    "protocolVersion": self.protocol_version,
                    "capabilities": {},
                    "clientInfo": {"name": "jin10-research", "version": "0.1.0"},
                },
            },
            include_protocol=False,
        )
        result = self._result(message, "initialize")
        self.protocol_version = result.get("protocolVersion", self.protocol_version)
        self.server_info = result.get("serverInfo", {})
        self._post(
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
        )
        self.initialized = True

    def list_tools(self) -> Dict[str, Any]:
        self.initialize()
        request_id = self._next_id()
        return self._result(
            self._post(
                {"jsonrpc": "2.0", "id": request_id, "method": "tools/list", "params": {}}
            ),
            "tools/list",
        )

    def list_resources(self) -> Dict[str, Any]:
        self.initialize()
        request_id = self._next_id()
        return self._result(
            self._post(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "resources/list",
                    "params": {},
                }
            ),
            "resources/list",
        )

    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        self.initialize()
        request_id = self._next_id()
        result = self._result(
            self._post(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": arguments or {}},
                }
            ),
            "tools/call {}".format(name),
        )
        if result.get("isError"):
            raise MCPError("{} returned isError=true".format(name))
        structured = result.get("structuredContent")
        if not isinstance(structured, dict):
            raise MCPError("{} did not return result.structuredContent".format(name))
        if structured.get("status") not in (None, 200):
            raise MCPError("{} business error: {}".format(name, structured.get("message")))
        return structured

