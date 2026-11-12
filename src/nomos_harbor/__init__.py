"""Nomos deterministic governance adapter for Harbor, Terminal-Bench 2.0, and AgentDojo."""

from __future__ import annotations

from nomos_harbor.agentdojo_defense import (
    NomosAgentDojoDefense,
    NomosHookVerdict,
    resolve_local_nomos_binary,
)
from nomos_harbor.hooks import (
    DEFAULT_INSTALL_URL,
    DEFAULT_NOMOS_VERSION,
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
from nomos_harbor.leaderboard_kit import (
    build_agentdojo_eval_plan,
    build_terminal_bench_commands,
    validate_harbor_job_for_submission,
)
from nomos_harbor.mcts_features import extract_trial_features, load_job_corpus
from nomos_harbor.mcts_models import (
    MODEL_ADVISORY_PROFILES,
    MCTSOptimizationReport,
    ModelAdvisoryProfile,
    TrialTrajectoryFeatures,
    resolve_model_advisory_profile,
)
from nomos_harbor.mcts_search import optimize_profile_mcts

__version__ = "0.5.0"

__all__ = [
    "DEFAULT_INSTALL_URL",
    "DEFAULT_NOMOS_VERSION",
    "DEFAULT_REMOTE_NOMOS_BIN",
    "MCTSOptimizationReport",
    "MODEL_ADVISORY_PROFILES",
    "ModelAdvisoryProfile",
    "NomosAgentDojoDefense",
    "NomosClaudeCode",
    "NomosClaudeCodeAgent",
    "NomosClaudeCodeOptions",
    "NomosGeminiCli",
    "NomosGeminiCliAgent",
    "NomosGeminiCliOptions",
    "NomosHookVerdict",
    "TrialTrajectoryFeatures",
    "VertexGeminiCli",
    "VertexGeminiCliOptions",
    "__version__",
    "build_agentdojo_eval_plan",
    "build_claude_web_search_guard_settings",
    "build_gemini_tool_hygiene_settings",
    "build_nomos_gemini_hook_manifest",
    "build_nomos_hook_manifest",
    "build_nomos_install_command",
    "build_terminal_bench_commands",
    "extract_trial_features",
    "load_job_corpus",
    "merge_hook_manifests",
    "optimize_profile_mcts",
    "parse_combined_version",
    "resolve_local_nomos_binary",
    "resolve_model_advisory_profile",
    "resolve_trial_budget_seconds",
    "validate_harbor_job_for_submission",
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
