"""Panel snapshot toplama ve hub'a metrik gonderimi."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess

import httpx

logger = logging.getLogger("yedek-agent.metrics")

METRICS_INTERVAL_SEC = 900  # 15 dk
METRICS_INTERVAL_RUNNING_SEC = 15  # yedek calisirken

_CONTAINER_PREFIX = re.compile(r"^(trtek-vlan-|radius-)")


def _service_from_container(name: str) -> str:
    if name.startswith("trtek-vlan-"):
        return name[len("trtek-vlan-") :]
    if name.startswith("radius-"):
        return name[len("radius-") :]
    return name


def _version_from_image(image: str) -> str:
    img = (image or "").strip()
    if not img:
        return "—"
    last = img.split("/")[-1]
    if ":" in last:
        return last.rsplit(":", 1)[-1] or "latest"
    if last.startswith("ana-proje-") or "/" not in img:
        return "local"
    return "latest"


def collect_radius_container_images() -> list[dict[str, str]]:
    """Host docker.sock uzerinden radius stack container surumleri."""
    try:
        proc = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.Names}}\t{{.Image}}\t{{.Status}}"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        if proc.returncode != 0:
            return []
        rows: list[dict[str, str]] = []
        for line in proc.stdout.splitlines():
            if not _CONTAINER_PREFIX.match(line):
                continue
            parts = line.split("\t", 2)
            if len(parts) < 2:
                continue
            name, image = parts[0], parts[1]
            status_raw = parts[2] if len(parts) > 2 else ""
            rows.append(
                {
                    "service": _service_from_container(name),
                    "container": name,
                    "image": image,
                    "version": _version_from_image(image),
                    "status": "running" if status_raw.startswith("Up") else "stopped",
                }
            )
        rows.sort(key=lambda r: r["service"])
        return rows
    except Exception as exc:
        logger.warning("Radius container listesi alinamadi: %s", exc)
        return []


def enrich_radius_snapshot(snapshot: dict) -> dict:
    if snapshot.get("product") != "radius":
        return snapshot
    images = collect_radius_container_images()
    if not images:
        return snapshot
    snapshot = {**snapshot, "container_images": images}
    radius_status = snapshot.get("radius_status")
    if isinstance(radius_status, dict):
        snapshot["radius_status"] = {**radius_status, "container_images": images}
    return snapshot


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
        snapshot = await asyncio.to_thread(enrich_radius_snapshot, snapshot)
        await ws.send(json.dumps({"type": "metrics_report", "payload": snapshot}))
        logger.info(
            "Metrik gonderildi: instances=%s containers=%s",
            snapshot.get("instance_count"),
            len(snapshot.get("container_images") or []),
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
