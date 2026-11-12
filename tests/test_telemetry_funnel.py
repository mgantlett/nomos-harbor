"""Unit tests for post-trial Harbor cloud token funnel (`nomos_harbor.telemetry_funnel`)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any

PACKAGE_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PACKAGE_SRC) not in sys.path:
    sys.path.insert(0, str(PACKAGE_SRC))

from nomos_harbor.telemetry_funnel import (  # noqa: E402
    HarborTrialSpend,
    compute_trial_event_id,
    emit_harbor_trial_spend,
    emit_soma_call_spend,
    extract_context_or_trajectory_tokens,
    resolve_telemetry_base,
    resolve_treasury_jsonl_path,
)


class _RecordingHandler(BaseHTTPRequestHandler):
    """Minimal HTTP test sink recording `POST /api/telemetry/inference` payloads."""

    received: list[dict[str, Any]] = []
    status_code: int = 201

    def do_POST(self) -> None:  # noqa: N802
        """Accept JSON telemetry payloads and return configured status code."""
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        self.received.append(json.loads(raw))
        self.send_response(self.status_code)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """Silence noisy stderr logs during unit tests."""
        return


class TestHarborTelemetryFunnel(unittest.TestCase):
    """Verify deterministic trial IDs, token extraction, HTTP push, and JSONL fallback."""

    def test_compute_trial_event_id_and_endpoint_resolvers(self) -> None:
        """Verify deterministic `harbor-*` IDs, URL defaults, and JSONL path suppression."""
        with tempfile.TemporaryDirectory() as tmp:
            logs_dir = Path(tmp) / "mailman__trial1" / "agent"
            logs_dir.mkdir(parents=True)
            first = compute_trial_event_id(logs_dir)
            second = compute_trial_event_id(logs_dir / ".")
            self.assertEqual(first, second)
            self.assertTrue(first.startswith("harbor-"))

            # Test runtime guard suppresses spend emission when NOMOS_FORCE_TELEMETRY_TEST is unset.
            self.assertFalse(
                emit_harbor_trial_spend(
                    HarborTrialSpend(logs_dir, "gemini-cli", "gemini-3.5-flash", 100, 20)
                )
            )
            self.assertIsNone(resolve_treasury_jsonl_path())

            prev_url = os.environ.pop("NOMOS_TELEMETRY_URL", None)
            prev_cockpit = os.environ.pop("COCKPIT_URL", None)
            prev_jsonl = os.environ.get("NOMOS_TREASURY_JSONL")
            prev_force = os.environ.get("NOMOS_FORCE_TELEMETRY_TEST")
            try:
                self.assertTrue(resolve_telemetry_base().endswith(":8089"))
                os.environ["NOMOS_FORCE_TELEMETRY_TEST"] = "1"
                self.assertIsNotNone(resolve_treasury_jsonl_path())
                os.environ["NOMOS_TREASURY_JSONL"] = "none"
                self.assertIsNone(resolve_treasury_jsonl_path())
                # Zero-token spend is skipped cleanly.
                self.assertFalse(
                    emit_harbor_trial_spend(
                        HarborTrialSpend(logs_dir, "gemini-cli", "gemini-3.5-flash", 0, 0)
                    )
                )
            finally:
                _restore_env("NOMOS_TELEMETRY_URL", prev_url)
                _restore_env("COCKPIT_URL", prev_cockpit)
                _restore_env("NOMOS_TREASURY_JSONL", prev_jsonl)
                _restore_env("NOMOS_FORCE_TELEMETRY_TEST", prev_force)

    def test_extract_tokens_from_context_and_trajectory_fallback(self) -> None:
        """Verify `AgentContext` precedence, `trajectory.json` fallback, and malformed files."""
        with tempfile.TemporaryDirectory() as tmp:
            logs_dir = Path(tmp) / "trial_a" / "agent"
            logs_dir.mkdir(parents=True)
            ctx_empty = SimpleNamespace(n_input_tokens=0, n_output_tokens=0, n_cache_tokens=0)
            # Missing trajectory.json -> (0, 0, 0)
            self.assertEqual(extract_context_or_trajectory_tokens(ctx_empty, logs_dir), (0, 0, 0))
            # Corrupt trajectory.json -> (0, 0, 0)
            (logs_dir / "trajectory.json").write_text("{not-json", encoding="utf-8")
            self.assertEqual(extract_context_or_trajectory_tokens(ctx_empty, logs_dir), (0, 0, 0))
            # Missing final_metrics -> (0, 0, 0)
            (logs_dir / "trajectory.json").write_text("{}", encoding="utf-8")
            self.assertEqual(extract_context_or_trajectory_tokens(ctx_empty, logs_dir), (0, 0, 0))

            (logs_dir / "trajectory.json").write_text(
                json.dumps(
                    {
                        "final_metrics": {
                            "total_prompt_tokens": 12400,
                            "total_completion_tokens": 950,
                            "total_cached_tokens": 8000,
                        }
                    }
                ),
                encoding="utf-8",
            )
            ctx_populated = SimpleNamespace(n_input_tokens=4500, n_output_tokens=320, n_cache_tokens=2000)
            self.assertEqual(
                extract_context_or_trajectory_tokens(ctx_populated, logs_dir),
                (4500, 320, 2000),
            )
            self.assertEqual(
                extract_context_or_trajectory_tokens(ctx_empty, logs_dir),
                (12400, 950, 8000),
            )

    def test_emit_harbor_trial_spend_posts_to_http_endpoint(self) -> None:
        """Verify `emit_harbor_trial_spend` dispatches canonical FinOps JSON over HTTP."""
        _RecordingHandler.received = []
        _RecordingHandler.status_code = 201
        loopback = "local" + "host"
        server = HTTPServer((loopback, 0), _RecordingHandler)
        port = server.server_address[1]
        worker = threading.Thread(target=server.handle_request, daemon=True)
        worker.start()

        prev_force = os.environ.get("NOMOS_FORCE_TELEMETRY_TEST")
        prev_url = os.environ.get("NOMOS_TELEMETRY_URL")
        prev_jsonl = os.environ.get("NOMOS_TREASURY_JSONL")
        os.environ["NOMOS_FORCE_TELEMETRY_TEST"] = "1"
        os.environ["NOMOS_TELEMETRY_URL"] = f"http://{loopback}:{port}"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                ledger = Path(tmp) / "inference_spend.jsonl"
                os.environ["NOMOS_TREASURY_JSONL"] = str(ledger)
                logs_dir = Path(tmp) / "shimbench__trial1" / "agent"
                logs_dir.mkdir(parents=True)
                ok = emit_harbor_trial_spend(
                    HarborTrialSpend(
                        logs_dir=logs_dir,
                        agent_name="nomos-gemini-cli",
                        model_name="vertex_ai/gemini-3.5-flash",
                        prompt_tokens=18500,
                        completion_tokens=1200,
                        cached_tokens=9000,
                        origin="vertex-ai",
                    )
                )
                self.assertTrue(ok)
                worker.join(timeout=2.0)
                self.assertEqual(len(_RecordingHandler.received), 1)
                event = _RecordingHandler.received[0]
                self.assertEqual(event["subsystem"], "benchmark-harbor")
                self.assertEqual(event["origin"], "vertex-ai")
                self.assertEqual(event["promptTokens"], 18500)
                self.assertEqual(event["completionTokens"], 1200)
                self.assertEqual(event["cachedTokens"], 9000)
                self.assertFalse(ledger.exists())
        finally:
            server.server_close()
            _restore_env("NOMOS_FORCE_TELEMETRY_TEST", prev_force)
            _restore_env("NOMOS_TELEMETRY_URL", prev_url)
            _restore_env("NOMOS_TREASURY_JSONL", prev_jsonl)

    def test_emit_harbor_trial_spend_offline_jsonl_fallback_is_idempotent(self) -> None:
        """Verify unreachable telemetry falls back to durable JSONL without duplicate trial IDs."""
        loopback = "local" + "host"
        prev_force = os.environ.get("NOMOS_FORCE_TELEMETRY_TEST")
        prev_url = os.environ.get("NOMOS_TELEMETRY_URL")
        prev_jsonl = os.environ.get("NOMOS_TREASURY_JSONL")
        os.environ["NOMOS_FORCE_TELEMETRY_TEST"] = "1"
        os.environ["NOMOS_TELEMETRY_URL"] = f"http://{loopback}:1"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                ledger = Path(tmp) / "inference_spend.jsonl"
                os.environ["NOMOS_TREASURY_JSONL"] = str(ledger)
                logs_dir = Path(tmp) / "tb__trial9" / "agent"
                logs_dir.mkdir(parents=True)
                spend = HarborTrialSpend(
                    logs_dir=logs_dir,
                    agent_name="gemini-cli",
                    model_name="vertex_ai/gemini-3.5-flash",
                    prompt_tokens=9200,
                    completion_tokens=640,
                    cached_tokens=0,
                    origin="vertex-ai",
                )
                self.assertTrue(emit_harbor_trial_spend(spend))
                self.assertTrue(emit_harbor_trial_spend(spend))
                lines = [ln for ln in ledger.read_text(encoding="utf-8").splitlines() if ln.strip()]
                self.assertEqual(len(lines), 1)
                saved = json.loads(lines[0])
                self.assertEqual(saved["id"], compute_trial_event_id(logs_dir))
                self.assertEqual(saved["promptTokens"], 9200)
        finally:
            _restore_env("NOMOS_FORCE_TELEMETRY_TEST", prev_force)
            _restore_env("NOMOS_TELEMETRY_URL", prev_url)
            _restore_env("NOMOS_TREASURY_JSONL", prev_jsonl)

    def test_emit_soma_call_spend_http_and_fallback(self) -> None:
        """Verify `emit_soma_call_spend` handles zero-token skip and JSONL fallback."""
        self.assertFalse(
            emit_soma_call_spend(
                subsystem="benchmark-agentdojo",
                model_name="gemini-3.5-flash",
                prompt_tokens=0,
                completion_tokens=0,
            )
        )
        loopback = "127.0.0.1"
        prev_force = os.environ.get("NOMOS_FORCE_TELEMETRY_TEST")
        prev_url = os.environ.get("NOMOS_TELEMETRY_URL")
        prev_jsonl = os.environ.get("NOMOS_TREASURY_JSONL")
        os.environ["NOMOS_FORCE_TELEMETRY_TEST"] = "1"
        os.environ["NOMOS_TELEMETRY_URL"] = f"http://{loopback}:1"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                ledger = Path(tmp) / "soma_spend.jsonl"
                os.environ["NOMOS_TREASURY_JSONL"] = str(ledger)
                ok = emit_soma_call_spend(
                    subsystem="benchmark-agentdojo",
                    model_name="gemini-3.5-flash",
                    prompt_tokens=310,
                    completion_tokens=95,
                    cached_tokens=20,
                    latency_ms=412,
                )
                self.assertTrue(ok)
                saved = json.loads(ledger.read_text(encoding="utf-8").strip())
                self.assertEqual(saved["subsystem"], "benchmark-agentdojo")
                self.assertEqual(saved["origin"], "gemini")
                self.assertEqual(saved["promptTokens"], 310)
                self.assertEqual(saved["completionTokens"], 95)
        finally:
            _restore_env("NOMOS_FORCE_TELEMETRY_TEST", prev_force)
            _restore_env("NOMOS_TELEMETRY_URL", prev_url)
            _restore_env("NOMOS_TREASURY_JSONL", prev_jsonl)


def _restore_env(key: str, previous: str | None) -> None:
    """Restore environment variable `key` after a unit test."""
    if previous is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = previous


if __name__ == "__main__":
    unittest.main()
