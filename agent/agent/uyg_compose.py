"""Hub secimini /yedek/uyg/docker-compose.yml dosyasina yazar.

Proje adi uyg. Etiket sabittir. Imaj makinede yoksa cekilmez.
Yedek compose dosyasina dokunulmaz.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
from pathlib import Path

logger = logging.getLogger("yedek-agent.uyg")

COMPOSE_PATH = "/yedek/uyg/docker-compose.yml"
_TEMP_PATH = "/yedek/config/.uyg-compose.yml.tmp"
_DIR = "/yedek/uyg"

_IMAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@-]{0,240}$")
_TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")
_ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_PORT_RE = re.compile(r"^[0-9]{1,5}$")
_NAME_RE = re.compile(r"^uyg-[A-Za-z0-9][A-Za-z0-9_.-]{0,80}$")

_compose_bin: list[str] | None = None


def _uyg_enabled() -> bool:
    from agent.metrics import configured_products

    return "uyg" in configured_products()


def _nsenter_ok() -> bool:
    return os.path.exists("/usr/bin/nsenter") and os.path.exists("/proc/1/ns/mnt")


def _host(argv: list[str], timeout: float = 60) -> dict[str, object]:
    if not _nsenter_ok():
        return {"ok": False, "code": 127, "stdout": "", "stderr": "nsenter yok"}
    cmd = ["nsenter", "-t", "1", "-m", "-u", "-i", "-n", "--", *argv]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": 124, "stdout": "", "stderr": "timeout"}
    except OSError as exc:
        return {"ok": False, "code": 1, "stdout": "", "stderr": str(exc)[:200]}
    return {
        "ok": proc.returncode == 0,
        "code": int(proc.returncode or 0),
        "stdout": proc.stdout or "",
        "stderr": (proc.stderr or "")[:400],
    }


def pin_image(image: str, version: str) -> str:
    image = (image or "").strip()
    version = (version or "").strip()
    if not _IMAGE_RE.fullmatch(image) or not _TAG_RE.fullmatch(version):
        return ""
    if "/" in image:
        head, last = image.rsplit("/", 1)
        if ":" in last:
            last = last.split(":", 1)[0]
        base = f"{head}/{last}"
    elif ":" in image:
        base = image.split(":", 1)[0]
    else:
        base = image
    pinned = f"{base}:{version}"
    if not _IMAGE_RE.fullmatch(pinned):
        return ""
    return pinned


def _clean_env(raw: object) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    env: dict[str, str] = {}
    for key, value in raw.items():
        name = str(key or "").strip()
        if not _ENV_KEY.fullmatch(name):
            continue
        text = str(value or "").replace("\x00", "").replace("\r", "").replace("\n", "").strip()
        if not text or len(text) > 500:
            continue
        env[name] = text
    return env


def _publish_port(env: dict[str, str]) -> str:
    port = env.get("port", "")
    if not _PORT_RE.fullmatch(port):
        return ""
    number = int(port)
    if number < 1 or number > 65535:
        return ""
    return port


def render_compose(repos: list) -> tuple[str, list[dict[str, str]], list[str], bool]:
    """yaml, pinned services, etiketsiz id'ler, acik bos secim."""
    pinned: list[dict[str, str]] = []
    held: list[str] = []
    seen: set[str] = set()
    for raw in repos:
        if not isinstance(raw, dict):
            continue
        service_id = str(raw.get("id") or "").strip()
        if not _ID_RE.fullmatch(service_id) or service_id in seen:
            continue
        seen.add(service_id)
        image = pin_image(str(raw.get("image") or ""), str(raw.get("version") or ""))
        if not image:
            held.append(service_id)
            continue
        env = _clean_env(raw.get("env"))
        port = _publish_port(env)
        pinned.append({"id": service_id, "image": image, "port": port, "env": env})
    pinned.sort(key=lambda item: item["id"])
    lines = ["name: uyg", "services:"]
    if not pinned:
        lines = ["name: uyg", "services: {}"]
    for item in pinned:
        lines.append(f"  {item['id']}:")
        lines.append(f"    image: {item['image']}")
        lines.append(f"    container_name: uyg-{item['id']}")
        lines.append("    restart: unless-stopped")
        env = item["env"]
        if env:
            lines.append("    environment:")
            for key in sorted(env):
                lines.append(f"      {key}: {json.dumps(env[key], ensure_ascii=False)}")
        if item["port"]:
            lines.append("    ports:")
            lines.append(f"      - {json.dumps(item['port'] + ':' + item['port'])}")
    text = "\n".join(lines) + "\n"
    return text, pinned, held, not repos


def _compose() -> list[str] | None:
    global _compose_bin
    if _compose_bin is not None:
        return _compose_bin or None
    if _host(["docker", "compose", "version"], timeout=20)["ok"]:
        _compose_bin = ["docker", "compose"]
    elif _host(["docker-compose", "version"], timeout=20)["ok"]:
        _compose_bin = ["docker-compose"]
    else:
        _compose_bin = []
        logger.warning("uyg docker compose yok")
    return _compose_bin or None


def _read() -> str | None:
    result = _host(["cat", COMPOSE_PATH], timeout=10)
    if not result["ok"]:
        return None
    return str(result["stdout"])


def _write(text: str) -> None:
    path = Path(_TEMP_PATH)
    path.write_text(text, encoding="utf-8")
    os.chmod(path, 0o600)
    try:
        made = _host(["mkdir", "-p", _DIR], timeout=15)
        if not made["ok"]:
            raise RuntimeError(str(made["stderr"])[:200] or "dizin yok")
        _host(["chmod", "700", _DIR], timeout=15)
        installed = _host(
            ["install", "-m", "600", "-o", "root", "-g", "root", _TEMP_PATH, COMPOSE_PATH],
            timeout=15,
        )
        if not installed["ok"]:
            raise RuntimeError(str(installed["stderr"])[:200] or "dosya yazilamadi")
    finally:
        try:
            path.unlink()
        except OSError:
            pass


def _version_of(image: str) -> str:
    last = image.rsplit("/", 1)[-1]
    if ":" not in last:
        return ""
    return last.rsplit(":", 1)[-1]


def _containers() -> list[dict[str, str]]:
    result = _host(
        [
            "docker",
            "ps",
            "-a",
            "--filter",
            "label=com.docker.compose.project=uyg",
            "--format",
            "{{.Label \"com.docker.compose.service\"}}\t{{.Names}}\t{{.Image}}\t{{.Status}}",
        ],
        timeout=20,
    )
    if not result["ok"]:
        return []
    rows: list[dict[str, str]] = []
    for line in str(result["stdout"]).splitlines():
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        service, name, image, status = (part.strip() for part in parts[:4])
        if not _ID_RE.fullmatch(service) or not _NAME_RE.fullmatch(name):
            continue
        rows.append(
            {
                "service": service,
                "name": name,
                "image": image,
                "status": status,
                "version": _version_of(image),
                "up": "1" if status.startswith("Up") else "",
            }
        )
    return rows


def _remove(name: str) -> None:
    if not _NAME_RE.fullmatch(name):
        return
    result = _host(["docker", "rm", "-f", name], timeout=60)
    if result["ok"]:
        logger.info("uyg kaldirildi: %s", name)
    else:
        logger.warning("uyg kaldirilamadi: %s code=%s", name, result["code"])
    poke_status()


def _up(service_ids: list[str], *, orphans: bool) -> None:
    compose = _compose()
    if not compose:
        return
    argv = [*compose, "-p", "uyg", "-f", COMPOSE_PATH, "up", "-d", "--no-deps"]
    if orphans:
        argv.append("--remove-orphans")
    argv.extend(service_ids)
    result = _host(argv, timeout=600)
    if result["ok"]:
        logger.info("uyg compose up services=%s", ",".join(service_ids))
    else:
        detail = ""
        lines = str(result["stderr"]).splitlines()
        if lines:
            detail = lines[-1][:200]
        logger.warning("uyg compose up olmadi code=%s %s", result["code"], detail)
    poke_status()


def _down() -> None:
    compose = _compose()
    if not compose:
        return
    result = _host(
        [*compose, "-p", "uyg", "-f", COMPOSE_PATH, "down", "--remove-orphans"],
        timeout=120,
    )
    if result["ok"]:
        logger.info("uyg durduruldu")
    else:
        logger.warning("uyg durdurulamadi code=%s", result["code"])
    poke_status()


def apply_repos(repos: list) -> None:
    if not _uyg_enabled():
        return
    if not isinstance(repos, list):
        logger.warning("uyg sync bozuk, dokunulmadi")
        return
    if not _nsenter_ok():
        logger.warning("uyg nsenter yok, dosya yazilmadi")
        return
    text, pinned, held, clear = render_compose(repos)
    if repos and not pinned and not held:
        logger.warning("uyg sync bozuk, dokunulmadi")
        return
    if held and not pinned:
        logger.warning("uyg etiket yok, dosya duruyor: %s", ",".join(held))
        return
    current = _read()
    if text != current:
        _write(text)
        logger.info("uyg dosya yazildi services=%s", ",".join(item["id"] for item in pinned) or "-")
    containers = _containers()
    keep = {item["id"] for item in pinned} | set(held)
    for item in containers:
        if item["service"] not in keep:
            _remove(item["name"])
    if clear:
        if containers:
            _down()
        return
    runnable = [item["id"] for item in pinned]
    if not runnable:
        return
    running = {item["service"] for item in containers if item["up"] and item["service"] in keep}
    extras = [item for item in containers if item["service"] not in keep]
    if text == current and set(runnable) == running and not extras:
        return
    _up(runnable, orphans=not held)


def uyg_report() -> dict[str, list]:
    if not _uyg_enabled():
        return {"repos": []}
    try:
        rows = [
            {
                "id": item["service"],
                "version": item["version"],
                "status": "running" if item["up"] else "stopped",
            }
            for item in _containers()
        ]
    except Exception:
        logger.warning("uyg durum okunamadi")
        return {"repos": []}
    return {"repos": rows}


class UygSync:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._latest: list | None = None
        self._last: list | None = None
        self._busy = False
        self.changed = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None

    async def submit(self, repos: list) -> None:
        async with self._lock:
            self._latest = list(repos)
            self._last = list(repos)
            if self._busy:
                return
            self._busy = True
        await self._drain()

    async def recheck(self) -> None:
        if self._last is None:
            return
        await self.submit(self._last)

    async def _drain(self) -> None:
        while True:
            async with self._lock:
                batch = self._latest
                self._latest = None
                if batch is None:
                    self._busy = False
                    return
            try:
                await asyncio.to_thread(apply_repos, batch)
            except Exception:
                logger.exception("uyg compose uygulanamadi")


def poke_status() -> None:
    loop = SYNC._loop
    if loop is None or loop.is_closed():
        return
    loop.call_soon_threadsafe(SYNC.changed.set)


SYNC = UygSync()
