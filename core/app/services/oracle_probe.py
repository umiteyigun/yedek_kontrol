import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.config.constants import ORACLE_DIRECTORY_NAME

logger = logging.getLogger(__name__)

HOST_OUTPUT = Path(os.getenv("HOST_OUTPUT", "/host-output"))
# nsenter -m host mount namespace: /yedek/config/... host yolu
PROBE_SCRIPT_NSENTER = "/yedek/config/oracle-probe.sh"
SCHEMAS_SCRIPT_NSENTER = "/yedek/config/oracle-schemas.sh"


@dataclass
class OracleProbeResult:
    ok: bool
    error: str = ""
    oracle_sid: str = ""
    directory: str = ORACLE_DIRECTORY_NAME
    directory_path: str = ""
    directorydizini: str = ""
    yedek_dir: str = ""
    oracle_ver: str = ""
    oracle_version_full: str = ""
    hostname: str = ""
    running: bool = False

    @classmethod
    def from_dict(cls, data: dict) -> "OracleProbeResult":
        return cls(
            ok=bool(data.get("ok")),
            error=str(data.get("error") or ""),
            oracle_sid=str(data.get("oracle_sid") or ""),
            directory=str(data.get("directory") or ORACLE_DIRECTORY_NAME),
            directory_path=str(data.get("directory_path") or ""),
            directorydizini=str(data.get("directorydizini") or data.get("directory_path") or ""),
            yedek_dir=str(data.get("yedek_dir") or "").rstrip("/"),
            oracle_ver=str(data.get("oracle_ver") or ""),
            oracle_version_full=str(data.get("oracle_version_full") or ""),
            hostname=str(data.get("hostname") or ""),
            running=bool(data.get("running")),
        )


def _normalize_yedek_path(path: str) -> str:
    clean = path.strip().rstrip("/")
    return clean or "/yedek/orayedek"


def _run_on_host(script_path: Path, *args: str, timeout: int = 45) -> tuple[int, str, str]:
    """Container icinden host namespace'te script calistir (root -> su oracle)."""
    # -i: Oracle shared memory (IPC) icin zorunlu
    cmd = ["nsenter", "-t", "1", "-m", "-p", "-i", "--", str(script_path), *args]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", "Oracle sorgusu zaman asimi"
    except FileNotFoundError:
        return 127, "", "nsenter bulunamadi (container privileged+pid:host gerekli)"


def is_instance_running(oracle_sid: str) -> bool:
    """ora_pmon_<SID> process kontrolu — hizli acik/kapali durumu."""
    sid = (oracle_sid or "").strip().lower()
    if not sid:
        return False
    # cmdline tam eslesme (^$); -f tek basina shell komut satirinda false positive verir
    pattern = f"^ora_pmon_{re.escape(sid)}$"
    cmd = ["nsenter", "-t", "1", "-p", "--", "pgrep", "-f", pattern]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=8, check=False)
        return proc.returncode == 0 and bool(proc.stdout.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def instance_runtime_map(settings_dict: dict) -> dict[str, dict[str, object]]:
    """Her instance icin Oracle acik/kapali ozeti."""
    result: dict[str, dict[str, object]] = {}
    for inst in settings_dict.get("instances", []):
        inst_id = str(inst.get("id", ""))
        sid = str(inst.get("oracle_sid", ""))
        running = is_instance_running(sid)
        result[inst_id] = {
            "oracle_sid": sid,
            "oracle_running": running,
            "oracle_status": "acik" if running else "kapali",
            "oracle_status_label": "Acik" if running else "Kapali",
        }
    return result


def probe_instance(
    oracle_sid: str,
    password: str = "",
    directory_name: str = ORACLE_DIRECTORY_NAME,
) -> OracleProbeResult:
    running = is_instance_running(oracle_sid)
    if not oracle_sid:
        return OracleProbeResult(ok=False, error="SID bos", oracle_sid=oracle_sid, running=False)

    if not running:
        return OracleProbeResult(
            ok=False,
            error=f"Oracle instance ayakta degil (SID={oracle_sid})",
            oracle_sid=oracle_sid,
            running=False,
        )

    code, stdout, stderr = _run_on_host(Path(PROBE_SCRIPT_NSENTER), oracle_sid, password, directory_name)

    payload = stdout
    if not payload and stderr:
        return OracleProbeResult(ok=False, error=stderr, oracle_sid=oracle_sid)

    try:
        data = json.loads(payload.splitlines()[-1])
        result = OracleProbeResult.from_dict(data)
        result.running = running
        if not result.ok and stderr and not result.error:
            result.error = stderr
        if result.directory_path:
            result.directorydizini = _normalize_yedek_path(result.directory_path) + "/"
            result.yedek_dir = _normalize_yedek_path(result.directory_path)
        return result
    except json.JSONDecodeError:
        snippet = (stdout or stderr)[:300]
        logger.warning("Oracle probe JSON parse hatasi sid=%s: %s", oracle_sid, snippet)
        return OracleProbeResult(ok=False, error=f"Gecersiz probe cevabi: {snippet}", oracle_sid=oracle_sid)


def apply_probe_to_settings_dict(
    settings_dict: dict,
    probes: dict[str, OracleProbeResult],
    *,
    sync_directories: bool = False,
) -> dict:
    """Kilitli alanlari Oracle sonucuyla guncelle."""
    updated = dict(settings_dict)
    instances = []
    global_yedek_dir = updated.get("yedek_dir", "/yedek/orayedek")
    global_ver = updated.get("oracle_ver", "")
    global_host = updated.get("hostname", "")
    primary_yedek_dir = ""

    for inst in updated.get("instances", []):
        row = dict(inst)
        probe = probes.get(row.get("id", ""))
        if probe and probe.ok:
            row["directory"] = ORACLE_DIRECTORY_NAME
            if sync_directories or not str(row.get("directorydizini", "")).strip():
                row["directorydizini"] = probe.directorydizini or f"{probe.yedek_dir}/"
            row["oracle_sid"] = probe.oracle_sid or row.get("oracle_sid", "")
            if probe.yedek_dir and not primary_yedek_dir:
                primary_yedek_dir = probe.yedek_dir
            if probe.oracle_ver:
                global_ver = probe.oracle_ver
            if probe.hostname:
                global_host = probe.hostname
        else:
            row["directory"] = ORACLE_DIRECTORY_NAME
        instances.append(row)

    updated["instances"] = instances
    if primary_yedek_dir:
        global_yedek_dir = primary_yedek_dir
    updated["yedek_dir"] = global_yedek_dir
    updated["oracle_ver"] = global_ver
    updated["hostname"] = global_host
    return updated


@dataclass
class OracleSchemasResult:
    ok: bool
    error: str = ""
    oracle_sid: str = ""
    schemas: list[str] | None = None

    def __post_init__(self) -> None:
        if self.schemas is None:
            self.schemas = []


def list_instance_schemas(oracle_sid: str) -> OracleSchemasResult:
    sid = (oracle_sid or "").strip()
    if not sid:
        return OracleSchemasResult(ok=False, error="SID bos", oracle_sid=sid)

    if not is_instance_running(sid):
        return OracleSchemasResult(
            ok=False,
            error=f"Oracle instance ayakta degil (SID={sid})",
            oracle_sid=sid,
        )

    code, stdout, stderr = _run_on_host(Path(SCHEMAS_SCRIPT_NSENTER), sid, timeout=60)
    payload = stdout
    if not payload and stderr:
        return OracleSchemasResult(ok=False, error=stderr, oracle_sid=sid)

    try:
        data = json.loads(payload.splitlines()[-1])
        return OracleSchemasResult(
            ok=bool(data.get("ok")),
            error=str(data.get("error") or ""),
            oracle_sid=str(data.get("oracle_sid") or sid),
            schemas=[str(s).strip() for s in (data.get("schemas") or []) if str(s).strip()],
        )
    except json.JSONDecodeError:
        snippet = (stdout or stderr)[:300]
        logger.warning("Oracle schemas JSON parse hatasi sid=%s: %s", sid, snippet)
        return OracleSchemasResult(ok=False, error=f"Gecersiz cevap: {snippet}", oracle_sid=sid)


def probe_all_instances(settings_dict: dict) -> tuple[dict[str, OracleProbeResult], list[str]]:
    probes: dict[str, OracleProbeResult] = {}
    errors: list[str] = []
    for inst in settings_dict.get("instances", []):
        inst_id = inst.get("id", "")
        sid = inst.get("oracle_sid", "")
        password = inst.get("password", "")
        result = probe_instance(sid, password)
        probes[inst_id] = result
        if not result.ok:
            label = inst.get("label") or inst.get("hastane") or inst_id
            errors.append(f"{label} ({sid}): {result.error}")
    return probes, errors
