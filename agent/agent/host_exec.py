"""Hub kontrollu host komutlari — panel/core bagimsiz (docker.sock / nsenter)."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shlex
from typing import Any

logger = logging.getLogger(__name__)

MAX_OUTPUT = 64 * 1024
DEFAULT_TIMEOUT = 60.0
RELEASE_TIMEOUT = 600.0

SHELL_BLOCKLIST = re.compile(
    r"(^|[;&|`\n]|\$\()\s*(reboot|shutdown|halt|poweroff|init\s+0|mkfs|dd\s+if=)",
    re.I,
)

TAG_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

ALLOWLIST: dict[str, dict[str, Any]] = {
    "docker_ps": {
        "argv": ["docker", "ps", "-a", "--format", "{{.Names}}\t{{.Status}}\t{{.Image}}"],
        "timeout": 30.0,
    },
    "docker_start_core": {
        "argv": ["docker", "start", "yedek-core"],
        "timeout": 60.0,
    },
    "docker_start_agent": {
        "argv": ["docker", "start", "yedek-central-agent"],
        "timeout": 60.0,
    },
    "docker_logs_core": {
        "argv": ["docker", "logs", "--tail", "80", "yedek-core"],
        "timeout": 30.0,
    },
    "health_curl": {
        "argv": ["curl", "-sf", "--max-time", "5", "http://127.0.0.1:8090/health"],
        "timeout": 15.0,
    },
}


def _can_nsenter() -> bool:
    return os.path.exists("/proc/1/ns/mnt") and os.path.exists("/usr/bin/nsenter")


def _wrap_host(argv: list[str]) -> list[str]:
    """Mumkunse PID 1 namespace'inde calistir (gercek host)."""
    if _can_nsenter():
        return ["nsenter", "-t", "1", "-m", "-u", "-i", "-n", "--", *argv]
    return argv


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT:
        return text
    return text[: MAX_OUTPUT - 32] + "\n...[truncated]..."


async def _run(argv: list[str], *, timeout: float, env: dict[str, str] | None = None) -> dict[str, Any]:
    cmd = _wrap_host(argv)
    logger.info("host_exec run: %s", " ".join(shlex.quote(c) for c in cmd[:12]))
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, **(env or {})},
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return {
                "ok": False,
                "exit_code": 124,
                "stdout": "",
                "stderr": f"timeout after {int(timeout)}s",
            }
    except FileNotFoundError as exc:
        return {"ok": False, "exit_code": 127, "stdout": "", "stderr": str(exc)[:300]}
    except OSError as exc:
        return {"ok": False, "exit_code": 1, "stdout": "", "stderr": str(exc)[:300]}

    code = int(proc.returncode or 0)
    return {
        "ok": code == 0,
        "exit_code": code,
        "stdout": _truncate(stdout_b.decode("utf-8", errors="replace")),
        "stderr": _truncate(stderr_b.decode("utf-8", errors="replace")),
    }


async def run_allowlist(action: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    params = params or {}
    if action == "release_force":
        tag = str(params.get("tag") or "").strip()
        if not TAG_RE.fullmatch(tag):
            return {"ok": False, "exit_code": 2, "stdout": "", "stderr": "gecersiz tag"}
        updater = "/yedek/config/release-updater.sh"
        if not os.path.exists(updater) and not _can_nsenter():
            return {
                "ok": False,
                "exit_code": 2,
                "stdout": "",
                "stderr": "release-updater.sh yok (mount / nsenter)",
            }
        env = {
            "FORCE_TAG": tag,
            "RELEASE_TRACK": "latest",
            "RELEASE_UNLOCK_LATEST": "1",
        }
        return await _run(
            ["bash", updater],
            timeout=RELEASE_TIMEOUT,
            env=env,
        )

    spec = ALLOWLIST.get(action)
    if not spec:
        return {
            "ok": False,
            "exit_code": 2,
            "stdout": "",
            "stderr": f"bilinmeyen action: {action}",
            "unsupported": True,
        }
    return await _run(list(spec["argv"]), timeout=float(spec["timeout"]))


async def run_shell(command: str, *, allow_shell: bool, timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
    if not allow_shell:
        return {
            "ok": False,
            "exit_code": 403,
            "stdout": "",
            "stderr": "shell izni yok (superadmin gerekli)",
        }
    cmd = (command or "").strip()
    if not cmd:
        return {"ok": False, "exit_code": 2, "stdout": "", "stderr": "bos komut"}
    if len(cmd) > 4000:
        return {"ok": False, "exit_code": 2, "stdout": "", "stderr": "komut cok uzun"}
    if SHELL_BLOCKLIST.search(cmd):
        return {"ok": False, "exit_code": 403, "stdout": "", "stderr": "yasakli komut"}
    to = max(5.0, min(float(timeout or DEFAULT_TIMEOUT), 120.0))
    return await _run(["bash", "-lc", cmd], timeout=to)


async def handle_host_exec(msg: dict[str, Any]) -> dict[str, Any]:
    """WS host_exec → host_exec_resp."""
    req_id = str(msg.get("id") or "")
    action = str(msg.get("action") or "").strip()
    params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
    allow_shell = bool(msg.get("allow_shell"))
    timeout = float(msg.get("timeout") or DEFAULT_TIMEOUT)

    try:
        if action == "shell":
            result = await run_shell(str(msg.get("command") or ""), allow_shell=allow_shell, timeout=timeout)
        else:
            result = await run_allowlist(action, params)
    except Exception as exc:
        logger.exception("host_exec hata action=%s", action)
        result = {"ok": False, "exit_code": 1, "stdout": "", "stderr": str(exc)[:300]}

    return {
        "type": "host_exec_resp",
        "id": req_id,
        "action": action,
        **result,
    }
