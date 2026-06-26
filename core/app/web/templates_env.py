"""Paylasilan Jinja2 sablon ortami."""

from __future__ import annotations

from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


def static_url(path: str, request: Request) -> str:
    prefix = request.headers.get("x-yedek-central-proxy-prefix") or ""
    return f"{prefix}{path}"


templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
templates.env.globals["static_url"] = static_url
