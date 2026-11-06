"""Automatic Harbor per-trial timeout and `--budget-sec` discovery helpers."""

from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path
from typing import Any

from nomos_harbor.hook_constants import UTF8_ENCODING


def _read_json_dict(path: Path) -> dict[str, Any]:
    """Read a JSON object from `path`, returning `{}` on missing or invalid data."""
    if not path.is_file():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding=UTF8_ENCODING))
    except (OSError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _read_task_toml_timeout(path: Path) -> float | None:
    """Extract positive `[agent].timeout_sec` from a `task.toml` file if present."""
    if not path.is_file():
        return None
    try:
        parsed = tomllib.loads(path.read_text(encoding=UTF8_ENCODING))
        raw_timeout = (parsed.get("agent") or {}).get("timeout_sec")
        if isinstance(raw_timeout, (int, float)) and raw_timeout > 0:
            return float(raw_timeout)
    except (OSError, tomllib.TOMLDecodeError, TypeError, ValueError):
        return None
    return None


def _subdict(mapping: dict[str, Any], key: str) -> dict[str, Any]:
    """Return nested `dict` at `mapping[key]`, or `{}` when absent or non-dict."""
    val = mapping.get(key)
    return val if isinstance(val, dict) else {}


def _first_positive_float(values: tuple[Any, ...]) -> float | None:
    """Return the first positive numeric value in `values` converted to `float`."""
    for item in values:
        if isinstance(item, (int, float)) and not isinstance(item, bool) and item > 0:
            return float(item)
    return None


def _cap_and_scale_timeout(
    base_sec: float, max_sec: float | None, multiplier: float
) -> int:
    """Bound `base_sec` by `max_sec` and scale by `multiplier` to a positive integer."""
    bounded = min(base_sec, max_sec) if max_sec is not None else base_sec
    return max(1, int(bounded * multiplier))


def _extract_timeout_bounds(
    cfg: dict[str, Any], lock: dict[str, Any]
) -> tuple[float, float | None, float | None]:
    """Extract `(multiplier, max_timeout_sec, override_timeout_sec)` from trial metadata."""
    multiplier = (
        _first_positive_float(
            (
                cfg.get("agent_timeout_multiplier"),
                lock.get("agent_timeout_multiplier"),
                cfg.get("timeout_multiplier"),
                lock.get("timeout_multiplier"),
            )
        )
        or 1.0
    )
    agent_cfg = _subdict(cfg, "agent")
    agent_lock = _subdict(lock, "agent")
    max_sec = _first_positive_float(
        (agent_cfg.get("max_timeout_sec"), agent_lock.get("max_timeout_sec"))
    )
    override_sec = _first_positive_float(
        (agent_cfg.get("override_timeout_sec"), agent_lock.get("override_timeout_sec"))
    )
    return multiplier, max_sec, override_sec


def _extract_task_locator(
    cfg: dict[str, Any], lock: dict[str, Any]
) -> tuple[str, str, str]:
    """Extract `(task_path, task_name, sha256_digest_hex)` from trial `config.json`/`lock.json`."""
    task_cfg = _subdict(cfg, "task")
    task_lock = _subdict(lock, "task")
    task_path = str(task_cfg.get("path") or task_lock.get("path") or "").strip()
    task_name = (
        str(task_cfg.get("name") or task_lock.get("name") or "").strip().strip("/")
    )
    digest = str(task_cfg.get("ref") or task_lock.get("digest") or "").strip()
    digest_hex = digest.removeprefix("sha256:") if digest.startswith("sha256:") else ""
    return task_path, task_name, digest_hex


def _candidate_cached_task_tomls(
    tasks_cache: Path, task_path: str, task_name: str, digest_hex: str
) -> list[Path]:
    """Enumerate candidate cached `task.toml` paths under Harbor's XDG task cache."""
    candidates: list[Path] = []
    if "/" in task_name:
        org, pkg_name = task_name.split("/", 1)
        pkg_root = tasks_cache / "packages" / org / pkg_name
        if digest_hex:
            candidates.append(pkg_root / digest_hex / "task.toml")
        if pkg_root.is_dir():
            candidates.extend(sorted(pkg_root.glob("*/task.toml")))

    clean_rel = task_path.strip("/")
    if clean_rel:
        candidates.extend(sorted(tasks_cache.glob(f"*/{clean_rel}/task.toml")))
    if task_name and "/" not in task_name and task_name != clean_rel:
        candidates.extend(sorted(tasks_cache.glob(f"*/{task_name}/task.toml")))
    return candidates


def resolve_trial_budget_seconds(
    logs_dir: Path | None,
    explicit_budget_sec: int | None = None,
    auto_budget_radar: bool = True,
) -> int | None:
    """Resolve effective session budget seconds from `--ak`, `config.json`, `lock.json`, and `task.toml`."""
    if explicit_budget_sec is not None and explicit_budget_sec > 0:
        return int(explicit_budget_sec)
    if not auto_budget_radar or logs_dir is None:
        return None

    trial_dir = logs_dir.parent
    cfg = _read_json_dict(trial_dir / "config.json")
    lock = _read_json_dict(trial_dir / "lock.json")
    if not cfg and not lock:
        return None

    multiplier, max_sec, override_sec = _extract_timeout_bounds(cfg, lock)
    if override_sec is not None:
        return _cap_and_scale_timeout(override_sec, max_sec, multiplier)

    task_path, task_name, digest_hex = _extract_task_locator(cfg, lock)
    if task_path:
        found = _read_task_toml_timeout(Path(task_path).expanduser() / "task.toml")
        if found is not None:
            return _cap_and_scale_timeout(found, max_sec, multiplier)

    cache_base = Path(
        os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
    ).expanduser()
    tasks_cache = cache_base / "harbor" / "tasks"
    if not tasks_cache.is_dir():
        return None

    for candidate in _candidate_cached_task_tomls(
        tasks_cache, task_path, task_name, digest_hex
    ):
        found = _read_task_toml_timeout(candidate)
        if found is not None:
            return _cap_and_scale_timeout(found, max_sec, multiplier)
    return None
