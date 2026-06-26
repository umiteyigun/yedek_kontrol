import json
import logging
import re
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config.ldap_config import apply_ldap_defaults_to_dict, ldap_config_is_uninitialized
from app.config.models import YedekSettings

logger = logging.getLogger(__name__)

Listener = Callable[[YedekSettings], None]


class ConfigStore:
    """Tek kaynak: settings.json + bellek icinde canli config."""

    def __init__(self, config_dir: Path) -> None:
        self._config_dir = config_dir
        self._settings_file = config_dir / "settings.json"
        self._lock = threading.RLock()
        self._listeners: list[Listener] = []
        self._settings = YedekSettings()
        self.version = 0
        self.applied_at: datetime | None = None

    def subscribe(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def get(self) -> YedekSettings:
        with self._lock:
            return self._settings.model_copy(deep=True)

    def load(self) -> YedekSettings:
        with self._lock:
            if self._settings_file.exists():
                raw = json.loads(self._settings_file.read_text(encoding="utf-8"))
                migrate = ldap_config_is_uninitialized(raw)
                normalized = apply_ldap_defaults_to_dict(raw)
                self._settings = YedekSettings.model_validate(normalized)
                if migrate:
                    logger.info("LDAP ayarlari TRTEK sablonu ile dolduruldu (%s)", normalized.get("ldap_host"))
                    self._persist()
            self.version += 1
            self.applied_at = datetime.now(timezone.utc)
            return self.get()

    def update(self, data: dict[str, Any]) -> YedekSettings:
        with self._lock:
            merged = self._settings.model_dump()
            for key, value in data.items():
                if value is not None and value != "":
                    merged[key] = value
            self._settings = YedekSettings.model_validate(merged)
            self._persist()
            return self.get()

    def replace(self, data: dict[str, Any]) -> YedekSettings:
        with self._lock:
            self._settings = YedekSettings.model_validate(data)
            self._persist()
            return self.get()

    def _persist(self) -> None:
        self._config_dir.mkdir(parents=True, exist_ok=True)
        self._settings_file.write_text(
            json.dumps(self._settings.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.version += 1
        self.applied_at = datetime.now(timezone.utc)
        logger.info(
            "Config guncellendi v%s | %s instance",
            self.version,
            len(self._settings.instances),
        )
        for listener in list(self._listeners):
            try:
                listener(self.get())
            except Exception:
                logger.exception("Config listener hatasi")

    def import_from_yedekconfig(self, path: Path) -> YedekSettings | None:
        if not path.exists():
            return None
        text = path.read_text(encoding="utf-8", errors="replace")
        current = self.get().model_dump()
        patterns = {
            "hastane": r"^hastane=(.+)$",
            "il": r"^il=(.+)$",
            "password": r"^password=(.+)$",
            "directory": r"^directory=(.+)$",
            "directorydizini": r"^directorydizini=(.+)$",
            "hostname": r"^hostname=(.+)$",
            "kurumkodu": r"^kurumkodu=(.+)$",
            "oracle_ver": r"^ORACLE_VER=(.+)$",
            "oracle_sid": r"^ORACLE_SID=(.+)$",
            "localftpip": r"^localftpip='([^']*)'",
            "localftpuser": r"^localftpuser='([^']*)'",
            "localftppass": r"^localftppass='([^']*)'",
        }
        for key, pattern in patterns.items():
            match = re.search(pattern, text, re.MULTILINE)
            if match:
                current[key] = match.group(1).strip()
        return self.update(current)


def mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 2:
        return "*" * len(value)
    return value[0] + "*" * (len(value) - 2) + value[-1]
