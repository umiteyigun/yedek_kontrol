"""Yerel panel kullanicilari — config/local_users.json"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config.ldap_config import ROLE_FULL, ROLE_LIMITED

logger = logging.getLogger(__name__)

USERNAME_RE = re.compile(r"^[a-zA-Z0-9._-]{3,32}$")
PBKDF2_ITERATIONS = 200_000


@dataclass
class LocalUserPublic:
    username: str
    role: str
    enabled: bool
    created_at: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return (
        f"pbkdf2_sha256${PBKDF2_ITERATIONS}$"
        f"{base64.urlsafe_b64encode(salt).decode().rstrip('=')}$"
        f"{base64.urlsafe_b64encode(digest).decode().rstrip('=')}"
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, iterations_raw, salt_b64, digest_b64 = encoded.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        iterations = int(iterations_raw)
        pad = "=" * (-len(salt_b64) % 4)
        salt = base64.urlsafe_b64decode(salt_b64 + pad)
        expected = base64.urlsafe_b64decode(digest_b64 + ("=" * (-len(digest_b64) % 4)))
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return secrets.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


class LocalUserStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.RLock()
        self._users: dict[str, dict[str, Any]] = {}
        self.load()

    def load(self) -> None:
        with self._lock:
            if not self._path.exists():
                self._users = {}
                return
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                logger.exception("local_users.json okunamadi")
                self._users = {}
                return
            users: dict[str, dict[str, Any]] = {}
            for row in data.get("users", []):
                username = str(row.get("username", "")).strip().lower()
                if not username:
                    continue
                users[username] = row
            self._users = users

    def _persist(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"users": list(self._users.values())}
        self._path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def list_users(self) -> list[LocalUserPublic]:
        with self._lock:
            rows = []
            for row in self._users.values():
                rows.append(
                    LocalUserPublic(
                        username=str(row.get("username", "")),
                        role=str(row.get("role") or ROLE_LIMITED),
                        enabled=bool(row.get("enabled", True)),
                        created_at=str(row.get("created_at") or ""),
                    )
                )
            rows.sort(key=lambda item: item.username)
            return rows

    def verify(self, username: str, password: str) -> str | None:
        clean = (username or "").strip().lower()
        if not clean or not password:
            return None
        with self._lock:
            row = self._users.get(clean)
            if not row or not row.get("enabled", True):
                return None
            if not verify_password(password, str(row.get("password_hash") or "")):
                return None
            role = str(row.get("role") or ROLE_LIMITED)
            if role not in {ROLE_FULL, ROLE_LIMITED}:
                return None
            return role

    def add_user(self, username: str, password: str, role: str) -> None:
        clean = username.strip().lower()
        if not USERNAME_RE.fullmatch(clean):
            raise ValueError("Kullanici adi 3-32 karakter; harf, rakam, . _ -")
        if len(password) < 6:
            raise ValueError("Sifre en az 6 karakter olmali")
        if role not in {ROLE_FULL, ROLE_LIMITED}:
            raise ValueError("Rol full veya limited olmali")
        with self._lock:
            if clean in self._users:
                raise ValueError("Bu kullanici adi zaten var")
            self._users[clean] = {
                "username": clean,
                "password_hash": hash_password(password),
                "role": role,
                "enabled": True,
                "created_at": _utc_now(),
            }
            self._persist()

    def update_user(
        self,
        username: str,
        *,
        role: str | None = None,
        enabled: bool | None = None,
        password: str | None = None,
    ) -> None:
        clean = username.strip().lower()
        with self._lock:
            row = self._users.get(clean)
            if not row:
                raise ValueError("Kullanici bulunamadi")
            if role is not None:
                if role not in {ROLE_FULL, ROLE_LIMITED}:
                    raise ValueError("Rol full veya limited olmali")
                row["role"] = role
            if enabled is not None:
                row["enabled"] = enabled
            if password:
                if len(password) < 6:
                    raise ValueError("Sifre en az 6 karakter olmali")
                row["password_hash"] = hash_password(password)
            self._persist()

    def delete_user(self, username: str) -> None:
        clean = username.strip().lower()
        with self._lock:
            if clean not in self._users:
                raise ValueError("Kullanici bulunamadi")
            del self._users[clean]
            self._persist()
