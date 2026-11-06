"""Dataclasses and calibrated model-family profiles for `nomos_harbor` UCT/MCTS tuning."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ModelAdvisoryProfile:
    """Calibrated, model-family-specific advisory configuration for `nomos-harbor`."""

    name: str
    budget_warn_pct: int = 75
    budget_crit_pct: int = 90
    loop_strikes: int = 3
    advisory_style: str = "standard"
    disable_web_search: bool = True
    exclude_cli_help: bool = True
    gemini_taint_guidance: bool = True
    empirical_notes: str = ""


@dataclass(frozen=True)
class TrialTrajectoryFeatures:
    """Empirical behavioral telemetry extracted from one Harbor trial directory."""

    job_name: str
    task_name: str
    agent_name: str
    model_name: str
    reward: float
    timed_out: bool
    verifier_infra_error: bool
    total_steps: int
    shell_calls: int
    web_search_calls: int
    cli_help_calls: int
    policy_denials: int
    max_identical_streak: int
    duration_sec: float
    budget_sec: float
    budget_radar_fired: bool
    late_step_fraction: float


@dataclass
class _MCTSNode:
    """Internal Upper Confidence Bound for Trees (UCT) search node."""

    depth: int
    partial: dict[str, Any]
    parent: _MCTSNode | None = None
    children: dict[Any, _MCTSNode] = field(default_factory=dict)
    untried_actions: list[Any] = field(default_factory=list)
    visits: int = 0
    total_value: float = 0.0

    @property
    def mean_value(self) -> float:
        """Return empirical mean value `Q / N` for visited nodes."""
        return (self.total_value / self.visits) if self.visits > 0 else 0.0


@dataclass(frozen=True)
class MCTSOptimizationReport:
    """Summary of offline UCT/MCTS calibration over Harbor ATIF trial corpora."""

    model_family: str
    trials_analyzed: int
    rollouts: int
    baseline_score: float
    best_score: float
    best_profile: ModelAdvisoryProfile
    empirical_summary: dict[str, Any]
    top_candidates: list[dict[str, Any]]


# Empirically calibrated defaults across model families (arXiv:2611.06254, arXiv:2611.06116).
# Hard Nomos safety gates (Law 3, Law 7, CapBAC) remain 100% invariant across all profiles.
MODEL_ADVISORY_PROFILES: dict[str, ModelAdvisoryProfile] = {
    "gemini": ModelAdvisoryProfile(
        name="gemini",
        budget_warn_pct=70,
        budget_crit_pct=88,
        loop_strikes=3,
        advisory_style="terse",
        disable_web_search=True,
        exclude_cli_help=True,
        gemini_taint_guidance=True,
        empirical_notes=(
            "Calibrated via UCT/MCTS across 411 Terminal-Bench 2.0 & 4.0 Gemini ATIF trials "
            "(1,893 web-search calls, 40 cli_help subagent spirals, 37 policy denials, 354/411 "
            "high-step >=55 trajectories): disables web-search & cli_help, shifts warn/crit to "
            "70%/88% for container verification runway, keeps 3-strike loop threshold, and emits "
            "terse single-clause advisories."
        ),
    ),
    "claude": ModelAdvisoryProfile(
        name="claude",
        budget_warn_pct=75,
        budget_crit_pct=90,
        loop_strikes=3,
        advisory_style="standard",
        disable_web_search=True,
        exclude_cli_help=True,
        gemini_taint_guidance=False,
        empirical_notes=(
            "Calibrated for Claude Sonnet/Opus trajectories: preserves standard structured "
            "advisories at 75%/90% budget checkpoints while blocking web-search rabbit holes."
        ),
    ),
    "gpt": ModelAdvisoryProfile(
        name="gpt",
        budget_warn_pct=70,
        budget_crit_pct=88,
        loop_strikes=3,
        advisory_style="terse",
        disable_web_search=True,
        exclude_cli_help=True,
        gemini_taint_guidance=False,
        empirical_notes=(
            "Calibrated for GPT/o-series high-call-rate agents: terse single-clause advisories "
            "(arXiv:2611.06116) with 70%/88% budget checkpoints and 3-strike loop protection."
        ),
    ),
    "default": ModelAdvisoryProfile(
        name="default",
        budget_warn_pct=75,
        budget_crit_pct=90,
        loop_strikes=3,
        advisory_style="standard",
        disable_web_search=True,
        exclude_cli_help=True,
        gemini_taint_guidance=True,
        empirical_notes="Canonical balanced profile for unknown model identifiers.",
    ),
}


def resolve_model_advisory_profile(
    model_name: str | None = None,
    explicit_profile: str | None = None,
) -> ModelAdvisoryProfile:
    """Select the calibrated `ModelAdvisoryProfile` from an explicit name or model ID."""
    if explicit_profile and explicit_profile.strip():
        key = explicit_profile.strip().lower()
        if key in MODEL_ADVISORY_PROFILES:
            return MODEL_ADVISORY_PROFILES[key]
        raise ValueError(
            f"Unknown advisory_profile {explicit_profile!r}; expected one of "
            f"{sorted(MODEL_ADVISORY_PROFILES.keys())}"
        )

    lower = (model_name or "").strip().lower()
    if "gemini" in lower:
        return MODEL_ADVISORY_PROFILES["gemini"]
    if "claude" in lower or "anthropic" in lower:
        return MODEL_ADVISORY_PROFILES["claude"]
    if any(tok in lower for tok in ("gpt", "openai", "o1", "o3", "o4", "codex")):
        return MODEL_ADVISORY_PROFILES["gpt"]
    return MODEL_ADVISORY_PROFILES["default"]
