"""Selectable reasoning substrates for the Hermes conversation loop.

This module is intentionally independent of ARC services.  The ARC-inspired
part is the runtime boundary and state/telemetry vocabulary; Hermes' existing
conversation loop remains responsible for tools, approvals, persistence,
compression, retries, streaming, and steering.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, Optional


_METRICS_WRITE_LOCK = threading.Lock()


def _output_item_type(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("type") or "")
    return str(getattr(item, "type", None) or "")


BACKEND_LEGACY = "legacy"
BACKEND_ARC_CONTINUOUS = "arc_continuous"


@dataclass(frozen=True)
class ProviderCapabilities:
    """Capabilities resolved from the active provider transport."""

    native_conversation_continuation: bool = False
    persistent_reasoning_state: bool = False
    provider_side_compaction: bool = False
    local_compaction: bool = False
    reasoning_effort_control: bool = False
    mid_turn_steering: bool = False
    tool_call_continuation: bool = False
    resumable_response_state: bool = False
    resumable_sessions: bool = False
    substrate_managed_continuity: bool = False
    native_encrypted_reasoning_replay: bool = False

    def to_dict(self) -> Dict[str, bool]:
        return {
            "native_conversation_continuation": self.native_conversation_continuation,
            "persistent_reasoning_state": self.persistent_reasoning_state,
            "provider_side_compaction": self.provider_side_compaction,
            "local_compaction": self.local_compaction,
            "reasoning_effort_control": self.reasoning_effort_control,
            "mid_turn_steering": self.mid_turn_steering,
            "tool_call_continuation": self.tool_call_continuation,
            "resumable_response_state": self.resumable_response_state,
            "resumable_sessions": self.resumable_sessions,
            "substrate_managed_continuity": self.substrate_managed_continuity,
            "native_encrypted_reasoning_replay": self.native_encrypted_reasoning_replay,
        }


def resolve_provider_capabilities(agent: Any) -> ProviderCapabilities:
    """Resolve transport capabilities once, at the runtime boundary.

    In particular, selecting ``arc_continuous`` never upgrades a provider to
    a native state protocol.  Responses transports can preserve native output
    items; all other transports use Hermes' explicit transcript/checkpoints.
    """

    responses = getattr(agent, "api_mode", "") == "codex_responses"
    provider = (getattr(agent, "provider", "") or "").strip().lower()
    base_url = (getattr(agent, "base_url", "") or "").lower()
    # The Responses wire format alone is not proof that a relay preserves
    # OpenAI's opaque reasoning state. Keep the native claim restricted to
    # direct OpenAI/ChatGPT Codex destinations until another adapter documents
    # equivalent replay semantics.
    direct_openai = (
        provider in {"openai", "openai-codex"}
        or "api.openai.com" in base_url
        or ("chatgpt.com" in base_url and "/backend-api/codex" in base_url)
    )
    openai_native_responses = responses and direct_openai
    runtime_caps = getattr(agent, "runtime_capabilities", {}) or {}
    native_replay = bool(
        openai_native_responses
        and getattr(agent, "_codex_reasoning_replay_enabled", False)
    )
    reasoning_config = getattr(agent, "reasoning_config", None)
    session_db = getattr(agent, "_session_db", None)
    return ProviderCapabilities(
        native_conversation_continuation=openai_native_responses,
        persistent_reasoning_state=native_replay,
        provider_side_compaction=bool(runtime_caps.get("native_compaction")),
        local_compaction=bool(getattr(agent, "compression_enabled", False)),
        reasoning_effort_control=bool(reasoning_config),
        # Hermes currently steers at a safe turn boundary.  This becomes true
        # only when a transport explicitly implements native mid-turn steering.
        mid_turn_steering=bool(getattr(agent, "native_mid_turn_steering", False)),
        tool_call_continuation=responses or getattr(agent, "api_mode", "")
        in {"chat_completions", "anthropic_messages", "bedrock_converse"},
        resumable_response_state=bool(
            responses and getattr(agent, "native_response_state_handle", False)
        ),
        resumable_sessions=bool(session_db and getattr(agent, "session_id", None)),
        substrate_managed_continuity=True,
        native_encrypted_reasoning_replay=native_replay,
    )


@dataclass
class ReasoningMetrics:
    backend: str
    capabilities: Dict[str, bool]
    model: Optional[str] = None
    provider: Optional[str] = None
    api_mode: Optional[str] = None
    session_id: Optional[str] = None
    started_at: float = field(default_factory=time.time)
    duration_ms: float = 0.0
    success: Optional[bool] = None
    model_calls: int = 0
    retries: int = 0
    errors: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cached_input_tokens: int = 0
    total_tokens: int = 0
    tool_calls: int = 0
    redundant_tool_calls: int = 0
    context_samples: list[int] = field(default_factory=list)
    compaction_events: int = 0
    trajectory_compactions: int = 0
    substrate_context_projections: int = 0
    checkpoint_events: int = 0
    native_state_reuses: int = 0
    native_state_mode: str = "none"
    estimated_cost_usd: Optional[float] = None

    def finish(self, result: Any, agent: Any) -> None:
        self.duration_ms = max(0.0, (time.time() - self.started_at) * 1000.0)
        if isinstance(result, dict):
            self.success = bool(result.get("completed")) and not bool(
                result.get("failed") or result.get("error")
            )
            messages = result.get("messages") or []
            self.tool_calls = sum(
                len(message.get("tool_calls") or [])
                for message in messages
                if isinstance(message, dict)
            )
            tool_keys: list[str] = []
            for message in messages:
                if not isinstance(message, dict) or message.get("role") != "tool":
                    continue
                tool_keys.append(
                    json.dumps(
                        {
                            "name": message.get("name") or message.get("tool_name"),
                            "content": message.get("content"),
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                        default=str,
                    )
                )
            self.redundant_tool_calls = max(0, len(tool_keys) - len(set(tool_keys)))
            logical_calls = int(result.get("api_calls") or 0)
            self.retries = max(self.retries, self.model_calls - logical_calls)
        else:
            self.success = False

        baseline = getattr(self, "_baseline", {})
        self.input_tokens = max(
            0,
            int(getattr(agent, "session_input_tokens", 0) or 0)
            - int(baseline.get("input_tokens", 0) or 0),
        )
        self.output_tokens = max(
            0,
            int(getattr(agent, "session_output_tokens", 0) or 0)
            - int(baseline.get("output_tokens", 0) or 0),
        )
        self.reasoning_tokens = max(
            0,
            int(getattr(agent, "session_reasoning_tokens", 0) or 0)
            - int(baseline.get("reasoning_tokens", 0) or 0),
        )
        self.cached_input_tokens = max(
            0,
            int(
                getattr(
                    agent,
                    "session_cached_input_tokens",
                    getattr(agent, "session_cache_read_tokens", 0),
                )
                or 0
            )
            - int(baseline.get("cached_input_tokens", 0) or 0),
        )
        self.total_tokens = self.input_tokens + self.output_tokens
        cost = getattr(agent, "session_estimated_cost_usd", None)
        base_cost = baseline.get("cost")
        self.estimated_cost_usd = (
            max(0.0, float(cost) - float(base_cost or 0.0))
            if cost is not None
            else None
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "backend": self.backend,
            "model": self.model,
            "provider": self.provider,
            "api_mode": self.api_mode,
            "session_id": self.session_id,
            "capabilities": dict(self.capabilities),
            "success": self.success,
            "duration_ms": round(self.duration_ms, 3),
            "model_calls": self.model_calls,
            "retries": self.retries,
            "errors": self.errors,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "total_tokens": self.total_tokens,
            "tool_calls": self.tool_calls,
            "redundant_tool_calls": self.redundant_tool_calls,
            "context_samples": list(self.context_samples),
            "compaction_events": self.compaction_events,
            "trajectory_compactions": self.trajectory_compactions,
            "substrate_context_projections": self.substrate_context_projections,
            "checkpoint_events": self.checkpoint_events,
            "native_state_reuses": self.native_state_reuses,
            "native_state_mode": self.native_state_mode,
            "estimated_cost_usd": self.estimated_cost_usd,
        }


class ReasoningBackend:
    """Small adapter boundary around the existing Hermes loop."""

    name = BACKEND_LEGACY

    def run(self, agent: Any, runner: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        metrics = ReasoningMetrics(
            self.name,
            resolve_provider_capabilities(agent).to_dict(),
            model=getattr(agent, "model", None),
            provider=getattr(agent, "provider", None),
            api_mode=getattr(agent, "api_mode", None),
            session_id=getattr(agent, "session_id", None),
        )
        metrics._baseline = {
            "input_tokens": getattr(agent, "session_input_tokens", 0),
            "output_tokens": getattr(agent, "session_output_tokens", 0),
            "reasoning_tokens": getattr(agent, "session_reasoning_tokens", 0),
            "cached_input_tokens": getattr(
                agent,
                "session_cached_input_tokens",
                getattr(agent, "session_cache_read_tokens", 0),
            ),
            "cost": getattr(agent, "session_estimated_cost_usd", 0.0),
        }
        caps = resolve_provider_capabilities(agent)
        metrics.native_state_mode = (
            "provider_native"
            if caps.persistent_reasoning_state
            else "substrate_managed"
            if self.name == BACKEND_ARC_CONTINUOUS
            else "legacy_transcript"
        )
        agent._reasoning_metrics = metrics
        originals = self._install_observers(agent, metrics)
        try:
            result = runner(agent, *args, **kwargs)
            metrics.finish(result, agent)
            _persist_metrics(agent, metrics)
            return _annotate_result(result, metrics)
        except Exception:
            metrics.errors += 1
            metrics.finish(None, agent)
            _persist_metrics(agent, metrics)
            raise
        finally:
            self._restore_observers(agent, originals)

    @staticmethod
    def _install_observers(agent: Any, metrics: ReasoningMetrics) -> list[tuple[str, Any]]:
        originals: list[tuple[str, Any]] = []

        def wrap_call(name: str) -> None:
            original = getattr(agent, name, None)
            if not callable(original):
                return
            originals.append((name, original))

            def observed(*args: Any, **kwargs: Any) -> Any:
                metrics.model_calls += 1
                request = args[0] if args and isinstance(args[0], dict) else kwargs
                if isinstance(request, dict):
                    # This is intentionally an approximation: exact provider
                    # usage is recorded after the response, while this sample
                    # captures request growth even when a provider fails.
                    try:
                        approx_context = max(
                            0, len(str(request.get("input", request))) // 4
                        )
                        metrics.context_samples.append(approx_context)
                        del metrics.context_samples[:-200]
                    except Exception:
                        pass
                if metrics.model_calls > 1:
                    metrics.retries += 1 if kwargs.get("retry_count", 0) else 0
                try:
                    response = original(*args, **kwargs)
                except Exception:
                    metrics.errors += 1
                    raise
                output_items = getattr(response, "output", None)
                if isinstance(output_items, (list, tuple)) and any(
                    _output_item_type(item) == "compaction" for item in output_items
                ):
                    metrics.compaction_events += 1
                if (
                    metrics.capabilities.get("persistent_reasoning_state")
                    and metrics.model_calls > 1
                ):
                    metrics.native_state_reuses += 1
                return response

            setattr(agent, name, observed)

        wrap_call("_interruptible_api_call")
        wrap_call("_interruptible_streaming_api_call")

        original_compress = getattr(agent, "_compress_context", None)
        if callable(original_compress):
            originals.append(("_compress_context", original_compress))

            def observed_compress(*args: Any, **kwargs: Any) -> Any:
                metrics.compaction_events += 1
                return original_compress(*args, **kwargs)

            setattr(agent, "_compress_context", observed_compress)
        return originals

    @staticmethod
    def _restore_observers(agent: Any, originals: Iterable[tuple[str, Any]]) -> None:
        for name, original in originals:
            setattr(agent, name, original)


class LegacyReasoningBackend(ReasoningBackend):
    name = BACKEND_LEGACY


class ArcContinuousReasoningBackend(ReasoningBackend):
    """ARC-inspired runtime with a real working-state/context projection."""

    name = BACKEND_ARC_CONTINUOUS

    def run(self, agent: Any, runner: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        from agent.continuous_state import ContinuousStateStore

        manager = ContinuousStateStore.for_agent(agent)
        manager._agent = agent
        objective = args[0] if args else kwargs.get("user_message", "")
        manager.begin_turn(objective)
        capabilities = resolve_provider_capabilities(agent)
        agent._reasoning_native_tier = (
            3
            if capabilities.persistent_reasoning_state
            else 2
        )
        agent._continuous_state_store = manager
        previous_hook = getattr(agent, "_reasoning_backend_prepare_context", None)
        agent._reasoning_backend_prepare_context = manager.prepare_api_messages
        try:
            result = super().run(agent, runner, *args, **kwargs)
            if isinstance(result, dict):
                result_messages = result.get("messages") or []
                manager.observe_messages(result_messages)
                manager.checkpoint("turn_complete")
                result["reasoning_state"] = manager.summary()
                result["reasoning_continuity"] = (
                    "provider_native"
                    if agent._reasoning_native_tier >= 3
                    else "substrate_managed"
                )
            return result
        finally:
            manager.persist()
            if previous_hook is None:
                try:
                    delattr(agent, "_reasoning_backend_prepare_context")
                except AttributeError:
                    pass
            else:
                agent._reasoning_backend_prepare_context = previous_hook
            agent._reasoning_native_tier = 0


_BACKENDS = {
    BACKEND_LEGACY: LegacyReasoningBackend(),
    BACKEND_ARC_CONTINUOUS: ArcContinuousReasoningBackend(),
}


def get_reasoning_backend(name: Optional[str]) -> ReasoningBackend:
    normalized = (name or BACKEND_LEGACY).strip().lower()
    try:
        return _BACKENDS[normalized]
    except KeyError as exc:
        available = ", ".join(sorted(_BACKENDS))
        raise ValueError(
            f"Unknown reasoning backend {name!r}; choose one of: {available}"
        ) from exc


def available_reasoning_backends() -> tuple[str, ...]:
    return tuple(sorted(_BACKENDS))


def _annotate_result(result: Any, metrics: ReasoningMetrics) -> Any:
    if isinstance(result, dict):
        result["reasoning_backend"] = metrics.backend
        result["reasoning_capabilities"] = dict(metrics.capabilities)
        result["reasoning_metrics"] = metrics.to_dict()
    return result


def _persist_metrics(agent: Any, metrics: ReasoningMetrics) -> None:
    """Append sanitized metrics for later A/B analysis when a log dir exists."""
    logs_dir = getattr(agent, "logs_dir", None)
    session_id = getattr(agent, "session_id", None)
    if logs_dir is None or not session_id:
        return
    try:
        path = logs_dir / "reasoning_metrics.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        record = metrics.to_dict()
        record["timestamp"] = time.time()
        with _METRICS_WRITE_LOCK, path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        # Telemetry must never change task behavior or turn success.
        return
