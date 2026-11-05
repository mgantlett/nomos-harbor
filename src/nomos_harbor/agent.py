"""Harbor agent adapter wrapping stock Claude Code with compiled Go `nomos hook` guardrails.

Subclasses Harbor's official `ClaudeCode` agent so model credential resolution,
ACP/stream-json execution, ATIF `trajectory.json` conversion, and token/cost
accounting remain 100% identical to stock Harbor runs while injecting deterministic
Nomos pre-tool and post-tool hooks outside `/app`.
"""

from __future__ import annotations

import json
import os
import shlex
from pathlib import Path
from typing import Any, override

from harbor.agents.installed.claude_code import ClaudeCode, ClaudeCodeOptions
from harbor.environments.base import BaseEnvironment
from harbor.models.trajectories import Trajectory
from pydantic import Field, field_validator

from nomos_harbor.hooks import (
    DEFAULT_INSTALL_URL,
    DEFAULT_REMOTE_NOMOS_BIN,
    build_nomos_hook_manifest,
    build_nomos_install_command,
    merge_hook_manifests,
    parse_combined_version,
    resolve_trial_budget_seconds,
)


class NomosClaudeCodeOptions(ClaudeCodeOptions):
    """Declarative Harbor `--ak` options extending stock `ClaudeCodeOptions`."""

    nomos_version: str | None = Field(
        default=None,
        description="Optional Nomos release tag (e.g. v4.5.0) when downloading via HTTPS.",
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
    budget_sec: int | None = Field(
        default=None,
        description="Optional session wall-clock budget in seconds for 75%/90% Nomos PostToolUse advisories.",
    )
    auto_budget_radar: bool = Field(
        default=True,
        description="Automatically derive `--budget-sec` from each Harbor trial's `task.toml` timeout when `budget_sec` is unset.",
    )

    @field_validator("nomos_binary_path")
    @classmethod
    def _validate_binary_path(cls, value: str | None) -> str | None:
        """Fail closed during Harbor preflight when a custom binary path is missing."""
        if value is None or not value.strip():
            return None
        resolved = Path(value.strip()).expanduser().resolve()
        if not resolved.is_file():
            raise ValueError(f"nomos_binary_path does not exist: {resolved}")
        return str(resolved)


class NomosClaudeCode(ClaudeCode):
    """Claude Code governed by compiled Go `nomos hook` interceptors inside Harbor trials."""

    AGENT_NAME = "nomos-claude-code"
    options_model = NomosClaudeCodeOptions
    options: NomosClaudeCodeOptions

    @staticmethod
    @override
    def name() -> str:
        """Return the distinct Harbor agent identifier for leaderboard and ATIF tracking."""
        return NomosClaudeCode.AGENT_NAME

    def __init__(self, logs_dir: Path, *args: Any, **kwargs: Any) -> None:
        """Initialize `ClaudeCode` and pre-merge Nomos hooks into any explicit `--settings`."""
        super().__init__(logs_dir, *args, **kwargs)
        self._nomos_manifest = self._current_nomos_manifest()
        if self.config_source is not None:
            self._base_settings = merge_hook_manifests(
                self._base_settings,
                self._nomos_manifest,
            )

    def _current_nomos_manifest(self) -> dict[str, Any]:
        """Build the Nomos Claude hook manifest using latest `config.json` / `lock.json` state."""
        effective_budget = resolve_trial_budget_seconds(
            logs_dir=self.logs_dir,
            explicit_budget_sec=self.options.budget_sec,
            auto_budget_radar=self.options.auto_budget_radar,
        )
        self._nomos_manifest = build_nomos_hook_manifest(
            nomos_bin=DEFAULT_REMOTE_NOMOS_BIN,
            hermetic_dir=self.options.hermetic_dir,
            container_sandbox=self.options.container_sandbox,
            budget_sec=effective_budget,
        )
        return self._nomos_manifest

    def _resolve_local_nomos_binary(self) -> Path | None:
        """Resolve an optional host-side Linux `nomos` binary for fast offline upload."""
        raw_path = (
            self.options.nomos_binary_path
            or os.environ.get("NOMOS_HARBOR_BINARY")
            or ""
        ).strip()
        if not raw_path:
            return None
        candidate = Path(raw_path).expanduser().resolve()
        if not candidate.is_file():
            raise ValueError(f"nomos_binary_path does not exist: {candidate}")
        return candidate

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        """Install stock Claude Code and provision `/usr/local/bin/nomos` inside the container."""
        await super().install(environment)
        local_bin = self._resolve_local_nomos_binary()
        if local_bin is not None:
            await environment.upload_file(local_bin, DEFAULT_REMOTE_NOMOS_BIN)
            await self.exec_as_root(
                environment,
                command=(
                    f"chmod 755 {shlex.quote(DEFAULT_REMOTE_NOMOS_BIN)} && "
                    f"{shlex.quote(DEFAULT_REMOTE_NOMOS_BIN)} --version"
                ),
            )
            return

        install_cmd = build_nomos_install_command(
            nomos_version=self.options.nomos_version,
            install_url=self.options.nomos_install_url,
        )
        await self.exec_as_root(environment, command=install_cmd)

    @override
    async def _upload_base_settings(self, environment: BaseEnvironment) -> None:
        """Write merged Nomos hooks into `$CLAUDE_CONFIG_DIR/settings.json` outside `/app`."""
        await super()._upload_base_settings(environment)
        session_settings_path = (
            self.environment_logs_dir / "sessions" / "settings.json"
        ).as_posix()
        merged_session_settings = merge_hook_manifests(
            self._base_settings if self.config_source is not None else None,
            self._current_nomos_manifest(),
        )
        await self._upload_config_text(
            environment,
            content=json.dumps(merged_session_settings, indent=2) + "\n",
            remote_path=session_settings_path,
            filename="settings.json",
        )

    @override
    def convert_trajectory(self, logs_dir: Path) -> Trajectory | None:
        """Preserve `nomos-claude-code` agent provenance inside ATIF `trajectory.json`."""
        trajectory = super().convert_trajectory(logs_dir)
        if trajectory is not None and trajectory.agent is not None:
            trajectory.agent.name = self.name()
            extra = dict(trajectory.agent.extra or {})
            extra["harness_substrate"] = "nomos"
            extra["container_sandbox"] = self.options.container_sandbox
            if self.options.hermetic_dir:
                extra["hermetic_dir"] = self.options.hermetic_dir
            trajectory.agent.extra = extra
        return trajectory

    @override
    def get_version_command(self) -> str | None:
        """Query both Claude Code and Nomos versions for ATIF trajectory provenance."""
        quoted_nomos = shlex.quote(DEFAULT_REMOTE_NOMOS_BIN)
        return (
            'export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"; '
            f"claude --version && {quoted_nomos} --version"
        )

    @override
    def parse_version(self, stdout: str) -> str:
        """Format combined `<claude_semver>+nomos-<nomos_semver>` version string."""
        return parse_combined_version(stdout)


NomosClaudeCodeAgent = NomosClaudeCode
