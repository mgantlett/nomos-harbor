"""Shared hook constants, container repair scripts, and CLI flag formatters for `nomos_harbor`."""

from __future__ import annotations

import json
import re
import shlex
from typing import Any

DEFAULT_INSTALL_URL = "https://sophialabs.ca/install"
DEFAULT_NOMOS_VERSION = "v4.17.0"
DEFAULT_REMOTE_NOMOS_BIN = "/usr/local/bin/nomos"
HOOK_KEY_COMMAND = "command"
HOOK_KEY_HOOKS = "hooks"
HOOK_KEY_MATCHER = "matcher"
HOOK_KEY_NAME = "name"
HOOK_KEY_TIMEOUT = "timeout"
HOOK_KEY_TYPE = "type"
UTF8_ENCODING = "utf-8"

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


def make_command_hook_entry(
    command: str,
    timeout: int,
    name: str | None = None,
) -> dict[str, Any]:
    """Construct a single command-type hook entry dictionary."""
    entry: dict[str, Any] = {
        HOOK_KEY_TYPE: HOOK_KEY_COMMAND,
        HOOK_KEY_COMMAND: command,
        HOOK_KEY_TIMEOUT: timeout,
    }
    if name is not None:
        entry[HOOK_KEY_NAME] = name
    return entry


def _scope_flag_token(hermetic_dir: str | None, container_sandbox: bool) -> str:
    """Format the sandbox or hermetic-dir CLI flag token."""
    if hermetic_dir and hermetic_dir.strip():
        return f" --hermetic-dir {shlex.quote(hermetic_dir.strip())}"
    if container_sandbox:
        return " --container-sandbox"
    return ""


def _override_int_token(flag: str, value: int | None, default_val: int) -> str:
    """Format an integer CLI flag token when explicitly overridden from `default_val`."""
    if value is None or int(value) == default_val:
        return ""
    return f" {flag} {int(value)}"


def _style_flag_token(advisory_style: str | None) -> str:
    """Format `--advisory-style` when overridden from `standard`."""
    clean_style = (advisory_style or "").strip().lower()
    if clean_style and clean_style != "standard":
        return f" --advisory-style {shlex.quote(clean_style)}"
    return ""


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
    budget_token = (
        f" --budget-sec {int(budget_sec)}"
        if budget_sec is not None and budget_sec > 0
        else ""
    )
    return "".join(
        (
            _scope_flag_token(hermetic_dir, container_sandbox),
            budget_token,
            _override_int_token("--budget-warn-pct", budget_warn_pct, 75),
            _override_int_token("--budget-crit-pct", budget_crit_pct, 90),
            _override_int_token("--loop-strikes", loop_strikes, 3),
            _style_flag_token(advisory_style),
        )
    )


def build_pre_and_post_hook_commands(
    nomos_bin: str = DEFAULT_REMOTE_NOMOS_BIN,
    hermetic_dir: str | None = None,
    container_sandbox: bool = True,
    budget_sec: int | None = None,
    budget_warn_pct: int | None = None,
    budget_crit_pct: int | None = None,
    loop_strikes: int | None = None,
    advisory_style: str | None = None,
) -> tuple[str, str]:
    """Build shell-quoted `(pre_tool_cmd, post_tool_cmd)` strings for `nomos hook`."""
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
    return (
        f"{quoted_bin} hook pre-tool{pre_suffix}",
        f"{quoted_bin} hook post-tool{post_suffix}",
    )
