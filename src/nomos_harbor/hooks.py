"""Pure-Python hook manifest synthesis and container bootstrap helpers for Harbor trials.

Provides additive Claude Code and Gemini CLI settings.json merging and container shell
commands that wire compiled Go `nomos hook` interceptors around stock Tier 1 coding
agents without polluting the `/app` benchmark workspace.
"""

from __future__ import annotations

import copy
import json
import shlex
from typing import Any

from nomos_harbor.hook_budget import (
    _read_json_dict,
    _read_task_toml_timeout,
    resolve_trial_budget_seconds,
)
from nomos_harbor.hook_constants import (
    BUDGET_POST_TOOL_MATCHER,
    DEFAULT_INSTALL_URL,
    DEFAULT_REMOTE_NOMOS_BIN,
    EOL_DEBIAN_APT_REPAIR_COMMAND,
    GEMINI_BUDGET_POST_TOOL_MATCHER,
    GEMINI_POST_TOOL_MATCHER,
    GEMINI_PRE_TOOL_MATCHER,
    GEMINI_SESSION_TAINT_GUIDANCE,
    GEMINI_TAINT_GUIDANCE_COMMAND,
    HOOK_KEY_HOOKS,
    HOOK_KEY_MATCHER,
    LOGS_ROOT_HYGIENE_COMMAND,
    POST_TOOL_MATCHER,
    PRE_TOOL_MATCHER,
    SEMVER_RE,
    VERTEX_INLINE_MEDIA_GUARD_SCRIPT,
    _build_hook_flag_suffix,
    build_pre_and_post_hook_commands,
    make_command_hook_entry,
)
from nomos_harbor.hook_gemini import (
    build_gemini_tool_hygiene_settings,
    build_nomos_gemini_hook_manifest,
    build_vertex_media_guard_hook_manifest,
)

__all__ = [
    "BUDGET_POST_TOOL_MATCHER",
    "DEFAULT_INSTALL_URL",
    "DEFAULT_REMOTE_NOMOS_BIN",
    "EOL_DEBIAN_APT_REPAIR_COMMAND",
    "GEMINI_BUDGET_POST_TOOL_MATCHER",
    "GEMINI_POST_TOOL_MATCHER",
    "GEMINI_PRE_TOOL_MATCHER",
    "GEMINI_SESSION_TAINT_GUIDANCE",
    "GEMINI_TAINT_GUIDANCE_COMMAND",
    "LOGS_ROOT_HYGIENE_COMMAND",
    "POST_TOOL_MATCHER",
    "PRE_TOOL_MATCHER",
    "SEMVER_RE",
    "VERTEX_INLINE_MEDIA_GUARD_SCRIPT",
    "_build_hook_flag_suffix",
    "_read_json_dict",
    "_read_task_toml_timeout",
    "build_claude_web_search_guard_settings",
    "build_gemini_tool_hygiene_settings",
    "build_nomos_gemini_hook_manifest",
    "build_nomos_hook_manifest",
    "build_nomos_install_command",
    "build_vertex_media_guard_hook_manifest",
    "merge_hook_manifests",
    "parse_combined_version",
    "resolve_trial_budget_seconds",
]


def build_claude_web_search_guard_settings() -> dict[str, Any]:
    """Return documented Claude Code permission rules disabling `WebSearch` and `WebFetch`."""
    return {
        "permissions": {
            "deny": ["WebSearch", "WebFetch"],
        }
    }


def build_nomos_hook_manifest(
    nomos_bin: str = DEFAULT_REMOTE_NOMOS_BIN,
    hermetic_dir: str | None = None,
    container_sandbox: bool = True,
    budget_sec: int | None = None,
    budget_warn_pct: int | None = None,
    budget_crit_pct: int | None = None,
    loop_strikes: int | None = None,
    advisory_style: str | None = None,
) -> dict[str, Any]:
    """Construct the canonical Claude Code settings.json hook dictionary for Nomos.

    Defaults to `--container-sandbox` for full-OS Harbor benchmarks (blocking
    verifier/oracle tampering and identical-failure loops while allowing `/etc` and `/tmp`),
    or `--hermetic-dir` when strict workspace path confinement is requested.
    """
    pre_cmd, post_cmd = build_pre_and_post_hook_commands(
        nomos_bin=nomos_bin,
        hermetic_dir=hermetic_dir,
        container_sandbox=container_sandbox,
        budget_sec=budget_sec,
        budget_warn_pct=budget_warn_pct,
        budget_crit_pct=budget_crit_pct,
        loop_strikes=loop_strikes,
        advisory_style=advisory_style,
    )
    return {
        HOOK_KEY_HOOKS: {
            "PreToolUse": [
                {
                    HOOK_KEY_MATCHER: PRE_TOOL_MATCHER,
                    HOOK_KEY_HOOKS: [make_command_hook_entry(pre_cmd, 10)],
                }
            ],
            "PostToolUse": [
                {
                    HOOK_KEY_MATCHER: POST_TOOL_MATCHER,
                    HOOK_KEY_HOOKS: [make_command_hook_entry(post_cmd, 5)],
                }
            ],
        }
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
    """Additively merge `incoming` Nomos hooks into an existing agent settings map."""
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
        env_parts.append(f"NOMOS_VERSION={shlex.quote(nomos_version.strip())}")
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
