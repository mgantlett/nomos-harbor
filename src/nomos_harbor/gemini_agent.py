"""Harbor agent adapters for Gemini CLI on Google Cloud Vertex AI and Nomos governance.

Provides two clean subclasses of Harbor's official `GeminiCli` agent:
1. `VertexGeminiCli`: Stock `GeminiCli` (`agent.name = "gemini-cli"`) extended strictly with
   ephemeral container staging of host Google Application Default Credentials (`gcloud` ADC)
   so paired Control baselines can run against GCP Vertex AI credits inside Docker.
2. `NomosGeminiCli`: Extends `VertexGeminiCli` (`agent.name = "nomos-gemini-cli"`) by
   installing `/usr/local/bin/nomos` and additively merging deterministic `BeforeTool` and
   `AfterTool` Nomos hooks into `~/.gemini/settings.json` outside `/app`.
"""

from __future__ import annotations

import json
import os
import shlex
from pathlib import Path
from typing import Any, override

from harbor.agents.installed.gemini_cli import GeminiCli, GeminiCliOptions
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.trajectories import Trajectory
from pydantic import Field, field_validator

from nomos_harbor.hooks import (
    DEFAULT_INSTALL_URL,
    DEFAULT_REMOTE_NOMOS_BIN,
    EOL_DEBIAN_APT_REPAIR_COMMAND,
    build_nomos_gemini_hook_manifest,
    build_nomos_install_command,
    build_vertex_media_guard_hook_manifest,
    merge_hook_manifests,
    parse_combined_version,
    resolve_trial_budget_seconds,
)


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
        if value is None or not value.strip():
            return None
        resolved = Path(value.strip()).expanduser().resolve()
        if not resolved.is_file():
            raise ValueError(f"gcp_adc_path does not exist: {resolved}")
        return str(resolved)


class NomosGeminiCliOptions(VertexGeminiCliOptions):
    """Declarative Harbor `--ak` options extending `VertexGeminiCliOptions` with Nomos hooks."""

    nomos_version: str | None = Field(
        default=None,
        description="Optional Nomos release tag (e.g. v4.5.1) when downloading via HTTPS.",
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
    budget_sec: int | None = Field(
        default=None,
        description="Optional session wall-clock budget in seconds for 75%/90% Nomos AfterTool advisories.",
    )
    auto_budget_radar: bool = Field(
        default=True,
        description="Automatically derive `--budget-sec` from each Harbor trial's `task.toml` timeout when `budget_sec` is unset.",
    )

    @field_validator("nomos_binary_path")
    @classmethod
    def _validate_nomos_binary_path(cls, value: str | None) -> str | None:
        """Fail closed during Harbor preflight when an explicit binary file path is missing."""
        if value is None or not value.strip():
            return None
        resolved = Path(value.strip()).expanduser().resolve()
        if not resolved.is_file():
            raise ValueError(f"nomos_binary_path does not exist: {resolved}")
        return str(resolved)


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
        explicit = (
            self.options.gcp_adc_path
            or self._get_env("GOOGLE_APPLICATION_CREDENTIALS")
            or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
            or ""
        ).strip()
        if explicit:
            candidate = Path(explicit).expanduser().resolve()
            return candidate if candidate.is_file() else None
        gcloud_dir = Path(
            os.environ.get("CLOUDSDK_CONFIG")
            or (Path.home() / ".config" / "gcloud")
        ).expanduser()
        default_adc = gcloud_dir / "application_default_credentials.json"
        return default_adc if default_adc.is_file() else None

    @staticmethod
    def _read_adc_quota_project(adc_path: Path | None) -> str | None:
        """Extract `quota_project_id` or `project_id` from an ADC JSON file if present."""
        if adc_path is None:
            return None
        try:
            payload = json.loads(adc_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        for key in ("quota_project_id", "project_id"):
            val = payload.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        return None

    def _remote_adc_path(self) -> str:
        """Return the ephemeral in-container path where Vertex ADC JSON is staged."""
        return (self._REMOTE_SECRETS_DIR / "adc.json").as_posix()

    @override
    def _resolve_auth_env(self) -> dict[str, str]:
        """Extend Harbor's Gemini auth env with Vertex project, location, and container ADC path."""
        env = dict(super()._resolve_auth_env())
        if not self._is_vertex_mode():
            return env

        adc_path = self._resolve_vertex_adc_path()
        if adc_path is not None:
            for key_var in (
                "GEMINI_API_KEY",
                "GOOGLE_API_KEY",
                "GOOGLE_GENERATIVE_AI_API_KEY",
            ):
                env.pop(key_var, None)
            env["GOOGLE_APPLICATION_CREDENTIALS"] = self._remote_adc_path()

        project = (
            self.options.gcp_project
            or env.get("GOOGLE_CLOUD_PROJECT")
            or self._get_env("GOOGLE_CLOUD_PROJECT")
            or os.environ.get("GOOGLE_CLOUD_PROJECT")
            or os.environ.get("CLOUDSDK_CORE_PROJECT")
            or self._read_adc_quota_project(adc_path)
        )
        if project:
            env["GOOGLE_CLOUD_PROJECT"] = project

        location = (
            self.options.gcp_location
            or env.get("GOOGLE_CLOUD_LOCATION")
            or self._get_env("GOOGLE_CLOUD_LOCATION")
            or os.environ.get("GOOGLE_CLOUD_LOCATION")
            or "global"
        )
        env["GOOGLE_CLOUD_LOCATION"] = location
        existing_node_opts = env.get("NODE_OPTIONS", "").strip()
        if "--dns-result-order=" not in existing_node_opts:
            env["NODE_OPTIONS"] = (
                f"{existing_node_opts} --dns-result-order=ipv4first".strip()
            )
        return env

    async def _inject_vertex_adc(self, environment: BaseEnvironment) -> None:
        """Stage host ADC credentials at `/tmp/gemini-secrets/adc.json` (`0600`) for Vertex runs."""
        adc_path = self._resolve_vertex_adc_path()
        if adc_path is None:
            return
        remote_dir = self._REMOTE_SECRETS_DIR.as_posix()
        remote_adc = self._remote_adc_path()
        await self.exec_as_agent(
            environment,
            command=f"mkdir -p {shlex.quote(remote_dir)}",
        )
        await environment.upload_file(adc_path, remote_adc)
        if environment.default_user is not None:
            await self.exec_as_root(
                environment,
                command=f"chown {environment.default_user} {shlex.quote(remote_adc)}",
            )
        await self.exec_as_agent(
            environment,
            command=f"chmod 600 {shlex.quote(remote_adc)}",
        )

    @override
    async def ensure_system_dependencies(
        self,
        environment: BaseEnvironment,
        dependencies: tuple[str, ...],
    ) -> None:
        """Install system dependencies with automatic fallback to `archive.debian.org` on EOL Debian."""
        try:
            await super().ensure_system_dependencies(environment, dependencies)
        except Exception as first_err:
            repair = await environment.exec(
                command=EOL_DEBIAN_APT_REPAIR_COMMAND,
                user="root",
            )
            if repair.return_code != 0:
                raise first_err
            await super().ensure_system_dependencies(environment, dependencies)

    @override
    def _build_settings_config(
        self,
        model: str | None = None,
        auth_type: str | None = None,
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Optionally attach the Vertex inline-media HTTP 400 guard when `vertex_media_guard=True`."""
        base_config, model_alias = super()._build_settings_config(model, auth_type)
        if self.options.vertex_media_guard and (
            auth_type == "vertex-ai" or self._is_vertex_mode()
        ):
            seed = (
                base_config
                if base_config is not None
                else {"experimental": {"skills": True}}
            )
            return merge_hook_manifests(
                seed, build_vertex_media_guard_hook_manifest()
            ), model_alias
        return base_config, model_alias

    @override
    async def acp_install(self, environment: BaseEnvironment) -> None:
        """Stage Vertex ADC if configured before running stock Gemini ACP install."""
        await self._inject_vertex_adc(environment)
        await super().acp_install(environment)

    @override
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        """Stage Vertex ADC before invoking `GeminiCli.run` and ensure secret teardown."""
        await self._inject_vertex_adc(environment)
        try:
            await super().run(instruction, environment, context)
        finally:
            try:
                await self.exec_as_agent(
                    environment,
                    command=f"rm -rf {shlex.quote(self._REMOTE_SECRETS_DIR.as_posix())}",
                )
            except Exception:
                pass


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
        effective_budget = resolve_trial_budget_seconds(
            logs_dir=logs_dir,
            explicit_budget_sec=self.options.budget_sec,
            auto_budget_radar=self.options.auto_budget_radar,
        )
        self._nomos_manifest = build_nomos_gemini_hook_manifest(
            nomos_bin=DEFAULT_REMOTE_NOMOS_BIN,
            hermetic_dir=self.options.hermetic_dir,
            container_sandbox=self.options.container_sandbox,
            budget_sec=effective_budget,
        )

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
    def _build_settings_config(
        self,
        model: str | None = None,
        auth_type: str | None = None,
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Merge Nomos `BeforeTool`/`AfterTool` hooks first, followed by optional Vertex guards."""
        base_config, model_alias = GeminiCli._build_settings_config(
            self, model, auth_type
        )
        seed = (
            base_config
            if base_config is not None
            else {"experimental": {"skills": True}}
        )
        merged = merge_hook_manifests(seed, self._nomos_manifest)
        if self.options.vertex_media_guard and (
            auth_type == "vertex-ai" or self._is_vertex_mode()
        ):
            merged = merge_hook_manifests(
                merged, build_vertex_media_guard_hook_manifest()
            )
        return merged, model_alias

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        """Install stock Gemini CLI, `/usr/local/bin/nomos`, and baseline `~/.gemini/settings.json`."""
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
        else:
            install_cmd = build_nomos_install_command(
                nomos_version=self.options.nomos_version,
                install_url=self.options.nomos_install_url,
            )
            await self.exec_as_root(environment, command=install_cmd)

        baseline = merge_hook_manifests(
            {"experimental": {"skills": True}},
            self._nomos_manifest,
        )
        escaped = shlex.quote(json.dumps(baseline, indent=2) + "\n")
        await self.exec_as_agent(
            environment,
            command=f"mkdir -p ~/.gemini && printf %s {escaped} > ~/.gemini/settings.json",
        )

    def _stamp_trajectory_provenance(
        self, trajectory: Trajectory | None
    ) -> Trajectory | None:
        """Stamp `nomos-gemini-cli` agent identity and sandbox metadata onto ATIF trajectories."""
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
    def _convert_gemini_to_atif(
        self, gemini_trajectory: dict[str, Any]
    ) -> Trajectory | None:
        """Preserve `nomos-gemini-cli` provenance in headless ATIF `trajectory.json`."""
        return self._stamp_trajectory_provenance(
            super()._convert_gemini_to_atif(gemini_trajectory)
        )

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
