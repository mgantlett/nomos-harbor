"""Harbor agent adapters for Gemini CLI on Google Cloud Vertex AI and Nomos governance.

Provides `VertexGeminiCli` (stock `GeminiCli` with ephemeral Google Cloud ADC container
staging) and `NomosGeminiCli` (extending `VertexGeminiCli` with compiled Go `nomos hook`
interceptors, MCTS-calibrated model profiles, and tool hygiene settings).
"""

from __future__ import annotations

import json
import os
import shlex
from pathlib import Path
from typing import Any, override

from harbor.agents.installed.gemini_cli import GeminiCli
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.trajectories import Trajectory

from nomos_harbor.gemini_auth import (
    build_vertex_auth_env,
    locate_vertex_adc_path,
    read_adc_quota_project,
    stage_vertex_adc_credentials,
)
from nomos_harbor.gemini_options import (
    NomosGeminiCliOptions,
    VertexGeminiCliOptions,
    effective_advisory_kwargs,
    install_nomos_binary_in_container,
    resolve_host_nomos_binary,
    stamp_nomos_trajectory,
)
from nomos_harbor.hooks import (
    DEFAULT_REMOTE_NOMOS_BIN,
    EOL_DEBIAN_APT_REPAIR_COMMAND,
    build_gemini_tool_hygiene_settings,
    build_nomos_gemini_hook_manifest,
    build_vertex_media_guard_hook_manifest,
    merge_hook_manifests,
    parse_combined_version,
    resolve_trial_budget_seconds,
)
from nomos_harbor.mcts_profiles import (
    ModelAdvisoryProfile,
    resolve_model_advisory_profile,
)

__all__ = [
    "NomosGeminiCli",
    "NomosGeminiCliAgent",
    "NomosGeminiCliOptions",
    "VertexGeminiCli",
    "VertexGeminiCliOptions",
]


class VertexGeminiCli(GeminiCli):
    """Stock Gemini CLI with ephemeral container staging for Google Cloud Vertex AI ADC."""

    options_model = VertexGeminiCliOptions
    options: VertexGeminiCliOptions

    def _is_vertex_mode(self) -> bool:
        """Return True when the trial is routed through Google Cloud Vertex AI."""
        if self._resolve_oauth_creds_path() is not None:
            return False
        return self._resolve_env_auth_type() == "vertex-ai"

    def _resolve_vertex_adc_path(self) -> Path | None:
        """Locate host Google Application Default Credentials JSON when running in Vertex mode."""
        if not self._is_vertex_mode():
            return None
        return locate_vertex_adc_path(
            self.options.gcp_adc_path,
            self._get_env("GOOGLE_APPLICATION_CREDENTIALS"),
        )

    @staticmethod
    def _read_adc_quota_project(adc_path: Path | None) -> str | None:
        """Extract `quota_project_id` or `project_id` from an ADC JSON file if present."""
        return read_adc_quota_project(adc_path)

    def _remote_adc_path(self) -> str:
        """Return the ephemeral in-container path where Vertex ADC JSON is staged."""
        return (self._REMOTE_SECRETS_DIR / "adc.json").as_posix()

    @override
    def _resolve_auth_env(self) -> dict[str, str]:
        """Extend Harbor's Gemini auth env with Vertex project, location, and container ADC path."""
        base_env = dict(super()._resolve_auth_env())
        if not self._is_vertex_mode():
            return base_env
        adc_path = self._resolve_vertex_adc_path()
        return build_vertex_auth_env(
            base_env=base_env,
            adc_path=adc_path,
            remote_adc_path=self._remote_adc_path(),
            project_candidates=(
                self.options.gcp_project,
                base_env.get("GOOGLE_CLOUD_PROJECT"),
                self._get_env("GOOGLE_CLOUD_PROJECT"),
                os.environ.get("GOOGLE_CLOUD_PROJECT"),
                os.environ.get("CLOUDSDK_CORE_PROJECT"),
                self._read_adc_quota_project(adc_path),
            ),
            location_candidates=(
                self.options.gcp_location,
                base_env.get("GOOGLE_CLOUD_LOCATION"),
                self._get_env("GOOGLE_CLOUD_LOCATION"),
                os.environ.get("GOOGLE_CLOUD_LOCATION"),
            ),
        )

    async def _inject_vertex_adc(self, environment: BaseEnvironment) -> None:
        """Stage host ADC credentials at `/tmp/gemini-secrets/adc.json` (`0600`) for Vertex runs."""
        adc_path = self._resolve_vertex_adc_path()
        if adc_path is None:
            return
        await stage_vertex_adc_credentials(
            self,
            environment,
            adc_path=adc_path,
            remote_dir=self._REMOTE_SECRETS_DIR.as_posix(),
            remote_adc=self._remote_adc_path(),
        )

    @override
    async def ensure_system_dependencies(
        self, environment: BaseEnvironment, dependencies: tuple[str, ...]
    ) -> None:
        """Install system dependencies with automatic fallback to `archive.debian.org` on EOL Debian."""
        try:
            await super().ensure_system_dependencies(environment, dependencies)
        except Exception as first_err:
            repair = await environment.exec(command=EOL_DEBIAN_APT_REPAIR_COMMAND, user="root")
            if repair.return_code != 0:
                raise first_err
            await super().ensure_system_dependencies(environment, dependencies)

    @override
    def _build_settings_config(
        self, model: str | None = None, auth_type: str | None = None
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Optionally attach the Vertex inline-media HTTP 400 guard when `vertex_media_guard=True`."""
        base_config, model_alias = super()._build_settings_config(model, auth_type)
        if self.options.vertex_media_guard and (
            auth_type == "vertex-ai" or self._is_vertex_mode()
        ):
            seed = base_config if base_config is not None else {"experimental": {"skills": True}}
            return merge_hook_manifests(seed, build_vertex_media_guard_hook_manifest()), model_alias
        return base_config, model_alias

    @override
    async def acp_install(self, environment: BaseEnvironment) -> None:
        """Stage Vertex ADC if configured before running stock Gemini ACP install."""
        await self._inject_vertex_adc(environment)
        await super().acp_install(environment)

    @override
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        """Stage Vertex ADC before invoking `GeminiCli.run` and ensure secret teardown."""
        await self._inject_vertex_adc(environment)
        try:
            await super().run(instruction, environment, context)
        finally:
            cleanup_cmd = f"rm -rf {shlex.quote(self._REMOTE_SECRETS_DIR.as_posix())}"
            try:
                await self.exec_as_agent(environment, command=cleanup_cmd)
            except Exception as cleanup_err:
                _ = cleanup_err


class NomosGeminiCli(VertexGeminiCli):
    """Gemini CLI governed by compiled Go `nomos hook` interceptors inside Harbor trials."""

    AGENT_NAME = "nomos-gemini-cli"
    options_model = NomosGeminiCliOptions
    options: NomosGeminiCliOptions

    @staticmethod
    @override
    def name() -> str:
        """Return the distinct Harbor agent identifier for leaderboard and ATIF tracking."""
        return NomosGeminiCli.AGENT_NAME

    def __init__(self, logs_dir: Path, *args: Any, **kwargs: Any) -> None:
        """Initialize `VertexGeminiCli` and construct the deterministic Nomos Gemini hook manifest."""
        super().__init__(logs_dir, *args, **kwargs)
        self._nomos_manifest = self._current_nomos_manifest()

    def _resolved_advisory_profile(self) -> ModelAdvisoryProfile:
        """Resolve the calibrated MCTS `ModelAdvisoryProfile` for the active model."""
        return resolve_model_advisory_profile(
            model_name=getattr(self, "model_name", None),
            explicit_profile=self.options.advisory_profile,
        )

    def _current_nomos_manifest(self) -> dict[str, Any]:
        """Build the Nomos Gemini hook manifest using latest `config.json` / `lock.json` state."""
        profile = self._resolved_advisory_profile()
        effective_budget = resolve_trial_budget_seconds(
            logs_dir=self.logs_dir,
            explicit_budget_sec=self.options.budget_sec,
            auto_budget_radar=self.options.auto_budget_radar,
        )
        self._nomos_manifest = build_nomos_gemini_hook_manifest(
            nomos_bin=DEFAULT_REMOTE_NOMOS_BIN,
            hermetic_dir=self.options.hermetic_dir,
            container_sandbox=self.options.container_sandbox,
            budget_sec=effective_budget,
            gemini_taint_guidance=self.options.gemini_taint_guidance,
            **effective_advisory_kwargs(self.options, profile),
        )
        return self._nomos_manifest

    def _resolve_local_nomos_binary(self) -> Path | None:
        """Resolve an optional host-side Linux `nomos` binary for fast offline upload."""
        return resolve_host_nomos_binary(self.options.nomos_binary_path)

    @override
    def _build_settings_config(
        self, model: str | None = None, auth_type: str | None = None
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Merge tool hygiene, MCTS-calibrated Nomos hooks, and optional Vertex guards."""
        base_config, model_alias = GeminiCli._build_settings_config(self, model, auth_type)
        seed = base_config if base_config is not None else {"experimental": {"skills": True}}
        hygiene = build_gemini_tool_hygiene_settings(
            disable_web_search=self.options.disable_web_search,
            exclude_cli_help=self.options.exclude_cli_help,
        )
        merged = merge_hook_manifests(merge_hook_manifests(seed, hygiene), self._current_nomos_manifest())
        if self.options.vertex_media_guard and (
            auth_type == "vertex-ai" or self._is_vertex_mode()
        ):
            merged = merge_hook_manifests(merged, build_vertex_media_guard_hook_manifest())
        return merged, model_alias

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        """Install stock Gemini CLI, `/usr/local/bin/nomos`, and baseline `~/.gemini/settings.json`."""
        await super().install(environment)
        await install_nomos_binary_in_container(
            self,
            environment,
            local_bin=self._resolve_local_nomos_binary(),
            nomos_version=self.options.nomos_version,
            install_url=self.options.nomos_install_url,
        )
        baseline_cfg, _ = self._build_settings_config(
            model=getattr(self, "model_name", None),
            auth_type=self._resolve_env_auth_type(),
        )
        if baseline_cfg is None:
            baseline_cfg = merge_hook_manifests(
                {"experimental": {"skills": True}}, self._current_nomos_manifest()
            )
        escaped = shlex.quote(json.dumps(baseline_cfg, indent=2) + "\n")
        await self.exec_as_agent(
            environment,
            command=f"mkdir -p ~/.gemini && printf %s {escaped} > ~/.gemini/settings.json",
        )

    def _stamp_trajectory_provenance(self, trajectory: Trajectory | None) -> Trajectory | None:
        """Stamp `nomos-gemini-cli` agent identity and MCTS profile onto ATIF trajectories."""
        return stamp_nomos_trajectory(
            trajectory, self.name(), self.options, self._resolved_advisory_profile().name
        )

    @override
    def _convert_gemini_to_atif(self, gemini_trajectory: dict[str, Any]) -> Trajectory | None:
        """Preserve `nomos-gemini-cli` provenance in headless ATIF `trajectory.json`."""
        return self._stamp_trajectory_provenance(super()._convert_gemini_to_atif(gemini_trajectory))

    @override
    def convert_trajectory(self, logs_dir: Path) -> Trajectory | None:
        """Preserve `nomos-gemini-cli` provenance when `convert_trajectory` is invoked directly."""
        return self._stamp_trajectory_provenance(super().convert_trajectory(logs_dir))

    @override
    def get_version_command(self) -> str | None:
        """Query both Gemini CLI and Nomos versions for ATIF trajectory provenance."""
        quoted_nomos = shlex.quote(DEFAULT_REMOTE_NOMOS_BIN)
        return (
            "if [ -s ~/.nvm/nvm.sh ]; then . ~/.nvm/nvm.sh; fi; "
            f"gemini --version && {quoted_nomos} --version"
        )

    @override
    def parse_version(self, stdout: str) -> str:
        """Format combined `<gemini_semver>+nomos-<nomos_semver>` version string."""
        return parse_combined_version(stdout)


NomosGeminiCliAgent = NomosGeminiCli
