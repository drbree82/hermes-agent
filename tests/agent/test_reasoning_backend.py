from types import SimpleNamespace

import pytest

from agent.reasoning_backend import (
    ArcContinuousReasoningBackend,
    ProviderCapabilities,
    get_reasoning_backend,
    resolve_provider_capabilities,
)
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
