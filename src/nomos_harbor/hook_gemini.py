"""Gemini CLI hook manifest builders, tool-hygiene settings, and Vertex media guards."""

from __future__ import annotations

from typing import Any

from nomos_harbor.hook_constants import (
    DEFAULT_REMOTE_NOMOS_BIN,
    GEMINI_POST_TOOL_MATCHER,
    GEMINI_PRE_TOOL_MATCHER,
    GEMINI_TAINT_GUIDANCE_COMMAND,
    HOOK_KEY_HOOKS,
    HOOK_KEY_MATCHER,
    VERTEX_INLINE_MEDIA_GUARD_SCRIPT,
    build_pre_and_post_hook_commands,
    make_command_hook_entry,
)


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
        cfg["agents"] = {"overrides": {"cli_help": {"enabled": False}}}
    if excluded:
        cfg["tools"] = {"exclude": excluded}
    return cfg


def _build_gemini_hooks_block(
    pre_cmd: str, post_cmd: str, gemini_taint_guidance: bool
) -> dict[str, Any]:
    """Assemble `BeforeTool`, `AfterTool`, and optional `SessionStart` Gemini hook lists."""
    hooks_block: dict[str, Any] = {
        "BeforeTool": [
            {
                HOOK_KEY_MATCHER: GEMINI_PRE_TOOL_MATCHER,
                HOOK_KEY_HOOKS: [
                    make_command_hook_entry(pre_cmd, 10000, name="nomos-pre-tool")
                ],
            }
        ],
        "AfterTool": [
            {
                HOOK_KEY_MATCHER: GEMINI_POST_TOOL_MATCHER,
                HOOK_KEY_HOOKS: [
                    make_command_hook_entry(post_cmd, 5000, name="nomos-post-tool")
                ],
            }
        ],
    }
    if gemini_taint_guidance:
        hooks_block["SessionStart"] = [
            {
                HOOK_KEY_HOOKS: [
                    make_command_hook_entry(
                        GEMINI_TAINT_GUIDANCE_COMMAND,
                        3000,
                        name="nomos-headless-taint-guidance",
                    )
                ]
            }
        ]
    return hooks_block


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
    """Construct the canonical Gemini CLI `~/.gemini/settings.json` hook dictionary."""
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
        "hooksConfig": {"enabled": True},
        HOOK_KEY_HOOKS: _build_gemini_hooks_block(
            pre_cmd, post_cmd, gemini_taint_guidance
        ),
    }


def build_vertex_media_guard_hook_manifest() -> dict[str, Any]:
    """Build a `BeforeTool` guard preventing Gemini CLI Vertex HTTP 400 inline-media crashes."""
    return {
        "hooksConfig": {"enabled": True},
        HOOK_KEY_HOOKS: {
            "BeforeTool": [
                {
                    HOOK_KEY_MATCHER: "read_file",
                    HOOK_KEY_HOOKS: [
                        make_command_hook_entry(
                            VERTEX_INLINE_MEDIA_GUARD_SCRIPT,
                            5000,
                            name="vertex-inline-media-guard",
                        )
                    ],
                }
            ],
        },
    }
