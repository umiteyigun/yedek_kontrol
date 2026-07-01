"""Host shell PTY koprusu — sadece admin WebSocket oturumlari icin."""

from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import os
import pty
import re
import signal
import struct
import termios
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from app.auth import (
    TERMINAL_SESSION_COOKIE,
    is_full_access_session,
    resolve_terminal_session,
)
from app.config.ldap_config import ROLE_FULL
from app.services.central_proxy_auth import (
    is_central_proxy_headers,
    resolve_central_proxy_headers,
)

logger = logging.getLogger(__name__)

HOST_OUTPUT = Path(os.getenv("HOST_OUTPUT", "/host-output"))
AUDIT_LOG = HOST_OUTPUT / "terminal-audit.log"

TERMINAL_SHELL = "/yedek/config/terminal-shell.sh"
NSENTER_BIN = "/usr/bin/nsenter"

SHELL_CMD = [
    NSENTER_BIN,
    "-t",
    "1",
    "-m",
    "-p",
    "-i",
    "--",
    TERMINAL_SHELL,
]

MAX_GLOBAL_SESSIONS = int(os.getenv("TERMINAL_MAX_SESSIONS", "10"))
# 0 = kullanici basina limit yok (hub + kurum paneli ayni anda acilabilir)
MAX_PER_USER = int(os.getenv("TERMINAL_MAX_PER_USER", "0"))
IDLE_TIMEOUT_SEC = int(os.getenv("TERMINAL_IDLE_SEC", "900"))  # 15 dk
MAX_DURATION_SEC = int(os.getenv("TERMINAL_MAX_SEC", "1800"))  # 30 dk


def _shell_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if isinstance(v, str)}
    env.update(
        {
            "TERM": "xterm-256color",
            "COLORTERM": "truecolor",
            "CLORTERM": "true",
            "CLICOLOR": "1",
            "FORCE_COLOR": "1",
            "WEB_TERMINAL": "1",
        }
    )
    return env


_PTY_NOISE_RE = re.compile(
    rb"(?:"
    rb"bash: no job control in this shell\r?\n|"
    rb"-bash: no job control in this shell\r?\n|"
    rb"Last login: [^\r\n]+\r?\n|"
    rb"'abrt-cli status' timed out\r?\n|"
    rb"bash: /usr/share/bashdb/bashdb-main\.inc: No such file or directory\r?\n|"
    rb"bash: warning: cannot start debugger; debugging mode disabled\r?\n"
    rb")"
)

_BLOCKED_LINE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"/etc/(passwd|shadow|gshadow)\b", re.I),
    re.compile(r"local_users\.json", re.I),
    re.compile(r"\btee\b.*/etc/(passwd|shadow|gshadow)", re.I),
    re.compile(r">\s*/etc/(passwd|shadow|gshadow)", re.I),
)

_BLOCKED_CMD_MSG = "\r\n[Yedek Terminal] Bu komut engellendi.\r\n"


class TerminalInputGuard:
    """PTY'ye gitmeden once tehlikeli komut satirlarini yakala."""

    def __init__(self) -> None:
        self._line = bytearray()

    def _is_blocked(self, line: str) -> bool:
        clean = line.strip()
        if not clean:
            return False
        return any(pattern.search(clean) for pattern in _BLOCKED_LINE_PATTERNS)

    def feed(self, data: bytes) -> tuple[bytes, str | None]:
        """Donus: (pty'ye yazilacak veri, engellenen satir veya None)."""
        out = bytearray()
        blocked_line: str | None = None

        for byte in data:
            if byte == 3:  # Ctrl+C
                self._line.clear()
                out.append(byte)
                continue
            if byte in (127, 8):  # backspace
                if self._line:
                    self._line.pop()
                out.append(byte)
                continue
            if byte in (10, 13):
                line = self._line.decode("utf-8", errors="replace")
                if self._is_blocked(line):
                    blocked_line = line.strip()
                    self._line.clear()
                    continue
                # Karakterler yazilirken zaten PTY'ye gitti; Enter'da yalnizca CR gonder.
                if not (byte == 10 and not self._line):
                    out.append(13)
                self._line.clear()
                continue

            self._line.append(byte)
            out.append(byte)

        return bytes(out), blocked_line


def _scrub_pty_output(data: bytes) -> bytes:
    return _PTY_NOISE_RE.sub(b"", data)


def _fork_shell_on_pty(slave_fd: int, env: dict[str, str]) -> int:
    """PTY slave uzerinde controlling terminal ile host shell baslat."""
    pid = os.fork()
    if pid != 0:
        return pid

    try:
        os.setsid()
        os.dup2(slave_fd, 0)
        os.dup2(slave_fd, 1)
        os.dup2(slave_fd, 2)
        if slave_fd > 2:
            os.close(slave_fd)
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    except OSError:
        pass

    try:
        os.execve(SHELL_CMD[0], SHELL_CMD, env)
    except OSError:
        os._exit(127)
    os._exit(127)


async def _terminate_pid(pid: int) -> None:
    loop = asyncio.get_running_loop()

    def _stop() -> None:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        for _ in range(30):
            try:
                wpid, _ = os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                return
            if wpid == pid:
                return
            import time

            time.sleep(0.1)
        try:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
        except (ProcessLookupError, ChildProcessError):
            pass

    await loop.run_in_executor(None, _stop)


@dataclass
class TerminalAuth:
    user: str
    role: str
    client_ip: str
    source: str = "local"
    hub_role: str = ""


class TerminalSessionRegistry:
    """Eszamanli terminal oturumlarini sinirla — farkli kullanicilar paralel baglanabilir."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._by_user: dict[str, int] = {}
        self._total = 0
        self._ws_owner: dict[int, str] = {}

    async def acquire(self, user: str, websocket: WebSocket) -> tuple[bool, str]:
        ws_key = id(websocket)
        async with self._lock:
            if self._total >= MAX_GLOBAL_SESSIONS:
                return False, f"Maksimum eszamanli terminal ({MAX_GLOBAL_SESSIONS}) dolu"
            count = self._by_user.get(user, 0)
            if MAX_PER_USER > 0 and count >= MAX_PER_USER:
                return (
                    False,
                    f"Bu kullanici icin en fazla {MAX_PER_USER} terminal acilabilir",
                )
            self._by_user[user] = count + 1
            self._total += 1
            self._ws_owner[ws_key] = user
        return True, ""

    async def release(self, user: str, websocket: WebSocket) -> None:
        ws_key = id(websocket)
        async with self._lock:
            if self._ws_owner.get(ws_key) != user:
                return
            self._ws_owner.pop(ws_key, None)
            count = self._by_user.get(user, 0)
            if count <= 1:
                self._by_user.pop(user, None)
            else:
                self._by_user[user] = count - 1
            self._total = max(0, self._total - 1)


registry = TerminalSessionRegistry()


def _client_ip(websocket: WebSocket) -> str:
    forwarded = websocket.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if websocket.client:
        return websocket.client.host
    return "?"


def _split_host_port(netloc: str) -> tuple[str, int | None]:
    value = (netloc or "").strip().lower()
    if not value:
        return "", None
    if value.startswith("["):
        end = value.find("]")
        if end != -1:
            host = value[: end + 1]
            rest = value[end + 1 :]
            if rest.startswith(":") and rest[1:].isdigit():
                return host, int(rest[1:])
            return host, None
    if ":" in value:
        host, port_text = value.rsplit(":", 1)
        if port_text.isdigit():
            return host, int(port_text)
    return value, None


def _origin_allowed(websocket: WebSocket) -> bool:
    """CSWSH: Origin host ile Host header eslesmeli (nginx port dusurse bile)."""
    if is_central_proxy_headers(websocket.headers):
        # Hub koprusu: tarayici origin hub, panel host yerel — yalnizca imzali token ile.
        return bool(resolve_central_proxy_headers(websocket.headers))

    origin = websocket.headers.get("origin")
    host = websocket.headers.get("host")
    if not host or not origin:
        return False

    origin_host, origin_port = _split_host_port(parsed.netloc)
    req_host, req_port = _split_host_port(host)
    if origin_host != req_host:
        return False
    if origin_port is not None and req_port is not None:
        return origin_port == req_port
    # Nginx $host port icermeyebilir; hostname yeterli
    return True


def authorize_terminal_ws(websocket: WebSocket) -> TerminalAuth | None:
    if not _origin_allowed(websocket):
        logger.warning(
            "Terminal WS reddedildi: origin uyumsuz origin=%s host=%s ip=%s",
            websocket.headers.get("origin"),
            websocket.headers.get("host"),
            _client_ip(websocket),
        )
        audit_ws_denied(websocket, "origin_uyumsuz")
        return None

    token = websocket.cookies.get(TERMINAL_SESSION_COOKIE)
    store = websocket.app.state.session_store
    session = resolve_terminal_session(
        store,
        token,
        ip=_client_ip(websocket),
        user_agent=websocket.headers.get("user-agent"),
    )
    if is_full_access_session(session):
        user = str(session.get("user") or "")
        role = str(session.get("role") or "")
        if role == ROLE_FULL and user:
            return TerminalAuth(user=user, role=role, client_ip=_client_ip(websocket))

    proxy_user = resolve_central_proxy_headers(websocket.headers)
    if proxy_user and str(proxy_user.get("panel_role") or "") == ROLE_FULL:
        user = str(proxy_user.get("username") or "")
        if user:
            return TerminalAuth(
                user=user,
                role=ROLE_FULL,
                client_ip=_client_ip(websocket),
                source="central",
                hub_role=str(proxy_user.get("hub_role") or ""),
            )

    logger.warning("Terminal WS reddedildi: yetkisiz veya oturum yok ip=%s", _client_ip(websocket))
    audit_ws_denied(websocket, "oturum_yok_veya_yetersiz_rol")
    return None


def audit_ws_denied(websocket: WebSocket, reason: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    source = "central" if is_central_proxy_headers(websocket.headers) else "local"
    line = f"{ts}\tDENIED\tuser=-\tip={_client_ip(websocket)}\tsource={source}\t{reason}"
    try:
        AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT_LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError as exc:
        logger.warning("Terminal audit yazilamadi: %s", exc)


def audit(event: str, auth: TerminalAuth, detail: str = "") -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = (
        f"{ts}\t{event}\tuser={auth.user}\tip={auth.client_ip}"
        f"\tsource={auth.source}"
    )
    if auth.hub_role:
        line += f"\thub_role={auth.hub_role}"
    if detail:
        line += f"\t{detail}"
    try:
        AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT_LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError as exc:
        logger.warning("Terminal audit yazilamadi: %s", exc)


def set_winsize(fd: int, rows: int, cols: int) -> None:
    winsize = struct.pack("HHHH", rows, cols, 0, 0)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)


async def _read_pty(master_fd: int, websocket: WebSocket, last_activity: list[float]) -> None:
    loop = asyncio.get_running_loop()
    while True:
        try:
            data = await loop.run_in_executor(None, os.read, master_fd, 8192)
        except OSError:
            break
        if not data:
            break
        data = _scrub_pty_output(data)
        if not data:
            continue
        last_activity[0] = loop.time()
        await websocket.send_bytes(data)


async def _watch_timeouts(
    websocket: WebSocket,
    started: float,
    last_activity: list[float],
    stop: asyncio.Event,
) -> None:
    loop = asyncio.get_running_loop()
    while not stop.is_set():
        await asyncio.sleep(30)
        now = loop.time()
        if now - started > MAX_DURATION_SEC:
            await websocket.close(code=4000, reason="Maksimum oturum suresi doldu")
            stop.set()
            return
        if now - last_activity[0] > IDLE_TIMEOUT_SEC:
            await websocket.close(code=4001, reason="Hareketsizlik nedeniyle kapatildi")
            stop.set()
            return


async def _handle_ws_input(
    master_fd: int,
    websocket: WebSocket,
    last_activity: list[float],
    stop: asyncio.Event,
    auth: TerminalAuth,
    guard: TerminalInputGuard,
) -> None:
    loop = asyncio.get_running_loop()
    while not stop.is_set():
        try:
            message = await websocket.receive()
        except WebSocketDisconnect:
            stop.set()
            return

        if message["type"] == "websocket.disconnect":
            stop.set()
            return

        last_activity[0] = loop.time()

        if message.get("bytes"):
            payload, blocked_line = guard.feed(message["bytes"])
            if blocked_line is not None:
                audit("BLOCKED_CMD", auth, blocked_line[:120])
                try:
                    os.write(master_fd, _BLOCKED_CMD_MSG.encode("utf-8"))
                except OSError:
                    stop.set()
                    return
                continue
            if not payload:
                continue
            try:
                os.write(master_fd, payload)
            except OSError:
                stop.set()
                return
            continue

        text = message.get("text")
        if not text:
            continue

        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue

        if payload.get("type") == "resize":
            rows = int(payload.get("rows") or 24)
            cols = int(payload.get("cols") or 80)
            rows = max(2, min(rows, 200))
            cols = max(10, min(cols, 500))
            set_winsize(master_fd, rows, cols)


async def run_terminal_session(websocket: WebSocket, auth: TerminalAuth) -> None:
    ok, reason = await registry.acquire(auth.user, websocket)
    if not ok:
        await websocket.accept()
        await websocket.close(code=4003, reason=reason)
        audit("DENIED", auth, reason)
        return

    master_fd: int | None = None
    shell_pid: int | None = None
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    started = loop.time()
    last_activity = [started]
    input_guard = TerminalInputGuard()

    try:
        await websocket.accept()
        audit("OPEN", auth)

        master_fd, slave_fd = pty.openpty()
        set_winsize(master_fd, 24, 80)

        shell_pid = await loop.run_in_executor(None, _fork_shell_on_pty, slave_fd, _shell_env())
        os.close(slave_fd)

        pty_task = asyncio.create_task(_read_pty(master_fd, websocket, last_activity))
        input_task = asyncio.create_task(
            _handle_ws_input(master_fd, websocket, last_activity, stop, auth, input_guard)
        )
        timeout_task = asyncio.create_task(_watch_timeouts(websocket, started, last_activity, stop))

        done, pending = await asyncio.wait(
            {pty_task, input_task, timeout_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        stop.set()
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        await asyncio.gather(pty_task, return_exceptions=True)

    except WebSocketDisconnect:
        audit("CLOSE", auth, "disconnect")
    except Exception as exc:
        logger.exception("Terminal oturumu hatasi user=%s", auth.user)
        audit("ERROR", auth, str(exc)[:200])
        try:
            await websocket.close(code=1011, reason="Sunucu hatasi")
        except Exception:
            pass
    finally:
        await registry.release(auth.user, websocket)
        if shell_pid:
            await _terminate_pid(shell_pid)
        if master_fd is not None:
            try:
                os.close(master_fd)
            except OSError:
                pass
        audit("CLOSE", auth, "cleanup")
