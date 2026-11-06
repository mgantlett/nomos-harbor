"""Pure-Python hook manifest synthesis and container bootstrap helpers for Harbor trials.

Provides additive Claude Code and Gemini CLI settings.json merging and container shell
commands that wire compiled Go `nomos hook` interceptors around stock Tier 1 coding
agents without polluting the `/app` benchmark workspace.
"""

from __future__ import annotations

import copy
import json
import os
import re
import shlex
import tomllib
from pathlib import Path
from typing import Any

DEFAULT_INSTALL_URL = "https://sophialabs.ca/install"
DEFAULT_REMOTE_NOMOS_BIN = "/usr/local/bin/nomos"
PRE_TOOL_MATCHER = (
    "Bash|Read|Write|Edit|MultiEdit|NotebookEdit|Glob|Grep|WebFetch|WebSearch|Task.*|mcp__.*"
)
POST_TOOL_MATCHER = "Bash|Write|Edit|MultiEdit|NotebookEdit"
BUDGET_POST_TOOL_MATCHER = POST_TOOL_MATCHER
GEMINI_PRE_TOOL_MATCHER = (
    "run_shell_command|read_file|write_file|replace|list_directory|glob|"
    "search_file_content|web_fetch|google_web_search|mcp_.*"
)
GEMINI_POST_TOOL_MATCHER = "run_shell_command|write_file|replace"
GEMINI_BUDGET_POST_TOOL_MATCHER = GEMINI_POST_TOOL_MATCHER
GEMINI_SESSION_TAINT_GUIDANCE = (
    "[Nomos Container Note] Headless Gemini CLI marks tokens starting with '/' from "
    "prior command stdout as <untrusted_context> and may reject subsequent shell commands "
    "reusing those literal '/' flags or modified build scripts with 'Tool execution denied "
    "by policy.' When a legitimate task command (outside protected /tests, /solution, "
    "/logs/verifier) hits that headless check, pass relative paths from cwd or invoke "
    "via `python3 -c 'import subprocess; ...'` instead of searching for CLI policy docs."
)
GEMINI_TAINT_GUIDANCE_COMMAND = (
    "printf '%s\\n' "
    + shlex.quote(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "additionalContext": GEMINI_SESSION_TAINT_GUIDANCE
                }
            },
            separators=(",", ":"),
        )
    )
)
LOGS_ROOT_HYGIENE_COMMAND = (
    "if [ -d /logs ]; then chown 0:0 /logs && chmod 0755 /logs; fi"
)
VERTEX_INLINE_MEDIA_GUARD_SCRIPT = (
    "if ! command -v node >/dev/null 2>&1 && [ -s ~/.nvm/nvm.sh ]; then "
    ". ~/.nvm/nvm.sh >/dev/null 2>&1; fi; "
    "node -e '"
    'let d="";process.stdin.on("data",c=>d+=c);process.stdin.on("end",()=>{'
    'try{const j=JSON.parse(d||"{}"),i=j.tool_input||{},p=String(i.file_path||i.path||"");'
    "if(/\\.(png|jpe?g|gif|webp|bmp|tiff?|ico|pdf|mp4|mkv|avi|mov|webm|mp3|wav)$/i.test(p)){"
    "process.stdout.write(JSON.stringify({decision:\"deny\",reason:"
    "\"Vertex AI Gemini CLI rejects inline binary/image/PDF read_file turns with HTTP 400 INVALID_ARGUMENT. "
    "Inspect or process this file via run_shell_command using python3 (PIL, cv2, numpy, pypdf) or CLI utilities instead.\"}));"
    'return}}catch(e){}process.stdout.write("{}")})'
    "'"
)
EOL_DEBIAN_APT_REPAIR_COMMAND = (
    "set -eu; "
    "repaired=0; "
    "for f in /etc/apt/sources.list /etc/apt/sources.list.d/*.list; do "
    '[ -f "$f" ] || continue; '
    'if grep -Eq "bullseye|buster|stretch" "$f"; then '
    "sed -i "
    "-e '/-updates/d' "
    "-e '/-backports/d' "
    r"-e 's|https\?://deb\.debian\.org/debian-security|http://archive.debian.org/debian-security|g' "
    r"-e 's|https\?://security\.debian\.org/debian-security|http://archive.debian.org/debian-security|g' "
    r"-e 's|https\?://security\.debian\.org|http://archive.debian.org/debian-security|g' "
    r"-e 's|https\?://deb\.debian\.org/debian|http://archive.debian.org/debian|g' "
    '"$f"; '
    "repaired=1; "
    "fi; "
    "done; "
    '[ "$repaired" -eq 1 ]; '
    "mkdir -p /etc/apt/apt.conf.d; "
    'printf \'Acquire::Check-Valid-Until "false";\\n\' > /etc/apt/apt.conf.d/99archive-valid-until; '
    "rm -rf /var/lib/apt/lists/*"
)
SEMVER_RE = re.compile(r"(\d+\.\d+\.\d+)")


def _read_json_dict(path: Path) -> dict[str, Any]:
    """Read a JSON object from `path`, returning `{}` on missing or invalid data."""
    if not path.is_file():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _read_task_toml_timeout(path: Path) -> float | None:
    """Extract positive `[agent].timeout_sec` from a `task.toml` file if present."""
    if not path.is_file():
        return None
    try:
        parsed = tomllib.loads(path.read_text(encoding="utf-8"))
        raw_timeout = (parsed.get("agent") or {}).get("timeout_sec")
        if isinstance(raw_timeout, (int, float)) and raw_timeout > 0:
            return float(raw_timeout)
    except (OSError, tomllib.TOMLDecodeError, TypeError, ValueError):
        return None
    return None


def resolve_trial_budget_seconds(
    logs_dir: Path | None,
    explicit_budget_sec: int | None = None,
    auto_budget_radar: bool = True,
) -> int | None:
    """Resolve effective session budget seconds from `--ak`, `config.json`, `lock.json`, and `task.toml`."""
    if explicit_budget_sec is not None and explicit_budget_sec > 0:
        return int(explicit_budget_sec)
    if not auto_budget_radar or logs_dir is None:
        return None

    trial_dir = logs_dir.parent
    cfg = _read_json_dict(trial_dir / "config.json")
    lock = _read_json_dict(trial_dir / "lock.json")
    if not cfg and not lock:
        return None

    raw_mult = (
        cfg.get("agent_timeout_multiplier")
        or lock.get("agent_timeout_multiplier")
        or cfg.get("timeout_multiplier")
        or lock.get("timeout_multiplier")
        or 1.0
    )
    multiplier = float(raw_mult) if isinstance(raw_mult, (int, float)) and raw_mult > 0 else 1.0

    agent_cfg = cfg.get("agent") if isinstance(cfg.get("agent"), dict) else {}
    agent_lock = lock.get("agent") if isinstance(lock.get("agent"), dict) else {}
    max_sec_raw = agent_cfg.get("max_timeout_sec") or agent_lock.get("max_timeout_sec")
    max_sec = float(max_sec_raw) if isinstance(max_sec_raw, (int, float)) and max_sec_raw > 0 else None

    def _cap_and_scale(base_sec: float) -> int:
        bounded = min(base_sec, max_sec) if max_sec is not None else base_sec
        return max(1, int(bounded * multiplier))

    override_sec = agent_cfg.get("override_timeout_sec") or agent_lock.get("override_timeout_sec")
    if isinstance(override_sec, (int, float)) and override_sec > 0:
        return _cap_and_scale(float(override_sec))

    task_cfg = cfg.get("task") if isinstance(cfg.get("task"), dict) else {}
    task_lock = lock.get("task") if isinstance(lock.get("task"), dict) else {}
    task_path = str(task_cfg.get("path") or task_lock.get("path") or "").strip()
    task_name = str(task_cfg.get("name") or task_lock.get("name") or "").strip().strip("/")
    digest = str(task_cfg.get("ref") or task_lock.get("digest") or "").strip()
    digest_hex = digest.removeprefix("sha256:") if digest.startswith("sha256:") else ""

    if task_path:
        local_toml = Path(task_path).expanduser() / "task.toml"
        found = _read_task_toml_timeout(local_toml)
        if found is not None:
            return _cap_and_scale(found)

    cache_base = Path(
        os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
    ).expanduser()
    tasks_cache = cache_base / "harbor" / "tasks"
    if not tasks_cache.is_dir():
        return None

    candidates: list[Path] = []
    if "/" in task_name:
        org, pkg_name = task_name.split("/", 1)
        pkg_root = tasks_cache / "packages" / org / pkg_name
        if digest_hex:
            candidates.append(pkg_root / digest_hex / "task.toml")
        if pkg_root.is_dir():
            candidates.extend(sorted(pkg_root.glob("*/task.toml")))

    clean_rel = task_path.strip("/")
    if clean_rel:
        candidates.extend(sorted(tasks_cache.glob(f"*/{clean_rel}/task.toml")))
    if task_name and "/" not in task_name and task_name != clean_rel:
        candidates.extend(sorted(tasks_cache.glob(f"*/{task_name}/task.toml")))

    for candidate in candidates:
        found = _read_task_toml_timeout(candidate)
        if found is not None:
            return _cap_and_scale(found)
    return None


def _build_hook_flag_suffix(
    hermetic_dir: str | None = None,
    container_sandbox: bool = True,
    budget_sec: int | None = None,
    budget_warn_pct: int | None = None,
    budget_crit_pct: int | None = None,
    loop_strikes: int | None = None,
    advisory_style: str | None = None,
) -> str:
    """Format CLI flags for `nomos hook` inside disposable or repo-scoped containers."""
    parts: list[str] = []
    if hermetic_dir and hermetic_dir.strip():
        parts.append(f" --hermetic-dir {shlex.quote(hermetic_dir.strip())}")
    elif container_sandbox:
        parts.append(" --container-sandbox")
    if budget_sec is not None and budget_sec > 0:
        parts.append(f" --budget-sec {int(budget_sec)}")
    if budget_warn_pct is not None and int(budget_warn_pct) != 75:
        parts.append(f" --budget-warn-pct {int(budget_warn_pct)}")
    if budget_crit_pct is not None and int(budget_crit_pct) != 90:
        parts.append(f" --budget-crit-pct {int(budget_crit_pct)}")
    if loop_strikes is not None and int(loop_strikes) != 3:
        parts.append(f" --loop-strikes {int(loop_strikes)}")
    clean_style = (advisory_style or "").strip().lower()
    if clean_style and clean_style != "standard":
        parts.append(f" --advisory-style {shlex.quote(clean_style)}")
    return "".join(parts)


def build_claude_web_search_guard_settings() -> dict[str, Any]:
    """Return documented Claude Code permission rules disabling `WebSearch` and `WebFetch`."""
    return {
        "permissions": {
            "deny": ["WebSearch", "WebFetch"],
        }
    }


def build_gemini_tool_hygiene_settings(
    disable_web_search: bool = True,
    exclude_cli_help: bool = True,
) -> dict[str, Any]:
    """Return documented Gemini CLI settings excluding web-search and `cli_help` distractions."""
    cfg: dict[str, Any] = {}
    excluded: list[str] = []
    if disable_web_search:
        excluded.extend(["google_web_search", "web_fetch"])
    if exclude_cli_help:
        excluded.append("cli_help")
        cfg["agents"] = {
            "overrides": {
                "cli_help": {
                    "enabled": False,
                }
            }
        }
    if excluded:
        cfg["tools"] = {"exclude": excluded}
    return cfg


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
    quoted_bin = shlex.quote(nomos_bin)
    pre_suffix = _build_hook_flag_suffix(
        hermetic_dir=hermetic_dir,
        container_sandbox=container_sandbox,
        budget_sec=budget_sec,
        loop_strikes=loop_strikes,
    )
    post_suffix = _build_hook_flag_suffix(
        hermetic_dir=hermetic_dir,
        container_sandbox=container_sandbox,
        budget_sec=budget_sec,
        budget_warn_pct=budget_warn_pct,
        budget_crit_pct=budget_crit_pct,
        advisory_style=advisory_style,
    )
    pre_cmd = f"{quoted_bin} hook pre-tool{pre_suffix}"
    post_cmd = f"{quoted_bin} hook post-tool{post_suffix}"
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
    budget_sec: int | None = None,
    budget_warn_pct: int | None = None,
    budget_crit_pct: int | None = None,
    loop_strikes: int | None = None,
    advisory_style: str | None = None,
    gemini_taint_guidance: bool = False,
) -> dict[str, Any]:
    """Construct the canonical Gemini CLI `~/.gemini/settings.json` hook dictionary.

    Enables `hooksConfig.enabled = True` and registers `BeforeTool` (10000ms),
    `AfterTool` (5000ms, unconditionally covering `run_shell_command` for Verified
    Host/Tool-Health Snapshots), and optional `SessionStart` headless guidance.
    """
    quoted_bin = shlex.quote(nomos_bin)
    pre_suffix = _build_hook_flag_suffix(
        hermetic_dir=hermetic_dir,
        container_sandbox=container_sandbox,
        budget_sec=budget_sec,
        loop_strikes=loop_strikes,
    )
    post_suffix = _build_hook_flag_suffix(
        hermetic_dir=hermetic_dir,
        container_sandbox=container_sandbox,
        budget_sec=budget_sec,
        budget_warn_pct=budget_warn_pct,
        budget_crit_pct=budget_crit_pct,
        advisory_style=advisory_style,
    )
    pre_cmd = f"{quoted_bin} hook pre-tool{pre_suffix}"
    post_cmd = f"{quoted_bin} hook post-tool{post_suffix}"
    hooks_block: dict[str, Any] = {
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
    }
    if gemini_taint_guidance:
        hooks_block["SessionStart"] = [
            {
                "hooks": [
                    {
                        "name": "nomos-headless-taint-guidance",
                        "type": "command",
                        "command": GEMINI_TAINT_GUIDANCE_COMMAND,
                        "timeout": 3000,
                    }
                ]
            }
        ]
    return {
        "hooksConfig": {
            "enabled": True,
        },
        "hooks": hooks_block,
    }


def build_vertex_media_guard_hook_manifest() -> dict[str, Any]:
    """Build a `BeforeTool` guard preventing Gemini CLI Vertex HTTP 400 inline-media crashes."""
    return {
        "hooksConfig": {
            "enabled": True,
        },
        "hooks": {
            "BeforeTool": [
                {
                    "matcher": "read_file",
                    "hooks": [
                        {
                            "name": "vertex-inline-media-guard",
                            "type": "command",
                            "command": VERTEX_INLINE_MEDIA_GUARD_SCRIPT,
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
