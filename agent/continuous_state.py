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


CONTINUOUS_STATE_SCHEMA_VERSION = 1
_MAX_RECENT_OBSERVATIONS = 18
_MAX_FACTS = 24
_MAX_DECISIONS = 18
_MAX_FAILURES = 12
_MAX_TRAJECTORY_COMPACTIONS = 12
_MAX_EVENT_KEYS = 256
_MAX_EXCERPT_CHARS = 4_000
_PATH_RE = re.compile(r"(?:^|[\s'\"`(])((?:/|\./|\.\./|[A-Za-z]:[\\/])[^\s'\"`,;)]+)")


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
    objective: str = ""
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
        if version != CONTINUOUS_STATE_SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported continuous state schema_version={version!r}; "
                f"expected {CONTINUOUS_STATE_SCHEMA_VERSION}."
            )
        fields = {field_name for field_name in cls.__dataclass_fields__}
        payload = {key: value[key] for key in fields if key in value}
        payload["state_id"] = str(payload.get("state_id") or state_id)
        state = cls(**payload)
        state._normalise()
        return state

    def _normalise(self) -> None:
        self.schema_version = CONTINUOUS_STATE_SCHEMA_VERSION
        self.state_id = str(self.state_id or "")
        for name in (
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
        self.current_plan = [str(item) for item in self.current_plan if item]
        self.important_facts = [str(item) for item in self.important_facts if item]
        self.decisions = [str(item) for item in self.decisions if item]
        self.hypotheses = [str(item) for item in self.hypotheses if item]
        self.unresolved_questions = [str(item) for item in self.unresolved_questions if item]
        self.artifacts = [str(item) for item in self.artifacts if item]
        self.failures_and_retries = [str(item) for item in self.failures_and_retries if item]
        self.active_constraints = [str(item) for item in self.active_constraints if item]
        self.completion_criteria = [str(item) for item in self.completion_criteria if item]
        self.event_keys = [str(item) for item in self.event_keys][- _MAX_EVENT_KEYS :]
        self.tool_observations = [
            item for item in self.tool_observations if isinstance(item, dict)
        ][- _MAX_RECENT_OBSERVATIONS :]
        self.durable_tool_observations = [
            item
            for item in self.durable_tool_observations
            if isinstance(item, dict)
        ][-32:]

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

    def begin_turn(self, objective: Any) -> None:
        self._turn_objective = _excerpt(objective, 8_000)
        if not self.state.objective:
            self.state.objective = self._turn_objective
        self.state.turns += 1
        self.state.last_updated_at = time.time()
        self._derive_objective_fields(self._turn_objective)
        self.checkpoint("turn_start")

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
            content = _excerpt(raw_content)
            self.state.observed_messages += 1
            if role == "assistant":
                latest_assistant = content or latest_assistant
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
        self._record_metric("trajectory_compactions", 1)
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
            for item in cleaned:
                item.pop("_hermes_source_index", None)
                item.pop("_hermes_current_turn", None)
            self._record_metric("native_state_reuses", 1 if self.state.turns > 1 else 0)
            self.checkpoint("native_turn")
            return cleaned

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
        capsule = self.render_capsule()
        for item in projected:
            if item.get("role") == "user":
                item["content"] = _append_text(item.get("content"), capsule)
                break
        for item in projected:
            item.pop("_hermes_source_index", None)
            item.pop("_hermes_current_turn", None)
        self._record_metric("substrate_context_projections", 1)
        self.checkpoint("generic_turn")
        return projected

    def render_capsule(self) -> str:
        """Render a bounded, explicit working-state instruction."""

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
        return (
            "<hermes-continuous-state schema_version=1>\n"
            "This is the persistent working state for the active task. "
            "Use it as operational memory; do not restart completed work or "
            "discard constraints merely because older chat turns are omitted.\n"
            f"Objective: {self.state.objective or self._turn_objective or 'unknown'}\n"
            f"Current working state: {self.state.current_working_state or 'not yet observed'}\n"
            + block("Current plan", self.state.current_plan)
            + "\n"
            + block("Important facts", self.state.important_facts)
            + "\n"
            + block("Decisions", self.state.decisions)
            + "\n"
            + block("Hypotheses", self.state.hypotheses)
            + "\n"
            + block("Unresolved questions", self.state.unresolved_questions)
            + "\n"
            + block("Recent tool observations", observations)
            + "\n"
            + block("Durable important tool observations", durable_observations)
            + "\n"
            + block("Artifacts/files", self.state.artifacts)
            + "\n"
            + block("Failures and retries", self.state.failures_and_retries)
            + "\n"
            + block("Active constraints", self.state.active_constraints)
            + "\n"
            + block("Completion criteria", self.state.completion_criteria)
            + "\n</hermes-continuous-state>"
        )

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
            "turns": self.state.turns,
            "observed_messages": self.state.observed_messages,
            "tool_observations": len(self.state.tool_observations),
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
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        plan_lines = [
            re.sub(r"^(?:[-*]|\d+[.)])\s+", "", line).strip()
            for line in lines
            if re.match(r"^(?:[-*]|\d+[.)])\s+", line)
        ]
        if plan_lines:
            self.state.current_plan = plan_lines[-10:]
        lower = content.lower()
        if any(word in lower for word in ("decided", "will use", "choosing", "therefore")):
            self.state.decisions = _merge_recent(self.state.decisions, lines[-3:], _MAX_DECISIONS)
        if any(word in lower for word in ("hypothesis", "assume", "likely", "suspect")):
            self.state.hypotheses = _merge_recent(self.state.hypotheses, lines[-3:], 12)
        self.state.unresolved_questions = _merge_recent(
            self.state.unresolved_questions,
            [line for line in lines if line.endswith("?")],
            12,
        )

    def _record_tool_observation(self, message: Mapping[str, Any], content: str) -> None:
        tool_name = str(message.get("name") or message.get("tool_name") or "tool")
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

    def _record_artifacts(self, message: Mapping[str, Any]) -> None:
        for value in message.get("tool_calls") or []:
            if isinstance(value, Mapping):
                self._record_artifact_text(_text(value.get("function")))

    def _record_artifact_text(self, content: str) -> None:
        found = _PATH_RE.findall(content or "")
        self.state.artifacts = _merge_recent(self.state.artifacts, found, 24)

    def _record_metric(self, key: str, amount: int) -> None:
        agent = getattr(self, "_agent", None)
        metrics = getattr(agent, "_reasoning_metrics", None) if agent else None
        if metrics is not None and hasattr(metrics, key):
            setattr(metrics, key, int(getattr(metrics, key, 0) or 0) + amount)


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
