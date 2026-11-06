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
from dataclasses import asdict
from pathlib import Path

from nomos_harbor.mcts_eval import evaluate_profile_counterfactual
from nomos_harbor.mcts_features import (
    _parse_iso_seconds,
    extract_trial_features,
    load_job_corpus,
    summarize_corpus,
)
from nomos_harbor.mcts_models import (
    MODEL_ADVISORY_PROFILES,
    MCTSOptimizationReport,
    ModelAdvisoryProfile,
    TrialTrajectoryFeatures,
    _MCTSNode,
    resolve_model_advisory_profile,
)
from nomos_harbor.mcts_search import (
    _action_space_for_depth,
    _apply_action,
    _build_profile_from_partial,
    optimize_profile_mcts,
)

__all__ = [
    "MCTSOptimizationReport",
    "MODEL_ADVISORY_PROFILES",
    "ModelAdvisoryProfile",
    "TrialTrajectoryFeatures",
    "_MCTSNode",
    "_action_space_for_depth",
    "_apply_action",
    "_build_profile_from_partial",
    "_cli_main",
    "_parse_iso_seconds",
    "evaluate_profile_counterfactual",
    "extract_trial_features",
    "load_job_corpus",
    "main",
    "optimize_profile_mcts",
    "resolve_model_advisory_profile",
    "summarize_corpus",
]


def _discover_job_dirs(root: Path) -> list[Path]:
    """Locate Harbor job directories under `root` (supporting single-job or multi-job roots)."""
    if (root / "result.json").is_file() and any(
        (d / "result.json").is_file() for d in root.iterdir() if d.is_dir()
    ):
        return [root]
    return [
        d
        for d in sorted(root.iterdir())
        if d.is_dir() and (d / "result.json").is_file()
    ]


def main(argv: list[str] | None = None) -> int:
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
    args = parser.parse_args(argv)

    root: Path = args.jobs_dir.expanduser().resolve()
    job_dirs = _discover_job_dirs(root)
    trials = load_job_corpus(job_dirs)
    report = optimize_profile_mcts(
        trials=trials,
        model_family=args.model_family,
        rollouts=args.rollouts,
        seed=args.seed,
    )
    print(json.dumps(asdict(report), indent=2))
    return 0


_cli_main = main


if __name__ == "__main__":
    raise SystemExit(main())
