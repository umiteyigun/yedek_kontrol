"""Paylasilan Jinja2 sablon ortami."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.auth import can as auth_can

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
_PROXY_PREFIX_RE = re.compile(
    r"^/o/[a-z0-9][a-z0-9_-]*/n/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _normalize_proxy_prefix(raw: str) -> str:
    value = (raw or "").strip()
    if not value or not _PROXY_PREFIX_RE.match(value):
        return ""
    return value


def _proxy_prefix(request: Request) -> str:
    return _normalize_proxy_prefix(request.headers.get("x-yedek-central-proxy-prefix") or "")


def panel_url(path: str, request: Request) -> str:
    """Merkez hub proxy altinda /o/{org}/n/{node} oneki (yerelde bos)."""
    prefix = _proxy_prefix(request)
    if not path.startswith("/"):
        path = "/" + path
    return f"{prefix}{path}"


def static_url(path: str, request: Request) -> str:
    return panel_url(path, request)


def proxy_prefix_for(request: Request) -> str:
    return _proxy_prefix(request)


templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
templates.env.globals["panel_url"] = panel_url
templates.env.globals["static_url"] = static_url
templates.env.globals["proxy_prefix_for"] = proxy_prefix_for
templates.env.globals["can_perm"] = auth_can
