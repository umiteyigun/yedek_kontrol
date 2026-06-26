import json
import logging
from dataclasses import dataclass
from pathlib import Path

from app.services.oracle_probe import _run_on_host, is_instance_running

logger = logging.getLogger(__name__)

RMAN_PROBE_SCRIPT = "/yedek/config/oracle-rman-probe.sh"


@dataclass
class RmanProbeResult:
    ok: bool
    error: str = ""
    oracle_sid: str = ""
    log_mode: str = "UNKNOWN"
    archivelog: bool = False


def probe_rman_instance(oracle_sid: str) -> RmanProbeResult:
    sid = (oracle_sid or "").strip()
    if not sid:
        return RmanProbeResult(ok=False, error="SID bos", oracle_sid=sid)

    if not is_instance_running(sid):
        return RmanProbeResult(
            ok=False,
            error=f"Oracle instance ayakta degil (SID={sid})",
            oracle_sid=sid,
        )

    code, stdout, stderr = _run_on_host(Path(RMAN_PROBE_SCRIPT), sid, timeout=45)
    payload = stdout
    if not payload and stderr:
        return RmanProbeResult(ok=False, error=stderr, oracle_sid=sid)

    try:
        data = json.loads(payload.splitlines()[-1])
        return RmanProbeResult(
            ok=bool(data.get("ok")),
            error=str(data.get("error") or ""),
            oracle_sid=str(data.get("oracle_sid") or sid),
            log_mode=str(data.get("log_mode") or "UNKNOWN"),
            archivelog=bool(data.get("archivelog")),
        )
    except json.JSONDecodeError:
        snippet = (stdout or stderr)[:300]
        logger.warning("RMAN probe JSON parse hatasi sid=%s: %s", sid, snippet)
        return RmanProbeResult(ok=False, error=f"Gecersiz cevap: {snippet}", oracle_sid=sid)


def rman_runtime_map(settings_dict: dict) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for inst in settings_dict.get("instances", []):
        inst_id = str(inst.get("id", ""))
        sid = str(inst.get("oracle_sid", ""))
        probe = probe_rman_instance(sid) if sid else RmanProbeResult(ok=False, error="SID bos")
        result[inst_id] = {
            "rman_probe_ok": probe.ok,
            "rman_probe_error": probe.error,
            "log_mode": probe.log_mode,
            "archivelog": probe.archivelog,
            "archivelog_label": "Acik" if probe.archivelog else "Kapali",
        }
    return result
