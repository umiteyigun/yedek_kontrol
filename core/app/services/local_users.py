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

from app.config.ldap_config import ROLE_LIMITED

logger = logging.getLogger(__name__)

USERNAME_RE = re.compile(r"^[a-zA-Z0-9._-]{3,32}$")
PBKDF2_ITERATIONS = 200_000


@dataclass
class LocalUserPublic:
    username: str
    role: str
    role_label: str
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

    def get_user(self, username: str) -> dict[str, Any] | None:
        clean = (username or "").strip().lower()
        with self._lock:
            row = self._users.get(clean)
            return dict(row) if row else None

    def role_counts(self) -> dict[str, int]:
        with self._lock:
            counts: dict[str, int] = {}
            for row in self._users.values():
                role = str(row.get("role") or ROLE_LIMITED)
                counts[role] = counts.get(role, 0) + 1
            return counts

    def count_users_with_role(self, role_id: str) -> int:
        clean = (role_id or "").strip().lower()
        with self._lock:
            return sum(1 for row in self._users.values() if str(row.get("role") or "") == clean)

    def list_users(self, role_labels: dict[str, str] | None = None) -> list[LocalUserPublic]:
        labels = role_labels or {}
        with self._lock:
            rows = []
            for row in self._users.values():
                role = str(row.get("role") or ROLE_LIMITED)
                rows.append(
                    LocalUserPublic(
                        username=str(row.get("username", "")),
                        role=role,
                        role_label=labels.get(role, role),
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
            return str(row.get("role") or ROLE_LIMITED)

    def add_user(self, username: str, password: str, role: str) -> None:
        clean = username.strip().lower()
        if not USERNAME_RE.fullmatch(clean):
            raise ValueError("Kullanici adi 3-32 karakter; harf, rakam, . _ -")
        if len(password) < 6:
            raise ValueError("Sifre en az 6 karakter olmali")
        role_clean = role.strip().lower()
        if not role_clean:
            raise ValueError("Rol secilmeli")
        with self._lock:
            if clean in self._users:
                raise ValueError("Bu kullanici adi zaten var")
            self._users[clean] = {
                "username": clean,
                "password_hash": hash_password(password),
                "role": role_clean,
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
                role_clean = role.strip().lower()
                if not role_clean:
                    raise ValueError("Rol secilmeli")
                row["role"] = role_clean
            if enabled is not None:
                row["enabled"] = enabled
            if password:
                if len(password) < 6:
                    raise ValueError("Sifre en az 6 karakter olmali")
                row["password_hash"] = hash_password(password)
            row.pop("permissions", None)
            self._persist()

    def delete_user(self, username: str) -> None:
        clean = username.strip().lower()
        with self._lock:
            if clean not in self._users:
                raise ValueError("Kullanici bulunamadi")
            del self._users[clean]
            self._persist()
