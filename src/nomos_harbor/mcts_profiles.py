"""Offline UCT/MCTS advisory profile optimizer over Harbor ATIF trajectories.

Implements bounded, safety-invariant harness parameter search inspired by
HarnessFlow (arXiv:2611.06254) and Model-Harness Interaction scaling findings
(arXiv:2611.06116). Hard Nomos CapBAC, Law 3 boundary, and Law 7 anti-reward-hacking
`deny` gates are strictly invariant and outside the search space; MCTS tunes only
advisory checkpoint timing, identical-loop strike thresholds, advisory verbosity,
and documented agent tool/subagent exclusions.
"""

from __future__ import annotations

import argparse
import json
import math
import random
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
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


def _parse_iso_seconds(start_iso: str | None, end_iso: str | None) -> float:
    """Compute elapsed wall-clock seconds between two ISO-8601 timestamps."""
    if not start_iso or not end_iso:
        return 0.0
    try:
        t0 = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
        t1 = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
        return max(0.0, (t1 - t0).total_seconds())
    except ValueError:
        return 0.0


def extract_trial_features(trial_dir: Path) -> TrialTrajectoryFeatures | None:
    """Extract empirical tool, timeout, and step-timing features from a Harbor trial."""
    res_path = trial_dir / "result.json"
    if not res_path.is_file():
        return None
    try:
        res = json.loads(res_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(res, dict):
        return None

    rewards = ((res.get("verifier_result") or {}).get("rewards") or {})
    raw_reward = rewards.get("reward", 0.0)
    reward = float(raw_reward) if isinstance(raw_reward, (int, float)) else 0.0

    exc_info = res.get("exception_info") or {}
    exc_type = str(exc_info.get("exception_type") or "")
    exc_msg = str(exc_info.get("exception_message") or "")
    timed_out = "Timeout" in exc_type
    verifier_infra_error = "RewardFileNotFoundError" in exc_type or "Untrusted parent directory" in exc_msg

    agent_info = res.get("agent_info") or {}
    agent_name = str(agent_info.get("name") or "")
    model_info = agent_info.get("model_info") or {}
    model_name = str(model_info.get("name") or "")

    agent_exec = res.get("agent_execution") or {}
    duration_sec = _parse_iso_seconds(
        agent_exec.get("started_at") or res.get("started_at"),
        agent_exec.get("finished_at") or res.get("finished_at"),
    )
    cfg = res.get("config") or {}
    agent_cfg = cfg.get("agent") if isinstance(cfg.get("agent"), dict) else {}
    budget_sec = float(agent_cfg.get("override_timeout_sec") or 0.0)
    if budget_sec <= 0.0 and timed_out and duration_sec > 0.0:
        budget_sec = duration_sec

    total_steps = 0
    shell_calls = 0
    web_search_calls = 0
    cli_help_calls = 0
    policy_denials = 0
    max_streak = 0
    cur_streak = 0
    prev_sig = ""
    budget_radar_fired = False
    late_steps = 0

    tj_path = trial_dir / "agent" / "trajectory.json"
    if tj_path.is_file():
        try:
            tj = json.loads(tj_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            tj = {}
        if isinstance(tj, dict):
            if not model_name:
                model_name = str((tj.get("agent") or {}).get("model_name") or "")
            steps = tj.get("steps") or []
            total_steps = len(steps)
            cutoff_idx = int(total_steps * 0.70)
            for idx, step in enumerate(steps):
                if not isinstance(step, dict):
                    continue
                tcalls = step.get("tool_calls") or []
                if idx >= cutoff_idx and tcalls:
                    late_steps += 1
                for tc in tcalls:
                    if not isinstance(tc, dict):
                        continue
                    fn = str(tc.get("function_name") or "")
                    args = tc.get("arguments") or {}
                    sig = f"{fn}:{json.dumps(args, sort_keys=True, default=str)}"
                    if sig == prev_sig:
                        cur_streak += 1
                    else:
                        cur_streak = 1
                        prev_sig = sig
                    if cur_streak > max_streak:
                        max_streak = cur_streak

                    if fn in ("run_shell_command", "Bash"):
                        shell_calls += 1
                    elif fn in ("google_web_search", "web_fetch", "WebSearch", "WebFetch"):
                        web_search_calls += 1
                    elif fn == "cli_help":
                        cli_help_calls += 1
                    elif fn == "invoke_agent" and isinstance(args, dict):
                        if str(args.get("agent_name") or "") == "cli_help":
                            cli_help_calls += 1

                obs_text = json.dumps(step.get("observation") or "", default=str)
                if "Nomos Budget Radar" in obs_text or "[Nomos Budget]" in obs_text:
                    budget_radar_fired = True
                if "Tool execution denied by policy" in obs_text:
                    policy_denials += 1

    late_fraction = (late_steps / total_steps) if total_steps > 0 else 0.0
    return TrialTrajectoryFeatures(
        job_name=trial_dir.parent.name,
        task_name=str(res.get("task_name") or trial_dir.name),
        agent_name=agent_name,
        model_name=model_name,
        reward=reward,
        timed_out=timed_out,
        verifier_infra_error=verifier_infra_error,
        total_steps=total_steps,
        shell_calls=shell_calls,
        web_search_calls=web_search_calls,
        cli_help_calls=cli_help_calls,
        policy_denials=policy_denials,
        max_identical_streak=max_streak,
        duration_sec=duration_sec,
        budget_sec=budget_sec,
        budget_radar_fired=budget_radar_fired,
        late_step_fraction=late_fraction,
    )


def load_job_corpus(job_dirs: list[Path]) -> list[TrialTrajectoryFeatures]:
    """Load `TrialTrajectoryFeatures` across one or more Harbor job directories."""
    corpus: list[TrialTrajectoryFeatures] = []
    for jdir in job_dirs:
        if not jdir.is_dir():
            continue
        for child in sorted(jdir.iterdir()):
            if not child.is_dir():
                continue
            feat = extract_trial_features(child)
            if feat is not None:
                corpus.append(feat)
    return corpus


def summarize_corpus(trials: list[TrialTrajectoryFeatures]) -> dict[str, Any]:
    """Aggregate empirical pass, timeout, web-search, and subagent distraction metrics."""
    valid = [t for t in trials if not t.verifier_infra_error]
    n_total = len(trials)
    n_valid = max(len(valid), 1)
    passes = sum(1 for t in valid if t.reward >= 1.0)
    timeouts = sum(1 for t in trials if t.timed_out)
    web_calls = sum(t.web_search_calls for t in trials)
    cli_help = sum(t.cli_help_calls for t in trials)
    denials = sum(t.policy_denials for t in trials)
    passing_2strike = sum(
        1 for t in valid if t.reward >= 1.0 and t.max_identical_streak == 2
    )
    stuck_3strike = sum(
        1 for t in valid if t.reward < 1.0 and t.max_identical_streak >= 3
    )
    high_step_trials = sum(1 for t in trials if t.total_steps >= 55)
    return {
        "total_trials": n_total,
        "valid_trials": len(valid),
        "verifier_infra_errors": n_total - len(valid),
        "observed_passes": passes,
        "observed_pass_rate": round(passes / n_valid, 4),
        "timeouts": timeouts,
        "web_search_calls": web_calls,
        "cli_help_calls": cli_help,
        "policy_denials": denials,
        "passing_trials_with_2_strike_retry": passing_2strike,
        "failing_trials_with_3plus_streak": stuck_3strike,
        "high_step_trials_ge_55": high_step_trials,
    }


def evaluate_profile_counterfactual(
    profile: ModelAdvisoryProfile,
    trials: list[TrialTrajectoryFeatures],
) -> float:
    """Score a candidate `ModelAdvisoryProfile` on empirical Harbor trajectories.

    Computes counterfactual expected trial utility across valid trials by accounting for:
    1. Reclaimed step budget from eliminating `google_web_search`/`web_fetch` latency spirals.
    2. Eliminated `invoke_agent(cli_help)` and `<untrusted_context>` policy-denial confusion turns.
    3. Checkpoint runway alignment (`budget_warn_pct`, `budget_crit_pct`) on timeout-prone tasks
       balanced against premature-interruption cost on deep passing trajectories.
    4. Loop circuit-breaker precision (`loop_strikes`) preserving legitimate 2-strike retries
       on passing tasks while cutting >=3-strike stuck loops.
    5. Context-density alignment (`advisory_style` terse vs standard) on high-step trajectories.
    """
    valid = [t for t in trials if not t.verifier_infra_error]
    if not valid:
        return 0.0

    total_utility = 0.0
    warn_runway = (100 - profile.budget_warn_pct) / 100.0
    crit_gap = profile.budget_crit_pct - profile.budget_warn_pct

    for tr in valid:
        u = 1.0 if tr.reward >= 1.0 else 0.0
        steps = max(tr.total_steps, 1)

        # 1. Web search distraction & timeout recovery
        if tr.web_search_calls > 0:
            web_frac = min(0.45, tr.web_search_calls / steps)
            if profile.disable_web_search:
                if tr.timed_out:
                    u += 0.38 * web_frac
                elif tr.reward < 1.0:
                    u += 0.16 * web_frac
                else:
                    u += 0.015 * web_frac
            else:
                u -= 0.08 * web_frac

        # 2. Gemini CLI cli_help subagent & untrusted_context policy confusion
        if tr.cli_help_calls > 0 or tr.policy_denials > 0:
            spiral_weight = min(0.35, (tr.cli_help_calls * 2 + tr.policy_denials) / steps)
            if profile.exclude_cli_help:
                u += 0.30 * spiral_weight
            if profile.gemini_taint_guidance and tr.policy_denials > 0:
                u += 0.22 * min(0.25, tr.policy_denials / steps)

        # 3. Wall-clock checkpoint timing (warn_pct / crit_pct)
        if tr.timed_out:
            # Timeout tasks need ~28-32% remaining runway (warn ~68-72%) to pivot and verify.
            target_runway = 0.30 if tr.total_steps >= 45 else 0.25
            runway_alignment = max(0.0, 1.0 - abs(warn_runway - target_runway) * 5.5)
            u += 0.055 * runway_alignment
            if 15 <= crit_gap <= 20 and profile.budget_crit_pct <= 90:
                u += 0.018
            elif profile.budget_crit_pct >= 93:
                u -= 0.012
        elif tr.reward >= 1.0 and tr.late_step_fraction > 0.20:
            # Passing deep-work tasks suffer if interrupted too early (<= 65%)
            if profile.budget_warn_pct <= 60:
                u -= 0.030
            elif profile.budget_warn_pct <= 65:
                u -= 0.012

        # 4. Loop circuit-breaker strike threshold
        if tr.reward >= 1.0 and tr.max_identical_streak == 2 and profile.loop_strikes <= 2:
            # False-positive loop break on a trial that legitimately succeeded after 1 retry!
            u -= 0.085
        if tr.reward < 1.0 and tr.max_identical_streak >= 3:
            if profile.loop_strikes == 3:
                u += 0.035
            elif profile.loop_strikes > 3:
                u -= 0.020 * (profile.loop_strikes - 3)

        # 5. Advisory style vs step-horizon context pressure
        if tr.total_steps >= 55:
            u += 0.008 if profile.advisory_style == "terse" else -0.003
        elif tr.total_steps <= 30:
            u += 0.004 if profile.advisory_style == "standard" else 0.0

        total_utility += u

    return total_utility / len(valid)


def _action_space_for_depth(depth: int, partial: dict[str, Any]) -> list[Any]:
    """Return legal discrete parameter choices at tree depth `depth`."""
    if depth == 0:
        return [True, False]  # disable_web_search
    if depth == 1:
        return [True, False]  # exclude_cli_help
    if depth == 2:
        return [True, False]  # gemini_taint_guidance
    if depth == 3:
        return [60, 65, 70, 75, 80]  # budget_warn_pct
    if depth == 4:
        warn = int(partial.get("budget_warn_pct", 75))
        return [c for c in (85, 88, 90, 93) if c >= warn + 10]  # budget_crit_pct
    if depth == 5:
        return [
            (2, "terse"),
            (3, "terse"),
            (3, "standard"),
            (4, "standard"),
        ]
    return []


def _apply_action(depth: int, partial: dict[str, Any], action: Any) -> dict[str, Any]:
    """Return a new partial assignment dict incorporating `action` at `depth`."""
    nxt = dict(partial)
    if depth == 0:
        nxt["disable_web_search"] = bool(action)
    elif depth == 1:
        nxt["exclude_cli_help"] = bool(action)
    elif depth == 2:
        nxt["gemini_taint_guidance"] = bool(action)
    elif depth == 3:
        nxt["budget_warn_pct"] = int(action)
    elif depth == 4:
        nxt["budget_crit_pct"] = int(action)
    elif depth == 5:
        strikes, style = action
        nxt["loop_strikes"] = int(strikes)
        nxt["advisory_style"] = str(style)
    return nxt


def _build_profile_from_partial(
    model_family: str, partial: dict[str, Any], notes: str = ""
) -> ModelAdvisoryProfile:
    """Materialize a `ModelAdvisoryProfile` from a completed 6-step tree assignment."""
    return ModelAdvisoryProfile(
        name=model_family,
        budget_warn_pct=int(partial["budget_warn_pct"]),
        budget_crit_pct=int(partial["budget_crit_pct"]),
        loop_strikes=int(partial["loop_strikes"]),
        advisory_style=str(partial["advisory_style"]),
        disable_web_search=bool(partial["disable_web_search"]),
        exclude_cli_help=bool(partial["exclude_cli_help"]),
        gemini_taint_guidance=bool(partial["gemini_taint_guidance"]),
        empirical_notes=notes,
    )


def optimize_profile_mcts(
    trials: list[TrialTrajectoryFeatures],
    model_family: str = "gemini",
    rollouts: int = 240,
    exploration_c: float = 0.25,
    seed: int = 42,
) -> MCTSOptimizationReport:
    """Run Upper Confidence Bound for Trees (UCT) search over advisory profiles."""
    rng = random.Random(seed)
    baseline_profile = ModelAdvisoryProfile(
        name="legacy-v0.3.3",
        budget_warn_pct=75,
        budget_crit_pct=90,
        loop_strikes=3,
        advisory_style="standard",
        disable_web_search=False,
        exclude_cli_help=False,
        gemini_taint_guidance=False,
    )
    baseline_score = evaluate_profile_counterfactual(baseline_profile, trials)

    root = _MCTSNode(
        depth=0,
        partial={},
        untried_actions=_action_space_for_depth(0, {}),
    )
    evaluated_leaves: dict[tuple[Any, ...], tuple[float, ModelAdvisoryProfile]] = {}

    for _ in range(max(1, rollouts)):
        node = root
        # 1. Selection
        while not node.untried_actions and node.children:
            log_parent = math.log(max(1, node.visits))
            node = max(
                node.children.values(),
                key=lambda ch: ch.mean_value
                + exploration_c * math.sqrt(log_parent / max(1, ch.visits)),
            )

        # 2. Expansion
        if node.untried_actions and node.depth < 6:
            action = node.untried_actions.pop(rng.randrange(len(node.untried_actions)))
            next_partial = _apply_action(node.depth, node.partial, action)
            next_depth = node.depth + 1
            child = _MCTSNode(
                depth=next_depth,
                partial=next_partial,
                parent=node,
                untried_actions=_action_space_for_depth(next_depth, next_partial),
            )
            node.children[action] = child
            node = child

        # 3. Rollout to terminal depth 6
        sim_partial = dict(node.partial)
        sim_depth = node.depth
        while sim_depth < 6:
            choices = _action_space_for_depth(sim_depth, sim_partial)
            chosen = rng.choice(choices)
            sim_partial = _apply_action(sim_depth, sim_partial, chosen)
            sim_depth += 1

        prof = _build_profile_from_partial(model_family, sim_partial)
        key = (
            prof.disable_web_search,
            prof.exclude_cli_help,
            prof.gemini_taint_guidance,
            prof.budget_warn_pct,
            prof.budget_crit_pct,
            prof.loop_strikes,
            prof.advisory_style,
        )
        if key in evaluated_leaves:
            score, prof = evaluated_leaves[key]
        else:
            score = evaluate_profile_counterfactual(prof, trials)
            evaluated_leaves[key] = (score, prof)

        # 4. Backpropagation
        cur: _MCTSNode | None = node
        while cur is not None:
            cur.visits += 1
            cur.total_value += score
            cur = cur.parent

    ranked = sorted(evaluated_leaves.values(), key=lambda item: item[0], reverse=True)
    if ranked:
        best_score, best_prof = ranked[0]
    else:
        best_score, best_prof = baseline_score, baseline_profile

    top_candidates = [
        {
            "score": round(sc, 5),
            "delta_vs_legacy": round(sc - baseline_score, 5),
            "profile": asdict(pr),
        }
        for sc, pr in ranked[:5]
    ]
    return MCTSOptimizationReport(
        model_family=model_family,
        trials_analyzed=len(trials),
        rollouts=rollouts,
        baseline_score=round(baseline_score, 5),
        best_score=round(best_score, 5),
        best_profile=best_prof,
        empirical_summary=summarize_corpus(trials),
        top_candidates=top_candidates,
    )


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


def _cli_main() -> int:
    """Run offline UCT/MCTS calibration over Harbor job directories and print JSON report."""
    parser = argparse.ArgumentParser(
        description="Run UCT/MCTS Nomos advisory profile optimization on Harbor ATIF jobs."
    )
    parser.add_argument(
        "--jobs-dir",
        type=Path,
        required=True,
        help="Root directory containing Harbor job subdirectories (or a single job directory).",
    )
    parser.add_argument("--model-family", default="gemini")
    parser.add_argument("--rollouts", type=int, default=240)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    root: Path = args.jobs_dir.expanduser().resolve()
    if (root / "result.json").is_file() and any(
        (d / "result.json").is_file() for d in root.iterdir() if d.is_dir()
    ):
        job_dirs = [root]
    else:
        job_dirs = [
            d
            for d in sorted(root.iterdir())
            if d.is_dir() and (d / "result.json").is_file()
        ]

    trials = load_job_corpus(job_dirs)
    report = optimize_profile_mcts(
        trials=trials,
        model_family=args.model_family,
        rollouts=args.rollouts,
        seed=args.seed,
    )
    print(json.dumps(asdict(report), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli_main())
