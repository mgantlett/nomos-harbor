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
    build_claude_web_search_guard_settings,
    build_gemini_tool_hygiene_settings,
    build_nomos_gemini_hook_manifest,
    build_nomos_hook_manifest,
    build_nomos_install_command,
    merge_hook_manifests,
    parse_combined_version,
    resolve_trial_budget_seconds,
)
from nomos_harbor.mcts_profiles import (  # noqa: E402
    extract_trial_features,
    optimize_profile_mcts,
    resolve_model_advisory_profile,
)


class TestNomosHarborHooks(unittest.TestCase):
    """Verify pure-Python hook manifest synthesis, additive chaining, and version parsing."""

    def test_build_manifest_defaults_to_container_sandbox_and_unconditional_bash(self) -> None:
        """Ensure default Harbor manifest uses `--container-sandbox` and unconditionally matches `Bash`."""
        default_manifest = build_nomos_hook_manifest("/usr/local/bin/nomos")
        pre = default_manifest["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        post_group = default_manifest["hooks"]["PostToolUse"][0]
        post = post_group["hooks"][0]["command"]
        self.assertEqual(pre, "/usr/local/bin/nomos hook pre-tool --container-sandbox")
        self.assertEqual(post, "/usr/local/bin/nomos hook post-tool --container-sandbox")
        self.assertIn("Bash|", post_group["matcher"])

        scoped_manifest = build_nomos_hook_manifest(
            "/usr/local/bin/nomos",
            hermetic_dir="/app",
            budget_sec=900,
            budget_warn_pct=70,
            budget_crit_pct=88,
            loop_strikes=4,
            advisory_style="terse",
        )
        scoped_pre = scoped_manifest["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        scoped_post = scoped_manifest["hooks"]["PostToolUse"][0]["hooks"][0]["command"]
        self.assertEqual(
            scoped_pre,
            "/usr/local/bin/nomos hook pre-tool --hermetic-dir /app --budget-sec 900 --loop-strikes 4",
        )
        self.assertEqual(
            scoped_post,
            "/usr/local/bin/nomos hook post-tool --hermetic-dir /app --budget-sec 900 "
            "--budget-warn-pct 70 --budget-crit-pct 88 --advisory-style terse",
        )

    def test_build_gemini_manifest_uses_before_after_and_optional_session_start(self) -> None:
        """Ensure Gemini CLI manifest enables `hooksConfig`, `run_shell_command`, and taint guidance."""
        gemini_manifest = build_nomos_gemini_hook_manifest("/usr/local/bin/nomos")
        self.assertTrue(gemini_manifest["hooksConfig"]["enabled"])
        before = gemini_manifest["hooks"]["BeforeTool"][0]["hooks"][0]
        after_group = gemini_manifest["hooks"]["AfterTool"][0]
        after = after_group["hooks"][0]
        self.assertEqual(
            before["command"],
            "/usr/local/bin/nomos hook pre-tool --container-sandbox",
        )
        self.assertEqual(before["timeout"], 10000)
        self.assertIn("run_shell_command|", after_group["matcher"])
        self.assertEqual(
            after["command"],
            "/usr/local/bin/nomos hook post-tool --container-sandbox",
        )
        self.assertEqual(after["timeout"], 5000)
        self.assertNotIn("SessionStart", gemini_manifest["hooks"])

        guided = build_nomos_gemini_hook_manifest(
            "/usr/local/bin/nomos",
            budget_sec=1200,
            budget_warn_pct=70,
            budget_crit_pct=88,
            advisory_style="terse",
            gemini_taint_guidance=True,
        )
        self.assertIn("SessionStart", guided["hooks"])
        self.assertIn(
            "--budget-warn-pct 70 --budget-crit-pct 88 --advisory-style terse",
            guided["hooks"]["AfterTool"][0]["hooks"][0]["command"],
        )

    def test_tool_hygiene_and_merge_hook_manifests_are_idempotent(self) -> None:
        """Ensure Gemini/Claude tool hygiene blocks merge additively without duplicates."""
        existing = {
            "env": {"FOO": "bar"},
            "tools": {"exclude": ["google_web_search", "web_fetch"]},
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [{"type": "command", "command": "echo custom"}],
                    }
                ]
            },
        }
        incoming = merge_hook_manifests(
            build_nomos_hook_manifest(),
            build_gemini_tool_hygiene_settings(disable_web_search=True, exclude_cli_help=True),
        )
        once = merge_hook_manifests(existing, incoming)
        twice = merge_hook_manifests(once, incoming)
        self.assertEqual(once, twice)
        self.assertEqual(twice["env"], {"FOO": "bar"})
        self.assertEqual(
            twice["tools"]["exclude"],
            ["google_web_search", "web_fetch", "cli_help"],
        )
        self.assertFalse(twice["agents"]["overrides"]["cli_help"]["enabled"])
        claude_perm = build_claude_web_search_guard_settings()
        self.assertEqual(claude_perm["permissions"]["deny"], ["WebSearch", "WebFetch"])

    def test_build_install_command_and_version_parser(self) -> None:
        """Verify version-pinned install shell command and combined semver formatting."""
        cmd = build_nomos_install_command(nomos_version="v4.8.0")
        self.assertIn("NOMOS_VERSION=v4.8.0", cmd)
        self.assertIn("NOMOS_INSTALL_DIR=/usr/local/bin", cmd)
        self.assertTrue(cmd.startswith("set -eu;"))
        self.assertIn("bash /tmp/nomos-install.sh", cmd)
        self.assertEqual(
            parse_combined_version(
                "2.1.318 (Claude Code)\n"
                "nomos version 4.8.0 (linux/amd64)\n"
                "compiler: go1.26.5 · open-core substrate\n"
            ),
            "2.1.318+nomos-4.8.0",
        )
        self.assertEqual(
            parse_combined_version(
                "0.67.0\n"
                "nomos version 4.8.0 (linux/amd64)\n"
                "compiler: go1.26.5 · open-core substrate\n"
            ),
            "0.67.0+nomos-4.8.0",
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
                "[agent]\ntimeout_sec = 1800.0\n", encoding="utf-8"
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

                pkg_hash = "0" * 64
                pkg_dir = (
                    cache_dir / "harbor" / "tasks" / "packages" / "terminal-bench" / "foo-task" / pkg_hash
                )
                pkg_dir.mkdir(parents=True)
                (pkg_dir / "task.toml").write_text(
                    "[agent]\ntimeout_sec = 28800.0\n", encoding="utf-8"
                )
                pkg_trial = root / "foo-task__trial2"
                pkg_logs = pkg_trial / "agent"
                pkg_logs.mkdir(parents=True)
                (pkg_trial / "lock.json").write_text(
                    json.dumps(
                        {
                            "task": {
                                "name": "terminal-bench/foo-task",
                                "digest": f"sha256:{pkg_hash}",
                            },
                            "agent": {"max_timeout_sec": 1800.0},
                        }
                    ),
                    encoding="utf-8",
                )
                self.assertEqual(resolve_trial_budget_seconds(pkg_logs), 1800)
            finally:
                if prev_xdg is None:
                    sys.modules["os"].environ.pop("XDG_CACHE_HOME", None)
                else:
                    sys.modules["os"].environ["XDG_CACHE_HOME"] = prev_xdg

    def test_mcts_profile_optimizer_and_model_resolution(self) -> None:
        """Verify UCT/MCTS trajectory feature extraction and model profile selection."""
        self.assertEqual(resolve_model_advisory_profile("vertex_ai/gemini-3.5-flash").name, "gemini")
        self.assertEqual(resolve_model_advisory_profile("anthropic/claude-sonnet-4-5").name, "claude")
        self.assertEqual(resolve_model_advisory_profile("openai/gpt-5").name, "gpt")
        with self.assertRaises(ValueError):
            resolve_model_advisory_profile("vertex_ai/gemini-3.5-flash", "invalid-profile")

        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp) / "sample-job"
            trial_a = job_dir / "task-timeout__1"
            (trial_a / "agent").mkdir(parents=True)
            (trial_a / "result.json").write_text(
                json.dumps(
                    {
                        "task_name": "task-timeout",
                        "verifier_result": {"rewards": {"reward": 0.0}},
                        "exception_info": {"exception_type": "AgentTimeoutError"},
                        "config": {"agent": {"override_timeout_sec": 1800}},
                    }
                ),
                encoding="utf-8",
            )
            (trial_a / "agent" / "trajectory.json").write_text(
                json.dumps(
                    {
                        "agent": {"model_name": "gemini-3.5-flash"},
                        "steps": [
                            {
                                "tool_calls": [
                                    {"function_name": "google_web_search", "arguments": {"query": "q"}},
                                    {
                                        "function_name": "invoke_agent",
                                        "arguments": {"agent_name": "cli_help", "prompt": "policy"},
                                    },
                                ],
                                "observation": "Tool execution denied by policy.",
                            }
                        ]
                        * 20,
                    }
                ),
                encoding="utf-8",
            )
            feat = extract_trial_features(trial_a)
            assert feat is not None
            self.assertEqual(feat.web_search_calls, 20)
            self.assertEqual(feat.cli_help_calls, 20)
            self.assertEqual(feat.policy_denials, 20)

            report = optimize_profile_mcts([feat], model_family="gemini", rollouts=80, seed=7)
            self.assertGreater(report.best_score, report.baseline_score)
            self.assertTrue(report.best_profile.disable_web_search)
            self.assertTrue(report.best_profile.exclude_cli_help)


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
            fake_bin.write_bytes(b"#!/bin/sh\necho 'nomos version 4.8.0'\n")
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
            post_matcher = env.last_settings["hooks"]["PostToolUse"][0]["matcher"]  # type: ignore[index]
            self.assertIn("--container-sandbox", pre_cmd)
            self.assertIn("Bash|", post_matcher)
            self.assertEqual(
                env.last_settings["permissions"]["deny"],  # type: ignore[index]
                ["WebSearch", "WebFetch"],
            )
            self.assertTrue(any("chown 0:0 /logs" in c for c in env.commands))

    def test_gemini_agent_vertex_adc_mcts_profile_and_tool_hygiene(self) -> None:
        """Validate `NomosGeminiCli` MCTS profile flags, `cli_help`/web exclusions, and `/logs` hygiene."""
        if importlib.util.find_spec("harbor") is None:
            return

        from harbor.agents.factory import AgentFactory
        from harbor.models.trial.config import AgentConfig
        from nomos_harbor import NomosGeminiCli, VertexGeminiCli

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_bin = tmp_path / "nomos-static"
            fake_bin.write_bytes(b"#!/bin/sh\necho 'nomos version 4.8.0'\n")
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
            self.assertNotIn("tools", control_cfg or {})

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
            self.assertEqual(
                settings["tools"]["exclude"],
                ["google_web_search", "web_fetch", "cli_help"],
            )
            self.assertFalse(settings["agents"]["overrides"]["cli_help"]["enabled"])
            self.assertIn("SessionStart", settings["hooks"])

            before_cmd = settings["hooks"]["BeforeTool"][0]["hooks"][0]["command"]
            after_group = settings["hooks"]["AfterTool"][0]
            after_cmd = after_group["hooks"][0]["command"]
            self.assertIn("--container-sandbox", before_cmd)
            self.assertIn("run_shell_command|", after_group["matcher"])
            self.assertIn("--budget-warn-pct 70", after_cmd)
            self.assertIn("--budget-crit-pct 88", after_cmd)
            self.assertIn("--advisory-style terse", after_cmd)
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
            self.assertTrue(any("chown 0:0 /logs" in c for c in env.commands))

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
