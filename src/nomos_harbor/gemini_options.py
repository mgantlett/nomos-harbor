"""Declarative Harbor `--ak` Pydantic options and container helpers for Gemini adapters."""

from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import Any

from harbor.agents.installed.gemini_cli import GeminiCliOptions
from harbor.environments.base import BaseEnvironment
from harbor.models.trajectories import Trajectory
from pydantic import Field, field_validator

from nomos_harbor.hook_constants import (
    DEFAULT_INSTALL_URL,
    DEFAULT_REMOTE_NOMOS_BIN,
    LOGS_ROOT_HYGIENE_COMMAND,
)
from nomos_harbor.hooks import build_nomos_install_command
from nomos_harbor.mcts_models import (
    ModelAdvisoryProfile,
    resolve_model_advisory_profile,
)


def validate_existing_file_option(value: str | None, field_name: str) -> str | None:
    """Fail closed when an optional host file path is non-empty and missing."""
    if value is None or not value.strip():
        return None
    resolved = Path(value.strip()).expanduser().resolve()
    if not resolved.is_file():
        raise ValueError(f"{field_name} does not exist: {resolved}")
    return str(resolved)


def validate_advisory_profile_option(value: str | None) -> str | None:
    """Fail closed when an unknown MCTS advisory profile identifier is provided."""
    if value is None or not value.strip():
        return None
    return resolve_model_advisory_profile(None, value).name


def resolve_host_nomos_binary(configured_path: str | None) -> Path | None:
    """Resolve an optional host-side Linux `nomos` binary from `--ak` or `NOMOS_HARBOR_BINARY`."""
    raw_path = (configured_path or os.environ.get("NOMOS_HARBOR_BINARY") or "").strip()
    if not raw_path:
        return None
    validated = validate_existing_file_option(raw_path, "nomos_binary_path")
    return Path(validated) if validated else None


def effective_advisory_kwargs(
    options: Any, profile: ModelAdvisoryProfile
) -> dict[str, Any]:
    """Merge explicit `--ak` advisory overrides atop a calibrated `ModelAdvisoryProfile`."""
    return {
        "budget_warn_pct": (
            options.budget_warn_pct
            if options.budget_warn_pct is not None
            else profile.budget_warn_pct
        ),
        "budget_crit_pct": (
            options.budget_crit_pct
            if options.budget_crit_pct is not None
            else profile.budget_crit_pct
        ),
        "loop_strikes": (
            options.loop_strikes
            if options.loop_strikes is not None
            else profile.loop_strikes
        ),
        "advisory_style": (
            options.advisory_style
            if options.advisory_style is not None
            else profile.advisory_style
        ),
    }


async def install_nomos_binary_in_container(
    agent: Any,
    environment: BaseEnvironment,
    local_bin: Path | None,
    nomos_version: str | None,
    install_url: str,
) -> None:
    """Upload a local static `nomos` binary or install via HTTPS inside `environment`."""
    if local_bin is not None:
        await environment.upload_file(local_bin, DEFAULT_REMOTE_NOMOS_BIN)
        quoted = shlex.quote(DEFAULT_REMOTE_NOMOS_BIN)
        await agent.exec_as_root(
            environment,
            command=f"{LOGS_ROOT_HYGIENE_COMMAND}; chmod 755 {quoted} && {quoted} --version",
        )
        return
    install_cmd = build_nomos_install_command(
        nomos_version=nomos_version,
        install_url=install_url,
    )
    await agent.exec_as_root(
        environment,
        command=f"{LOGS_ROOT_HYGIENE_COMMAND}; {install_cmd}",
    )


def stamp_nomos_trajectory(
    trajectory: Trajectory | None,
    agent_name: str,
    options: Any,
    profile_name: str,
) -> Trajectory | None:
    """Stamp agent provenance and active MCTS advisory profile onto an ATIF `Trajectory`."""
    if trajectory is not None and trajectory.agent is not None:
        trajectory.agent.name = agent_name
        extra = dict(trajectory.agent.extra or {})
        extra["harness_substrate"] = "nomos"
        extra["container_sandbox"] = options.container_sandbox
        extra["advisory_profile"] = profile_name
        if options.hermetic_dir:
            extra["hermetic_dir"] = options.hermetic_dir
        trajectory.agent.extra = extra
    return trajectory


class VertexGeminiCliOptions(GeminiCliOptions):
    """Declarative Harbor `--ak` options adding Google Cloud Vertex ADC container staging."""

    gcp_project: str | None = Field(
        default=None,
        description="Optional Google Cloud project ID override for `vertex_ai/*` runs.",
    )
    gcp_location: str | None = Field(
        default=None,
        description="Optional Google Cloud Vertex location override (defaults to `global` in Vertex mode).",
    )
    gcp_adc_path: str | None = Field(
        default=None,
        description="Optional host path to Google Application Default Credentials JSON.",
    )
    vertex_media_guard: bool = Field(
        default=False,
        description="Opt-in BeforeTool guard preventing Gemini CLI Vertex HTTP 400 crashes on binary read_file.",
    )

    @field_validator("gcp_adc_path")
    @classmethod
    def _validate_adc_path(cls, value: str | None) -> str | None:
        """Fail closed during Harbor preflight when an explicit ADC file path is missing."""
        return validate_existing_file_option(value, "gcp_adc_path")


class NomosGeminiCliOptions(VertexGeminiCliOptions):
    """Declarative Harbor `--ak` options extending `VertexGeminiCliOptions` with Nomos hooks."""

    nomos_version: str | None = Field(
        default=None,
        description="Optional Nomos release tag (e.g. v4.8.0) when downloading via HTTPS.",
    )
    nomos_binary_path: str | None = Field(
        default=None,
        description="Optional host path to a prebuilt static Linux `nomos` binary.",
    )
    nomos_install_url: str = Field(
        default=DEFAULT_INSTALL_URL,
        description="HTTPS installer script URL when `nomos_binary_path` is not set.",
    )
    hermetic_dir: str | None = Field(
        default=None,
        description="Optional strict workspace root (`--hermetic-dir`) overriding `--container-sandbox`.",
    )
    container_sandbox: bool = Field(
        default=True,
        description="Enable `--container-sandbox` anti-reward-hacking mode when `hermetic_dir` is unset.",
    )
    vertex_media_guard: bool = Field(
        default=True,
        description="Enable BeforeTool guard preventing Gemini CLI Vertex HTTP 400 crashes on binary read_file.",
    )
    disable_web_search: bool = Field(
        default=True,
        description="Exclude `google_web_search` and `web_fetch` in Gemini `settings.json` by default.",
    )
    exclude_cli_help: bool = Field(
        default=True,
        description="Disable Gemini CLI's `cli_help` subagent (`agents.overrides.cli_help.enabled=false` and `tools.exclude`).",
    )
    gemini_taint_guidance: bool = Field(
        default=True,
        description="Inject concise SessionStart guidance for headless Gemini CLI `<untrusted_context>` shell path checks.",
    )
    budget_sec: int | None = Field(
        default=None,
        description="Optional session wall-clock budget in seconds for Nomos AfterTool advisories.",
    )
    auto_budget_radar: bool = Field(
        default=True,
        description="Automatically derive `--budget-sec` from each Harbor trial's `task.toml` timeout when `budget_sec` is unset.",
    )
    advisory_profile: str | None = Field(
        default=None,
        description="Optional MCTS-calibrated advisory profile override (`gemini`, `claude`, `gpt`, `default`).",
    )
    budget_warn_pct: int | None = Field(
        default=None,
        description="Optional budget warning checkpoint percentage [25..90] overriding the model profile.",
    )
    budget_crit_pct: int | None = Field(
        default=None,
        description="Optional budget critical checkpoint percentage [50..99] overriding the model profile.",
    )
    loop_strikes: int | None = Field(
        default=None,
        description="Optional identical-failure loop circuit-breaker threshold [2..10] overriding the model profile.",
    )
    advisory_style: str | None = Field(
        default=None,
        description="Optional AfterTool advisory style (`standard` or `terse`) overriding the model profile.",
    )

    @field_validator("nomos_binary_path")
    @classmethod
    def _validate_nomos_binary_path(cls, value: str | None) -> str | None:
        """Fail closed during Harbor preflight when an explicit binary file path is missing."""
        return validate_existing_file_option(value, "nomos_binary_path")

    @field_validator("advisory_profile")
    @classmethod
    def _validate_advisory_profile(cls, value: str | None) -> str | None:
        """Fail closed during Harbor preflight when an unknown profile name is supplied."""
        return validate_advisory_profile_option(value)
