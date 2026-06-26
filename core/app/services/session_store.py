"""Sunucu tarafli oturum deposu — panel ve terminal icin ayri tokenlar."""

from __future__ import annotations

import json
import logging
import os
import secrets
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

SessionKind = Literal["panel", "terminal"]

PANEL_SESSION_TTL = int(os.getenv("SESSION_MAX_AGE", str(60 * 60 * 2)))
TERMINAL_SESSION_TTL = int(os.getenv("TERMINAL_SESSION_MAX_AGE", str(15 * 60)))
SESSION_BIND_IP = os.getenv("SESSION_BIND_IP", "1").lower() in ("1", "true", "yes")
SESSION_BIND_UA = os.getenv("SESSION_BIND_UA", "1").lower() in ("1", "true", "yes")


@dataclass
class StoredSession:
    id: str
    user: str
    role: str
    auth: str
    kind: SessionKind
    parent_id: str | None
    created_at: float
    expires_at: float
    ip: str
    ua_hash: str
    revoked: bool = False

    def to_public(self) -> dict[str, Any]:
        return {
            "user": self.user,
            "role": self.role,
            "auth": self.auth,
            "sid": self.id,
            "kind": self.kind,
        }


class SessionStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write({})

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            raw = self.path.read_text(encoding="utf-8")
            data = json.loads(raw) if raw.strip() else {}
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Oturum dosyasi okunamadi: %s", exc)
            return {}

    def _write(self, data: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def _purge_expired(self, data: dict[str, Any]) -> dict[str, Any]:
        now = time.time()
        return {sid: row for sid, row in data.items() if float(row.get("expires_at", 0)) > now}

    def _save_record(self, record: StoredSession) -> None:
        data = self._purge_expired(self._read())
        data[record.id] = asdict(record)
        self._write(data)

    def create_panel_session(
        self,
        *,
        user: str,
        role: str,
        auth: str,
        ip: str,
        ua_hash: str,
    ) -> str:
        now = time.time()
        sid = secrets.token_urlsafe(32)
        record = StoredSession(
            id=sid,
            user=user,
            role=role,
            auth=auth,
            kind="panel",
            parent_id=None,
            created_at=now,
            expires_at=now + PANEL_SESSION_TTL,
            ip=ip,
            ua_hash=ua_hash,
        )
        self._save_record(record)
        return sid

    def create_terminal_session(
        self,
        *,
        parent_id: str,
        user: str,
        role: str,
        auth: str,
        ip: str,
        ua_hash: str,
    ) -> str | None:
        parent = self.get_valid(parent_id, kind="panel", ip=ip, ua_hash=ua_hash)
        if not parent:
            return None
        now = time.time()
        sid = secrets.token_urlsafe(32)
        record = StoredSession(
            id=sid,
            user=user,
            role=role,
            auth=auth,
            kind="terminal",
            parent_id=parent_id,
            created_at=now,
            expires_at=now + TERMINAL_SESSION_TTL,
            ip=ip,
            ua_hash=ua_hash,
        )
        self._save_record(record)
        return sid

    def get_valid(
        self,
        sid: str,
        *,
        kind: SessionKind,
        ip: str,
        ua_hash: str,
    ) -> StoredSession | None:
        if not sid:
            return None
        data = self._purge_expired(self._read())
        row = data.get(sid)
        if not row:
            self._write(data)
            return None
        record = StoredSession(**row)
        now = time.time()
        if record.revoked or record.expires_at <= now:
            return None
        if record.kind != kind:
            return None
        if (
            SESSION_BIND_IP
            and record.auth != "central"
            and record.ip
            and ip
            and record.ip != ip
        ):
            return None
        if (
            SESSION_BIND_UA
            and record.auth != "central"
            and record.ua_hash
            and ua_hash
            and record.ua_hash != ua_hash
        ):
            return None
        if kind == "terminal" and record.parent_id:
            parent = self.get_valid(record.parent_id, kind="panel", ip=ip, ua_hash=ua_hash)
            if not parent:
                return None
        self._write(data)
        return record

    def revoke(self, sid: str) -> None:
        if not sid:
            return
        data = self._read()
        row = data.get(sid)
        if not row:
            return
        row["revoked"] = True
        data[sid] = row
        self._write(data)

    def revoke_terminal_for_parent(self, parent_id: str) -> None:
        if not parent_id:
            return
        data = self._read()
        changed = False
        for sid, row in data.items():
            if row.get("kind") == "terminal" and row.get("parent_id") == parent_id:
                row["revoked"] = True
                data[sid] = row
                changed = True
        if changed:
            self._write(data)

    def revoke_all_for_user(self, user: str) -> None:
        if not user:
            return
        data = self._read()
        changed = False
        for sid, row in data.items():
            if row.get("user") == user and not row.get("revoked"):
                row["revoked"] = True
                data[sid] = row
                changed = True
        if changed:
            self._write(data)
