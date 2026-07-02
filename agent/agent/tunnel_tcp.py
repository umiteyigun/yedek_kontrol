"""Agent tarafinda hub kontrollu TCP tunelleri."""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

SendFn = Callable[[dict[str, Any]], Awaitable[None]]


class TunnelTcpManager:
  def __init__(self) -> None:
    self._streams: dict[str, tuple[asyncio.StreamReader, asyncio.StreamWriter]] = {}
    self._tasks: dict[str, asyncio.Task] = {}

  async def close_all(self) -> None:
    for tid in list(self._streams.keys()):
      await self.handle_close({"id": tid})

  async def handle_open(self, msg: dict[str, Any], send: SendFn) -> None:
    tid = str(msg.get("id") or "")
    host = str(msg.get("host") or "").strip()
    port = int(msg.get("port") or 0)
    if not tid or not host or port < 1 or port > 65535:
      await send({"type": "tunnel_open_fail", "id": tid, "error": "Gecersiz hedef"})
      return
    try:
      reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port),
        timeout=15.0,
      )
    except OSError as exc:
      logger.warning("Tunel acilamadi %s:%s — %s", host, port, exc)
      await send({"type": "tunnel_open_fail", "id": tid, "error": str(exc)[:200]})
      return
    self._streams[tid] = (reader, writer)
    self._tasks[tid] = asyncio.create_task(self._relay_from_remote(tid, reader, send))
    logger.info("Tunel acildi id=%s -> %s:%s", tid, host, port)
    await send({"type": "tunnel_open_ok", "id": tid})

  async def _relay_from_remote(
    self,
    tid: str,
    reader: asyncio.StreamReader,
    send: SendFn,
  ) -> None:
    try:
      while True:
        chunk = await reader.read(32768)
        if not chunk:
          break
        await send(
          {
            "type": "tunnel_data",
            "id": tid,
            "data_b64": base64.b64encode(chunk).decode(),
          }
        )
    except asyncio.CancelledError:
      raise
    except OSError as exc:
      logger.debug("Tunel okuma bitti %s: %s", tid, exc)
    finally:
      await send({"type": "tunnel_closed", "id": tid, "reason": "remote_eof"})
      await self.handle_close({"id": tid})

  async def handle_data(self, msg: dict[str, Any]) -> None:
    tid = str(msg.get("id") or "")
    pair = self._streams.get(tid)
    if not pair:
      return
    _reader, writer = pair
    raw = base64.b64decode(msg.get("data_b64") or "")
    if not raw:
      return
    try:
      writer.write(raw)
      await writer.drain()
    except OSError:
      await self.handle_close({"id": tid})

  async def handle_close(self, msg: dict[str, Any]) -> None:
    tid = str(msg.get("id") or "")
    task = self._tasks.pop(tid, None)
    if task:
      task.cancel()
    pair = self._streams.pop(tid, None)
    if pair:
      _reader, writer = pair
      try:
        writer.close()
        await writer.wait_closed()
      except OSError:
        pass
