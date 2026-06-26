"""Yerel yedek panel proxy — HTTP + WebSocket (terminal)."""

from __future__ import annotations

import asyncio
import base64
import logging
import ssl
from typing import Any, Awaitable, Callable

import httpx
import websockets

logger = logging.getLogger(__name__)

SendFn = Callable[[dict[str, Any]], Awaitable[None]]


class LocalProxy:
    def __init__(self, panel_url: str, verify_tls: bool) -> None:
        self.panel_url = panel_url.rstrip("/")
        self.verify = verify_tls
        self._http: httpx.AsyncClient | None = None
        self._upstream: dict[str, websockets.WebSocketClientProtocol] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    async def start(self) -> None:
        self._http = httpx.AsyncClient(verify=self.verify, timeout=120.0)

    async def close(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        for ws in self._upstream.values():
            await ws.close()
        if self._http:
            await self._http.aclose()

    def _ws_url(self, path: str) -> str:
        base = self.panel_url.replace("https://", "wss://").replace("http://", "ws://")
        return f"{base}{path}"

    def _ssl_context(self) -> ssl.SSLContext | None:
        if not self.panel_url.startswith("https"):
            return None
        ctx = ssl.create_default_context()
        if not self.verify:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx

    async def handle_http(self, msg: dict[str, Any]) -> dict[str, Any]:
        assert self._http
        path = msg.get("path", "/")
        url = f"{self.panel_url}{path}"
        body = base64.b64decode(msg.get("body_b64") or "")
        headers = {k: v for k, v in (msg.get("headers") or {}).items() if k.lower() != "host"}
        try:
            resp = await self._http.request(msg.get("method", "GET"), url, headers=headers, content=body)
            return {
                "type": "proxy_resp",
                "id": msg.get("id"),
                "status": resp.status_code,
                "headers": dict(resp.headers),
                "body_b64": base64.b64encode(resp.content).decode("ascii"),
            }
        except Exception as exc:
            logger.exception("HTTP proxy hata: %s", exc)
            return {
                "type": "proxy_resp",
                "id": msg.get("id"),
                "status": 502,
                "headers": {"content-type": "text/plain"},
                "body_b64": base64.b64encode(str(exc).encode()).decode("ascii"),
            }

    async def handle_ws_open(self, msg: dict[str, Any], send: SendFn) -> None:
        ch_id = msg["id"]
        path = msg.get("path", "/")
        skip = {
            "host",
            "connection",
            "upgrade",
            "sec-websocket-key",
            "sec-websocket-version",
            "sec-websocket-extensions",
            "sec-websocket-protocol",
            "content-length",
        }
        headers = {
            k: v
            for k, v in (msg.get("headers") or {}).items()
            if k.lower() not in skip
        }

        async def run() -> None:
            close_code = 1000
            close_reason = ""
            upstream = None
            try:
                upstream = await websockets.connect(
                    self._ws_url(path),
                    additional_headers=list(headers.items()),
                    ssl=self._ssl_context(),
                    open_timeout=30,
                )
                self._upstream[ch_id] = upstream

                async for data in upstream:
                    if isinstance(data, str):
                        frame = {
                            "type": "proxy_ws_frame",
                            "id": ch_id,
                            "opcode": "text",
                            "data_b64": base64.b64encode(data.encode()).decode("ascii"),
                        }
                    else:
                        frame = {
                            "type": "proxy_ws_frame",
                            "id": ch_id,
                            "opcode": "binary",
                            "data_b64": base64.b64encode(data).decode("ascii"),
                        }
                    await send(frame)
                close_code = int(getattr(upstream, "close_code", None) or 1000)
                close_reason = str(getattr(upstream, "close_reason", "") or "")
            except Exception as exc:
                logger.exception("WS open hata: %s", exc)
                msg = str(exc)
                if "403" in msg:
                    close_code = 4403
                    close_reason = "Panel terminal erisimi reddedildi"
                elif "4003" in msg:
                    close_code = 4003
                    close_reason = "Baska bir terminal oturumu acik"
                else:
                    close_code = 1011
                    close_reason = msg[:120]
            finally:
                self._upstream.pop(ch_id, None)
                self._tasks.pop(ch_id, None)
                if upstream is not None:
                    try:
                        await upstream.close()
                    except Exception:
                        pass
                await send(
                    {
                        "type": "proxy_ws_close",
                        "id": ch_id,
                        "code": close_code,
                        "reason": close_reason,
                    }
                )

        self._tasks[ch_id] = asyncio.create_task(run())

    async def handle_ws_frame(self, msg: dict[str, Any]) -> None:
        ch_id = msg.get("id", "")
        upstream = self._upstream.get(ch_id)
        if not upstream:
            return
        data = base64.b64decode(msg.get("data_b64") or "")
        if msg.get("opcode") == "text":
            await upstream.send(data.decode("utf-8", errors="replace"))
        else:
            await upstream.send(data)

    async def handle_ws_close(self, ch_id: str) -> None:
        task = self._tasks.pop(ch_id, None)
        if task:
            task.cancel()
        upstream = self._upstream.pop(ch_id, None)
        if upstream:
            await upstream.close()
