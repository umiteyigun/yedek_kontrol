"""Merkez hub proxy oturumu — yerel LDAP tekrar sormaz."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any, Mapping

from starlette.requests import Request

from app.config.ldap_config import ROLE_FULL, ROLE_LIMITED

logger = logging.getLogger(__name__)

CENTRAL_PROXY_SECRET = os.getenv("CENTRAL_PROXY_SECRET", "").strip()
LOCAL_AGENT_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def _b64url_decode(raw: str) -> bytes:
    pad = "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode(raw + pad)


def is_local_agent_request(request: Request) -> bool:
    host = (request.client.host if request.client else "").lower()
    return host in LOCAL_AGENT_HOSTS


def is_central_proxy_request(request: Request) -> bool:
    return is_central_proxy_headers(request.headers)


def is_central_proxy_headers(headers: Mapping[str, str]) -> bool:
    return headers.get("x-yedek-central-proxy") == "1"


def verify_central_token(token: str) -> dict[str, Any] | None:
    if not CENTRAL_PROXY_SECRET or not token:
        return None
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != "v1":
        return None
    body, sig = parts[1], parts[2]
    expected = hmac.new(
        CENTRAL_PROXY_SECRET.encode("utf-8"),
        body.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return None
    try:
        payload = json.loads(_b64url_decode(body).decode("utf-8"))
    except (json.JSONDecodeError, ValueError):
        return None
    exp = int(payload.get("exp") or 0)
    if exp <= int(time.time()):
        return None
    username = str(payload.get("sub") or "").strip()
    if not username:
        return None
    panel_role = str(payload.get("pr") or ROLE_LIMITED).lower()
    if panel_role not in (ROLE_FULL, ROLE_LIMITED):
        panel_role = ROLE_LIMITED
    return {
        "username": username,
        "display_name": str(payload.get("dn") or username),
        "panel_role": panel_role,
        "hub_role": str(payload.get("hr") or ""),
    }


def resolve_central_proxy_user(request: Request) -> dict[str, Any] | None:
    return resolve_central_proxy_headers(request.headers)


def resolve_central_proxy_headers(headers: Mapping[str, str]) -> dict[str, Any] | None:
    if not CENTRAL_PROXY_SECRET:
        return None
    if not is_central_proxy_headers(headers):
        return None
    header_user = (headers.get("x-yedek-central-user") or "").strip()
    token = (headers.get("x-yedek-central-auth") or "").strip()
    claims = verify_central_token(token)
    if not claims:
        logger.debug("Merkez proxy token gecersiz veya eksik")
        return None
    if header_user and header_user != claims["username"]:
        logger.warning(
            "Merkez proxy kullanici uyusmazligi header=%s token=%s",
            header_user,
            claims["username"],
        )
        return None
    header_role = (headers.get("x-yedek-central-panel-role") or "").strip().lower()
    if header_role in (ROLE_FULL, ROLE_LIMITED):
        claims["panel_role"] = header_role
    return claims
