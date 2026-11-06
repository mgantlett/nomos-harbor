"""Google Cloud Vertex AI Application Default Credentials (ADC) discovery and staging."""

from __future__ import annotations

import json
import os
import shlex
from pathlib import Path
from typing import Any

from harbor.environments.base import BaseEnvironment

from nomos_harbor.hook_constants import UTF8_ENCODING


def _first_nonempty(values: tuple[str | None, ...]) -> str | None:
    """Return the first stripped non-empty string in `values`, or `None`."""
    for item in values:
        if item and item.strip():
            return item.strip()
    return None


def read_adc_quota_project(adc_path: Path | None) -> str | None:
    """Extract `quota_project_id` or `project_id` from an ADC JSON file if present."""
    if adc_path is None:
        return None
    try:
        payload = json.loads(adc_path.read_text(encoding=UTF8_ENCODING))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    for key in ("quota_project_id", "project_id"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def locate_vertex_adc_path(
    explicit_adc: str | None, agent_env_adc: str | None
) -> Path | None:
    """Locate host Google Application Default Credentials JSON for Vertex AI trials."""
    explicit = _first_nonempty(
        (explicit_adc, agent_env_adc, os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"))
    )
    if explicit:
        candidate = Path(explicit).expanduser().resolve()
        return candidate if candidate.is_file() else None

    gcloud_cfg = _first_nonempty((os.environ.get("CLOUDSDK_CONFIG"),))
    gcloud_dir = (
        Path(gcloud_cfg).expanduser()
        if gcloud_cfg
        else (Path.home() / ".config" / "gcloud")
    )
    default_adc = gcloud_dir / "application_default_credentials.json"
    return default_adc if default_adc.is_file() else None


def build_vertex_auth_env(
    base_env: dict[str, str],
    adc_path: Path | None,
    remote_adc_path: str,
    project_candidates: tuple[str | None, ...],
    location_candidates: tuple[str | None, ...],
) -> dict[str, str]:
    """Populate `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`, ADC, and IPv4 DNS flags."""
    env = dict(base_env)
    if adc_path is not None:
        for key_var in (
            "GEMINI_API_KEY",
            "GOOGLE_API_KEY",
            "GOOGLE_GENERATIVE_AI_API_KEY",
        ):
            env.pop(key_var, None)
        env["GOOGLE_APPLICATION_CREDENTIALS"] = remote_adc_path

    project = _first_nonempty(project_candidates)
    if project:
        env["GOOGLE_CLOUD_PROJECT"] = project

    env["GOOGLE_CLOUD_LOCATION"] = _first_nonempty(location_candidates) or "global"
    existing_node_opts = env.get("NODE_OPTIONS", "").strip()
    if "--dns-result-order=" not in existing_node_opts:
        env["NODE_OPTIONS"] = (
            f"{existing_node_opts} --dns-result-order=ipv4first".strip()
        )
    return env


async def stage_vertex_adc_credentials(
    agent: Any,
    environment: BaseEnvironment,
    adc_path: Path,
    remote_dir: str,
    remote_adc: str,
) -> None:
    """Upload host ADC JSON into `remote_adc` with strict `0600` agent-owned permissions."""
    await agent.exec_as_agent(
        environment,
        command=f"mkdir -p {shlex.quote(remote_dir)}",
    )
    await environment.upload_file(adc_path, remote_adc)
    if environment.default_user is not None:
        owner = str(environment.default_user)
    else:
        uid_probe = await agent.exec_as_agent(environment, command="id -u")
        owner = (uid_probe.stdout or "").strip() or "0"
    await agent.exec_as_root(
        environment,
        command=(
            f"chown {shlex.quote(owner)} {shlex.quote(remote_adc)} && "
            f"chmod 600 {shlex.quote(remote_adc)}"
        ),
    )
