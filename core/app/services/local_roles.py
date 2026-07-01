"""Yerel panel rolleri — config/local_roles.json"""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config.ldap_config import ROLE_FULL, ROLE_LIMITED
from app.services.permissions import ROLE_DEFAULTS, normalize_permissions

logger = logging.getLogger(__name__)

ROLE_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{2,31}$")
BUILTIN_ROLE_IDS = frozenset({ROLE_FULL, ROLE_LIMITED})

BUILTIN_LABELS = {
    ROLE_FULL: "Tam yetki",
    ROLE_LIMITED: "Sinirli (yedek operatoru)",
}


@dataclass
class LocalRolePublic:
    role_id: str
    label: str
    builtin: bool
    permissions: dict[str, dict[str, bool]]
    created_at: str
    user_count: int = 0


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class LocalRoleStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.RLock()
        self._roles: dict[str, dict[str, Any]] = {}
        self.load()

    def load(self) -> None:
        with self._lock:
            if not self._path.exists():
                self._roles = {}
                self._ensure_builtin()
                self._persist()
                return
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                logger.exception("local_roles.json okunamadi")
                self._roles = {}
                self._ensure_builtin()
                return
            roles: dict[str, dict[str, Any]] = {}
            for row in data.get("roles", []):
                role_id = str(row.get("role_id", "")).strip().lower()
                if not role_id:
                    continue
                roles[role_id] = row
            self._roles = roles
            self._ensure_builtin()

    def _ensure_builtin(self) -> None:
        changed = False
        for role_id, label in BUILTIN_LABELS.items():
            if role_id not in self._roles:
                self._roles[role_id] = {
                    "role_id": role_id,
                    "label": label,
                    "builtin": True,
                    "permissions": normalize_permissions(ROLE_DEFAULTS[role_id]),
                    "created_at": _utc_now(),
                }
                changed = True
            else:
                row = self._roles[role_id]
                if not row.get("builtin"):
                    row["builtin"] = True
                    changed = True
                if not row.get("label"):
                    row["label"] = label
                    changed = True
                if not row.get("permissions"):
                    row["permissions"] = normalize_permissions(ROLE_DEFAULTS[role_id])
                    changed = True
        if changed:
            self._persist()

    def _persist(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"roles": list(self._roles.values())}
        self._path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def list_roles(self, user_counts: dict[str, int] | None = None) -> list[LocalRolePublic]:
        counts = user_counts or {}
        with self._lock:
            rows = []
            for row in self._roles.values():
                role_id = str(row.get("role_id", ""))
                rows.append(
                    LocalRolePublic(
                        role_id=role_id,
                        label=str(row.get("label") or role_id),
                        builtin=bool(row.get("builtin", False)),
                        permissions=normalize_permissions(row.get("permissions")),
                        created_at=str(row.get("created_at") or ""),
                        user_count=int(counts.get(role_id, 0)),
                    )
                )
            rows.sort(key=lambda item: (not item.builtin, item.label.lower()))
            return rows

    def get_role(self, role_id: str) -> dict[str, Any] | None:
        clean = (role_id or "").strip().lower()
        with self._lock:
            row = self._roles.get(clean)
            return dict(row) if row else None

    def role_exists(self, role_id: str) -> bool:
        clean = (role_id or "").strip().lower()
        with self._lock:
            return clean in self._roles

    def role_label(self, role_id: str) -> str:
        row = self.get_role(role_id)
        if row:
            return str(row.get("label") or role_id)
        return role_id

    def get_permissions(self, role_id: str) -> dict[str, dict[str, bool]]:
        row = self.get_role(role_id)
        if row and row.get("permissions"):
            return normalize_permissions(row["permissions"])
        return normalize_permissions(ROLE_DEFAULTS.get(role_id, ROLE_DEFAULTS[ROLE_LIMITED]))

    def add_role(
        self,
        role_id: str,
        label: str,
        permissions: dict[str, dict[str, bool]],
    ) -> None:
        clean = role_id.strip().lower()
        if not ROLE_ID_RE.fullmatch(clean):
            raise ValueError("Rol kodu 3-32 karakter; kucuk harf ile baslamali (a-z, 0-9, _, -)")
        if clean in BUILTIN_ROLE_IDS:
            raise ValueError("Bu rol kodu sistem tarafindan kullaniliyor")
        name = label.strip()
        if len(name) < 2:
            raise ValueError("Rol adi en az 2 karakter olmali")
        with self._lock:
            if clean in self._roles:
                raise ValueError("Bu rol kodu zaten var")
            self._roles[clean] = {
                "role_id": clean,
                "label": name,
                "builtin": False,
                "permissions": normalize_permissions(permissions),
                "created_at": _utc_now(),
            }
            self._persist()

    def update_role(
        self,
        role_id: str,
        *,
        label: str | None = None,
        permissions: dict[str, dict[str, bool]] | None = None,
    ) -> None:
        clean = role_id.strip().lower()
        with self._lock:
            row = self._roles.get(clean)
            if not row:
                raise ValueError("Rol bulunamadi")
            if label is not None:
                name = label.strip()
                if len(name) < 2:
                    raise ValueError("Rol adi en az 2 karakter olmali")
                row["label"] = name
            if permissions is not None:
                row["permissions"] = normalize_permissions(permissions)
            self._persist()

    def delete_role(self, role_id: str, *, in_use: bool = False) -> None:
        clean = role_id.strip().lower()
        if clean in BUILTIN_ROLE_IDS:
            raise ValueError("Sistem rolleri silinemez")
        with self._lock:
            if clean not in self._roles:
                raise ValueError("Rol bulunamadi")
            if in_use:
                raise ValueError("Bu role atanmis kullanicilar var; once kullanicilari degistirin")
            del self._roles[clean]
            self._persist()
