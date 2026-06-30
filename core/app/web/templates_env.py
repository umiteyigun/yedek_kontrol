"""Paylasilan Jinja2 sablon ortami."""

from __future__ import annotations

from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


def _proxy_prefix(request: Request) -> str:
    return request.headers.get("x-yedek-central-proxy-prefix") or ""


def panel_url(path: str, request: Request) -> str:
    """Merkez hub proxy altinda /o/{org}/n/{node} oneki (yerelde bos)."""
    prefix = _proxy_prefix(request)
    if not path.startswith("/"):
        path = "/" + path
    return f"{prefix}{path}"


def static_url(path: str, request: Request) -> str:
    return panel_url(path, request)


templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
templates.env.globals["panel_url"] = panel_url
templates.env.globals["static_url"] = static_url
