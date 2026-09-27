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

    def __init__(self, turns: Sequence[AssistantTurn]) -> None:
        self._turns = list(turns)
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
        # Past the script: keep asking for the same tool, which is how a model
        # stuck in a loop behaves and what the round bound exists to stop.
        return tool_turn("get_transaction", {"transaction_id": context.transaction_id})


def tool_turn(tool: str, arguments: Mapping[str, Any], call_id: str = "call-1") -> AssistantTurn:
    return AssistantTurn(
        tool_calls=(ToolCall(call_id=call_id, tool=tool, arguments=arguments),)
    )


def final_turn(**payload: Any) -> AssistantTurn:
    """A final result, with the common fields defaulted."""
    result: dict[str, Any] = {
        "classification": "PROCESSOR_FEE",
        "rootCause": "A settlement processing fee is consistent with the difference.",
        "confidence": 0.9,
        "evidence": [],
        "recommendedAction": "Review and classify as a processor fee adjustment.",
        "requiresHumanApproval": True,
    }
    result.update(payload)
    return AssistantTurn(final_result=result)
