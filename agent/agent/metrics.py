"""Panel snapshot toplama ve hub'a metrik gonderimi."""

from __future__ import annotations

import asyncio
import json
import logging

import httpx

logger = logging.getLogger("yedek-agent.metrics")

METRICS_INTERVAL_SEC = 900  # 15 dk
METRICS_INTERVAL_RUNNING_SEC = 15  # yedek calisirken


async def fetch_panel_snapshot(panel_url: str, *, verify_tls: bool) -> dict:
    base = panel_url.rstrip("/")
    url = f"{base}/api/v1/agent/snapshot"
    async with httpx.AsyncClient(timeout=90.0, verify=verify_tls) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(data.get("error") or "snapshot basarisiz")
    return data


async def send_metrics_report(ws, settings) -> dict | None:
    try:
        snapshot = await fetch_panel_snapshot(
            settings.panel_local_url,
            verify_tls=settings.verify_tls,
        )
        await ws.send(json.dumps({"type": "metrics_report", "payload": snapshot}))
        logger.info(
            "Metrik gonderildi: instances=%s",
            snapshot.get("instance_count"),
        )
        return snapshot
    except Exception as exc:
        logger.warning("Metrik toplanamadi: %s", exc)
        return None


def _backup_is_running(snapshot: dict | None) -> bool:
    if not snapshot:
        return False
    status = snapshot.get("backup_status")
    if isinstance(status, dict) and str(status.get("state") or "") == "running":
        return True
    return False


async def metrics_loop(ws, settings, is_enabled) -> None:
    """Onayli agent icin panel snapshot gonder; yedek calisirken daha sik."""
    await asyncio.sleep(5)
    last_snapshot: dict | None = None
    while True:
        if is_enabled():
            last_snapshot = await send_metrics_report(ws, settings)
        delay = METRICS_INTERVAL_RUNNING_SEC if _backup_is_running(last_snapshot) else METRICS_INTERVAL_SEC
        await asyncio.sleep(delay)
