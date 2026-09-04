"""Provider-neutral working state for the ARC-inspired Hermes substrate.

The normal Hermes transcript remains canonical for UI, sessions, approvals and
replay.  This module adds a second, deliberately smaller representation for
long-running ``arc_continuous`` turns.  It is a working set, not a replacement
for the transcript: the durable trajectory keeps the recent operational
observations while the state envelope keeps the facts needed to resume work.

There are no ARC service or game dependencies here.  The format is JSON so it
can be inspected, checkpointed, migrated, and resumed by tools outside the
agent process.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping


CONTINUOUS_STATE_SCHEMA_VERSION = 3
_MAX_RECENT_OBSERVATIONS = 18
_MAX_FACTS = 24
_MAX_DECISIONS = 18
_MAX_FAILURES = 12
_MAX_TRAJECTORY_COMPACTIONS = 12
_MAX_EVENT_KEYS = 256
_MAX_EXCERPT_CHARS = 4_000
_MAX_STATE_VALUE_CHARS = 1_200
_MAX_TASK_HISTORY = 8
_PATH_RE = re.compile(r"(?:^|[\s'\"`(])((?:/|\./|\.\./|[A-Za-z]:[\\/])[^\s'\"`,;)]+)")
_STATE_DELTA_RE = re.compile(
    r"<hermes-state-delta>\s*(\{.*?\})\s*</hermes-state-delta>", re.DOTALL
)
_SECRET_RE = re.compile(
    r"(?i)(?:api[_ -]?key|access[_ -]?token|auth(?:orization)?|password|secret)"
    r"\s*[:=]\s*[^\s,;]+|\b(?:sk|rk)-[A-Za-z0-9_-]{12,}\b"
)
_TASK_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "how", "i", "in", "is", "it", "me", "of", "on", "or", "please",
    "should", "the", "this", "to", "we", "with", "you", "your", "now",
}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, Mapping):
                item_text = item.get("text")
                if isinstance(item_text, str):
                    parts.append(item_text)
        return " ".join(parts)
    return str(value)


def _excerpt(value: Any, limit: int = _MAX_EXCERPT_CHARS) -> str:
    text = " ".join(_text(value).split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 24)] + " …[truncated]"


def _state_text(value: Any, limit: int = _MAX_STATE_VALUE_CHARS) -> str:
    """Bound and redact text before it enters durable auxiliary state."""

    return _excerpt(_SECRET_RE.sub("[REDACTED]", _text(value)), limit)


def _bounded_values(values: Any, limit: int) -> list[str]:
    if not isinstance(values, list):
        return []
    result: list[str] = []
    for value in values:
        item = _state_text(value)
        if item and item not in result:
            result.append(item)
    return result[-limit:]


def _task_tokens(value: Any) -> set[str]:
    tokens: set[str] = set()
    for token in re.findall(r"[a-z0-9_./:-]{3,}", _text(value).lower()):
        token = token.strip(".,:;!?()[]{}")
        if token.endswith("ing") and len(token) > 5:
            token = token[:-3]
        if token and token not in _TASK_STOPWORDS:
            tokens.add(token)
    return tokens


def _task_similarity(left: Any, right: Any) -> float:
    a, b = _task_tokens(left), _task_tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _explicit_task_switch(value: Any) -> bool:
    return bool(re.search(
    r"\b(?:instead|switch|new task|different task|forget|abandon|"
        r"drop|stop working on|move on to|replace)\b",
        _text(value).lower(),
    ))


def _json_key(value: Any) -> str:
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        encoded = repr(value)
    return hashlib.sha256(encoded.encode("utf-8", "replace")).hexdigest()


def message_key(message: Mapping[str, Any]) -> str:
    """Stable identity for an in-memory transcript message."""

    return _json_key(
        {
            "role": message.get("role"),
            "content": message.get("content"),
            "tool_calls": message.get("tool_calls"),
            "tool_call_id": message.get("tool_call_id"),
            "name": message.get("name"),
        }
    )


@dataclass
class ContinuousState:
    """Inspectable working state carried across a Hermes session."""

    schema_version: int = CONTINUOUS_STATE_SCHEMA_VERSION
    state_id: str = ""
    session_context: list[str] = field(default_factory=list)
    session_facts: list[str] = field(default_factory=list)
    session_artifacts: list[str] = field(default_factory=list)
    objective: str = ""
    current_task_objective: str = ""
    current_user_turn_objective: str = ""
    task_id: str = ""
    task_epoch: int = 0
    task_history: list[dict[str, Any]] = field(default_factory=list)
    continuity_mode: str = "collecting"
    continuity_activation_reason: str = ""
    projection_events: int = 0
    last_compacted_observed_messages: int = 0
    current_plan: list[str] = field(default_factory=list)
    current_working_state: str = ""
    important_facts: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    hypotheses: list[str] = field(default_factory=list)
    unresolved_questions: list[str] = field(default_factory=list)
    tool_observations: list[dict[str, Any]] = field(default_factory=list)
    durable_tool_observations: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    failures_and_retries: list[str] = field(default_factory=list)
    active_constraints: list[str] = field(default_factory=list)
    completion_criteria: list[str] = field(default_factory=list)
    compacted_trajectory: list[dict[str, Any]] = field(default_factory=list)
    checkpoints: list[dict[str, Any]] = field(default_factory=list)
    event_keys: list[str] = field(default_factory=list, repr=False)
    turns: int = 0
    observed_messages: int = 0
    compaction_count: int = 0
    checkpoint_count: int = 0
    last_updated_at: float = field(default_factory=time.time)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], *, state_id: str) -> "ContinuousState":
        version = value.get("schema_version", 0)
        if version not in (1, 2, CONTINUOUS_STATE_SCHEMA_VERSION):
            raise ValueError(
                f"Unsupported continuous state schema_version={version!r}; "
                f"expected {CONTINUOUS_STATE_SCHEMA_VERSION}."
            )
        fields = {field_name for field_name in cls.__dataclass_fields__}
        payload = {key: value[key] for key in fields if key in value}
        payload["state_id"] = str(payload.get("state_id") or state_id)
        state = cls(**payload)
        if version == 1 and not state.current_task_objective:
            state.current_task_objective = str(state.objective or "")
        state._normalise()
        return state

    def _normalise(self) -> None:
        self.schema_version = CONTINUOUS_STATE_SCHEMA_VERSION
        self.state_id = str(self.state_id or "")
        for name in (
            "session_context",
            "session_facts",
            "session_artifacts",
            "current_plan",
            "important_facts",
            "decisions",
            "hypotheses",
            "unresolved_questions",
            "tool_observations",
            "durable_tool_observations",
            "artifacts",
            "failures_and_retries",
            "active_constraints",
            "completion_criteria",
            "compacted_trajectory",
            "checkpoints",
            "event_keys",
        ):
            value = getattr(self, name)
            if not isinstance(value, list):
                setattr(self, name, [])
        self.session_context = _bounded_values(self.session_context, 12)
        self.session_facts = _bounded_values(self.session_facts, _MAX_FACTS)
        self.session_artifacts = _bounded_values(self.session_artifacts, 24)
        self.current_plan = _bounded_values(self.current_plan, 10)
        self.important_facts = _bounded_values(self.important_facts, _MAX_FACTS)
        self.decisions = _bounded_values(self.decisions, _MAX_DECISIONS)
        self.hypotheses = _bounded_values(self.hypotheses, 12)
        self.unresolved_questions = _bounded_values(self.unresolved_questions, 12)
        self.artifacts = _bounded_values(self.artifacts, 24)
        self.failures_and_retries = _bounded_values(self.failures_and_retries, _MAX_FAILURES)
        self.active_constraints = _bounded_values(self.active_constraints, 12)
        self.completion_criteria = _bounded_values(self.completion_criteria, 12)
        self.objective = _state_text(self.objective, 8_000)
        self.current_task_objective = _state_text(self.current_task_objective, 8_000)
        self.current_user_turn_objective = _state_text(self.current_user_turn_objective, 8_000)
        self.current_working_state = _state_text(self.current_working_state, 2_400)
        self.task_id = _state_text(self.task_id, 160)
        self.task_history = [item for item in self.task_history if isinstance(item, dict)][-_MAX_TASK_HISTORY:]
        self.continuity_mode = self.continuity_mode if self.continuity_mode in {"collecting", "projected", "provider_native"} else "collecting"
        self.continuity_activation_reason = _state_text(self.continuity_activation_reason, 200)
        self.event_keys = [str(item) for item in self.event_keys][- _MAX_EVENT_KEYS :]
        self.tool_observations = [
            item for item in self.tool_observations if isinstance(item, dict)
        ][- _MAX_RECENT_OBSERVATIONS :]
        self.durable_tool_observations = [
            item
            for item in self.durable_tool_observations
            if isinstance(item, dict)
        ][-32:]
        for observations in (self.tool_observations, self.durable_tool_observations):
            for item in observations:
                item["observation"] = _state_text(item.get("observation"), 1_600)
                item["tool"] = _state_text(item.get("tool"), 160)

    def to_dict(self) -> dict[str, Any]:
        self._normalise()
        return asdict(self)


class ContinuousStateStore:
    """Load, evolve, checkpoint and render a session's working state."""

    def __init__(self, *, state_id: str, path: Path | None = None) -> None:
        self.state_id = state_id
        self.path = path
        self.state = self._load()
        self._turn_objective = ""
        self._pending_metrics: dict[str, int] = {}
        self._last_task_event = "resumed" if self.state.task_epoch else "started"
        self._activation_mode = self.state.continuity_mode
        self._activation_reason = self.state.continuity_activation_reason
        self._stream_delta_buffer = ""

    @classmethod
    def for_agent(cls, agent: Any) -> "ContinuousStateStore":
        session_id = str(getattr(agent, "session_id", None) or "unspecified")
        path = getattr(agent, "continuous_state_path", None)
        if path is None:
            logs_dir = getattr(agent, "logs_dir", None)
            if logs_dir is not None:
                path = Path(logs_dir) / f"continuous_state_{_safe_filename(session_id)}.json"
            else:
                try:
                    from hermes_constants import get_hermes_home

                    path = (
                        Path(get_hermes_home())
                        / "sessions"
                        / "continuous_state"
                        / f"{_safe_filename(session_id)}.json"
                    )
                except Exception:
                    path = None
        return cls(state_id=session_id, path=Path(path) if path is not None else None)

    def _load(self) -> ContinuousState:
        if self.path is None or not self.path.exists():
            return ContinuousState(state_id=self.state_id)
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError("continuous state root must be an object")
            return ContinuousState.from_dict(value, state_id=self.state_id)
        except Exception:
            # A corrupt auxiliary state file must never prevent Hermes from
            # loading the canonical transcript. Start a new working set and
            # leave the original file available for debugging.
            return ContinuousState(state_id=self.state_id)

    def begin_turn(self, objective: Any, *, task_id: str | None = None) -> None:
        """Start a user turn and classify it against the task trajectory.

        Similarity is only one signal. Explicit replacement language, shared
        artifacts, and matching archived task snapshots are combined so a
        short refinement is not mistaken for a new task, while a new task is
        never allowed to inherit the prior task's plan and hypotheses.
        """

        self._turn_objective = _state_text(objective, 8_000)
        previous = self.state.current_task_objective or self.state.objective
        self.state.current_user_turn_objective = self._turn_objective
        if not previous:
            self._start_task(self._turn_objective, task_id=task_id, reason="initial")
        else:
            similarity = _task_similarity(previous, self._turn_objective)
            artifact_overlap = bool(
                set(_PATH_RE.findall(previous)) & set(_PATH_RE.findall(self._turn_objective))
            )
            explicit_switch = _explicit_task_switch(self._turn_objective)
            matching_history = self._matching_task_snapshot(self._turn_objective)
            if matching_history is not None and (explicit_switch or similarity < 0.35):
                self._archive_current_task("topic_switch")
                self._restore_task(matching_history, reason="return_to_previous_task")
            elif explicit_switch or (similarity < 0.12 and not artifact_overlap):
                self._archive_current_task("replaced")
                self._start_task(self._turn_objective, task_id=task_id, reason="new_task")
            else:
                self._last_task_event = "continued"
                self.state.current_task_objective = previous
                self.state.objective = previous
                self.state.task_id = self.state.task_id or self._task_identifier(task_id)
        self.state.turns += 1
        self.state.last_updated_at = time.time()
        self._derive_objective_fields(self._turn_objective)
        self.checkpoint("turn_start")

    def _task_identifier(self, turn_id: str | None) -> str:
        return f"{self.state.state_id}:task-{self.state.task_epoch}" + (
            f":{_safe_filename(turn_id)}" if turn_id else ""
        )

    def _start_task(self, objective: str, *, task_id: str | None, reason: str) -> None:
        self.state.task_epoch += 1
        self.state.task_id = self._task_identifier(task_id)
        self.state.current_task_objective = objective
        self.state.objective = objective
        self.state.current_plan = []
        self.state.current_working_state = ""
        self.state.important_facts = []
        self.state.decisions = []
        self.state.hypotheses = []
        self.state.unresolved_questions = []
        self.state.tool_observations = []
        self.state.durable_tool_observations = []
        self.state.artifacts = []
        self.state.failures_and_retries = []
        self.state.active_constraints = []
        self.state.completion_criteria = []
        self.state.session_context = _merge_recent(self.state.session_context, [objective], 12)
        self._last_task_event = reason
        self._activation_mode = "collecting"
        self._activation_reason = ""
        self.state.continuity_mode = "collecting"
        self.state.continuity_activation_reason = ""
        self._record_metric("task_epoch_changes", 1)
        self._record_metric("task_state_resets", 1)

    def _task_snapshot(self, status: str) -> dict[str, Any]:
        return {
            "objective": self.state.current_task_objective or self.state.objective,
            "task_id": self.state.task_id,
            "task_epoch": self.state.task_epoch,
            "status": status,
            "current_plan": list(self.state.current_plan),
            "current_working_state": self.state.current_working_state,
            "important_facts": list(self.state.important_facts),
            "decisions": list(self.state.decisions),
            "hypotheses": list(self.state.hypotheses),
            "unresolved_questions": list(self.state.unresolved_questions),
            "durable_tool_observations": list(self.state.durable_tool_observations),
            "artifacts": list(self.state.artifacts),
            "failures_and_retries": list(self.state.failures_and_retries),
            "active_constraints": list(self.state.active_constraints),
            "completion_criteria": list(self.state.completion_criteria),
        }

    def _archive_current_task(self, status: str) -> None:
        if self.state.current_task_objective:
            self.state.session_facts = _merge_recent(
                self.state.session_facts, self.state.important_facts, _MAX_FACTS
            )
            self.state.session_artifacts = _merge_recent(
                self.state.session_artifacts, self.state.artifacts, 24
            )
            self.state.task_history = (
                self.state.task_history + [self._task_snapshot(status)]
            )[-_MAX_TASK_HISTORY:]

    def _matching_task_snapshot(self, objective: str) -> dict[str, Any] | None:
        candidates = [
            item for item in self.state.task_history
            if _task_similarity(item.get("objective"), objective) >= 0.30
        ]
        return max(candidates, key=lambda item: _task_similarity(item.get("objective"), objective), default=None)

    def _restore_task(self, snapshot: Mapping[str, Any], *, reason: str) -> None:
        self.state.task_epoch += 1
        self.state.task_id = self._task_identifier(None)
        self.state.objective = _state_text(snapshot.get("objective"), 8_000)
        self.state.current_task_objective = self.state.objective
        for name in (
            "current_plan", "important_facts", "decisions", "hypotheses",
            "unresolved_questions", "durable_tool_observations", "artifacts",
            "failures_and_retries", "active_constraints", "completion_criteria",
        ):
            setattr(self.state, name, list(snapshot.get(name) or []))
        self.state.tool_observations = []
        self.state.current_working_state = _state_text(snapshot.get("current_working_state"), 2_400)
        self._last_task_event = reason
        self._activation_mode = "collecting"
        self._activation_reason = ""
        self.state.continuity_mode = "collecting"
        self.state.continuity_activation_reason = ""
        self._record_metric("task_epoch_changes", 1)

    def observe_messages(self, messages: Iterable[Mapping[str, Any]]) -> None:
        message_list = [message for message in messages if isinstance(message, Mapping)]
        latest_assistant = ""
        latest_observation = ""
        for message in message_list:
            key = message_key(message)
            if key in self.state.event_keys:
                continue
            self.state.event_keys.append(key)
            role = str(message.get("role") or "")
            raw_content = _text(message.get("content"))
            content = _excerpt(strip_state_delta_markup(raw_content))
            self.state.observed_messages += 1
            if role == "assistant":
                latest_assistant = content or latest_assistant
                delta = message.get("_hermes_state_delta")
                if isinstance(delta, Mapping):
                    self._apply_state_delta(delta)
                else:
                    self._derive_assistant_fields(raw_content)
                self._record_artifacts(message)
            elif role == "tool":
                latest_observation = content or latest_observation
                self._record_tool_observation(message, content)
                self._record_artifact_text(content)
        self.state.current_working_state = _excerpt(
            latest_observation or latest_assistant or self.state.current_working_state,
            2_400,
        )
        self.state.event_keys = self.state.event_keys[-_MAX_EVENT_KEYS:]
        self.state.last_updated_at = time.time()

    def sanitize_assistant_message(self, message: Mapping[str, Any]) -> dict[str, Any]:
        """Keep the optional state protocol internal to Hermes."""

        result = dict(message)
        content = result.get("content")
        if isinstance(content, str):
            delta = _extract_state_delta(content)
            if delta is not None:
                result["_hermes_state_delta"] = delta
                result["content"] = strip_state_delta_markup(content)
        return result

    def sanitize_stream_delta(self, text: str) -> str:
        """Filter state blocks even when streaming splits them across chunks."""

        if not isinstance(text, str):
            return text
        self._stream_delta_buffer += text
        output: list[str] = []
        opening = "<hermes-state-delta"
        closing = "</hermes-state-delta>"
        while self._stream_delta_buffer:
            start = self._stream_delta_buffer.find(opening)
            if start < 0:
                keep = 0
                for size in range(1, min(len(opening), len(self._stream_delta_buffer)) + 1):
                    if opening.startswith(self._stream_delta_buffer[-size:]):
                        keep = size
                output.append(self._stream_delta_buffer[:-keep] if keep else self._stream_delta_buffer)
                self._stream_delta_buffer = self._stream_delta_buffer[-keep:] if keep else ""
                break
            if start:
                output.append(self._stream_delta_buffer[:start])
            end = self._stream_delta_buffer.find(closing, start + len(opening))
            if end < 0:
                self._stream_delta_buffer = self._stream_delta_buffer[start:]
                break
            self._stream_delta_buffer = self._stream_delta_buffer[end + len(closing):]
        return "".join(output)

    def maybe_compact(self, *, raw_messages: Iterable[Mapping[str, Any]], reason: str) -> bool:
        messages = list(raw_messages)
        raw_chars = sum(len(_text(message.get("content"))) for message in messages)
        pressure = (
            len(messages) >= 28
            or len(self.state.tool_observations) > _MAX_RECENT_OBSERVATIONS
            or raw_chars >= 80_000
        )
        if not pressure:
            return False
        if self.state.observed_messages <= self.state.last_compacted_observed_messages:
            return False

        before = len(self.state.tool_observations)
        unique: list[dict[str, Any]] = []
        seen: set[str] = set()
        for observation in reversed(self.state.tool_observations):
            key = _json_key(
                {
                    "tool": observation.get("tool"),
                    "observation": observation.get("observation"),
                }
            )
            if key in seen:
                continue
            seen.add(key)
            unique.append(observation)
        unique.reverse()
        self.state.tool_observations = unique[-_MAX_RECENT_OBSERVATIONS:]
        self.state.compacted_trajectory.append(
            {
                "at": time.time(),
                "reason": reason,
                "messages_before": len(messages),
                "raw_chars_before": raw_chars,
                "observations_before": before,
                "observations_after": len(self.state.tool_observations),
                "facts_retained": len(self.state.important_facts),
                "decisions_retained": len(self.state.decisions),
                "failures_retained": len(self.state.failures_and_retries),
                "durable_observations_retained": len(
                    self.state.durable_tool_observations
                ),
            }
        )
        self.state.compacted_trajectory = self.state.compacted_trajectory[-_MAX_TRAJECTORY_COMPACTIONS:]
        self.state.compaction_count += 1
        self.state.last_compacted_observed_messages = self.state.observed_messages
        self._record_metric("trajectory_compactions", 1)
        self._record_metric("state_compaction_events", 1)
        self.checkpoint("trajectory_compaction")
        return True

    def prepare_api_messages(
        self,
        api_messages: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        current_turn_user_idx: int | None = None,
        effective_system: str = "",
    ) -> list[dict[str, Any]]:
        """Build the next generic-provider context from the working set.

        The canonical Hermes history is untouched. For Tier 2 providers the
        request retains the active user turn and all tool interactions in it,
        while prior turns are replaced by the trajectory capsule. Tier 3
        Responses providers retain their native encrypted reasoning items and
        existing server-side compaction path instead.
        """

        self.observe_messages(messages)
        self.maybe_compact(raw_messages=messages, reason="context_pressure")
        agent = getattr(self, "_agent", None)
        native = False
        if agent is not None:
            try:
                # Re-resolve on every request so a mid-turn fallback or model
                # switch cannot continue using a provider-native assumption
                # after the active transport changes.
                from agent.reasoning_backend import resolve_provider_capabilities

                native = resolve_provider_capabilities(
                    agent
                ).persistent_reasoning_state
                agent._reasoning_native_tier = 3 if native else 2
            except Exception:
                native = getattr(agent, "_reasoning_native_tier", 0) >= 3
        cleaned = [dict(item) for item in api_messages]
        if native:
            self._activation_mode = "provider_native"
            self.state.continuity_mode = "provider_native"
            for item in cleaned:
                item.pop("_hermes_source_index", None)
                item.pop("_hermes_current_turn", None)
                item.pop("_hermes_state_delta", None)
            self._record_metric("native_state_reuses", 1 if self.state.turns > 1 else 0)
            self._set_metric_text("continuity_mode", "provider_native")
            self.checkpoint("native_turn")
            return cleaned

        should_project, activation_reason = self._should_project(
            cleaned, messages, current_turn_user_idx
        )
        if not should_project:
            self._activation_mode = "collecting"
            self.state.continuity_mode = "collecting"
            self._record_metric("collecting_calls", 1)
            self._set_metric_text("continuity_mode", "collecting")
            for item in cleaned:
                item.pop("_hermes_source_index", None)
                item.pop("_hermes_current_turn", None)
                item.pop("_hermes_state_delta", None)
            self.checkpoint("collecting_turn")
            return cleaned

        if self._activation_mode != "projected":
            self._activation_mode = "projected"
            self._activation_reason = activation_reason
            self.state.continuity_mode = "projected"
            self.state.continuity_activation_reason = activation_reason
            self.state.projection_events += 1
            self._record_metric("continuity_activation_events", 1)
            self._set_metric_text("continuity_activation_reason", activation_reason)
        self._set_metric_text("continuity_mode", "projected")

        current_marker = None
        for index, item in enumerate(cleaned):
            if item.get("_hermes_current_turn"):
                current_marker = index
                break
        if current_marker is None and current_turn_user_idx is not None:
            for index, item in enumerate(cleaned):
                if item.get("_hermes_source_index") == current_turn_user_idx:
                    current_marker = index
                    break
        if current_marker is None:
            current_marker = next(
                (index for index, item in enumerate(cleaned) if item.get("role") == "user"),
                0,
            )

        projected: list[dict[str, Any]] = []
        for index, item in enumerate(cleaned):
            if item.get("role") == "system" or index >= current_marker:
                projected.append(item)
        omitted_tokens = max(
            0,
            sum(len(_text(item.get("content"))) for item in cleaned)
            - sum(len(_text(item.get("content"))) for item in projected),
        ) // 4
        self._record_metric("legacy_transcript_tokens_omitted", omitted_tokens)
        capsule = self.render_capsule()
        self._set_metric("capsule_chars", len(capsule))
        self._set_metric("capsule_tokens", max(1, len(capsule) // 4))
        self._set_metric("capsule_budget_tokens", _positive_int(self._continuity_config().get("capsule_token_budget"), 1_200))
        self._record_metric("net_context_savings", omitted_tokens - max(1, len(capsule) // 4))
        self._set_metric("state_facts", len(self.state.important_facts))
        self._set_metric("state_constraints", len(self.state.active_constraints))
        self._set_metric("durable_tool_observations", len(self.state.durable_tool_observations))
        self._set_metric("continuous_state_bytes", len(json.dumps(self.state.to_dict(), ensure_ascii=False)))
        for item in projected:
            if item.get("role") == "user":
                item["content"] = _append_text(item.get("content"), capsule)
                break
        for item in projected:
            item.pop("_hermes_source_index", None)
            item.pop("_hermes_current_turn", None)
            item.pop("_hermes_state_delta", None)
        self._record_metric("substrate_context_projections", 1)
        self.checkpoint("generic_turn")
        return projected

    def _continuity_config(self) -> dict[str, Any]:
        agent = getattr(self, "_agent", None)
        configured = getattr(agent, "reasoning_continuity_config", None) if agent else None
        return configured if isinstance(configured, dict) else {"mode": "always"}

    def _should_project(
        self,
        api_messages: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        current_turn_user_idx: int | None,
    ) -> tuple[bool, str]:
        cfg = self._continuity_config()
        mode = str(cfg.get("mode", "adaptive")).strip().lower()
        if mode in {"always", "forced", "eager"}:
            return True, "configured_always"
        if self._activation_mode == "projected":
            return True, "already_projected"
        if self.state.compaction_count:
            return True, "resume_checkpoint"
        if self.state.task_epoch > 1 and self.state.turns > 1:
            # A returned task has a durable trajectory worth restoring, while
            # an ordinary short session remains collecting.
            return True, "resumed_task_epoch"

        transcript_tokens = sum(len(_text(item.get("content"))) for item in api_messages) // 4
        message_limit = _positive_int(cfg.get("activation_message_count"), 24)
        if len(messages) >= message_limit:
            return True, "message_count_pressure"
        tool_chars = sum(
            len(_text(item.get("content")))
            for item in messages
            if item.get("role") == "tool"
        )
        if tool_chars >= _positive_int(cfg.get("activation_tool_chars"), 24_000):
            return True, "tool_output_pressure"
        context_length = getattr(
            getattr(getattr(self, "_agent", None), "context_compressor", None),
            "context_length",
            0,
        )
        ratio = _positive_float(cfg.get("activation_context_ratio"), 0.72)
        if isinstance(context_length, (int, float)) and context_length > 0:
            if transcript_tokens >= int(context_length * ratio):
                return True, "context_ratio_pressure"
        if current_turn_user_idx is None and self.state.turns > 1:
            return True, "resumed_without_turn_anchor"
        return False, "below_activation_threshold"

    def render_capsule(self) -> str:
        """Render a bounded capsule, prioritising operational state."""

        def block(title: str, values: Iterable[Any], limit: int = 8) -> str:
            rows = [str(value) for value in list(values)[-limit:] if value]
            return f"{title}:\n" + ("\n".join(f"- {row}" for row in rows) or "- none")

        observations = [
            f"{item.get('tool', 'tool')}: {item.get('observation', '')}"
            for item in self.state.tool_observations[-8:]
        ]
        durable_observations = [
            f"{item.get('tool', 'tool')}: {item.get('observation', '')}"
            for item in self.state.durable_tool_observations[-6:]
        ]
        cfg = self._continuity_config()
        budget = _positive_int(cfg.get("capsule_token_budget"), 1_200)
        limit = max(1_024, budget * 4)
        header = (
            "<hermes-continuous-state schema_version=3>\n"
            "Persistent operational state for the active task. Use it to resume "
            "work without rediscovering verified facts.\n"
            f"Task epoch: {self.state.task_epoch}\n"
            f"Current task objective: {self.state.current_task_objective or self.state.objective or 'unknown'}\n"
        )
        sections = [
            ("Active constraints", self.state.active_constraints),
            ("Current plan", self.state.current_plan),
            ("Current working state", [self.state.current_working_state]),
            ("Important facts", self.state.important_facts),
            ("Failures and retries", self.state.failures_and_retries),
            ("Durable tool observations", durable_observations),
            ("Artifacts/files", self.state.artifacts),
            ("Completion criteria", self.state.completion_criteria),
            ("Decisions", self.state.decisions),
            ("Hypotheses", self.state.hypotheses),
            ("Unresolved questions", self.state.unresolved_questions),
        ]
        result = header
        for title, values in sections:
            addition = "\n" + block(title, values)
            if len(result) + len(addition) <= limit - 180:
                result += addition
            else:
                remaining = limit - len(result) - 180
                if remaining > 80:
                    result += addition[:remaining] + " …[capsule budget]"
                break
        result += (
            "\nWhen a transition creates a verified fact, decision, changed artifact, "
            "resolved question, or plan replacement, optionally append a small valid "
            "JSON delta in <hermes-state-delta>...</hermes-state-delta>.\n"
            "</hermes-continuous-state>"
        )
        return result

    def checkpoint(self, reason: str) -> dict[str, Any]:
        self.state.checkpoint_count += 1
        checkpoint = {
            "id": f"{self.state.state_id}:{self.state.checkpoint_count}",
            "reason": reason,
            "at": time.time(),
            "turns": self.state.turns,
            "observed_messages": self.state.observed_messages,
            "compaction_count": self.state.compaction_count,
        }
        self.state.checkpoints.append(checkpoint)
        self.state.checkpoints = self.state.checkpoints[-20:]
        self.state.last_updated_at = time.time()
        self.persist()
        self._record_metric("checkpoint_events", 1)
        return checkpoint

    def persist(self) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(
                prefix=f".{self.path.name}.", dir=str(self.path.parent)
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    json.dump(self.state.to_dict(), stream, ensure_ascii=False, indent=2)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chmod(temp_name, 0o600)
                os.replace(temp_name, self.path)
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
        except Exception:
            # Auxiliary state is never allowed to break a Hermes turn.
            return

    def summary(self) -> dict[str, Any]:
        return {
            "schema_version": self.state.schema_version,
            "state_id": self.state.state_id,
            "objective": self.state.objective,
            "session_context": list(self.state.session_context[-3:]),
            "current_task_objective": self.state.current_task_objective,
            "current_user_turn_objective": self.state.current_user_turn_objective,
            "task_id": self.state.task_id,
            "task_epoch": self.state.task_epoch,
            "task_event": self._last_task_event,
            "task_history": len(self.state.task_history),
            "continuity_mode": self.state.continuity_mode,
            "continuity_activation_reason": self.state.continuity_activation_reason,
            "projection_events": self.state.projection_events,
            "turns": self.state.turns,
            "observed_messages": self.state.observed_messages,
            "tool_observations": len(self.state.tool_observations),
            "durable_tool_observations": len(self.state.durable_tool_observations),
            "important_facts": len(self.state.important_facts),
            "active_constraints": len(self.state.active_constraints),
            "compaction_count": self.state.compaction_count,
            "checkpoint_count": self.state.checkpoint_count,
            "last_checkpoint": self.state.checkpoints[-1] if self.state.checkpoints else None,
            # Keep the original public strategy label stable for callers of
            # the first implementation; expose the actual tier separately.
            "strategy": "continuous_conversation",
            "continuity_mode": (
                "provider_native"
                if getattr(self, "_agent", None)
                and getattr(self._agent, "_reasoning_native_tier", 0) >= 3
                else "substrate_managed"
            ),
        }

    def _derive_objective_fields(self, objective: str) -> None:
        lines = [line.strip(" -*\t") for line in objective.splitlines() if line.strip()]
        self.state.active_constraints = _merge_recent(self.state.active_constraints, [
            line for line in lines if any(token in line.lower() for token in ("must", "require", "only", "never", "do not", "without"))
        ], 12)
        self.state.completion_criteria = _merge_recent(self.state.completion_criteria, [
            line for line in lines if any(token in line.lower() for token in ("done", "complete", "success", "test", "deliver", "finish"))
        ], 12)

    def _derive_assistant_fields(self, content: str) -> None:
        if not content:
            return
        delta = _extract_state_delta(content)
        if delta is not None:
            self._apply_state_delta(delta)
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        plan_lines = [
            re.sub(r"^(?:[-*]|\d+[.)])\s+", "", line).strip()
            for line in lines
            if re.match(r"^(?:[-*]|\d+[.)])\s+", line)
        ]
        if plan_lines:
            # Structured deltas replace the plan deliberately. Heuristics are
            # retained only for providers that do not emit a delta.
            if delta is None or not delta.get("plan_replace"):
                self.state.current_plan = [_state_text(line) for line in plan_lines[-10:]]
        lower = content.lower()
        if delta is None and any(word in lower for word in ("decided", "will use", "choosing", "therefore")):
            self.state.decisions = _merge_recent(self.state.decisions, lines[-3:], _MAX_DECISIONS)
        if delta is None and any(word in lower for word in ("hypothesis", "assume", "likely", "suspect")):
            self.state.hypotheses = _merge_recent(self.state.hypotheses, lines[-3:], 12)
        if delta is None:
            self.state.unresolved_questions = _merge_recent(
                self.state.unresolved_questions,
                [_state_text(line) for line in lines if line.endswith("?")],
                12,
            )

    def _apply_state_delta(self, delta: Mapping[str, Any]) -> None:
        before = {name: len(getattr(self.state, name)) for name in (
            "important_facts", "hypotheses", "unresolved_questions"
        )}
        self.state.important_facts = _merge_recent(
            self.state.important_facts, delta.get("facts_add", []), _MAX_FACTS
        )
        self.state.important_facts = [
            item for item in self.state.important_facts
            if item not in set(_bounded_values(delta.get("facts_remove", []), _MAX_FACTS))
        ]
        self.state.decisions = _merge_recent(
            self.state.decisions, delta.get("decisions_add", []), _MAX_DECISIONS
        )
        self.state.decisions = [
            item for item in self.state.decisions
            if item not in set(_bounded_values(delta.get("decisions_remove", []), _MAX_DECISIONS))
        ]
        self.state.hypotheses = _merge_recent(self.state.hypotheses, delta.get("hypotheses_add", []), 12)
        self.state.hypotheses = [
            item for item in self.state.hypotheses
            if item not in set(_bounded_values(delta.get("hypotheses_resolved", []), 12))
        ]
        self.state.unresolved_questions = _merge_recent(
            self.state.unresolved_questions, delta.get("questions_add", []), 12
        )
        self.state.unresolved_questions = [
            item for item in self.state.unresolved_questions
            if item not in set(_bounded_values(delta.get("resolved_questions", []), 12))
        ]
        if delta.get("plan_replace"):
            self.state.current_plan = _bounded_values(delta["plan_replace"], 10)
        if delta.get("working_state") is not None:
            self.state.current_working_state = _state_text(delta["working_state"], 2_400)
        self.state.active_constraints = _merge_recent(
            self.state.active_constraints, delta.get("constraints_add", []), 12
        )
        self.state.active_constraints = [
            item for item in self.state.active_constraints
            if item not in set(_bounded_values(delta.get("constraints_remove", []), 12))
        ]
        self.state.artifacts = _merge_recent(self.state.artifacts, delta.get("artifacts_add", []), 24)
        self.state.completion_criteria = _merge_recent(
            self.state.completion_criteria, delta.get("completion_criteria_add", []), 12
        )
        if delta.get("completion_criteria_replace"):
            self.state.completion_criteria = _bounded_values(delta["completion_criteria_replace"], 12)
        for name, metric in (("important_facts", "facts_added"), ("hypotheses", "hypotheses_added"), ("unresolved_questions", "questions_added")):
            self._record_metric(metric, max(0, len(getattr(self.state, name)) - before[name]))
        self._record_metric("facts_removed", len(delta.get("facts_remove", [])))
        self._record_metric("hypotheses_resolved", len(delta.get("hypotheses_resolved", [])))
        self._record_metric("questions_resolved", len(delta.get("resolved_questions", [])))

    def _record_tool_observation(self, message: Mapping[str, Any], content: str) -> None:
        tool_name = str(message.get("name") or message.get("tool_name") or "tool")
        content = _state_text(content, 1_600)
        observation = {
            "tool": tool_name,
            "call_id": str(message.get("tool_call_id") or ""),
            "observation": content,
            "failed": _looks_failed(content),
            "at": time.time(),
        }
        self.state.tool_observations.append(observation)
        if observation["failed"] or _looks_operationally_important(content):
            self.state.durable_tool_observations.append(observation)
            self.state.durable_tool_observations = self.state.durable_tool_observations[-32:]
        if observation["failed"]:
            self.state.failures_and_retries = _merge_recent(
                self.state.failures_and_retries,
                [f"{tool_name}: {content}"],
                _MAX_FAILURES,
            )
        if observation["failed"] or _looks_operationally_important(content):
            self.state.important_facts = _merge_recent(
                self.state.important_facts,
                [f"{tool_name}: {content}"],
                _MAX_FACTS,
            )
            self._record_metric("facts_added", 1)

    def _record_artifacts(self, message: Mapping[str, Any]) -> None:
        for value in message.get("tool_calls") or []:
            if isinstance(value, Mapping):
                self._record_artifact_text(_text(value.get("function")))

    def _record_artifact_text(self, content: str) -> None:
        found = _PATH_RE.findall(content or "")
        self.state.artifacts = _merge_recent(self.state.artifacts, found, 24)

    def _record_metric(self, key: str, amount: int) -> None:
        if not amount:
            return
        agent = getattr(self, "_agent", None)
        metrics = getattr(agent, "_reasoning_metrics", None) if agent else None
        if metrics is not None and hasattr(metrics, key):
            setattr(metrics, key, int(getattr(metrics, key, 0) or 0) + amount)
        else:
            self._pending_metrics[key] = self._pending_metrics.get(key, 0) + amount

    def _set_metric(self, key: str, value: int) -> None:
        agent = getattr(self, "_agent", None)
        metrics = getattr(agent, "_reasoning_metrics", None) if agent else None
        if metrics is not None and hasattr(metrics, key):
            setattr(metrics, key, int(value))
        else:
            # A set-before-metrics value is kept as a one-item pending update.
            self._pending_metrics[key] = int(value)

    def _set_metric_text(self, key: str, value: str) -> None:
        agent = getattr(self, "_agent", None)
        metrics = getattr(agent, "_reasoning_metrics", None) if agent else None
        if metrics is not None and hasattr(metrics, key):
            setattr(metrics, key, str(value))

    def flush_metrics(self) -> None:
        pending, self._pending_metrics = self._pending_metrics, {}
        for key, amount in pending.items():
            self._record_metric(key, amount)


def _append_text(content: Any, suffix: str) -> Any:
    if isinstance(content, str):
        return f"{content}\n\n{suffix}"
    if isinstance(content, list):
        parts = list(content)
        parts.append({"type": "text", "text": suffix})
        return parts
    return f"{_text(content)}\n\n{suffix}"


def _merge_recent(existing: list[str], incoming: Iterable[str], limit: int) -> list[str]:
    values = [str(value).strip() for value in [*existing, *incoming] if str(value).strip()]
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result[-limit:]


def _looks_failed(value: str) -> bool:
    lower = (value or "").lower()
    return any(token in lower for token in ("error", "failed", "traceback", "exception", "permission denied"))


def _looks_operationally_important(value: str) -> bool:
    lower = (value or "").lower()
    return bool(
        _PATH_RE.search(value or "")
        or any(
            token in lower
            for token in (
                "created",
                "changed",
                "modified",
                "test",
                "root cause",
                "success",
                "result",
                "warning",
            )
        )
    )


def _safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)[:160] or "session"


def _positive_int(value: Any, default: int) -> int:
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _positive_float(value: Any, default: float) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _extract_state_delta(content: str) -> dict[str, Any] | None:
    """Parse only the explicit, bounded state protocol; malformed output is ignored."""

    match = _STATE_DELTA_RE.search(content)
    if not match:
        return None
    try:
        raw = json.loads(match.group(1))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    allowed = {
        "facts_add", "facts_remove", "decisions_add", "decisions_remove",
        "hypotheses_add", "hypotheses_resolved", "questions_add",
        "resolved_questions", "constraints_add", "constraints_remove",
        "artifacts_add", "plan_replace", "working_state",
        "completion_criteria_add", "completion_criteria_replace",
    }
    result: dict[str, Any] = {}
    for key, value in raw.items():
        if key not in allowed:
            continue
        if key == "working_state":
            if isinstance(value, str):
                result[key] = _state_text(value, 2_400)
            continue
        if not isinstance(value, list) or len(value) > 24:
            continue
        result[key] = _bounded_values(value, 24)
    return result


def strip_state_delta_markup(content: str) -> str:
    """Remove internal state-delta blocks while preserving surrounding prose."""

    if not isinstance(content, str):
        return content
    return _STATE_DELTA_RE.sub("", content).strip()
