"""Post-trial Harbor cloud token funnel to Nomos telemetry (`inference_spend.jsonl`)."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nomos_harbor.hook_constants import UTF8_ENCODING


@dataclass(frozen=True)
class HarborTrialSpend:
    """Verified Harbor container trial token consumption for FinOps ledger ingestion."""

    logs_dir: Path
    agent_name: str
    model_name: str
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int = 0
    origin: str = "vertex-ai"


def _is_test_runtime() -> bool:
    """Return True when running inside unittest/pytest unless telemetry test mode is active."""
    if os.environ.get("NOMOS_FORCE_TELEMETRY_TEST", "").strip() == "1":
        return False
    return "unittest" in sys.modules or "pytest" in sys.modules


def resolve_telemetry_base() -> str:
    """Resolve the local Nomos telemetry server base URL from environment configuration."""
    explicit = (
        os.environ.get("NOMOS_TELEMETRY_URL", "").strip()
        or os.environ.get("COCKPIT_URL", "").strip()
    )
    if explicit:
        return explicit.rstrip("/")
    scheme = os.environ.get("NOMOS_TELEMETRY_SCHEME", "http")
    host = os.environ.get("NOMOS_TELEMETRY_HOST", "localhost")
    port = os.environ.get("NOMOS_TELEMETRY_PORT", "8089")
    return f"{scheme}://{host}:{port}"


def resolve_treasury_jsonl_path() -> Path | None:
    """Resolve the durable append-only JSONL spend ledger path (`~/.nomos/telemetry/inference_spend.jsonl`)."""
    explicit = os.environ.get("NOMOS_TREASURY_JSONL", "").strip()
    if explicit:
        if explicit.lower() in ("none", "-"):
            return None
        return Path(explicit).expanduser()
    if _is_test_runtime():
        return None
    return Path.home() / ".nomos" / "telemetry" / "inference_spend.jsonl"


def compute_trial_event_id(logs_dir: Path) -> str:
    """Compute a deterministic idempotent event ID for a Harbor trial log directory."""
    digest = hashlib.sha256(str(logs_dir.resolve()).encode(UTF8_ENCODING)).hexdigest()[:16]
    return f"harbor-{digest}"


def _read_trajectory_metrics(logs_dir: Path) -> dict[str, Any] | None:
    """Load `final_metrics` from `logs_dir/trajectory.json` when present and valid."""
    traj_path = logs_dir / "trajectory.json"
    if not traj_path.is_file():
        return None
    try:
        payload = json.loads(traj_path.read_text(encoding=UTF8_ENCODING))
    except (OSError, json.JSONDecodeError):
        return None
    metrics = payload.get("final_metrics") if isinstance(payload, dict) else None
    return metrics if isinstance(metrics, dict) else None


def extract_context_or_trajectory_tokens(context: Any, logs_dir: Path) -> tuple[int, int, int]:
    """Extract `(prompt_tokens, completion_tokens, cached_tokens)` from `AgentContext` or `trajectory.json`."""
    n_in = int(getattr(context, "n_input_tokens", 0) or 0)
    n_out = int(getattr(context, "n_output_tokens", 0) or 0)
    n_cache = int(getattr(context, "n_cache_tokens", 0) or 0)
    if n_in > 0 or n_out > 0:
        return n_in, n_out, n_cache

    metrics = _read_trajectory_metrics(logs_dir)
    if metrics is None:
        return 0, 0, 0
    return (
        int(metrics.get("total_prompt_tokens") or 0),
        int(metrics.get("total_completion_tokens") or 0),
        int(metrics.get("total_cached_tokens") or 0),
    )


def _append_durable_jsonl(record: dict[str, Any]) -> bool:
    """Append `record` idempotently to `inference_spend.jsonl` when HTTP ingestion is unreachable."""
    ledger_path = resolve_treasury_jsonl_path()
    if ledger_path is None:
        return False
    event_id = str(record.get("id", ""))
    try:
        if event_id and ledger_path.is_file():
            existing = ledger_path.read_text(encoding=UTF8_ENCODING)
            if f'"id":"{event_id}"' in existing or f'"id": "{event_id}"' in existing:
                return True
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with ledger_path.open("a", encoding=UTF8_ENCODING) as fh:
            fh.write(json.dumps(record, separators=(",", ":")) + "\n")
        return True
    except OSError:
        return False


def emit_harbor_trial_spend(spend: HarborTrialSpend) -> bool:
    """Dispatch idempotent Harbor trial cloud spend to local telemetry with JSONL fallback."""
    if _is_test_runtime() or (spend.prompt_tokens <= 0 and spend.completion_tokens <= 0):
        return False
    trial_name = spend.logs_dir.parent.name or spend.logs_dir.name
    record: dict[str, Any] = {
        "id": compute_trial_event_id(spend.logs_dir),
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "subsystem": "benchmark-harbor",
        "origin": spend.origin,
        "model": spend.model_name or "vertex_ai/gemini-3.5-flash",
        "tier": "benchmark",
        "promptTokens": max(0, int(spend.prompt_tokens)),
        "completionTokens": max(0, int(spend.completion_tokens)),
        "cachedTokens": max(0, int(spend.cached_tokens)),
        "latencyMs": 0,
        "status": "SUCCESS",
        "details": f"Harbor {spend.agent_name} trial {trial_name} ({spend.prompt_tokens}+{spend.completion_tokens} tok)",
    }
    endpoint = f"{resolve_telemetry_base()}/api/telemetry/inference"
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(record).encode(UTF8_ENCODING),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            if 200 <= resp.status < 300:
                return True
    except (urllib.error.URLError, TimeoutError, OSError):
        return _append_durable_jsonl(record)
    return _append_durable_jsonl(record)
