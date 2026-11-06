"""Unit tests for UCT/MCTS trajectory feature extraction, evaluation, and CLI."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

PACKAGE_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PACKAGE_SRC) not in sys.path:
    sys.path.insert(0, str(PACKAGE_SRC))

from nomos_harbor.mcts_profiles import (  # noqa: E402
    ModelAdvisoryProfile,
    TrialTrajectoryFeatures,
    evaluate_profile_counterfactual,
    extract_trial_features,
    load_job_corpus,
    main,
    optimize_profile_mcts,
    resolve_model_advisory_profile,
    summarize_corpus,
)


def _write_sample_trial(trial_dir: Path) -> None:
    """Create a synthetic Harbor timeout trial with web-search and `cli_help` spirals."""
    (trial_dir / "agent").mkdir(parents=True)
    (trial_dir / "result.json").write_text(
        json.dumps(
            {
                "task_name": "task-timeout",
                "started_at": "2026-11-01T00:00:00Z",
                "finished_at": "2026-11-01T00:30:00Z",
                "verifier_result": {"rewards": {"reward": 0.0}},
                "exception_info": {"exception_type": "AgentTimeoutError"},
                "config": {"agent": {"override_timeout_sec": 1800}},
            }
        ),
        encoding="utf-8",
    )
    (trial_dir / "agent" / "trajectory.json").write_text(
        json.dumps(
            {
                "agent": {"model_name": "gemini-3.5-flash"},
                "steps": [
                    {
                        "tool_calls": [
                            {"function_name": "google_web_search", "arguments": {"query": "q"}},
                            {
                                "function_name": "invoke_agent",
                                "arguments": {"agent_name": "cli_help", "prompt": "policy"},
                            },
                            {"function_name": "run_shell_command", "arguments": {"cmd": "ls"}},
                        ],
                        "observation": "Tool execution denied by policy. [Nomos Budget]",
                    }
                ]
                * 20,
            }
        ),
        encoding="utf-8",
    )


class TestMCTSProfiles(unittest.TestCase):
    """Verify model profile selection, trajectory telemetry, UCT search, and CLI output."""

    def test_resolve_model_advisory_profile_families_and_errors(self) -> None:
        """Ensure model IDs map to calibrated family profiles and reject invalid overrides."""
        self.assertEqual(resolve_model_advisory_profile("vertex_ai/gemini-3.5-flash").name, "gemini")
        self.assertEqual(resolve_model_advisory_profile("anthropic/claude-sonnet-4-5").name, "claude")
        self.assertEqual(resolve_model_advisory_profile("openai/gpt-5").name, "gpt")
        self.assertEqual(resolve_model_advisory_profile("unknown/custom-llm").name, "default")
        with self.assertRaises(ValueError):
            resolve_model_advisory_profile("vertex_ai/gemini-3.5-flash", "invalid-profile")

    def test_extract_features_and_optimize_profile_mcts(self) -> None:
        """Verify ATIF feature extraction, corpus summarization, and UCT optimization."""
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp) / "sample-job"
            trial_a = job_dir / "task-timeout__1"
            _write_sample_trial(trial_a)
            (job_dir / "result.json").write_text("{}", encoding="utf-8")

            feat = extract_trial_features(trial_a)
            assert feat is not None
            self.assertEqual(feat.web_search_calls, 20)
            self.assertEqual(feat.cli_help_calls, 20)
            self.assertEqual(feat.shell_calls, 20)
            self.assertEqual(feat.policy_denials, 20)
            self.assertTrue(feat.budget_radar_fired)
            self.assertEqual(feat.duration_sec, 1800.0)

            corpus = load_job_corpus([job_dir])
            self.assertEqual(len(corpus), 1)
            summary = summarize_corpus(corpus)
            self.assertEqual(summary["timeouts"], 1)

            report = optimize_profile_mcts(corpus, model_family="gemini", rollouts=80, seed=7)
            self.assertGreater(report.best_score, report.baseline_score)
            self.assertTrue(report.best_profile.disable_web_search)
            self.assertTrue(report.best_profile.exclude_cli_help)

    def test_counterfactual_evaluator_deep_work_and_streak_penalties(self) -> None:
        """Verify counterfactual penalties on premature warnings and 2-strike loop breaks."""
        deep_pass = TrialTrajectoryFeatures(
            job_name="j",
            task_name="deep",
            agent_name="nomos-gemini-cli",
            model_name="gemini",
            reward=1.0,
            timed_out=False,
            verifier_infra_error=False,
            total_steps=60,
            shell_calls=50,
            web_search_calls=0,
            cli_help_calls=0,
            policy_denials=0,
            max_identical_streak=2,
            duration_sec=900.0,
            budget_sec=1800.0,
            budget_radar_fired=True,
            late_step_fraction=0.30,
        )
        aggressive = ModelAdvisoryProfile(
            name="agg", budget_warn_pct=60, budget_crit_pct=85, loop_strikes=2, advisory_style="standard"
        )
        balanced = resolve_model_advisory_profile("gemini")
        self.assertGreater(
            evaluate_profile_counterfactual(balanced, [deep_pass]),
            evaluate_profile_counterfactual(aggressive, [deep_pass]),
        )

    def test_cli_main_emits_json_report(self) -> None:
        """Verify `python3 -m nomos_harbor.mcts_profiles` CLI entrypoint outputs JSON."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job_dir = root / "job-1"
            _write_sample_trial(job_dir / "task-timeout__1")
            (job_dir / "result.json").write_text("{}", encoding="utf-8")

            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = main(["--jobs-dir", str(root), "--rollouts", "25", "--seed", "3"])
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertEqual(payload["trials_analyzed"], 1)
            self.assertEqual(payload["rollouts"], 25)
