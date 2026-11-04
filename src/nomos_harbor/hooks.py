"""Pure-Python hook manifest synthesis and container bootstrap helpers for Harbor trials.

Provides additive Claude Code and Gemini CLI settings.json merging and container shell
commands that wire compiled Go `nomos hook` interceptors around stock Tier 1 coding
agents without polluting the `/app` benchmark workspace.
"""

from __future__ import annotations

import copy
import json
import re
import shlex
from typing import Any

DEFAULT_INSTALL_URL = "https://sophialabs.ca/install"
DEFAULT_REMOTE_NOMOS_BIN = "/usr/local/bin/nomos"
PRE_TOOL_MATCHER = (
    "Bash|Read|Write|Edit|MultiEdit|NotebookEdit|Glob|Grep|WebFetch|WebSearch|Task.*|mcp__.*"
)
POST_TOOL_MATCHER = "Write|Edit|MultiEdit|NotebookEdit"
GEMINI_PRE_TOOL_MATCHER = (
    "run_shell_command|read_file|write_file|replace|list_directory|glob|"
    "search_file_content|web_fetch|google_web_search|mcp_.*"
)
GEMINI_POST_TOOL_MATCHER = "write_file|replace"
SEMVER_RE = re.compile(r"(\d+\.\d+\.\d+)")


def _build_hook_flag_suffix(
    hermetic_dir: str | None = None,
    container_sandbox: bool = True,
) -> str:
    """Format CLI flags for `nomos hook` inside disposable or repo-scoped containers."""
    if hermetic_dir and hermetic_dir.strip():
        return f" --hermetic-dir {shlex.quote(hermetic_dir.strip())}"
    if container_sandbox:
        return " --container-sandbox"
    return ""


def build_nomos_hook_manifest(
    nomos_bin: str = DEFAULT_REMOTE_NOMOS_BIN,
    hermetic_dir: str | None = None,
    container_sandbox: bool = True,
) -> dict[str, Any]:
    """Construct the canonical Claude Code settings.json hook dictionary for Nomos.

    Defaults to `--container-sandbox` for full-OS Harbor benchmarks (blocking
    verifier/oracle tampering and 3-strike loops while allowing `/etc` and `/tmp`),
    or `--hermetic-dir` when strict workspace path confinement is requested.
    """
    quoted_bin = shlex.quote(nomos_bin)
    suffix = _build_hook_flag_suffix(
        hermetic_dir=hermetic_dir,
        container_sandbox=container_sandbox,
    )
    pre_cmd = f"{quoted_bin} hook pre-tool{suffix}"
    post_cmd = f"{quoted_bin} hook post-tool{suffix}"
    return {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": PRE_TOOL_MATCHER,
                    "hooks": [
                        {
                            "type": "command",
                            "command": pre_cmd,
                            "timeout": 10,
                        }
                    ],
                }
            ],
            "PostToolUse": [
                {
                    "matcher": POST_TOOL_MATCHER,
                    "hooks": [
                        {
                            "type": "command",
                            "command": post_cmd,
                            "timeout": 5,
                        }
                    ],
                }
            ],
        }
    }


def build_nomos_gemini_hook_manifest(
    nomos_bin: str = DEFAULT_REMOTE_NOMOS_BIN,
    hermetic_dir: str | None = None,
    container_sandbox: bool = True,
) -> dict[str, Any]:
    """Construct the canonical Gemini CLI `~/.gemini/settings.json` hook dictionary.

    Enables `hooksConfig.enabled = True` and registers `BeforeTool` (10000ms) and
    `AfterTool` (5000ms) command interceptors backed by `nomos hook`.
    """
    quoted_bin = shlex.quote(nomos_bin)
    suffix = _build_hook_flag_suffix(
        hermetic_dir=hermetic_dir,
        container_sandbox=container_sandbox,
    )
    pre_cmd = f"{quoted_bin} hook pre-tool{suffix}"
    post_cmd = f"{quoted_bin} hook post-tool{suffix}"
    return {
        "hooksConfig": {
            "enabled": True,
        },
        "hooks": {
            "BeforeTool": [
                {
                    "matcher": GEMINI_PRE_TOOL_MATCHER,
                    "hooks": [
                        {
                            "name": "nomos-pre-tool",
                            "type": "command",
                            "command": pre_cmd,
                            "timeout": 10000,
                        }
                    ],
                }
            ],
            "AfterTool": [
                {
                    "matcher": GEMINI_POST_TOOL_MATCHER,
                    "hooks": [
                        {
                            "name": "nomos-post-tool",
                            "type": "command",
                            "command": post_cmd,
                            "timeout": 5000,
                        }
                    ],
                }
            ],
        },
    }


def _canonical_signature(item: Any) -> str:
    """Serialize a JSON hook entry canonically for idempotent deduplication."""
    return json.dumps(item, sort_keys=True, separators=(",", ":"))


def _merge_hook_slices(existing: list[Any], incoming: list[Any]) -> list[Any]:
    """Append incoming hook descriptors not already present in `existing`."""
    seen: set[str] = set()
    merged: list[Any] = []
    for entry in existing:
        sig = _canonical_signature(entry)
        seen.add(sig)
        merged.append(copy.deepcopy(entry))
    for entry in incoming:
        sig = _canonical_signature(entry)
        if sig not in seen:
            seen.add(sig)
            merged.append(copy.deepcopy(entry))
    return merged


def merge_hook_manifests(
    existing: dict[str, Any] | None,
    incoming: dict[str, Any],
) -> dict[str, Any]:
    """Additively merge `incoming` Nomos hooks into an existing agent settings map.

    Preserves unrelated top-level settings and deduplicates repeated hook entries
    so workspace or user hooks are chained rather than overwritten.
    """
    if not existing:
        return copy.deepcopy(incoming)
    result = copy.deepcopy(existing)
    _merge_object_maps(result, incoming)
    return result


def _merge_object_maps(dst: dict[str, Any], src: dict[str, Any]) -> None:
    """Recursively merge nested dictionaries and hook arrays in place."""
    for key, src_val in src.items():
        if key not in dst:
            dst[key] = copy.deepcopy(src_val)
            continue
        dst_val = dst[key]
        if isinstance(dst_val, dict) and isinstance(src_val, dict):
            _merge_object_maps(dst_val, src_val)
        elif isinstance(dst_val, list) and isinstance(src_val, list):
            dst[key] = _merge_hook_slices(dst_val, src_val)


def build_nomos_install_command(
    nomos_version: str | None = None,
    install_url: str = DEFAULT_INSTALL_URL,
    target_bin_dir: str = "/usr/local/bin",
) -> str:
    """Build a POSIX `/bin/sh`-compatible command that installs the static `nomos` binary."""
    env_parts = [f"NOMOS_INSTALL_DIR={shlex.quote(target_bin_dir)}"]
    if nomos_version and nomos_version.strip():
        clean_ver = nomos_version.strip()
        env_parts.append(f"NOMOS_VERSION={shlex.quote(clean_ver)}")
    env_prefix = " ".join(env_parts)
    bin_path = shlex.quote(f"{target_bin_dir.rstrip('/')}/nomos")
    tmp_installer = "/tmp/nomos-install.sh"
    return (
        "set -eu; "
        f"mkdir -p {shlex.quote(target_bin_dir)}; "
        f"curl -fsSL {shlex.quote(install_url)} -o {tmp_installer}; "
        f"if command -v bash >/dev/null 2>&1; then {env_prefix} bash {tmp_installer}; "
        f"else sed 's/-euo pipefail/-eu/' {tmp_installer} | {env_prefix} sh; fi; "
        f"rm -f {tmp_installer}; "
        f"chmod 755 {bin_path}; "
        f"{bin_path} --version"
    )


def parse_combined_version(stdout: str) -> str:
    """Parse combined agent `--version` and `nomos --version` output into `<agent>+nomos-<ver>`."""
    lines = [ln.strip() for ln in stdout.splitlines() if ln.strip()]
    if not lines:
        return "unknown"
    agent_match = SEMVER_RE.search(lines[0])
    agent_ver = agent_match.group(1) if agent_match else lines[0]
    if len(lines) < 2:
        return agent_ver
    nomos_line = next(
        (ln for ln in lines[1:] if "nomos version" in ln.lower()),
        lines[1],
    )
    nomos_match = SEMVER_RE.search(nomos_line)
    nomos_ver = nomos_match.group(1) if nomos_match else nomos_line
    return f"{agent_ver}+nomos-{nomos_ver}"
