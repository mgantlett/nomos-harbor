"""Upper Confidence Bound for Trees (UCT) search over `ModelAdvisoryProfile` parameters."""

from __future__ import annotations

import math
import random
from dataclasses import asdict
from typing import Any

from nomos_harbor.mcts_eval import evaluate_profile_counterfactual
from nomos_harbor.mcts_features import summarize_corpus
from nomos_harbor.mcts_models import (
    MCTSOptimizationReport,
    ModelAdvisoryProfile,
    TrialTrajectoryFeatures,
    _MCTSNode,
)


def _action_space_for_depth(depth: int, partial: dict[str, Any]) -> list[Any]:
    """Return legal discrete parameter choices at tree depth `depth`."""
    if depth in (0, 1, 2):
        return [True, False]
    if depth == 3:
        return [60, 65, 70, 75, 80]
    if depth == 4:
        warn = int(partial.get("budget_warn_pct", 75))
        return [c for c in (85, 88, 90, 93) if c >= warn + 10]
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
    bool_keys = ("disable_web_search", "exclude_cli_help", "gemini_taint_guidance")
    if 0 <= depth <= 2:
        nxt[bool_keys[depth]] = bool(action)
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


def _select_and_expand(
    root: _MCTSNode, rng: random.Random, exploration_c: float
) -> _MCTSNode:
    """Traverse UCT tree from `root` and expand one untried action when available."""
    node = root
    while not node.untried_actions and node.children:
        log_parent = math.log(max(1, node.visits))
        node = max(
            node.children.values(),
            key=lambda ch: ch.mean_value
            + exploration_c * math.sqrt(log_parent / max(1, ch.visits)),
        )

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
        return child
    return node


def _rollout_leaf_profile(
    node: _MCTSNode, model_family: str, rng: random.Random
) -> ModelAdvisoryProfile:
    """Complete a random rollout from `node` to terminal depth 6."""
    sim_partial = dict(node.partial)
    sim_depth = node.depth
    while sim_depth < 6:
        choices = _action_space_for_depth(sim_depth, sim_partial)
        sim_partial = _apply_action(sim_depth, sim_partial, rng.choice(choices))
        sim_depth += 1
    return _build_profile_from_partial(model_family, sim_partial)


def _backpropagate_score(node: _MCTSNode | None, score: float) -> None:
    """Propagate leaf utility `score` back up to the UCT root."""
    cur = node
    while cur is not None:
        cur.visits += 1
        cur.total_value += score
        cur = cur.parent


def _profile_cache_key(prof: ModelAdvisoryProfile) -> tuple[Any, ...]:
    """Return a hashable tuple of tunable fields for leaf memoization."""
    return (
        prof.disable_web_search,
        prof.exclude_cli_help,
        prof.gemini_taint_guidance,
        prof.budget_warn_pct,
        prof.budget_crit_pct,
        prof.loop_strikes,
        prof.advisory_style,
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
        depth=0, partial={}, untried_actions=_action_space_for_depth(0, {})
    )
    evaluated_leaves: dict[tuple[Any, ...], tuple[float, ModelAdvisoryProfile]] = {}

    for _ in range(max(1, rollouts)):
        node = _select_and_expand(root, rng, exploration_c)
        prof = _rollout_leaf_profile(node, model_family, rng)
        key = _profile_cache_key(prof)
        if key in evaluated_leaves:
            score, prof = evaluated_leaves[key]
        else:
            score = evaluate_profile_counterfactual(prof, trials)
            evaluated_leaves[key] = (score, prof)
        _backpropagate_score(node, score)

    ranked = sorted(evaluated_leaves.values(), key=lambda item: item[0], reverse=True)
    best_score, best_prof = (
        ranked[0] if ranked else (baseline_score, baseline_profile)
    )
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
