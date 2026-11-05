"""Nomos deterministic governance adapter for Harbor and Harbor Index evaluations."""

from __future__ import annotations

from nomos_harbor.hooks import (
    DEFAULT_INSTALL_URL,
    DEFAULT_REMOTE_NOMOS_BIN,
    build_nomos_gemini_hook_manifest,
    build_nomos_hook_manifest,
    build_nomos_install_command,
    merge_hook_manifests,
    parse_combined_version,
    resolve_trial_budget_seconds,
)

__version__ = "0.3.2"

__all__ = [
    "DEFAULT_INSTALL_URL",
    "DEFAULT_REMOTE_NOMOS_BIN",
    "NomosClaudeCode",
    "NomosClaudeCodeAgent",
    "NomosClaudeCodeOptions",
    "NomosGeminiCli",
    "NomosGeminiCliAgent",
    "NomosGeminiCliOptions",
    "VertexGeminiCli",
    "VertexGeminiCliOptions",
    "__version__",
    "build_nomos_gemini_hook_manifest",
    "build_nomos_hook_manifest",
    "build_nomos_install_command",
    "merge_hook_manifests",
    "parse_combined_version",
]


def __getattr__(attr_name: str):
    """Lazily import Harbor-dependent agent classes when `harbor` is installed."""
    if attr_name in {"NomosClaudeCode", "NomosClaudeCodeAgent", "NomosClaudeCodeOptions"}:
        from nomos_harbor import agent as _agent_mod

        return getattr(_agent_mod, attr_name)
    if attr_name in {
        "NomosGeminiCli",
        "NomosGeminiCliAgent",
        "NomosGeminiCliOptions",
        "VertexGeminiCli",
        "VertexGeminiCliOptions",
    }:
        from nomos_harbor import gemini_agent as _gemini_mod

        return getattr(_gemini_mod, attr_name)
    raise AttributeError(f"module 'nomos_harbor' has no attribute {attr_name!r}")
