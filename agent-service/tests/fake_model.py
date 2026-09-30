"""A scriptable stand-in for a language model.

Every test in this phase runs against this rather than a provider. The
investigation logic worth testing — the allowlist, the bounded loop, the
ledger, grounding validation — is all on our side of the model boundary, so
none of it needs a network, an API key or a paid call.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from app.investigation_model import AssistantTurn, ToolCall, TranscriptEntry
from app.investigation_models import InvestigationContext


class FakeModel:
    """Replays a scripted sequence of turns.

    Records what it was asked so tests can assert the workflow passed the right
    context, system prompt and tool specifications.
    """

    def __init__(
        self, turns: Sequence[AssistantTurn], raises_after: Exception | None = None
    ) -> None:
        self._turns = list(turns)
        # Raised once the script runs out, for testing a provider that fails
        # partway through an investigation — during a correction call, say.
        # Without it the fake loops on a tool request instead, which is a
        # different failure mode.
        self._raises_after = raises_after
        self.calls = 0
        self.seen_transcripts: list[list[TranscriptEntry]] = []
        self.seen_system_prompts: list[str] = []
        self.seen_tools: list[Sequence[Mapping[str, Any]]] = []

    async def next_turn(
        self,
        context: InvestigationContext,
        transcript: Sequence[TranscriptEntry],
        system_prompt: str,
        tools: Sequence[Mapping[str, Any]],
    ) -> AssistantTurn:
        self.calls += 1
        self.seen_transcripts.append(list(transcript))
        self.seen_system_prompts.append(system_prompt)
        self.seen_tools.append(tools)

        if self._turns:
            return self._turns.pop(0)
        if self._raises_after is not None:
            raise self._raises_after
        # Past the script: keep asking for the same tool, which is how a model
        # stuck in a loop behaves and what the round bound exists to stop.
        return tool_turn("get_transaction", {"transaction_id": context.transaction_id})


def tool_turn(tool: str, arguments: Mapping[str, Any], call_id: str = "call-1") -> AssistantTurn:
    return AssistantTurn(
        tool_calls=(ToolCall(call_id=call_id, tool=tool, arguments=arguments),)
    )


def final_turn(**payload: Any) -> AssistantTurn:
    """A final result, with the common fields defaulted.

    Passing ``...`` (Ellipsis) for a field **removes** it, which is how an
    omitted required field is expressed. That distinction matters: INV-1004
    failed because ``confidence`` was absent, not because it was null, and the
    two produce different Pydantic errors.
    """
    result: dict[str, Any] = {
        "classification": "PROCESSOR_FEE",
        "rootCause": "A settlement processing fee is consistent with the difference.",
        "confidence": 0.9,
        "evidence": [],
        "recommendedAction": "Review and classify as a processor fee adjustment.",
        "requiresHumanApproval": True,
    }
    result.update(payload)
    return AssistantTurn(
        final_result={key: value for key, value in result.items() if value is not ...}
    )
