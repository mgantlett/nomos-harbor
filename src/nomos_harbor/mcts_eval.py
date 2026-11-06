"""Counterfactual trajectory utility evaluator for UCT/MCTS advisory profile search."""

from __future__ import annotations

from nomos_harbor.mcts_models import ModelAdvisoryProfile, TrialTrajectoryFeatures


def _web_search_utility_delta(
    profile: ModelAdvisoryProfile, tr: TrialTrajectoryFeatures, steps: int
) -> float:
    """Score step-budget recovery from disabling benchmark web-search spirals."""
    if tr.web_search_calls <= 0:
        return 0.0
    web_frac = min(0.45, tr.web_search_calls / steps)
    if not profile.disable_web_search:
        return -0.08 * web_frac
    if tr.timed_out:
        return 0.38 * web_frac
    if tr.reward < 1.0:
        return 0.16 * web_frac
    return 0.015 * web_frac


def _spiral_and_taint_utility_delta(
    profile: ModelAdvisoryProfile, tr: TrialTrajectoryFeatures, steps: int
) -> float:
    """Score elimination of `cli_help` subagent turns and `<untrusted_context>` denials."""
    if tr.cli_help_calls <= 0 and tr.policy_denials <= 0:
        return 0.0
    delta = 0.0
    spiral_weight = min(0.35, (tr.cli_help_calls * 2 + tr.policy_denials) / steps)
    if profile.exclude_cli_help:
        delta += 0.30 * spiral_weight
    if profile.gemini_taint_guidance and tr.policy_denials > 0:
        delta += 0.22 * min(0.25, tr.policy_denials / steps)
    return delta


def _checkpoint_timing_utility_delta(
    profile: ModelAdvisoryProfile,
    tr: TrialTrajectoryFeatures,
    warn_runway: float,
    crit_gap: int,
) -> float:
    """Score budget warning/critical checkpoint alignment against timeout and deep-work runs."""
    if tr.timed_out:
        target_runway = 0.30 if tr.total_steps >= 45 else 0.25
        runway_alignment = max(0.0, 1.0 - abs(warn_runway - target_runway) * 5.5)
        delta = 0.055 * runway_alignment
        if 15 <= crit_gap <= 20 and profile.budget_crit_pct <= 90:
            delta += 0.018
        elif profile.budget_crit_pct >= 93:
            delta -= 0.012
        return delta

    if tr.reward >= 1.0 and tr.late_step_fraction > 0.20:
        if profile.budget_warn_pct <= 60:
            return -0.030
        if profile.budget_warn_pct <= 65:
            return -0.012
    return 0.0


def _loop_and_style_utility_delta(
    profile: ModelAdvisoryProfile, tr: TrialTrajectoryFeatures
) -> float:
    """Score loop circuit-breaker strike precision and step-horizon advisory verbosity."""
    delta = 0.0
    if tr.reward >= 1.0 and tr.max_identical_streak == 2 and profile.loop_strikes <= 2:
        delta -= 0.085
    if tr.reward < 1.0 and tr.max_identical_streak >= 3:
        if profile.loop_strikes == 3:
            delta += 0.035
        elif profile.loop_strikes > 3:
            delta -= 0.020 * (profile.loop_strikes - 3)

    if tr.total_steps >= 55:
        delta += 0.008 if profile.advisory_style == "terse" else -0.003
    elif tr.total_steps <= 30 and profile.advisory_style == "standard":
        delta += 0.004
    return delta


def evaluate_profile_counterfactual(
    profile: ModelAdvisoryProfile,
    trials: list[TrialTrajectoryFeatures],
) -> float:
    """Score a candidate `ModelAdvisoryProfile` on empirical Harbor trajectories.

    Computes counterfactual expected trial utility across valid trials by accounting for
    web-search elimination, `cli_help`/policy-denial avoidance, budget checkpoint runway,
    loop circuit-breaker precision, and advisory verbosity vs. step horizon.
    """
    valid = [t for t in trials if not t.verifier_infra_error]
    if not valid:
        return 0.0

    total_utility = 0.0
    warn_runway = (100 - profile.budget_warn_pct) / 100.0
    crit_gap = profile.budget_crit_pct - profile.budget_warn_pct

    for tr in valid:
        steps = max(tr.total_steps, 1)
        utility = 1.0 if tr.reward >= 1.0 else 0.0
        utility += _web_search_utility_delta(profile, tr, steps)
        utility += _spiral_and_taint_utility_delta(profile, tr, steps)
        utility += _checkpoint_timing_utility_delta(profile, tr, warn_runway, crit_gap)
        utility += _loop_and_style_utility_delta(profile, tr)
        total_utility += utility

    return total_utility / len(valid)
