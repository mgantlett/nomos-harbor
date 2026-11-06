"""Unit and Harbor agent integration tests for `nomos_harbor` hooks and adapters."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

PACKAGE_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PACKAGE_SRC) not in sys.path:
    sys.path.insert(0, str(PACKAGE_SRC))

from nomos_harbor.hook_constants import HOOK_KEY_COMMAND  # noqa: E402
from nomos_harbor.hooks import (  # noqa: E402
    DEFAULT_REMOTE_NOMOS_BIN,
    build_claude_web_search_guard_settings,
    build_gemini_tool_hygiene_settings,
    build_nomos_gemini_hook_manifest,
    build_nomos_hook_manifest,
    build_nomos_install_command,
    merge_hook_manifests,
    parse_combined_version,
    resolve_trial_budget_seconds,
)


def _write_fake_nomos_bin(tmp_path: Path) -> Path:
    """Create a stub executable `nomos` script for Harbor preflight and upload tests."""
    fake_bin = tmp_path / "nomos-static"
    fake_bin.write_bytes(b"#!/bin/sh\necho 'nomos version 4.8.0'\n")
    return fake_bin


class TestNomosHarborHooks(unittest.TestCase):
    """Verify pure-Python hook manifest synthesis, additive chaining, and version parsing."""

    def test_build_manifest_defaults_and_scoped_flags(self) -> None:
        """Ensure default Harbor manifest uses `--container-sandbox` and matches `Bash`."""
        default_manifest = build_nomos_hook_manifest(DEFAULT_REMOTE_NOMOS_BIN)
        pre = default_manifest["hooks"]["PreToolUse"][0]["hooks"][0][HOOK_KEY_COMMAND]
        post_group = default_manifest["hooks"]["PostToolUse"][0]
        self.assertEqual(pre, "/usr/local/bin/nomos hook pre-tool --container-sandbox")
        self.assertEqual(
            post_group["hooks"][0][HOOK_KEY_COMMAND],
            "/usr/local/bin/nomos hook post-tool --container-sandbox",
        )
        self.assertIn("Bash|", post_group["matcher"])

        scoped = build_nomos_hook_manifest(
            DEFAULT_REMOTE_NOMOS_BIN, "/app", True, 900, 70, 88, 4, "terse"
        )
        self.assertEqual(
            scoped["hooks"]["PreToolUse"][0]["hooks"][0][HOOK_KEY_COMMAND],
            "/usr/local/bin/nomos hook pre-tool --hermetic-dir /app --budget-sec 900 --loop-strikes 4",
        )
        self.assertIn(
            "--budget-warn-pct 70 --budget-crit-pct 88 --advisory-style terse",
            scoped["hooks"]["PostToolUse"][0]["hooks"][0][HOOK_KEY_COMMAND],
        )

    def test_build_gemini_manifest_and_taint_guidance(self) -> None:
        """Ensure Gemini CLI manifest enables `hooksConfig`, `run_shell_command`, and taint guidance."""
        manifest = build_nomos_gemini_hook_manifest(DEFAULT_REMOTE_NOMOS_BIN)
        self.assertTrue(manifest["hooksConfig"]["enabled"])
        self.assertEqual(manifest["hooks"]["BeforeTool"][0]["hooks"][0]["timeout"], 10000)
        self.assertIn("run_shell_command|", manifest["hooks"]["AfterTool"][0]["matcher"])
        self.assertNotIn("SessionStart", manifest["hooks"])

        guided = build_nomos_gemini_hook_manifest(
            DEFAULT_REMOTE_NOMOS_BIN, None, True, 1200, 70, 88, None, "terse", True
        )
        self.assertIn("SessionStart", guided["hooks"])
        self.assertIn(
            "--budget-warn-pct 70 --budget-crit-pct 88 --advisory-style terse",
            guided["hooks"]["AfterTool"][0]["hooks"][0][HOOK_KEY_COMMAND],
        )

    def test_tool_hygiene_and_merge_hook_manifests_are_idempotent(self) -> None:
        """Ensure Gemini/Claude tool hygiene blocks merge additively without duplicates."""
        existing = {
            "env": {"FOO": "bar"},
            "tools": {"exclude": ["google_web_search", "web_fetch"]},
            "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{HOOK_KEY_COMMAND: "echo 1"}]}]},
        }
        incoming = merge_hook_manifests(
            build_nomos_hook_manifest(), build_gemini_tool_hygiene_settings(True, True)
        )
        once = merge_hook_manifests(existing, incoming)
        twice = merge_hook_manifests(once, incoming)
        self.assertEqual(once, twice)
        self.assertEqual(twice["tools"]["exclude"], ["google_web_search", "web_fetch", "cli_help"])
        self.assertFalse(twice["agents"]["overrides"]["cli_help"]["enabled"])
        self.assertEqual(
            build_claude_web_search_guard_settings()["permissions"]["deny"],
            ["WebSearch", "WebFetch"],
        )

    def test_build_install_command_and_version_parser(self) -> None:
        """Verify version-pinned install shell command and combined semver formatting."""
        cmd = build_nomos_install_command(nomos_version="v4.8.0")
        self.assertIn("NOMOS_VERSION=v4.8.0", cmd)
        self.assertTrue(cmd.startswith("set -eu;"))
        self.assertEqual(
            parse_combined_version("2.1.318 (Claude Code)\nnomos version 4.8.0\n"),
            "2.1.318+nomos-4.8.0",
        )
        self.assertEqual(parse_combined_version("2.1.220 (Claude Code)"), "2.1.220")

    def test_resolve_trial_budget_seconds_from_config_and_lock_cache(self) -> None:
        """Verify automatic per-trial timeout discovery and capping from cached `task.toml`."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache_dir = root / "xdg_cache"
            task_dir = cache_dir / "harbor" / "tasks" / "abc123" / "mailman"
            task_dir.mkdir(parents=True)
            (task_dir / "task.toml").write_text("[agent]\ntimeout_sec = 1800.0\n", encoding="utf-8")
            logs_dir = root / "mailman__trial1" / "agent"
            logs_dir.mkdir(parents=True)
            (logs_dir.parent / "config.json").write_text(
                json.dumps({"task": {"path": "mailman"}, "timeout_multiplier": 1.5}), encoding="utf-8"
            )
            pkg_hash = "0" * 64
            pkg_dir = cache_dir / "harbor" / "tasks" / "packages" / "tb" / "foo" / pkg_hash
            pkg_dir.mkdir(parents=True)
            (pkg_dir / "task.toml").write_text("[agent]\ntimeout_sec = 28800.0\n", encoding="utf-8")
            pkg_logs = root / "foo__trial2" / "agent"
            pkg_logs.mkdir(parents=True)
            (pkg_logs.parent / "lock.json").write_text(
                json.dumps({"task": {"name": "tb/foo", "digest": f"sha256:{pkg_hash}"}, "agent": {"max_timeout_sec": 1800.0}}),
                encoding="utf-8",
            )
            prev_xdg = os.environ.get("XDG_CACHE_HOME")
            os.environ["XDG_CACHE_HOME"] = str(cache_dir)
            try:
                self.assertEqual(resolve_trial_budget_seconds(logs_dir), 2700)
                self.assertEqual(resolve_trial_budget_seconds(logs_dir, 600), 600)
                self.assertIsNone(resolve_trial_budget_seconds(logs_dir, None, False))
                self.assertEqual(resolve_trial_budget_seconds(pkg_logs), 1800)
            finally:
                if prev_xdg is None:
                    os.environ.pop("XDG_CACHE_HOME", None)
                else:
                    os.environ["XDG_CACHE_HOME"] = prev_xdg


class _FakeEnvironment:
    """Minimal mock of Harbor's `BaseEnvironment` for testing install and config uploads."""

    default_user = "root"

    def __init__(self) -> None:
        """Initialize empty upload and exec logs."""
        self.uploads: list[tuple[Path, str]] = []
        self.commands: list[str] = []
        self.last_settings: dict[str, object] = {}

    async def upload_file(self, source_path: Path | str, target_path: str) -> None:
        """Record uploaded file content before temporary directory cleanup."""
        src = Path(source_path)
        self.uploads.append((src, target_path))
        if src.name == "settings.json":
            self.last_settings = json.loads(src.read_text(encoding="utf-8"))

    async def exec(self, command: str, **_kwargs: object) -> SimpleNamespace:
        """Record executed container shell commands and return exit code 0."""
        self.commands.append(command)
        return SimpleNamespace(return_code=0, stdout="0.67.0\n", stderr="")


class TestNomosHarborAgentIntegration(unittest.TestCase):
    """Verify `NomosClaudeCode` and `NomosGeminiCli` subclass Harbor agents cleanly."""

    def test_claude_agent_preflight_and_session_settings_upload(self) -> None:
        """Validate Claude factory preflight, binary upload, and `$CLAUDE_CONFIG_DIR/settings.json`."""
        if importlib.util.find_spec("harbor") is None:
            return
        from harbor.agents.factory import AgentFactory
        from harbor.models.trial.config import AgentConfig
        from nomos_harbor import NomosClaudeCode

        with self.assertRaises(ValueError):
            AgentFactory.run_preflight(
                AgentConfig(
                    import_path="nomos_harbor:NomosClaudeCode",
                    model_name="anthropic/claude-sonnet-4-5",
                    env={"ANTHROPIC_API_KEY": "sk-ant-test"},
                    kwargs={"nomos_binary_path": "/nonexistent/nomos-bin"},
                )
            )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            logs_dir = tmp_path / "logs"
            logs_dir.mkdir()
            agent = NomosClaudeCode(
                logs_dir=logs_dir,
                model_name="anthropic/claude-sonnet-4-5",
                nomos_binary_path=str(_write_fake_nomos_bin(tmp_path)),
            )
            self.assertEqual(agent.name(), "nomos-claude-code")
            env = _FakeEnvironment()
            asyncio.run(agent.install(env))
            asyncio.run(agent._upload_base_settings(env))
            targets = [target for _, target in env.uploads]
            self.assertIn(DEFAULT_REMOTE_NOMOS_BIN, targets)
            self.assertIn("/logs/agent/sessions/settings.json", targets)
            self.assertEqual(env.last_settings["permissions"]["deny"], ["WebSearch", "WebFetch"])  # type: ignore[index]

    def test_gemini_agent_vertex_adc_mcts_profile_and_tool_hygiene(self) -> None:
        """Validate `VertexGeminiCli` control and `NomosGeminiCli` MCTS profile/ADC wiring."""
        if importlib.util.find_spec("harbor") is None:
            return
        from harbor.agents.factory import AgentFactory
        from harbor.models.trial.config import AgentConfig
        from nomos_harbor import NomosGeminiCli, VertexGeminiCli

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_bin = _write_fake_nomos_bin(tmp_path)
            fake_adc = tmp_path / "adc.json"
            fake_adc.write_text(json.dumps({"quota_project_id": "test-gcp-proj"}), encoding="utf-8")
            logs_dir = tmp_path / "logs"
            logs_dir.mkdir()
            AgentFactory.run_preflight(
                AgentConfig(
                    import_path="nomos_harbor:NomosGeminiCli",
                    model_name="vertex_ai/gemini-3.5-flash",
                    kwargs={"nomos_binary_path": str(fake_bin), "gcp_adc_path": str(fake_adc)},
                )
            )
            control = VertexGeminiCli(logs_dir=logs_dir, model_name="vertex_ai/gemini-3.5-flash", gcp_adc_path=str(fake_adc))
            control_cfg, _ = control._build_settings_config("gemini-3.5-flash", "vertex-ai")
            self.assertNotIn("hooks", control_cfg or {})

            agent = NomosGeminiCli(
                logs_dir=logs_dir, model_name="vertex_ai/gemini-3.5-flash", nomos_binary_path=str(fake_bin), gcp_adc_path=str(fake_adc)
            )
            self.assertEqual(agent._resolve_auth_env()["GOOGLE_CLOUD_PROJECT"], "test-gcp-proj")
            settings, _ = agent._build_settings_config("gemini-3.5-flash", "vertex-ai")
            assert settings is not None
            self.assertIn("--budget-warn-pct 70", settings["hooks"]["AfterTool"][0]["hooks"][0][HOOK_KEY_COMMAND])
            env = _FakeEnvironment()
            asyncio.run(agent.install(env))
            asyncio.run(agent._inject_vertex_adc(env))
            self.assertIn("/tmp/gemini-secrets/adc.json", [target for _, target in env.uploads])

    def test_eol_debian_apt_fallback_retries_after_archive_rewrite(self) -> None:
        """Verify `VertexGeminiCli.ensure_system_dependencies` recovers from EOL Debian 404s."""
        if importlib.util.find_spec("harbor") is None:
            return
        from nomos_harbor import VertexGeminiCli

        class _BullseyeEnv(_FakeEnvironment):
            def __init__(self) -> None:
                """Initialize apt attempt counter."""
                super().__init__()
                self.apt_attempts = 0

            async def exec(self, command: str, **_kwargs: object) -> SimpleNamespace:
                """Fail first apt-get update with 404 and succeed after archive rewrite."""
                self.commands.append(command)
                if command.startswith("command -v curl"):
                    return SimpleNamespace(return_code=1, stdout="", stderr="")
                if command.startswith("for manager in apt-get"):
                    return SimpleNamespace(return_code=0, stdout="apt-get", stderr="")
                if "apt-get update && apt-get install" in command:
                    self.apt_attempts += 1
                    return SimpleNamespace(return_code=100 if self.apt_attempts == 1 else 0, stdout="", stderr="")
                return SimpleNamespace(return_code=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            env = _BullseyeEnv()
            asyncio.run(VertexGeminiCli(logs_dir=Path(tmp), model_name="vertex_ai/gemini-3.5-flash").ensure_system_dependencies(env, ("curl",)))
            self.assertEqual(env.apt_attempts, 2)
