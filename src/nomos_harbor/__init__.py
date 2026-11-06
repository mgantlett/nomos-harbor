"""Nomos deterministic governance adapter for Harbor and Harbor Index evaluations."""

from __future__ import annotations

from nomos_harbor.hooks import (
    DEFAULT_INSTALL_URL,
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
from nomos_harbor.mcts_profiles import (
    MODEL_ADVISORY_PROFILES,
    MCTSOptimizationReport,
    ModelAdvisoryProfile,
    TrialTrajectoryFeatures,
    extract_trial_features,
    load_job_corpus,
    optimize_profile_mcts,
    resolve_model_advisory_profile,
)

__version__ = "0.4.0"

__all__ = [
    "DEFAULT_INSTALL_URL",
    "DEFAULT_REMOTE_NOMOS_BIN",
    "MCTSOptimizationReport",
    "MODEL_ADVISORY_PROFILES",
    "ModelAdvisoryProfile",
    "NomosClaudeCode",
    "NomosClaudeCodeAgent",
    "NomosClaudeCodeOptions",
    "NomosGeminiCli",
    "NomosGeminiCliAgent",
    "NomosGeminiCliOptions",
    "TrialTrajectoryFeatures",
    "VertexGeminiCli",
    "VertexGeminiCliOptions",
    "__version__",
    "build_claude_web_search_guard_settings",
    "build_gemini_tool_hygiene_settings",
    "build_nomos_gemini_hook_manifest",
    "build_nomos_hook_manifest",
    "build_nomos_install_command",
    "extract_trial_features",
    "load_job_corpus",
    "merge_hook_manifests",
    "optimize_profile_mcts",
    "parse_combined_version",
    "resolve_model_advisory_profile",
    "resolve_trial_budget_seconds",
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
