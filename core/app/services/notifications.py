import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Merkezi API (kurumsalapi) bekledigi alanlar
REMOTE_PARAMS = (
    "GuidKey",
    "YedekKodu",
    "KurumNo",
    "Tarih",
    "DisIp",
    "DiskAlani1",
    "DiskAlani2",
    "DiskAlani3",
    "YedekBoyutu",
    "Ftp",
    "Mail",
)

MAX_HISTORY = 200


class NotificationService:
    def __init__(self, config_dir: Path) -> None:
        self._file = config_dir / "notifications.json"

    def _load(self) -> list[dict[str, Any]]:
        if not self._file.exists():
            return []
        try:
            return json.loads(self._file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []

    def _save(self, items: list[dict[str, Any]]) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        self._file.write_text(json.dumps(items[-MAX_HISTORY:], ensure_ascii=False, indent=2), encoding="utf-8")

    def record(self, payload: dict[str, Any]) -> dict[str, Any]:
        entry = {**payload, "id": datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")}
        history = self._load()
        history.append(entry)
        self._save(history)
        return entry

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        return list(reversed(self._load()[-limit:]))

    async def forward_remote(self, remote_url: str, payload: dict[str, Any]) -> dict[str, Any]:
        """kurumsalapi iletimi — yedek kilidini bloklamamak icin kisa timeout."""
        if not remote_url:
            return {"forwarded": False, "reason": "remote_api_url bos"}

        remote_payload = {k: payload[k] for k in REMOTE_PARAMS if k in payload and payload[k] != ""}
        timeout = httpx.Timeout(8.0, connect=4.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(remote_url, params=remote_payload)
            logger.info("Merkezi API yaniti: %s %s", response.status_code, remote_url)
            return {
                "forwarded": True,
                "status_code": response.status_code,
                "url": remote_url,
            }
        except Exception as exc:  # noqa: BLE001 — bildirim asla yedegi kilitlemesin
            logger.warning("Merkezi API hatasi: %s", exc)
            return {"forwarded": False, "reason": str(exc)}


def build_payload(
    settings,
    *,
    guid_key: str = "",
    yedek_kodu: str = "",
    kurumkodu: str = "",
    tarih: str = "",
    dis_ip: str = "",
    disk1: str = "",
    disk2: str = "0",
    disk3: str = "0",
    yedek_boyutu: str = "",
    ftp: str = "",
    mail: str = "1",
    yedek_tipi: str = "",
    dosya_adi: str = "",
    instance_id: str = "",
    oracle_sid: str = "",
) -> dict[str, Any]:
    inst = settings.first_instance() if hasattr(settings, "first_instance") else None
    return {
        "GuidKey": guid_key or (inst.guid_key if inst else ""),
        "YedekKodu": yedek_kodu or (inst.yedek_kodu if inst else "Hbys"),
        "KurumNo": kurumkodu or (inst.kurumkodu if inst else ""),
        "Hastane": inst.hastane if inst else "",
        "Il": inst.il if inst else "",
        "Hostname": settings.hostname if hasattr(settings, "hostname") else "",
        "InstanceId": instance_id,
        "OracleSid": oracle_sid,
        "Tarih": tarih or datetime.now().strftime("%Y%m%d"),
        "DisIp": dis_ip,
        "DiskAlani1": disk1,
        "DiskAlani2": disk2,
        "DiskAlani3": disk3,
        "YedekBoyutu": yedek_boyutu,
        "Ftp": ftp,
        "Mail": mail if getattr(settings, "mail_notify", True) else "0",
        "YedekTipi": yedek_tipi,
        "DosyaAdi": dosya_adi,
        "received_at": datetime.now(timezone.utc).isoformat(),
    }
