import json
import os
import re
from pathlib import Path
from typing import Any

CONFIG_DIR = Path(os.getenv("CONFIG_DIR", "/app/config"))
SETTINGS_FILE = CONFIG_DIR / "settings.json"
GENERATED_DIR = CONFIG_DIR / "generated"
ENV_OUTPUT = Path(os.getenv("ENV_OUTPUT", "/app/output/.env"))
YEDEKCONFIG_OUTPUT = Path(os.getenv("YEDEKCONFIG_OUTPUT", "/app/output/yedekconfig.sh"))
YEDEKPARAMS_OUTPUT = Path(os.getenv("YEDEKPARAMS_OUTPUT", "/app/output/yedek-params.sh"))

TEMPLATE_DIR = Path(__file__).parent / "templates" / "generate"

DEFAULT_SETTINGS: dict[str, Any] = {
    "hastane": "",
    "il": "",
    "password": "",
    "schemas": "SYSTEM",
    "hostname": "data",
    "kurumkodu": "",
    "directory": "TRTEK",
    "directorydizini": "/yedek/orayedek/",
    "oracle_ver": "11.2.0.4",
    "oracle_sid": "orcl",
    "localftpip": "127.0.0.1",
    "localftpuser": "ftp",
    "localftppass": "ftp",
    "yedek_kodu": "Hbys",
    "guid_key": "",
    "retention_days": 2,
    "remote_api_url": "",
    "yedek_dir": "/yedek/orayedek",
    "pasv_address": "127.0.0.1",
    "api_port": 8080,
    "panel_port": 8090,
}


def _merge_defaults(data: dict[str, Any]) -> dict[str, Any]:
    merged = DEFAULT_SETTINGS.copy()
    merged.update({k: v for k, v in data.items() if v is not None})
    return merged


def load_settings() -> dict[str, Any]:
    if SETTINGS_FILE.exists():
        with SETTINGS_FILE.open(encoding="utf-8") as fh:
            return _merge_defaults(json.load(fh))
    return DEFAULT_SETTINGS.copy()


def save_settings(data: dict[str, Any]) -> dict[str, Any]:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    cleaned = _merge_defaults(data)
    with SETTINGS_FILE.open("w", encoding="utf-8") as fh:
        json.dump(cleaned, fh, ensure_ascii=False, indent=2)
    return cleaned


def _render_template(name: str, context: dict[str, Any]) -> str:
    template_path = TEMPLATE_DIR / name
    content = template_path.read_text(encoding="utf-8")
    for key, value in context.items():
        content = content.replace("{{ " + key + " }}", str(value))
    return content


def _write_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def apply_settings(settings: dict[str, Any]) -> list[str]:
    """settings.json -> .env, yedekconfig.sh, yedek-params.sh"""
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    written: list[str] = []

    env_content = _render_template("env.tpl", settings)
    ENV_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    ENV_OUTPUT.write_text(env_content, encoding="utf-8")
    written.append(str(ENV_OUTPUT))

    yedekconfig = _render_template("yedekconfig.sh.tpl", settings)
    _write_executable(YEDEKCONFIG_OUTPUT, yedekconfig)
    written.append(str(YEDEKCONFIG_OUTPUT))

    yedekparams = _render_template("yedek-params.sh.tpl", settings)
    _write_executable(YEDEKPARAMS_OUTPUT, yedekparams)
    written.append(str(YEDEKPARAMS_OUTPUT))

    # mirror to generated for audit
    (GENERATED_DIR / "yedekconfig.sh").write_text(yedekconfig, encoding="utf-8")
    (GENERATED_DIR / "yedek-params.sh").write_text(yedekparams, encoding="utf-8")
    (GENERATED_DIR / ".env").write_text(env_content, encoding="utf-8")

    return written


def import_from_yedekconfig(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    current = load_settings()

    patterns = {
        "hastane": r"^hastane=(.+)$",
        "il": r"^il=(.+)$",
        "password": r"^password=(.+)$",
        "directory": r"^directory=(.+)$",
        "directorydizini": r"^directorydizini=(.+)$",
        "hostname": r"^hostname=(.+)$",
        "kurumkodu": r"^kurumkodu=(.+)$",
        "oracle_ver": r"^ORACLE_VER=(.+)$",
        "oracle_sid": r"^ORACLE_SID=(.+)$",
        "localftpip": r"^localftpip='([^']*)'",
        "localftpuser": r"^localftpuser='([^']*)'",
        "localftppass": r"^localftppass='([^']*)'",
    }

    for key, pattern in patterns.items():
        match = re.search(pattern, text, re.MULTILINE)
        if match:
            current[key] = match.group(1).strip()

    return save_settings(current)


def mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 2:
        return "*" * len(value)
    return value[0] + "*" * (len(value) - 2) + value[-1]
