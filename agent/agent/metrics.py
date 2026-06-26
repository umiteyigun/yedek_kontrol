"""Panel snapshot toplama ve hub'a metrik gonderimi."""

from __future__ import annotations

import asyncio
import json
import logging

import httpx

logger = logging.getLogger("yedek-agent.metrics")

METRICS_INTERVAL_SEC = 900  # 15 dk


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


async def send_metrics_report(ws, settings) -> None:
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
    except Exception as exc:
        logger.warning("Metrik toplanamadi: %s", exc)


async def metrics_loop(ws, settings, is_enabled) -> None:
    """Onayli agent icin 15 dk'da bir panel snapshot gonder."""
    await asyncio.sleep(5)
    while True:
        if is_enabled():
            await send_metrics_report(ws, settings)
        await asyncio.sleep(METRICS_INTERVAL_SEC)
