"""Reproducible public leaderboard runner and preflight validator for `nomos-harbor`.

Generates exact evaluation commands, submission manifests, and job integrity checks for:
1. `Terminal-Bench 2.0` / `Harbor Index` (`tbench.ai` · `laude-institute/terminal-bench-leaderboard`)
   - `efficiency-flash`: `NomosGeminiCli` + `vertex_ai/gemini-3.5-flash` (`#1` lightweight class target)
   - `frontier-claude`: `NomosClaudeCode` + `anthropic/claude-opus-4-6` (Top-10 overall target)
   - `frontier-gemini`: `NomosGeminiCli` + `vertex_ai/gemini-3.1-pro-preview` (Top-10 overall target)
2. `AgentDojo` Official Defense Leaderboard (`agentdojo.spylab.ai` · `ethz-spylab/agentdojo`)
   - `NomosAgentDojoDefense` (`nomos v4.17.0`) across `workspace`, `slack`, `travel`, and `banking`.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from nomos_harbor.hook_constants import DEFAULT_NOMOS_VERSION, UTF8_ENCODING

TERMINAL_BENCH_DATASET = "terminal-bench@2.0"
TERMINAL_BENCH_TASK_COUNT = 89
OFFICIAL_LEADERBOARD_ATTEMPTS = 5
AGENTDOJO_SUITES = ("workspace", "slack", "travel", "banking")
DEFAULT_GEMINI_AGENTDOJO_MODEL = "gemini-3.5-flash"

TRACK_CONFIGS: dict[str, dict[str, str]] = {
    "efficiency-flash": {
        "agent": "nomos_harbor:NomosGeminiCli",
        "model": "vertex_ai/gemini-3.5-flash",
        "extra_ak": "",
        "description": "Lightweight/Flash efficiency leader (70.79%+ pass@1 vs. 47.4% stock Gemini CLI)",
    },
    "frontier-claude": {
        "agent": "nomos_harbor:NomosClaudeCode",
        "model": "anthropic/claude-opus-4-6",
        "extra_ak": "",
        "description": "Frontier Claude Code + Nomos v4.17.0 (Top-10 overall contender vs. 58.0% stock)",
    },
    "frontier-gemini": {
        "agent": "nomos_harbor:NomosGeminiCli",
        "model": "vertex_ai/gemini-3.1-pro-preview",
        "extra_ak": " --ak reasoning_effort=high",
        "description": "Frontier Gemini 3.1 Pro + Nomos v4.17.0 (Top-10 overall contender vs. 61.3% stock)",
    },
}


def build_terminal_bench_commands(
    track: str = "efficiency-flash",
    nomos_version: str = DEFAULT_NOMOS_VERSION,
    gcp_project: str | None = None,
    n_concurrent: int = 4,
    n_attempts: int = OFFICIAL_LEADERBOARD_ATTEMPTS,
    upload_public: bool = True,
) -> list[str]:
    """Build the reproducible Harbor evaluation and `lb submit` commands for `Terminal-Bench 2.0`."""
    if track not in TRACK_CONFIGS:
        valid = ", ".join(sorted(TRACK_CONFIGS))
        raise ValueError(f"unknown Terminal-Bench track {track!r}; expected one of: {valid}")
    cfg = TRACK_CONFIGS[track]
    ak_parts = [f"--ak nomos_version={nomos_version}"]
    if cfg["extra_ak"]:
        ak_parts.append(cfg["extra_ak"].strip())
    if gcp_project and gcp_project.strip():
        ak_parts.append(f"--ak gcp_project={gcp_project.strip()}")
    upload_flags = " --upload --public" if upload_public else ""
    run_cmd = (
        f"harbor run --dataset {TERMINAL_BENCH_DATASET} "
        f"--agent {cfg['agent']} "
        f"--model {cfg['model']} "
        f"{' '.join(ak_parts)} "
        f"--n-attempts {int(n_attempts)} "
        f"--n-concurrent {int(n_concurrent)}{upload_flags}"
    )
    submit_cmd = "uv run lb submit https://hub.harborframework.com/jobs/<job-uuid>"
    return [run_cmd, submit_cmd]


def build_agentdojo_eval_plan(
    model: str = DEFAULT_GEMINI_AGENTDOJO_MODEL,
    attack: str = "important_instructions",
    nomos_version: str = DEFAULT_NOMOS_VERSION,
) -> dict[str, Any]:
    """Build the reproducible `ethz-spylab/agentdojo` 4-suite evaluation & PR manifest."""
    suite_commands = [
        (
            f"python3 -m nomos_harbor.agentdojo_runner --suite {suite} "
            f"--model {model} --attack {attack}"
        )
        for suite in AGENTDOJO_SUITES
    ]
    return {
        "benchmark": "agentdojo",
        "defense_name": "nomos_v417_defense",
        "adapter": "nomos_harbor.agentdojo_defense:NomosAgentDojoDefense",
        "nomos_version": nomos_version,
        "model": model,
        "attack": attack,
        "suites": list(AGENTDOJO_SUITES),
        "commands": suite_commands,
        "leaderboard_repo": "https://github.com/ethz-spylab/agentdojo",
    }


def _extract_reward_score(trial_result: dict[str, Any]) -> tuple[bool, bool]:
    """Return `(passed, had_api_error)` from a Harbor trial `result.json` dictionary."""
    exc_info = trial_result.get("exception_info")
    had_error = bool(
        exc_info
        and "timeout" not in str(exc_info).lower()
    )
    verifier = trial_result.get("verifier_result") or {}
    rewards = verifier.get("rewards") or {}
    reward_val = rewards.get("reward", trial_result.get("reward", 0.0))
    try:
        passed = float(reward_val or 0.0) >= 1.0
    except (TypeError, ValueError):
        passed = False
    return passed, had_error


def validate_harbor_job_for_submission(
    job_dir: Path,
    expected_tasks: int = TERMINAL_BENCH_TASK_COUNT,
) -> dict[str, Any]:
    """Verify a completed Harbor job directory before opening a public leaderboard PR."""
    resolved = job_dir.expanduser().resolve()
    job_result_file = resolved / "result.json"
    if not job_result_file.is_file():
        raise FileNotFoundError(f"missing Harbor job result.json: {job_result_file}")

    trial_dirs = sorted(
        d for d in resolved.iterdir() if d.is_dir() and (d / "result.json").is_file()
    )
    passed_count = 0
    api_errors = 0
    for t_dir in trial_dirs:
        raw = json.loads((t_dir / "result.json").read_text(encoding=UTF8_ENCODING))
        passed, had_err = _extract_reward_score(raw)
        passed_count += int(passed)
        api_errors += int(had_err)

    completed = len(trial_dirs)
    pass_rate = round((passed_count / completed) * 100.0, 2) if completed else 0.0
    ready = completed >= expected_tasks and api_errors == 0
    return {
        "job_dir": str(resolved),
        "completed_trials": completed,
        "expected_tasks": expected_tasks,
        "passed_trials": passed_count,
        "pass_at_1_pct": pass_rate,
        "api_errors": api_errors,
        "ready_for_leaderboard": ready,
    }


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint emitting reproducible leaderboard commands or validating a Harbor job."""
    parser = argparse.ArgumentParser(
        description="Generate Terminal-Bench 2.0 / AgentDojo submission plans or validate Harbor jobs."
    )
    parser.add_argument(
        "--target",
        choices=("terminal-bench", "agentdojo", "validate-job"),
        default="terminal-bench",
    )
    parser.add_argument("--track", default="efficiency-flash")
    parser.add_argument("--model", default=DEFAULT_GEMINI_AGENTDOJO_MODEL)
    parser.add_argument("--n-attempts", type=int, default=OFFICIAL_LEADERBOARD_ATTEMPTS)
    parser.add_argument("--gcp-project", default=None)
    parser.add_argument("--job-dir", type=Path, default=None)
    args = parser.parse_args(argv)

    if args.target == "terminal-bench":
        payload: Any = {
            "track": args.track,
            "description": TRACK_CONFIGS[args.track]["description"],
            "commands": build_terminal_bench_commands(
                track=args.track,
                gcp_project=args.gcp_project,
                n_attempts=args.n_attempts,
            ),
        }
    elif args.target == "agentdojo":
        payload = build_agentdojo_eval_plan(model=args.model)
    else:
        if args.job_dir is None:
            raise ValueError("--job-dir is required when --target=validate-job")
        expected_total = TERMINAL_BENCH_TASK_COUNT * max(1, int(args.n_attempts))
        payload = validate_harbor_job_for_submission(args.job_dir, expected_tasks=expected_total)

    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
