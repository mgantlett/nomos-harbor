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
    build_nomos_hook_manifest,
    build_nomos_install_command,
    merge_hook_manifests,
    parse_combined_version,
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

        scoped_manifest = build_nomos_hook_manifest("/usr/local/bin/nomos", hermetic_dir="/app")
        scoped_pre = scoped_manifest["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        self.assertEqual(scoped_pre, "/usr/local/bin/nomos hook pre-tool --hermetic-dir /app")

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
        cmd = build_nomos_install_command(nomos_version="v4.4.1")
        self.assertIn("VERSION=v4.4.1", cmd)
        self.assertIn("BIN_DIR=/usr/local/bin", cmd)
        self.assertEqual(
            parse_combined_version(
                "2.1.318 (Claude Code)\n"
                "nomos version 4.4.1 (linux/amd64)\n"
                "compiler: go1.26.5 · open-core substrate\n"
            ),
            "2.1.318+nomos-4.4.1",
        )
        self.assertEqual(parse_combined_version("2.1.220 (Claude Code)"), "2.1.220")


class _FakeEnvironment:
    """Minimal mock of Harbor's `BaseEnvironment` for testing install and config uploads."""

    default_user = "root"

    def __init__(self) -> None:
        """Initialize empty upload and exec logs."""
        self.uploads: list[tuple[Path, str]] = []
        self.commands: list[str] = []

    async def upload_file(self, source_path: Path | str, target_path: str) -> None:
        """Record uploaded file content before temporary directory cleanup."""
        src = Path(source_path)
        self.uploads.append((src, target_path))
        if src.name == "settings.json":
            self.last_settings = json.loads(src.read_text(encoding="utf-8"))

    async def exec(self, command: str, **_kwargs: object) -> SimpleNamespace:
        """Record executed container shell commands and return exit code 0."""
        self.commands.append(command)
        return SimpleNamespace(return_code=0, stdout="2.1.220\n", stderr="")


class TestNomosHarborAgentIntegration(unittest.TestCase):
    """Verify `NomosClaudeCode` subclasses Harbor's `ClaudeCode` when `harbor` is present."""

    def test_agent_subclass_preflight_and_session_settings_upload(self) -> None:
        """Validate factory preflight, binary upload, and `$CLAUDE_CONFIG_DIR/settings.json`."""
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
            fake_bin.write_bytes(b"#!/bin/sh\necho 'nomos 4.4.1'\n")
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
            pre_cmd = env.last_settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
            self.assertIn("--container-sandbox", pre_cmd)


if __name__ == "__main__":
    unittest.main()
