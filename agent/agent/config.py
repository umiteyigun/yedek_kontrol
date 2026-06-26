"""Edge agent yapılandırması."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AgentSettings:
    org_enrollment_code: str
    hub_http_url: str
    hub_ws_url: str
    panel_local_url: str
    node_label: str
    node_role: str
    hostname: str
    state_dir: Path
    agent_token: str
    agent_id: str
    verify_tls: bool


def _read_state(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    data: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            data[k.strip()] = v.strip()
    return data


def _write_state(path: Path, data: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{k}={v}" for k, v in data.items()]
    path.write_text("\n".join(lines) + "\n")
    path.chmod(0o600)


def load_settings() -> AgentSettings:
    state_dir = Path(os.getenv("AGENT_STATE_DIR", "/var/lib/yedek-agent"))
    state_file = state_dir / "agent.env"
    state = _read_state(state_file)

    hub_http = os.getenv("HUB_HTTP_URL", "http://hub:8444").rstrip("/")
    hub_ws = os.getenv("HUB_WS_URL", "ws://hub:8444/agent/v1")

    org = os.getenv("ORG_ENROLLMENT_CODE", state.get("ORG_ENROLLMENT_CODE", ""))
    token = os.getenv("AGENT_TOKEN", state.get("AGENT_TOKEN", ""))
    agent_id = os.getenv("AGENT_ID", state.get("AGENT_ID", ""))

    import socket

    hostname = os.getenv("AGENT_HOSTNAME", socket.gethostname())

    return AgentSettings(
        org_enrollment_code=org,
        hub_http_url=hub_http,
        hub_ws_url=hub_ws,
        panel_local_url=os.getenv("PANEL_LOCAL_URL", "https://127.0.0.1:8443"),
        node_label=os.getenv("NODE_LABEL", "primary"),
        node_role=os.getenv("NODE_ROLE", "PRIMARY"),
        hostname=hostname,
        state_dir=state_dir,
        agent_token=token,
        agent_id=agent_id,
        verify_tls=os.getenv("AGENT_VERIFY_TLS", "0").strip() in {"1", "true", "yes"},
    )


def save_registration(settings: AgentSettings, agent_id: str, token: str, org_code: str) -> None:
    path = settings.state_dir / "agent.env"
    _write_state(
        path,
        {
            "AGENT_ID": agent_id,
            "AGENT_TOKEN": token,
            "ORG_ENROLLMENT_CODE": org_code,
        },
    )
