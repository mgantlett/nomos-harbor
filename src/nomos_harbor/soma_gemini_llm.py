"""SOMA-routed Gemini `google-genai` adapter for `AgentDojo` with `thought_signature` & spend emission."""

from __future__ import annotations

import json
import os
import time
import urllib.request
import uuid
from typing import Any

from nomos_harbor.hook_constants import MSG_CONTENT_KEY, UTF8_ENCODING
from nomos_harbor.telemetry_funnel import emit_soma_call_spend


def resolve_soma_base() -> str:
    """Resolve the local SOMA gateway URL dynamically from environment configuration."""
    explicit = os.environ.get("SOMA_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    scheme = os.environ.get("SOMA_SCHEME", "http")
    host = os.environ.get("SOMA_HOST", "localhost")
    port = os.environ.get("SOMA_PORT", "9000")
    return f"{scheme}://{host}:{port}"


def verify_soma_gateway_online(soma_url: str | None = None, timeout: float = 3.0) -> dict[str, Any]:
    """Fail closed unless SOMA `:9000` responds healthy on `/api/health`."""
    base = (soma_url or resolve_soma_base()).rstrip("/")
    health_url = f"{base}/api/health"
    with urllib.request.urlopen(health_url, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode(UTF8_ENCODING))
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        raise RuntimeError(f"SOMA gateway unhealthy at {health_url}: {payload!r}")
    return payload


def extract_genai_usage_tokens(response: Any) -> tuple[int, int, int]:
    """Extract `(prompt_tokens, completion_plus_thought_tokens, cached_tokens)` from `google-genai`."""
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return 0, 0, 0
    prompt_tok = int(getattr(usage, "prompt_token_count", 0) or 0)
    cand_tok = int(getattr(usage, "candidates_token_count", 0) or 0)
    thought_tok = int(getattr(usage, "thoughts_token_count", 0) or 0)
    cached_tok = int(getattr(usage, "cached_content_token_count", 0) or 0)
    return prompt_tok, cand_tok + thought_tok, cached_tok


class _SomaHeaderInjector:
    """Callable `httpx` request event hook stripping client keys and stamping SOMA headers."""

    def __init__(self, subsystem: str) -> None:
        self.subsystem = subsystem

    def __call__(self, request: Any) -> None:
        request.headers.pop("x-goog-api-key", None)
        request.headers.pop("authorization", None)
        request.headers["X-Nomos-Provider"] = "gemini"
        request.headers["X-Nomos-Subsystem"] = self.subsystem
        request.headers["X-Nomos-Tier"] = "benchmark"


def build_soma_genai_client(soma_url: str, subsystem: str) -> Any:
    """Construct a `google.genai.Client` routing `/v1beta/` calls exclusively through SOMA."""
    import httpx
    from google import genai
    from google.genai import types as genai_types

    hook = _SomaHeaderInjector(subsystem=subsystem)
    http_client = httpx.Client(event_hooks={"request": [hook]}, timeout=90.0)
    return genai.Client(
        api_key="soma-managed",
        http_options=genai_types.HttpOptions(
            base_url=soma_url.rstrip("/"),
            api_version="v1beta",
            httpx_client=http_client,
        ),
    )


def _assistant_msg_to_parts(msg: dict[str, Any], sig_cache: dict[str, bytes]) -> list[Any]:
    """Serialize an `AgentDojo` assistant message to `genai_types.Part`, preserving `thought_signature`."""
    from google.genai import types as genai_types

    parts: list[Any] = []
    for block in msg.get(MSG_CONTENT_KEY) or []:
        if isinstance(block, dict) and block.get(MSG_CONTENT_KEY):
            parts.append(genai_types.Part.from_text(text=str(block[MSG_CONTENT_KEY])))
    for tc in msg.get("tool_calls") or []:
        fc = genai_types.FunctionCall(name=tc.function, args=dict(tc.args or {}))
        sig = sig_cache.get(str(tc.id or ""))
        if sig:
            parts.append(genai_types.Part(function_call=fc, thought_signature=sig))
        else:
            parts.append(genai_types.Part(function_call=fc))
    return parts


def _tool_msg_to_content(msg: dict[str, Any]) -> Any:
    """Convert a single `AgentDojo` `role == 'tool'` message to a `google.genai` `Content`."""
    from google.genai import types as genai_types

    tc = msg["tool_call"]
    if msg.get("error") is not None:
        resp_obj: dict[str, Any] = {"error": msg["error"]}
    else:
        text_val = "\n".join(b[MSG_CONTENT_KEY] for b in msg[MSG_CONTENT_KEY] if isinstance(b, dict))
        resp_obj = {"result": text_val}
    f_part = genai_types.Part.from_function_response(name=tc.function, response=resp_obj)
    return genai_types.Content(role="user", parts=[f_part])


BLOCKED_RECOVERY_PROMPT = (
    "Nomos policy blocked the unauthorized tool call above. "
    "Complete ONLY the user's original request using allowed tools or provide your final answer."
)


def _coalesce_role_turns(
    items: list[Any],
    make_content: Any,
    make_text_part: Any,
) -> list[Any]:
    """Merge consecutive same-role turns and ensure the request ends on a `user` turn."""
    merged: list[Any] = []
    for item in items:
        item_parts = list(getattr(item, "parts", None) or [])
        if not item_parts:
            continue
        if merged and getattr(merged[-1], "role", None) == getattr(item, "role", None):
            prev_parts = list(getattr(merged[-1], "parts", None) or [])
            merged[-1] = make_content(role=item.role, parts=prev_parts + item_parts)
        else:
            merged.append(item)
    if not merged or getattr(merged[-1], "role", None) == "model":
        merged.append(make_content(role="user", parts=[make_text_part(text=BLOCKED_RECOVERY_PROMPT)]))
    return merged


def convert_messages_for_google(messages: list[dict[str, Any]], sig_cache: dict[str, bytes]) -> list[Any]:
    """Convert `AgentDojo` messages to `google.genai` `Content` items ending on a `user` turn."""
    from google.genai import types as genai_types

    converted: list[Any] = []
    for msg in messages:
        role = msg["role"]
        if role == "user":
            u_parts = [genai_types.Part.from_text(text=b[MSG_CONTENT_KEY]) for b in msg[MSG_CONTENT_KEY]]
            converted.append(genai_types.Content(role="user", parts=u_parts))
        elif role == "assistant":
            a_parts = _assistant_msg_to_parts(msg, sig_cache)
            if a_parts:
                converted.append(genai_types.Content(role="model", parts=a_parts))
        elif role == "tool":
            converted.append(_tool_msg_to_content(msg))
    return _coalesce_role_turns(converted, genai_types.Content, genai_types.Part.from_text)


def response_to_assistant_msg(response: Any, sig_cache: dict[str, bytes]) -> dict[str, Any]:
    """Convert `GenerateContentResponse` to `ChatAssistantMessage` and cache `thought_signature`."""
    from agentdojo.functions_runtime import FunctionCall
    from agentdojo.types import ChatAssistantMessage, text_content_block_from_string

    if not response.candidates or not response.candidates[0].content:
        return ChatAssistantMessage(role="assistant", content=[], tool_calls=[])
    raw_parts = response.candidates[0].content.parts or []
    tool_calls = []
    text_blocks = []
    for part in raw_parts:
        if part.function_call:
            call_id = part.function_call.id or f"call_{uuid.uuid4().hex[:12]}"
            fn_name = part.function_call.name or "<empty-function-name>"
            sig = getattr(part, "thought_signature", None)
            if sig:
                sig_cache[call_id] = sig
            tool_calls.append(
                FunctionCall(function=fn_name, args=dict(part.function_call.args or {}), id=call_id)
            )
        if getattr(part, "text", None):
            text_blocks.append(text_content_block_from_string(part.text))
    return ChatAssistantMessage(role="assistant", content=text_blocks, tool_calls=tool_calls)


def _is_retryable_api_error(exc: Exception) -> bool:
    """Return True for transient rate-limit (`429`) or gateway (`5xx`) errors."""
    code = int(getattr(exc, "code", 0) or getattr(exc, "status_code", 0) or 0)
    if code in {429, 500, 502, 503, 504}:
        return True
    msg = str(exc).lower()
    return "429" in msg or "rate limit" in msg or "circuit breaker" in msg


def _generate_with_rate_backoff(
    client: Any,
    model: str,
    contents: Any,
    config: Any,
    max_attempts: int = 8,
) -> Any:
    """Call `client.models.generate_content` with backoff on SOMA/Gemini `429` or `5xx`."""
    for attempt in range(max_attempts):
        try:
            return client.models.generate_content(model=model, contents=contents, config=config)
        except Exception as exc:
            if attempt + 1 >= max_attempts or not _is_retryable_api_error(exc):
                raise
            delay = min(30.0, 2.0 * (1.7**attempt))
            time.sleep(delay)
    raise RuntimeError("unreachable retry exhaustion")


def invoke_soma_generate(
    *,
    client: Any,
    model: str,
    subsystem: str,
    contents: Any,
    config: Any,
    label: str,
) -> Any:
    """Invoke `generate_content` via SOMA with `429` backoff and record token spend in Cockpit."""
    t0 = time.monotonic()
    resp = _generate_with_rate_backoff(client, model, contents, config)
    p_tok, c_tok, cache_tok = extract_genai_usage_tokens(resp)
    emit_soma_call_spend(
        subsystem=subsystem,
        model_name=model,
        prompt_tokens=p_tok,
        completion_tokens=c_tok,
        cached_tokens=cache_tok,
        latency_ms=int((time.monotonic() - t0) * 1000),
        details=f"{label} {model}",
    )
    return resp
