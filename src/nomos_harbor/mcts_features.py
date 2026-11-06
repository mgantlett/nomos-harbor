"""ATIF trajectory telemetry extraction and Harbor job corpus aggregation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from nomos_harbor.hook_constants import UTF8_ENCODING
from nomos_harbor.mcts_models import TrialTrajectoryFeatures


@dataclass
class _StepAccumulator:
    """Mutable counters collected while scanning ATIF trajectory steps."""

    shell_calls: int = 0
    web_search_calls: int = 0
    cli_help_calls: int = 0
    policy_denials: int = 0
    max_streak: int = 0
    cur_streak: int = 0
    prev_sig: str = ""
    budget_radar_fired: bool = False
    late_steps: int = 0


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


def _load_json_object(path: Path) -> dict[str, Any] | None:
    """Read a JSON object from `path`, returning `None` if missing or non-dict."""
    if not path.is_file():
        return None
    try:
        parsed = json.loads(path.read_text(encoding=UTF8_ENCODING))
    except (OSError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _subdict(mapping: dict[str, Any], key: str) -> dict[str, Any]:
    """Return nested `dict` at `mapping[key]`, or `{}` when absent or non-dict."""
    val = mapping.get(key)
    return val if isinstance(val, dict) else {}


def _extract_reward_and_status(res: dict[str, Any]) -> tuple[float, bool, bool]:
    """Extract `(reward, timed_out, verifier_infra_error)` from `result.json`."""
    verifier_res = _subdict(res, "verifier_result")
    rewards = _subdict(verifier_res, "rewards")
    raw_reward = rewards.get("reward", 0.0)
    reward = float(raw_reward) if isinstance(raw_reward, (int, float)) else 0.0

    exc_info = _subdict(res, "exception_info")
    exc_type = str(exc_info.get("exception_type") or "")
    exc_msg = str(exc_info.get("exception_message") or "")
    timed_out = "Timeout" in exc_type
    infra_err = (
        "RewardFileNotFoundError" in exc_type
        or "Untrusted parent directory" in exc_msg
    )
    return reward, timed_out, infra_err


def _extract_timing_and_identity(
    res: dict[str, Any], timed_out: bool
) -> tuple[str, str, float, float]:
    """Extract `(agent_name, model_name, duration_sec, budget_sec)` from `result.json`."""
    agent_info = _subdict(res, "agent_info")
    agent_name = str(agent_info.get("name") or "")
    model_info = _subdict(agent_info, "model_info")
    model_name = str(model_info.get("name") or "")

    agent_exec = _subdict(res, "agent_execution")
    started = agent_exec.get("started_at") or res.get("started_at")
    finished = agent_exec.get("finished_at") or res.get("finished_at")
    duration_sec = _parse_iso_seconds(started, finished)

    agent_cfg = _subdict(_subdict(res, "config"), "agent")
    budget_sec = float(agent_cfg.get("override_timeout_sec") or 0.0)
    if budget_sec <= 0.0 and timed_out and duration_sec > 0.0:
        budget_sec = duration_sec
    return agent_name, model_name, duration_sec, budget_sec


def _record_tool_call(acc: _StepAccumulator, tc: Any) -> None:
    """Update streak and tool-category counters for a single ATIF tool call."""
    if not isinstance(tc, dict):
        return
    fn = str(tc.get("function_name") or "")
    args = tc.get("arguments") or {}
    sig = f"{fn}:{json.dumps(args, sort_keys=True, default=str)}"
    acc.cur_streak = acc.cur_streak + 1 if sig == acc.prev_sig else 1
    acc.prev_sig = sig
    if acc.cur_streak > acc.max_streak:
        acc.max_streak = acc.cur_streak

    if fn in ("run_shell_command", "Bash"):
        acc.shell_calls += 1
    elif fn in ("google_web_search", "web_fetch", "WebSearch", "WebFetch"):
        acc.web_search_calls += 1
    elif fn == "cli_help":
        acc.cli_help_calls += 1
    elif fn == "invoke_agent" and isinstance(args, dict):
        if str(args.get("agent_name") or "") == "cli_help":
            acc.cli_help_calls += 1


def _record_step_observation(acc: _StepAccumulator, observation: Any) -> None:
    """Detect Nomos Budget Radar advisories and headless policy denials in step output."""
    obs_text = json.dumps(observation or "", default=str)
    if "Nomos Budget Radar" in obs_text or "[Nomos Budget]" in obs_text:
        acc.budget_radar_fired = True
    if "Tool execution denied by policy" in obs_text:
        acc.policy_denials += 1


def _scan_trajectory_file(
    tj_path: Path, fallback_model: str
) -> tuple[str, int, _StepAccumulator]:
    """Parse `trajectory.json` and tally step, streak, and tool-invocation metrics."""
    acc = _StepAccumulator()
    tj = _load_json_object(tj_path)
    if tj is None:
        return fallback_model, 0, acc

    model_name = fallback_model or str(_subdict(tj, "agent").get("model_name") or "")
    steps = tj.get("steps")
    if not isinstance(steps, list):
        return model_name, 0, acc

    total_steps = len(steps)
    cutoff_idx = int(total_steps * 0.70)
    for idx, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        tcalls = step.get("tool_calls") or []
        if idx >= cutoff_idx and tcalls:
            acc.late_steps += 1
        for tc in tcalls:
            _record_tool_call(acc, tc)
        _record_step_observation(acc, step.get("observation"))
    return model_name, total_steps, acc


def extract_trial_features(trial_dir: Path) -> TrialTrajectoryFeatures | None:
    """Extract empirical tool, timeout, and step-timing features from a Harbor trial."""
    res = _load_json_object(trial_dir / "result.json")
    if res is None:
        return None

    reward, timed_out, infra_err = _extract_reward_and_status(res)
    agent_name, model_name, duration_sec, budget_sec = _extract_timing_and_identity(
        res, timed_out
    )
    model_name, total_steps, acc = _scan_trajectory_file(
        trial_dir / "agent" / "trajectory.json", model_name
    )
    late_fraction = (acc.late_steps / total_steps) if total_steps > 0 else 0.0
    return TrialTrajectoryFeatures(
        job_name=trial_dir.parent.name,
        task_name=str(res.get("task_name") or trial_dir.name),
        agent_name=agent_name,
        model_name=model_name,
        reward=reward,
        timed_out=timed_out,
        verifier_infra_error=infra_err,
        total_steps=total_steps,
        shell_calls=acc.shell_calls,
        web_search_calls=acc.web_search_calls,
        cli_help_calls=acc.cli_help_calls,
        policy_denials=acc.policy_denials,
        max_identical_streak=acc.max_streak,
        duration_sec=duration_sec,
        budget_sec=budget_sec,
        budget_radar_fired=acc.budget_radar_fired,
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


def _count_valid_outcomes(
    valid: list[TrialTrajectoryFeatures],
) -> tuple[int, int, int]:
    """Count `(passes, passing_2strike_retries, failing_3plus_streaks)` across valid trials."""
    passes = 0
    passing_2strike = 0
    stuck_3strike = 0
    for trial in valid:
        if trial.reward >= 1.0:
            passes += 1
            if trial.max_identical_streak == 2:
                passing_2strike += 1
        elif trial.max_identical_streak >= 3:
            stuck_3strike += 1
    return passes, passing_2strike, stuck_3strike


def summarize_corpus(trials: list[TrialTrajectoryFeatures]) -> dict[str, Any]:
    """Aggregate empirical pass, timeout, web-search, and subagent distraction metrics."""
    valid = [t for t in trials if not t.verifier_infra_error]
    n_total = len(trials)
    n_valid = max(len(valid), 1)
    passes, passing_2strike, stuck_3strike = _count_valid_outcomes(valid)
    return {
        "total_trials": n_total,
        "valid_trials": len(valid),
        "verifier_infra_errors": n_total - len(valid),
        "observed_passes": passes,
        "observed_pass_rate": round(passes / n_valid, 4),
        "timeouts": sum(1 for t in trials if t.timed_out),
        "web_search_calls": sum(t.web_search_calls for t in trials),
        "cli_help_calls": sum(t.cli_help_calls for t in trials),
        "policy_denials": sum(t.policy_denials for t in trials),
        "passing_trials_with_2_strike_retry": passing_2strike,
        "failing_trials_with_3plus_streak": stuck_3strike,
        "high_step_trials_ge_55": sum(1 for t in trials if t.total_steps >= 55),
    }
