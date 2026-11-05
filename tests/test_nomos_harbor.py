"""Unit and Harbor integration tests for `nomos_harbor`."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

PACKAGE_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PACKAGE_SRC) not in sys.path:
    sys.path.insert(0, str(PACKAGE_SRC))

from nomos_harbor.hooks import (  # noqa: E402
    DEFAULT_REMOTE_NOMOS_BIN,
    build_nomos_gemini_hook_manifest,
    build_nomos_hook_manifest,
    build_nomos_install_command,
    merge_hook_manifests,
    parse_combined_version,
    resolve_trial_budget_seconds,
)


class TestNomosHarborHooks(unittest.TestCase):
    """Verify pure-Python hook manifest synthesis, additive chaining, and version parsing."""

    def test_build_manifest_defaults_to_container_sandbox(self) -> None:
        """Ensure default Harbor manifest uses `--container-sandbox` and supports `--hermetic-dir`."""
        default_manifest = build_nomos_hook_manifest("/usr/local/bin/nomos")
        pre = default_manifest["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        post = default_manifest["hooks"]["PostToolUse"][0]["hooks"][0]["command"]
        self.assertEqual(pre, "/usr/local/bin/nomos hook pre-tool --container-sandbox")
        self.assertEqual(post, "/usr/local/bin/nomos hook post-tool --container-sandbox")

        scoped_manifest = build_nomos_hook_manifest(
            "/usr/local/bin/nomos", hermetic_dir="/app", budget_sec=900
        )
        scoped_pre = scoped_manifest["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        scoped_post_entry = scoped_manifest["hooks"]["PostToolUse"][0]
        self.assertEqual(
            scoped_pre,
            "/usr/local/bin/nomos hook pre-tool --hermetic-dir /app --budget-sec 900",
        )
        self.assertIn("Bash|", scoped_post_entry["matcher"])

    def test_build_gemini_manifest_uses_before_and_after_tool(self) -> None:
        """Ensure Gemini CLI manifest enables `hooksConfig` and millisecond timeouts."""
        gemini_manifest = build_nomos_gemini_hook_manifest(
            "/usr/local/bin/nomos", budget_sec=1200
        )
        self.assertTrue(gemini_manifest["hooksConfig"]["enabled"])
        before = gemini_manifest["hooks"]["BeforeTool"][0]["hooks"][0]
        after_group = gemini_manifest["hooks"]["AfterTool"][0]
        after = after_group["hooks"][0]
        self.assertEqual(
            before["command"],
            "/usr/local/bin/nomos hook pre-tool --container-sandbox --budget-sec 1200",
        )
        self.assertEqual(before["timeout"], 10000)
        self.assertIn("run_shell_command|", after_group["matcher"])
        self.assertEqual(
            after["command"],
            "/usr/local/bin/nomos hook post-tool --container-sandbox --budget-sec 1200",
        )
        self.assertEqual(after["timeout"], 5000)

    def test_merge_hook_manifests_is_additive_and_idempotent(self) -> None:
        """Ensure existing Claude settings and custom hooks survive repeated merges."""
        existing = {
            "env": {"FOO": "bar"},
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [{"type": "command", "command": "echo custom"}],
                    }
                ]
            },
        }
        incoming = build_nomos_hook_manifest()
        once = merge_hook_manifests(existing, incoming)
        twice = merge_hook_manifests(once, incoming)
        self.assertEqual(once, twice)
        self.assertEqual(twice["env"], {"FOO": "bar"})
        self.assertEqual(len(twice["hooks"]["PreToolUse"]), 2)
        self.assertEqual(len(twice["hooks"]["PostToolUse"]), 1)

    def test_build_install_command_and_version_parser(self) -> None:
        """Verify version-pinned install shell command and combined semver formatting."""
        cmd = build_nomos_install_command(nomos_version="v4.5.1")
        self.assertIn("NOMOS_VERSION=v4.5.1", cmd)
        self.assertIn("NOMOS_INSTALL_DIR=/usr/local/bin", cmd)
        self.assertTrue(cmd.startswith("set -eu;"))
        self.assertIn("bash /tmp/nomos-install.sh", cmd)
        self.assertEqual(
            parse_combined_version(
                "2.1.318 (Claude Code)\n"
                "nomos version 4.5.1 (linux/amd64)\n"
                "compiler: go1.26.5 · open-core substrate\n"
            ),
            "2.1.318+nomos-4.5.1",
        )
        self.assertEqual(
            parse_combined_version(
                "0.36.0\n"
                "nomos version 4.5.1 (linux/amd64)\n"
                "compiler: go1.26.5 · open-core substrate\n"
            ),
            "0.36.0+nomos-4.5.1",
        )
        self.assertEqual(parse_combined_version("2.1.220 (Claude Code)"), "2.1.220")

    def test_resolve_trial_budget_seconds_reads_cached_task_toml(self) -> None:
        """Verify automatic per-trial timeout discovery from Harbor's cached `task.toml`."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache_dir = root / "xdg_cache"
            task_dir = cache_dir / "harbor" / "tasks" / "abc123" / "mailman"
            task_dir.mkdir(parents=True)
            (task_dir / "task.toml").write_text(
                '[agent]\ntimeout_sec = 1800.0\n', encoding="utf-8"
            )
            trial_dir = root / "mailman__trial1"
            logs_dir = trial_dir / "agent"
            logs_dir.mkdir(parents=True)
            (trial_dir / "config.json").write_text(
                json.dumps({"task": {"path": "mailman"}, "timeout_multiplier": 1.5}),
                encoding="utf-8",
            )
            prev_xdg = sys.modules["os"].environ.get("XDG_CACHE_HOME")
            sys.modules["os"].environ["XDG_CACHE_HOME"] = str(cache_dir)
            try:
                resolved = resolve_trial_budget_seconds(logs_dir)
                self.assertEqual(resolved, 2700)
                self.assertEqual(resolve_trial_budget_seconds(logs_dir, 600), 600)
                self.assertIsNone(
                    resolve_trial_budget_seconds(logs_dir, None, auto_budget_radar=False)
                )
            finally:
                if prev_xdg is None:
                    sys.modules["os"].environ.pop("XDG_CACHE_HOME", None)
                else:
                    sys.modules["os"].environ["XDG_CACHE_HOME"] = prev_xdg


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
        return SimpleNamespace(return_code=0, stdout="0.36.0\n", stderr="")


class TestNomosHarborAgentIntegration(unittest.TestCase):
    """Verify `NomosClaudeCode` and `NomosGeminiCli` subclass Harbor agents cleanly."""

    def test_claude_agent_subclass_preflight_and_session_settings_upload(self) -> None:
        """Validate Claude factory preflight, binary upload, and `$CLAUDE_CONFIG_DIR/settings.json`."""
        if importlib.util.find_spec("harbor") is None:
            self.assertEqual(DEFAULT_REMOTE_NOMOS_BIN, "/usr/local/bin/nomos")
            return

        from harbor.agents.factory import AgentFactory
        from harbor.models.trial.config import AgentConfig
        from nomos_harbor import NomosClaudeCode

        bad_cfg = AgentConfig(
            import_path="nomos_harbor:NomosClaudeCode",
            model_name="anthropic/claude-sonnet-4-5",
            env={"ANTHROPIC_API_KEY": "sk-ant-test"},
            kwargs={"nomos_binary_path": "/nonexistent/nomos-bin"},
        )
        with self.assertRaises(ValueError):
            AgentFactory.run_preflight(bad_cfg)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_bin = tmp_path / "nomos-static"
            fake_bin.write_bytes(b"#!/bin/sh\necho 'nomos version 4.5.1'\n")
            logs_dir = tmp_path / "logs"
            logs_dir.mkdir()

            agent = NomosClaudeCode(
                logs_dir=logs_dir,
                model_name="anthropic/claude-sonnet-4-5",
                nomos_binary_path=str(fake_bin),
            )
            self.assertEqual(agent.name(), "nomos-claude-code")

            env = _FakeEnvironment()
            asyncio.run(agent.install(env))
            asyncio.run(agent._upload_base_settings(env))

            upload_targets = [target for _, target in env.uploads]
            self.assertIn("/usr/local/bin/nomos", upload_targets)
            self.assertIn("/logs/agent/sessions/settings.json", upload_targets)
            pre_cmd = env.last_settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]  # type: ignore[index]
            self.assertIn("--container-sandbox", pre_cmd)

    def test_gemini_agent_vertex_adc_staging_and_settings_hooks(self) -> None:
        """Validate `NomosGeminiCli` Vertex ADC resolution, `settings.json` hooks, and ATIF tag."""
        if importlib.util.find_spec("harbor") is None:
            return

        from harbor.agents.factory import AgentFactory
        from harbor.models.trial.config import AgentConfig
        from nomos_harbor import NomosGeminiCli, VertexGeminiCli

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_bin = tmp_path / "nomos-static"
            fake_bin.write_bytes(b"#!/bin/sh\necho 'nomos version 4.5.1'\n")
            fake_adc = tmp_path / "adc.json"
            fake_adc.write_text(
                json.dumps({"type": "authorized_user", "quota_project_id": "test-gcp-proj"}),
                encoding="utf-8",
            )
            logs_dir = tmp_path / "logs"
            logs_dir.mkdir()

            valid_cfg = AgentConfig(
                import_path="nomos_harbor:NomosGeminiCli",
                model_name="vertex_ai/gemini-3.5-flash",
                kwargs={
                    "nomos_binary_path": str(fake_bin),
                    "gcp_adc_path": str(fake_adc),
                },
            )
            AgentFactory.run_preflight(valid_cfg)

            control = VertexGeminiCli(
                logs_dir=logs_dir,
                model_name="vertex_ai/gemini-3.5-flash",
                gcp_adc_path=str(fake_adc),
            )
            self.assertEqual(control.name(), "gemini-cli")
            control_cfg, _ = control._build_settings_config("gemini-3.5-flash", "vertex-ai")
            self.assertIsNotNone(control_cfg)
            self.assertNotIn("hooks", control_cfg or {})

            agent = NomosGeminiCli(
                logs_dir=logs_dir,
                model_name="vertex_ai/gemini-3.5-flash",
                nomos_binary_path=str(fake_bin),
                gcp_adc_path=str(fake_adc),
            )
            self.assertEqual(agent.name(), "nomos-gemini-cli")
            auth_env = agent._resolve_auth_env()
            self.assertEqual(auth_env["GOOGLE_GENAI_USE_VERTEXAI"], "true")
            self.assertEqual(auth_env["GOOGLE_APPLICATION_CREDENTIALS"], "/tmp/gemini-secrets/adc.json")
            self.assertEqual(auth_env["GOOGLE_CLOUD_PROJECT"], "test-gcp-proj")
            self.assertEqual(auth_env["GOOGLE_CLOUD_LOCATION"], "global")
            self.assertIn("--dns-result-order=ipv4first", auth_env["NODE_OPTIONS"])

            settings, _ = agent._build_settings_config("gemini-3.5-flash", "vertex-ai")
            assert settings is not None
            self.assertEqual(settings["security"]["auth"]["selectedType"], "vertex-ai")
            self.assertTrue(settings["hooksConfig"]["enabled"])
            before_cmd = settings["hooks"]["BeforeTool"][0]["hooks"][0]["command"]
            self.assertIn("--container-sandbox", before_cmd)
            self.assertEqual(
                settings["hooks"]["BeforeTool"][1]["hooks"][0]["name"],
                "vertex-inline-media-guard",
            )

            env = _FakeEnvironment()
            asyncio.run(agent.install(env))
            asyncio.run(agent._inject_vertex_adc(env))
            upload_targets = [target for _, target in env.uploads]
            self.assertIn("/usr/local/bin/nomos", upload_targets)
            self.assertIn("/tmp/gemini-secrets/adc.json", upload_targets)

    def test_eol_debian_apt_fallback_retries_after_archive_rewrite(self) -> None:
        """Verify `VertexGeminiCli.ensure_system_dependencies` recovers from EOL Debian 404s."""
        if importlib.util.find_spec("harbor") is None:
            return

        from nomos_harbor import VertexGeminiCli

        class _BullseyeEnv(_FakeEnvironment):
            def __init__(self) -> None:
                super().__init__()
                self.apt_attempts = 0

            async def exec(self, command: str, **_kwargs: object) -> SimpleNamespace:
                self.commands.append(command)
                if command.startswith("command -v curl"):
                    return SimpleNamespace(return_code=1, stdout="", stderr="")
                if command.startswith("for manager in apt-get"):
                    return SimpleNamespace(return_code=0, stdout="apt-get", stderr="")
                if "apt-get update && apt-get install" in command:
                    self.apt_attempts += 1
                    if self.apt_attempts == 1:
                        return SimpleNamespace(
                            return_code=100,
                            stdout="E: Failed to fetch http://deb.debian.org/debian-security/... 404 Not Found",
                            stderr="",
                        )
                    return SimpleNamespace(return_code=0, stdout="installed", stderr="")
                return SimpleNamespace(return_code=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            agent = VertexGeminiCli(
                logs_dir=Path(tmp),
                model_name="vertex_ai/gemini-3.5-flash",
            )
            env = _BullseyeEnv()
            asyncio.run(agent.ensure_system_dependencies(env, ("curl",)))
            self.assertEqual(env.apt_attempts, 2)
            self.assertTrue(any("archive.debian.org" in cmd for cmd in env.commands))


if __name__ == "__main__":
    unittest.main()
