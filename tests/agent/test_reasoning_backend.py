import json
from types import SimpleNamespace

import pytest

from agent.reasoning_backend import (
    ArcContinuousReasoningBackend,
    ProviderCapabilities,
    get_reasoning_backend,
    resolve_provider_capabilities,
)
from agent.continuous_state import ContinuousStateStore
from agent.native_compaction import is_native_compaction_model


def test_backend_registry_and_a_backend_preserves_runner_contract():
    agent = SimpleNamespace(
        api_mode="chat_completions",
        runtime_capabilities={},
        compression_enabled=True,
        session_id="test-session",
        _session_db=None,
        session_input_tokens=0,
        session_output_tokens=0,
        session_reasoning_tokens=0,
        session_estimated_cost_usd=0.0,
        _codex_reasoning_replay_enabled=True,
    )

    def api_call(_request):
        agent.session_input_tokens += 12
        agent.session_output_tokens += 4
        return object()

    agent._interruptible_api_call = api_call
    agent._compress_context = lambda messages, *_args, **_kwargs: (messages, None)

    def runner(current_agent, prompt):
        current_agent._interruptible_api_call({"input": prompt})
        current_agent._compress_context([], None)
        return {"final_response": "ok", "completed": True, "messages": [], "api_calls": 1}

    result = get_reasoning_backend("arc_continuous").run(agent, runner, "same task")

    assert result["final_response"] == "ok"
    assert result["reasoning_backend"] == "arc_continuous"
    assert result["reasoning_metrics"]["model_calls"] == 1
    assert result["reasoning_metrics"]["compaction_events"] == 1
    assert result["reasoning_metrics"]["input_tokens"] == 12
    assert result["reasoning_state"]["strategy"] == "continuous_conversation"


def test_capabilities_do_not_claim_native_state_for_local_chat_endpoint():
    agent = SimpleNamespace(
        api_mode="chat_completions",
        runtime_capabilities={"native_compaction": False},
        compression_enabled=True,
        reasoning_config={"enabled": True},
        native_mid_turn_steering=False,
        native_response_state_handle=False,
        _codex_reasoning_replay_enabled=True,
        _session_db=object(),
        session_id="session",
    )

    caps = resolve_provider_capabilities(agent)

    assert caps.native_conversation_continuation is False
    assert caps.persistent_reasoning_state is False
    assert caps.local_compaction is True
    assert caps.resumable_sessions is True


def test_responses_capabilities_reflect_native_replay_and_compaction():
    agent = SimpleNamespace(
        api_mode="codex_responses",
        provider="openai",
        runtime_capabilities={"native_compaction": True},
        compression_enabled=True,
        reasoning_config={"effort": "high"},
        native_mid_turn_steering=False,
        native_response_state_handle=False,
        _codex_reasoning_replay_enabled=True,
        _session_db=None,
        session_id="session",
    )

    caps = resolve_provider_capabilities(agent)

    assert caps.native_conversation_continuation is True
    assert caps.persistent_reasoning_state is True
    assert caps.provider_side_compaction is True
    assert caps.tool_call_continuation is True


def test_responses_compatible_relay_does_not_inherit_openai_native_claims():
    agent = SimpleNamespace(
        api_mode="codex_responses",
        provider="xai",
        base_url="https://api.x.ai/v1",
        runtime_capabilities={"native_compaction": False},
        compression_enabled=True,
        reasoning_config={"effort": "high"},
        native_mid_turn_steering=False,
        native_response_state_handle=False,
        _codex_reasoning_replay_enabled=True,
        _session_db=None,
        session_id="session",
    )

    caps = resolve_provider_capabilities(agent)

    assert caps.native_conversation_continuation is False
    assert caps.persistent_reasoning_state is False


def test_astra_is_explicitly_compaction_eligible_but_unknown_models_are_not():
    assert is_native_compaction_model("gpt-6-astra") is True
    assert is_native_compaction_model("gpt-6-astra-2026-09-01") is True
    assert is_native_compaction_model("gpt-6") is False


def test_unknown_backend_is_rejected_before_a_turn():
    with pytest.raises(ValueError, match="Unknown reasoning backend"):
        get_reasoning_backend("arc-game-runner")


def test_substrate_projects_active_turn_and_persists_working_state(tmp_path):
    agent = SimpleNamespace(
        _reasoning_native_tier=2,
        _reasoning_metrics=None,
    )
    store = ContinuousStateStore(state_id="session-1", path=tmp_path / "state.json")
    store._agent = agent
    store.begin_turn("Repair the repository and keep the tests passing.")

    raw_messages = [
        {"role": "user", "content": "An earlier unrelated request."},
        {"role": "assistant", "content": "That earlier request is complete."},
        {"role": "user", "content": "Repair the repository and keep the tests passing."},
        {"role": "assistant", "content": "1. Inspect the failing test\n2. Apply the smallest fix"},
        {"role": "tool", "name": "terminal", "content": "pytest failed in /workspace/project/tests/test_app.py"},
    ]
    store.observe_messages(raw_messages)
    api_messages = [
        {"role": "system", "content": "Hermes"},
        {"role": "user", "content": "An earlier unrelated request.", "_hermes_source_index": 0},
        {"role": "assistant", "content": "That earlier request is complete.", "_hermes_source_index": 1},
        {"role": "user", "content": "Repair the repository and keep the tests passing.", "_hermes_source_index": 2, "_hermes_current_turn": True},
        {"role": "assistant", "content": "1. Inspect the failing test\n2. Apply the smallest fix", "_hermes_source_index": 3},
        {"role": "tool", "name": "terminal", "content": "pytest failed in /workspace/project/tests/test_app.py", "_hermes_source_index": 4},
    ]

    projected = store.prepare_api_messages(
        api_messages,
        raw_messages,
        current_turn_user_idx=2,
    )

    assert len(projected) == 4  # system + active user turn and its two follow-ups
    assert not any("earlier unrelated" in str(item) for item in projected)
    assert "<hermes-continuous-state schema_version=2>" in projected[1]["content"]
    assert store.state.current_plan == ["Inspect the failing test", "Apply the smallest fix"]
    assert store.state.failures_and_retries
    assert (tmp_path / "state.json").exists()

    resumed = ContinuousStateStore(state_id="session-1", path=tmp_path / "state.json")
    assert resumed.state.objective == "Repair the repository and keep the tests passing."
    assert resumed.state.checkpoint_count >= 1


def test_task_lifecycle_continuation_refinement_switch_and_return(tmp_path):
    store = ContinuousStateStore(state_id="lifecycle", path=tmp_path / "state.json")
    store.begin_turn("Fix the nginx configuration and verify the listener.")
    first_epoch = store.state.task_epoch
    store.state.current_plan = ["inspect nginx", "run config test"]
    store.state.important_facts = ["nginx listens on 8080"]

    store.begin_turn("Continue fixing nginx and test the listener after the change.")
    assert store.state.task_epoch == first_epoch
    assert store.state.current_plan == ["inspect nginx", "run config test"]

    store.begin_turn("Now analyse this Python repository instead.")
    assert store.state.task_epoch == first_epoch + 1
    assert store.state.current_task_objective.startswith("Now analyse")
    assert store.state.current_plan == []
    assert store.state.important_facts == []

    store.begin_turn("Return to the nginx configuration and verify the listener.")
    assert store.state.task_epoch == first_epoch + 2
    assert store.state.current_plan == ["inspect nginx", "run config test"]
    assert store.state.important_facts == ["nginx listens on 8080"]


def test_task_state_survives_restart_and_current_turn_is_distinct(tmp_path):
    path = tmp_path / "state.json"
    store = ContinuousStateStore(state_id="restart", path=path)
    store.begin_turn("Repair the broken Docker healthcheck.")
    store.state.important_facts = ["the service is exposed on port 8080"]
    store.checkpoint("test_saved")

    resumed = ContinuousStateStore(state_id="restart", path=path)
    resumed.begin_turn("Please verify the Docker healthcheck fix.")
    assert resumed.state.task_epoch == 1
    assert resumed.state.current_user_turn_objective.startswith("Please verify")
    assert resumed.state.important_facts == ["the service is exposed on port 8080"]


def test_structured_state_delta_updates_and_prunes_state(tmp_path):
    store = ContinuousStateStore(state_id="delta", path=tmp_path / "state.json")
    store.begin_turn("Repair the service and leave it tested.")
    delta = {
        "facts_add": ["pytest passes after changing app.py"],
        "decisions_add": ["Use the existing test suite"],
        "hypotheses_add": ["The healthcheck is the root cause"],
        "questions_add": ["Is the service ready?"],
        "plan_replace": ["edit app.py", "run pytest"],
        "working_state": "The code is fixed; verification remains.",
    }
    content = "<hermes-state-delta>" + json.dumps(delta) + "</hermes-state-delta>"
    store.observe_messages([{"role": "assistant", "content": content}])
    assert store.state.important_facts == ["pytest passes after changing app.py"]
    assert store.state.current_plan == ["edit app.py", "run pytest"]
    assert store.state.unresolved_questions == ["Is the service ready?"]

    follow_up = {
        "facts_remove": ["pytest passes after changing app.py"],
        "hypotheses_resolved": ["The healthcheck is the root cause"],
        "resolved_questions": ["Is the service ready?"],
        "facts_add": ["pytest passes and the service is ready"],
    }
    store.observe_messages([{
        "role": "assistant",
        "content": "<hermes-state-delta>" + json.dumps(follow_up) + "</hermes-state-delta>",
    }])
    assert store.state.important_facts == ["pytest passes and the service is ready"]
    assert store.state.hypotheses == []
    assert store.state.unresolved_questions == []


def test_tool_facts_are_bounded_and_secrets_are_not_persisted(tmp_path):
    store = ContinuousStateStore(state_id="hygiene", path=tmp_path / "state.json")
    store.begin_turn("Check the deployment result.")
    store.observe_messages([{
        "role": "tool",
        "name": "terminal",
        "content": "pytest passed in /workspace/app; API_KEY=sk-super-secret-value",
    }])
    assert store.state.important_facts
    assert "super-secret" not in json.dumps(store.state.to_dict())
    assert len(store.state.important_facts[0]) <= 1200


def test_trajectory_compaction_deduplicates_redundant_observations(tmp_path):
    agent = SimpleNamespace(_reasoning_native_tier=2, _reasoning_metrics=None)
    store = ContinuousStateStore(state_id="session-2", path=tmp_path / "state.json")
    store._agent = agent
    store.begin_turn("Investigate the service failure.")
    messages = [{"role": "user", "content": "Investigate the service failure."}]
    for index in range(28):
        messages.append({"role": "assistant", "content": f"step {index}"})
        messages.append({"role": "tool", "name": "terminal", "content": "same output"})
    store.observe_messages(messages)

    assert store.maybe_compact(raw_messages=messages, reason="test_pressure") is True
    assert store.state.compaction_count == 1
    assert len(store.state.tool_observations) == 1
    assert store.state.compacted_trajectory[-1]["reason"] == "test_pressure"


def test_native_tier_keeps_provider_items_instead_of_generic_projection():
    agent = SimpleNamespace(
        _reasoning_native_tier=3,
        _reasoning_metrics=None,
        api_mode="codex_responses",
        provider="openai",
        _codex_reasoning_replay_enabled=True,
    )
    store = ContinuousStateStore(state_id="session-3")
    store._agent = agent
    store.begin_turn("Continue the task.")
    api_messages = [
        {"role": "system", "content": "Hermes"},
        {"role": "user", "content": "old turn", "_hermes_source_index": 0},
        {"role": "assistant", "content": "old reasoning", "_hermes_source_index": 1},
        {"role": "user", "content": "Continue the task.", "_hermes_current_turn": True},
    ]

    result = store.prepare_api_messages(api_messages, [
        {"role": "user", "content": "old turn"},
        {"role": "assistant", "content": "old reasoning"},
        {"role": "user", "content": "Continue the task."},
    ])

    assert len(result) == 4
    assert not any("hermes-continuous-state" in str(item) for item in result)
    assert all("_hermes_source_index" not in item for item in result)


def test_arc_backend_changes_the_request_context_between_tool_turns(tmp_path):
    common = [
        {"role": "user", "content": "Earlier task."},
        {"role": "assistant", "content": "Earlier task is complete."},
        {"role": "user", "content": "Inspect the fixture and write a report."},
    ]
    captured = []
    agent = SimpleNamespace(
        api_mode="chat_completions",
        provider="local",
        base_url="http://127.0.0.1:8000/v1",
        runtime_capabilities={},
        compression_enabled=True,
        session_id="integration-session",
        logs_dir=tmp_path,
        _session_db=None,
        session_input_tokens=0,
        session_output_tokens=0,
        session_reasoning_tokens=0,
        session_cache_read_tokens=0,
        session_estimated_cost_usd=0.0,
        _codex_reasoning_replay_enabled=False,
    )

    def runner(current_agent, _prompt):
        messages = list(common)
        for tool_output in ("read /tmp/one.txt", "read /tmp/two.txt"):
            messages.extend([
                {"role": "assistant", "content": "I will inspect the next artifact."},
                {"role": "tool", "name": "terminal", "content": tool_output},
            ])
            api = [
                {"role": "system", "content": "Hermes"},
                *[
                    {**message, "_hermes_source_index": index}
                    for index, message in enumerate(messages)
                ],
            ]
            api[3]["_hermes_current_turn"] = True
            captured.append(
                current_agent._reasoning_backend_prepare_context(
                    api,
                    messages,
                    current_turn_user_idx=2,
                )
            )
        return {"final_response": "report", "completed": True, "messages": messages, "api_calls": 2}

    result = get_reasoning_backend("arc_continuous").run(agent, runner, "Inspect the fixture")

    assert result["reasoning_continuity"] == "substrate_managed"
    assert captured[0][1]["role"] == "user"
    assert len(captured[0]) == 4  # system + active user + assistant/tool tail
    assert len(captured[1]) == 6  # same active turn, now with another tool result
    assert all("Earlier task" not in str(item) for item in captured[0])
    assert result["reasoning_metrics"]["substrate_context_projections"] == 2
    assert result["reasoning_state"]["tool_observations"] == 2
