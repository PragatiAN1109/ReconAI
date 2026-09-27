"""Tests for the Anthropic adapter, against the SDK's own types.

Responses are constructed from real ``anthropic.types`` objects rather than
hand-written dictionaries, so these tests fail if the SDK changes shape. Nothing
here contacts the API, and no key is required: the client is replaced with a
stub that returns prepared responses.

The adapter is translation only. Its job is to turn our transcript into the
provider's request shape and the provider's reply back into ours.
"""

import sys
from collections.abc import Sequence
from typing import Any

import pytest

from app.events import ExceptionType
from app.investigation_model import (
    AssistantTurn,
    InvestigationModelError,
    ToolCall,
    ToolResult,
    ToolResults,
)
from app.investigation_models import InvestigationContext

anthropic = pytest.importorskip("anthropic")
from anthropic.types import Message, TextBlock, ToolUseBlock, Usage  # noqa: E402

from app.anthropic_model import (  # noqa: E402
    SUBMIT_RESULT_TOOL,
    AnthropicInvestigationModel,
)

CONTEXT = InvestigationContext(
    investigationId="INV-1001",
    exceptionId="EX-1008",
    transactionId="TX-10009",
    exceptionType=ExceptionType.AMOUNT_MISMATCH,
)

RESULT_PAYLOAD = {
    "classification": "PROCESSOR_FEE",
    "rootCause": "A processing fee matches the difference.",
    "confidence": 0.86,
    "evidence": [{"sourceType": "FEE_RULE", "reference": "FR-14"}],
    "recommendedAction": "Review and classify as a processor fee adjustment.",
    "requiresHumanApproval": True,
}


def message(*blocks: Any, stop_reason: str = "tool_use") -> Message:
    """A real SDK Message, so these tests track the SDK's actual shape."""
    return Message(
        id="msg_01",
        type="message",
        role="assistant",
        model="claude-sonnet-5",
        content=list(blocks),
        stop_reason=stop_reason,
        stop_sequence=None,
        usage=Usage(input_tokens=10, output_tokens=5),
    )


def tool_use(name: str, arguments: dict[str, Any], block_id: str = "toolu_01") -> ToolUseBlock:
    return ToolUseBlock(id=block_id, name=name, input=arguments, type="tool_use")


class StubMessages:
    """Stands in for ``client.messages``; records requests, returns prepared replies."""

    def __init__(self, replies: Sequence[Any]) -> None:
        self._replies = list(replies)
        self.requests: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def adapter(*replies: Any) -> tuple[AnthropicInvestigationModel, StubMessages]:
    """An adapter whose transport is a stub. No key is used and no call is made."""
    model = AnthropicInvestigationModel.__new__(AnthropicInvestigationModel)
    messages = StubMessages(replies)
    model._client = type("StubClient", (), {"messages": messages})()  # noqa: SLF001
    model._model = "claude-sonnet-5"  # noqa: SLF001
    model._max_tokens = 2048  # noqa: SLF001
    model._provider_error = anthropic.AnthropicError  # noqa: SLF001
    return model, messages


async def turn(model: AnthropicInvestigationModel, transcript=()) -> AssistantTurn:
    return await model.next_turn(CONTEXT, list(transcript), "system prompt", [])


# ---------------------------------------------------------------------------
# Tool-use translation
# ---------------------------------------------------------------------------


async def test_a_tool_use_block_becomes_a_tool_call() -> None:
    model, _ = adapter(message(tool_use("get_transaction", {"transaction_id": "TX-10009"})))

    result = await turn(model)

    assert not result.is_final
    assert len(result.tool_calls) == 1
    assert isinstance(result.tool_calls[0], ToolCall)


async def test_the_tool_name_is_preserved_exactly() -> None:
    model, _ = adapter(message(tool_use("search_policy_documents", {"query": "fee"})))

    result = await turn(model)

    assert result.tool_calls[0].tool == "search_policy_documents"


async def test_tool_arguments_are_preserved() -> None:
    arguments = {
        "merchant_id": "MERCHANT-PHASE43-DEMO",
        "processor": "NORTHSTAR_PAYMENTS",
        "currency": "USD",
        "active": True,
    }
    model, _ = adapter(message(tool_use("get_fee_rules", arguments)))

    result = await turn(model)

    assert dict(result.tool_calls[0].arguments) == arguments


async def test_the_tool_use_id_is_preserved_for_correlation() -> None:
    """Anthropic matches a tool result to its request by this id."""
    model, _ = adapter(message(tool_use("get_transaction", {"transaction_id": "TX-1"}, "toolu_xyz")))

    result = await turn(model)

    assert result.tool_calls[0].call_id == "toolu_xyz"


async def test_several_tool_use_blocks_in_one_reply_are_all_collected() -> None:
    model, _ = adapter(
        message(
            tool_use("get_transaction", {"transaction_id": "TX-10009"}, "toolu_a"),
            tool_use("get_settlements", {"transaction_id": "TX-10009"}, "toolu_b"),
        )
    )

    result = await turn(model)

    assert [call.tool for call in result.tool_calls] == ["get_transaction", "get_settlements"]
    assert [call.call_id for call in result.tool_calls] == ["toolu_a", "toolu_b"]


async def test_text_blocks_alongside_tool_use_are_ignored() -> None:
    """Prose is not evidence and is not part of the contract."""
    model, _ = adapter(
        message(
            TextBlock(type="text", text="Let me look at the transaction.", citations=None),
            tool_use("get_transaction", {"transaction_id": "TX-10009"}),
        )
    )

    result = await turn(model)

    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].tool == "get_transaction"


# ---------------------------------------------------------------------------
# Final-result translation
# ---------------------------------------------------------------------------


async def test_the_submit_result_tool_becomes_a_final_turn() -> None:
    model, _ = adapter(
        message(tool_use(SUBMIT_RESULT_TOOL, RESULT_PAYLOAD), stop_reason="tool_use")
    )

    result = await turn(model)

    assert result.is_final
    assert result.final_result == RESULT_PAYLOAD
    assert result.tool_calls == ()


async def test_the_final_payload_is_passed_through_unvalidated() -> None:
    """Validation belongs to the workflow, not to a provider adapter."""
    nonsense = {"classification": "NOT_A_CLASSIFICATION", "confidence": 99}
    model, _ = adapter(message(tool_use(SUBMIT_RESULT_TOOL, nonsense)))

    result = await turn(model)

    assert result.final_result == nonsense


async def test_a_result_submitted_alongside_tool_requests_wins() -> None:
    model, _ = adapter(
        message(
            tool_use("get_transaction", {"transaction_id": "TX-1"}, "toolu_a"),
            tool_use(SUBMIT_RESULT_TOOL, RESULT_PAYLOAD, "toolu_b"),
        )
    )

    result = await turn(model)

    assert result.is_final
    assert result.final_result == RESULT_PAYLOAD


# ---------------------------------------------------------------------------
# Request construction
# ---------------------------------------------------------------------------


async def test_the_request_carries_the_model_prompt_and_tools() -> None:
    model, stub = adapter(message(tool_use("get_transaction", {"transaction_id": "TX-1"})))

    await model.next_turn(
        CONTEXT, [], "the system prompt", [{"name": "get_transaction", "input_schema": {}}]
    )

    request = stub.requests[0]
    assert request["model"] == "claude-sonnet-5"
    assert request["max_tokens"] == 2048
    assert request["system"] == "the system prompt"
    # The controlled tools, plus the submit-result tool the adapter adds.
    assert [tool["name"] for tool in request["tools"]] == ["get_transaction", SUBMIT_RESULT_TOOL]


async def test_the_opening_message_states_the_investigation_context() -> None:
    model, stub = adapter(message(tool_use("get_transaction", {"transaction_id": "TX-1"})))

    await turn(model)

    first = stub.requests[0]["messages"][0]
    assert first["role"] == "user"
    assert "EX-1008" in first["content"]
    assert "TX-10009" in first["content"]
    assert "AMOUNT_MISMATCH" in first["content"]


async def test_tool_results_are_translated_into_provider_blocks() -> None:
    transcript = [
        AssistantTurn(
            tool_calls=(ToolCall("toolu_a", "get_transaction", {"transaction_id": "TX-1"}),)
        ),
        ToolResults(
            results=(ToolResult(call_id="toolu_a", tool="get_transaction", content='{"ok":1}'),)
        ),
    ]
    model, stub = adapter(message(tool_use(SUBMIT_RESULT_TOOL, RESULT_PAYLOAD)))

    await turn(model, transcript)

    messages = stub.requests[0]["messages"]
    assistant, tool_message = messages[1], messages[2]
    assert assistant["role"] == "assistant"
    assert assistant["content"][0] == {
        "type": "tool_use",
        "id": "toolu_a",
        "name": "get_transaction",
        "input": {"transaction_id": "TX-1"},
    }
    assert tool_message["role"] == "user"
    assert tool_message["content"][0] == {
        "type": "tool_result",
        "tool_use_id": "toolu_a",
        "content": '{"ok":1}',
        "is_error": False,
    }


async def test_the_correlation_id_survives_the_round_trip() -> None:
    """The id the provider issued must come back on the matching tool result."""
    model, stub = adapter(
        message(tool_use("get_transaction", {"transaction_id": "TX-1"}, "toolu_roundtrip")),
        message(tool_use(SUBMIT_RESULT_TOOL, RESULT_PAYLOAD)),
    )

    first = await turn(model)
    issued = first.tool_calls[0].call_id
    transcript = [
        first,
        ToolResults(results=(ToolResult(call_id=issued, tool="get_transaction", content="{}"),)),
    ]
    await turn(model, transcript)

    sent = stub.requests[1]["messages"]
    assert sent[1]["content"][0]["id"] == "toolu_roundtrip"
    assert sent[2]["content"][0]["tool_use_id"] == "toolu_roundtrip"


async def test_a_failed_tool_is_marked_as_an_error_for_the_provider() -> None:
    transcript = [
        AssistantTurn(tool_calls=(ToolCall("toolu_a", "unknown_tool", {}),)),
        ToolResults(
            results=(
                ToolResult(
                    call_id="toolu_a", tool="unknown_tool", content="Unknown tool", failed=True
                ),
            )
        ),
    ]
    model, stub = adapter(message(tool_use(SUBMIT_RESULT_TOOL, RESULT_PAYLOAD)))

    await turn(model, transcript)

    assert stub.requests[0]["messages"][2]["content"][0]["is_error"] is True


# ---------------------------------------------------------------------------
# Stop reasons
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("stop_reason", ["tool_use", "end_turn", "stop_sequence", "pause_turn"])
async def test_a_complete_reply_is_translated(stop_reason: str) -> None:
    model, _ = adapter(
        message(tool_use(SUBMIT_RESULT_TOOL, RESULT_PAYLOAD), stop_reason=stop_reason)
    )

    result = await turn(model)

    assert result.is_final


@pytest.mark.parametrize(
    "stop_reason", ["max_tokens", "refusal", "model_context_window_exceeded"]
)
async def test_an_incomplete_reply_fails_explicitly(stop_reason: str) -> None:
    """A truncated reply must not be parsed into a turn as if it were whole."""
    model, _ = adapter(
        message(tool_use(SUBMIT_RESULT_TOOL, RESULT_PAYLOAD), stop_reason=stop_reason)
    )

    with pytest.raises(InvestigationModelError) as failure:
        await turn(model)

    assert stop_reason in str(failure.value)


# ---------------------------------------------------------------------------
# Unexpected replies fabricate nothing
# ---------------------------------------------------------------------------


async def test_a_reply_with_no_blocks_yields_no_result() -> None:
    model, _ = adapter(message(stop_reason="end_turn"))

    result = await turn(model)

    assert result.tool_calls == ()
    assert result.final_result is None


async def test_a_text_only_reply_yields_no_fabricated_result() -> None:
    """Prose is never scraped into a result; the workflow rejects the empty turn."""
    model, _ = adapter(
        message(
            TextBlock(type="text", text="I think this is a processor fee.", citations=None),
            stop_reason="end_turn",
        )
    )

    result = await turn(model)

    assert result.final_result is None
    assert result.tool_calls == ()


# ---------------------------------------------------------------------------
# The provider error boundary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        anthropic.APIConnectionError(request=None),  # type: ignore[arg-type]
        anthropic.APITimeoutError(request=None),  # type: ignore[arg-type]
        anthropic.AnthropicError("something provider-specific"),
    ],
)
async def test_provider_errors_become_a_reconai_error(error: Exception) -> None:
    model, _ = adapter(error)

    with pytest.raises(InvestigationModelError):
        await turn(model)


async def test_no_provider_exception_type_escapes_the_adapter() -> None:
    model, _ = adapter(anthropic.APIConnectionError(request=None))  # type: ignore[arg-type]

    with pytest.raises(InvestigationModelError) as failure:
        await turn(model)

    assert not isinstance(failure.value, anthropic.AnthropicError)
    # The type is named for diagnosis; the provider object does not propagate.
    assert "APIConnectionError" in str(failure.value)


async def test_the_provider_error_is_not_an_investigation_failure() -> None:
    """"We could not ask" and "the evidence does not support a conclusion" differ."""
    from app.investigation_agent import InvestigationFailed  # noqa: PLC0415

    assert not issubclass(InvestigationModelError, InvestigationFailed)


# ---------------------------------------------------------------------------
# Secrets and the optional dependency
# ---------------------------------------------------------------------------


def test_the_api_key_never_appears_in_the_adapter_repr() -> None:
    model = AnthropicInvestigationModel(api_key="sk-ant-do-not-print", model="claude-sonnet-5")

    assert "do-not-print" not in repr(model)
    assert "do-not-print" not in str(vars(model).get("_model", ""))


def test_the_module_imports_without_the_sdk_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The SDK is optional: importing this module must not require it."""
    import importlib  # noqa: PLC0415

    monkeypatch.setitem(sys.modules, "anthropic", None)
    module = importlib.reload(importlib.import_module("app.anthropic_model"))

    assert module is not None


def test_configuring_the_provider_without_the_sdk_says_what_to_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib  # noqa: PLC0415

    monkeypatch.setitem(sys.modules, "anthropic", None)
    module = importlib.reload(importlib.import_module("app.anthropic_model"))

    with pytest.raises(RuntimeError) as failure:
        module.AnthropicInvestigationModel(api_key="sk-test", model="claude-sonnet-5")

    assert '".[llm]"' in str(failure.value)

    # Restore the real module for any test that follows.
    monkeypatch.undo()
    importlib.reload(importlib.import_module("app.anthropic_model"))


def test_the_application_starts_with_no_provider_configured() -> None:
    """The default path: no key, no SDK needed, only investigation unavailable."""
    from app.config import Settings  # noqa: PLC0415
    from app.main import _build_model  # noqa: PLC0415

    assert _build_model(Settings(_env_file=None)) is None
