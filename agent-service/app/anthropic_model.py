"""An Anthropic-backed implementation of the investigation model boundary.

Translation only. It converts the provider-neutral transcript into Anthropic's
message format and converts the reply back. It holds no investigation logic: the
tool allowlist, the bounded loop, the evidence ledger and grounding validation
all live on our side of the boundary and are tested without any provider.

The SDK is an optional dependency, imported lazily, so the service installs and
its tests run with no provider package present.

Verified structurally against anthropic SDK 0.125.0 with constructed response
objects of the SDK's own types. **Not exercised against a live API** — no
credentials were available — so treat the first real call as a verification
step for latency, auth and rate-limit behaviour rather than for shape.
"""

import json
import logging
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from app.investigation_model import (
    AssistantTurn,
    InvestigationModelError,
    ToolCall,
    ToolResults,
    TranscriptEntry,
)
from app.investigation_models import InvestigationContext

logger = logging.getLogger(__name__)

#: Stop reasons that mean the reply cannot be trusted as complete. Taken from
#: the SDK's own Literal on Message.stop_reason.
_INCOMPLETE_STOP_REASONS = frozenset(
    {"max_tokens", "refusal", "model_context_window_exceeded"}
)

#: Asked for when the model has gathered enough. Making the final answer a tool
#: means the provider enforces its shape, so a result arrives as structured data
#: rather than prose to be scraped.
SUBMIT_RESULT_TOOL = "submit_investigation_result"

_SUBMIT_RESULT_SPECIFICATION: dict[str, Any] = {
    "name": SUBMIT_RESULT_TOOL,
    "description": (
        "Submit the final investigation result. Call this once, when the "
        "evidence gathered supports a conclusion or clearly does not."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "classification": {
                "type": "string",
                "enum": [
                    "PROCESSOR_FEE",
                    "PROCESSOR_DELAY",
                    "DUPLICATE_PROCESSING",
                    "CURRENCY_CONVERSION",
                    "PROCESSOR_ERROR",
                    "UNKNOWN",
                    "INSUFFICIENT_EVIDENCE",
                ],
            },
            "rootCause": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "evidence": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "sourceType": {
                            "type": "string",
                            "enum": [
                                "TRANSACTION",
                                "SETTLEMENT",
                                "FEE_RULE",
                                "POLICY_DOCUMENT",
                            ],
                        },
                        "reference": {"type": "string"},
                        "section": {"type": "string"},
                    },
                    "required": ["sourceType", "reference"],
                },
            },
            "recommendedAction": {"type": "string"},
            "requiresHumanApproval": {"type": "boolean"},
        },
        "required": [
            "classification",
            "rootCause",
            "confidence",
            "evidence",
            "recommendedAction",
            "requiresHumanApproval",
        ],
    },
}


class AnthropicInvestigationModel:
    """Drives an investigation using the Anthropic Messages API."""

    def __init__(self, api_key: str, model: str, max_tokens: int = 2048) -> None:
        # Imported here rather than at module scope so that the SDK stays a
        # genuinely optional dependency: importing this module without it
        # installed must not fail, and configuring the provider without it must
        # fail with a message that says what to install.
        try:
            from anthropic import AnthropicError, AsyncAnthropic  # noqa: PLC0415
        except ImportError as error:
            raise RuntimeError(
                "The anthropic package is required for this provider. "
                'Install it with: pip install -e ".[llm]"'
            ) from error

        # The key is held by the SDK client and never logged, echoed or stored.
        self._client = AsyncAnthropic(api_key=api_key)
        # Held so provider exceptions can be caught without a module-level import.
        self._provider_error: type[Exception] = AnthropicError
        self._model = model
        self._max_tokens = max_tokens

    async def next_turn(
        self,
        context: InvestigationContext,
        transcript: Sequence[TranscriptEntry],
        system_prompt: str,
        tools: Sequence[Mapping[str, Any]],
    ) -> AssistantTurn:
        messages = self._to_messages(context, transcript)
        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=system_prompt,
                tools=[*tools, _SUBMIT_RESULT_SPECIFICATION],
                messages=messages,
            )
        except self._provider_error as error:
            # Provider exception types stop here. The workflow sees one error
            # it owns, and swapping providers cannot change what it must catch.
            logger.error(
                "Model provider call failed [investigation_id=%s error_type=%s]",
                context.investigation_id,
                type(error).__name__,
            )
            raise InvestigationModelError(
                f"The investigation model could not be reached: {type(error).__name__}"
            ) from error
        # Only identifiers and counts are logged. Provider payloads carry
        # financial evidence and are not written to logs.
        logger.debug(
            "Model turn received [investigation_id=%s stop_reason=%s blocks=%d]",
            context.investigation_id,
            response.stop_reason,
            len(response.content),
        )
        return self._to_turn(response, context.investigation_id)

    def _to_messages(
        self, context: InvestigationContext, transcript: Sequence[TranscriptEntry]
    ) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": (
                    f"Investigate reconciliation exception {context.exception_id}.\n"
                    f"Transaction: {context.transaction_id}\n"
                    f"Deterministic exception type: {context.exception_type.value}\n\n"
                    "Gather evidence with the tools, then submit a result."
                ),
            }
        ]

        for entry in transcript:
            if isinstance(entry, ToolResults):
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": result.call_id,
                                "content": result.content,
                                "is_error": result.failed,
                            }
                            for result in entry.results
                        ],
                    }
                )
            elif entry.tool_calls:
                messages.append(
                    {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": call.call_id,
                                "name": call.tool,
                                "input": dict(call.arguments),
                            }
                            for call in entry.tool_calls
                        ],
                    }
                )

        return messages

    @staticmethod
    def _to_turn(response: Any, investigation_id: str) -> AssistantTurn:
        """Split the reply into evidence requests and a possible final result.

        A reply the provider says is incomplete is rejected rather than parsed.
        A response truncated at the token limit, or one the model refused, would
        otherwise arrive as a turn with no tool calls and no result — which the
        workflow would report as a malformed turn, hiding the real cause.
        """
        if response.stop_reason in _INCOMPLETE_STOP_REASONS:
            logger.error(
                "Model reply was incomplete [investigation_id=%s stop_reason=%s]",
                investigation_id,
                response.stop_reason,
            )
            raise InvestigationModelError(
                f"The investigation model returned an incomplete reply "
                f"(stop_reason={response.stop_reason})"
            )

        tool_calls: list[ToolCall] = []

        for block in response.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            if block.name == SUBMIT_RESULT_TOOL:
                if tool_calls:
                    # A turn that both asks for evidence and submits a
                    # conclusion has already concluded. The conclusion wins, and
                    # the abandoned requests are logged rather than dropped
                    # silently.
                    logger.warning(
                        "Model submitted a result alongside tool requests; the requests "
                        "were not executed [investigation_id=%s abandoned=%s]",
                        investigation_id,
                        [call.tool for call in tool_calls],
                    )
                # The result is returned raw. Validating it here would put trust
                # in the provider adapter; the workflow validates instead.
                return AssistantTurn(final_result=dict(block.input))
            tool_calls.append(
                ToolCall(
                    call_id=block.id or str(uuid.uuid4()),
                    tool=block.name,
                    arguments=dict(block.input),
                )
            )

        return AssistantTurn(tool_calls=tuple(tool_calls))


def describe_payload(payload: Mapping[str, Any]) -> str:
    """Compact, log-safe rendering of a result payload."""
    return json.dumps({key: payload.get(key) for key in ("classification", "confidence")})
