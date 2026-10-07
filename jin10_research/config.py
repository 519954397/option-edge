from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple


DEFAULT_SERVER_URL = "https://mcp.jin10.com/mcp"
DEFAULT_PROTOCOL_VERSION = "2025-11-25"


@dataclass(frozen=True)
class Settings:
    server_url: str
    bearer_token: str
    protocol_version: str = DEFAULT_PROTOCOL_VERSION
    data_dir: Path = Path("data")
    report_dir: Path = Path("reports")


def _codex_jin10_config(path: Path) -> Tuple[Optional[str], Optional[str]]:
    """Read only the Jin10 URL and static Bearer header from Codex config.

    Python 3.9 has no tomllib, so this deliberately parses the one table and
    two string values that are relevant to this application.
    """
    if not path.is_file():
        return None, None
    text = path.read_text(encoding="utf-8")
    section_match = re.search(
        r"^\[mcp_servers\.jin10\]\s*$([\s\S]*?)(?=^\[|\Z)",
        text,
        flags=re.MULTILINE,
    )
    if not section_match:
        return None, None
    section = section_match.group(1)
    url_match = re.search(r'^url\s*=\s*"([^"]+)"', section, flags=re.MULTILINE)
    token_match = re.search(
        r'"Authorization"\s*=\s*"Bearer\s+([^"]+)"', section
    )
    return (
        url_match.group(1) if url_match else None,
        token_match.group(1) if token_match else None,
    )


def load_settings(require_token: bool = True) -> Settings:
    codex_url, codex_token = _codex_jin10_config(
        Path(os.environ.get("CODEX_CONFIG_FILE", Path.home() / ".codex" / "config.toml"))
    )
    token = (
        os.environ.get("JIN10_BEARER_TOKEN")
        or os.environ.get("JIN10_API_TOKEN")
        or codex_token
    )
    if require_token and not token:
        raise RuntimeError(
            "Missing Jin10 token. Set JIN10_BEARER_TOKEN or configure "
            "[mcp_servers.jin10] in ~/.codex/config.toml."
        )
    return Settings(
        server_url=os.environ.get("JIN10_MCP_SERVER_URL")
        or codex_url
        or DEFAULT_SERVER_URL,
        bearer_token=token or "",
        protocol_version=os.environ.get(
            "JIN10_MCP_PROTOCOL_VERSION", DEFAULT_PROTOCOL_VERSION
        ),
        data_dir=Path(os.environ.get("JIN10_DATA_DIR", "data")),
        report_dir=Path(os.environ.get("JIN10_REPORT_DIR", "reports")),
    )
