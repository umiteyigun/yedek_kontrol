"""Yedek Central Edge Agent — outbound hub bağlantısı."""

from __future__ import annotations

import asyncio
import json
import logging
import sys

import httpx
import websockets

from agent.config import AgentSettings, load_settings, save_registration
from agent.local_proxy import LocalProxy
from agent.metrics import metrics_loop, send_metrics_report

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("yedek-agent")


async def ensure_registered(settings: AgentSettings) -> AgentSettings:
    if settings.agent_token and settings.agent_id:
        return settings
    if not settings.org_enrollment_code:
        logger.error("ORG_ENROLLMENT_CODE gerekli")
        sys.exit(1)
    url = f"{settings.hub_http_url}/api/agents/register"
    payload = {
        "org_enrollment_code": settings.org_enrollment_code,
        "hostname": settings.hostname,
        "node_label": settings.node_label,
        "node_role": settings.node_role,
    }
    headers: dict[str, str] = {}
    if settings.register_secret:
        headers["X-Agent-Register-Key"] = settings.register_secret
    async with httpx.AsyncClient(timeout=30.0, verify=settings.verify_tls) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    save_registration(
        settings,
        data["agent_id"],
        data["agent_token"],
        settings.org_enrollment_code,
    )
    logger.info("Kayit tamam: agent_id=%s status=%s", data["agent_id"], data["status"])
    return load_settings()


async def run_agent() -> None:
    settings = await ensure_registered(load_settings())
    proxy = LocalProxy(settings.panel_local_url, settings.verify_tls)
    await proxy.start()

    ssl_ctx = None
    if settings.hub_ws_url.startswith("wss"):
        import ssl

        if settings.verify_tls:
            ssl_ctx = ssl.create_default_context()
        else:
            ssl_ctx = ssl.create_default_context()
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE

    while True:
        try:
            async with websockets.connect(
                settings.hub_ws_url,
                ssl=ssl_ctx,
                ping_interval=20,
                ping_timeout=20,
            ) as ws:
                await ws.send(
                    json.dumps({"type": "hello", "agent_token": settings.agent_token})
                )
                ack = json.loads(await ws.recv())
                proxy_enabled = bool(ack.get("proxy_enabled"))
                logger.info(
                    "Hub baglandi: status=%s proxy=%s",
                    ack.get("status"),
                    proxy_enabled,
                )

                async def heartbeat() -> None:
                    while True:
                        await asyncio.sleep(30)
                        await ws.send(json.dumps({"type": "heartbeat"}))

                hb = asyncio.create_task(heartbeat())
                metrics = asyncio.create_task(
                    metrics_loop(ws, settings, lambda: bool(ack.get("proxy_enabled")))
                )
                if proxy_enabled:
                    asyncio.create_task(send_metrics_report(ws, settings))
                try:
                    async for raw in ws:
                        msg = json.loads(raw)
                        mtype = msg.get("type")
                        if mtype == "status_update":
                            ack["status"] = msg.get("status")
                            proxy_enabled = bool(msg.get("proxy_enabled"))
                            logger.info(
                                "Hub durum guncellendi: status=%s proxy=%s",
                                ack.get("status"),
                                proxy_enabled,
                            )
                            if proxy_enabled:
                                asyncio.create_task(send_metrics_report(ws, settings))
                            continue
                        if mtype == "pong":
                            continue
                        if mtype == "proxy_req":
                            if not proxy_enabled:
                                await ws.send(
                                    json.dumps(
                                        {
                                            "type": "proxy_resp",
                                            "id": msg.get("id"),
                                            "status": 403,
                                            "headers": {},
                                            "body_b64": "",
                                        }
                                    )
                                )
                                continue
                            resp = await proxy.handle_http(msg)
                            await ws.send(json.dumps(resp))
                        elif mtype == "proxy_ws_open":
                            if proxy_enabled:
                                await proxy.handle_ws_open(
                                    msg,
                                    lambda frame: ws.send(json.dumps(frame)),
                                )
                        elif mtype == "proxy_ws_frame":
                            await proxy.handle_ws_frame(msg)
                        elif mtype == "proxy_ws_close":
                            await proxy.handle_ws_close(msg.get("id", ""))
                finally:
                    hb.cancel()
                    metrics.cancel()
        except Exception as exc:
            logger.warning("Baglanti koptu: %s — 5s sonra yeniden", exc)
            await asyncio.sleep(5)


def main() -> None:
    asyncio.run(run_agent())


if __name__ == "__main__":
    main()
