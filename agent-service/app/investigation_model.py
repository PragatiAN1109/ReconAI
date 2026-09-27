"""The boundary between the investigation workflow and a language model.

Kept deliberately thin. Everything interesting — the tool allowlist, the bounded
loop, the evidence ledger, grounding validation — lives on our side of this
line and is testable without a model, a network or an API key.

A provider's only job is: given the context and what has happened so far,
either ask for tools or produce a final result.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from app.investigation_models import InvestigationContext


class InvestigationModelError(RuntimeError):
    """The model provider could not be used.

    A provider outage, a rate limit, a rejected key or a truncated reply. Kept
    separate from a failed investigation: the difference between "the evidence
    does not support a conclusion" and "we could not ask" matters, and provider
    exception types must not leak into the workflow that consumes them.
    """


@dataclass(frozen=True)
class ToolCall:
    """A model's request to run one allowed tool. Not yet validated."""

    call_id: str
    tool: str
    arguments: Mapping[str, Any]


@dataclass(frozen=True)
class ToolResult:
    """What running a tool produced, on its way back to the model."""

    call_id: str
    tool: str
    content: str
    failed: bool = False


@dataclass(frozen=True)
class AssistantTurn:
    """One model turn: either tool requests, or a final answer.

    ``final_result`` is a raw mapping rather than an ``InvestigationResult``.
    Validation belongs to the workflow, so that a malformed result is something
    we detect rather than something a provider is trusted to prevent.
    """

    tool_calls: tuple[ToolCall, ...] = ()
    final_result: Mapping[str, Any] | None = None

    @property
    def is_final(self) -> bool:
        return self.final_result is not None


@dataclass(frozen=True)
class ToolResults:
    """A batch of tool results returned to the model."""

    results: tuple[ToolResult, ...]


#: What has happened so far, oldest first. A provider translates this into
#: whatever message format it needs; the workflow keeps it provider-neutral.
TranscriptEntry = AssistantTurn | ToolResults


@runtime_checkable
class InvestigationModel(Protocol):
    """Produces the next turn of an investigation.

    Stateless by design: the full transcript is passed each time rather than
    held on the provider, so one provider instance can serve concurrent
    investigations and a fake can be scripted by turn.
    """

    async def next_turn(
        self,
        context: InvestigationContext,
        transcript: Sequence[TranscriptEntry],
        system_prompt: str,
        tools: Sequence[Mapping[str, Any]],
    ) -> AssistantTurn:
        """Return the model's next turn: tool requests, or a final result."""
        ...


SYSTEM_PROMPT = """\
You are investigating why two authoritative financial records disagree.

A deterministic reconciliation engine has already established THAT they
disagree and classified the discrepancy. Your task is to establish WHY, or to
report that the available evidence does not support a conclusion.

Rules:

- Use only evidence returned by the tools. Do not rely on background knowledge
  about payment processing to assert facts about these records.
- Never invent an identifier. Only cite a transaction, settlement, fee rule or
  policy document that a tool actually returned to you in this conversation.
- The deterministic exception type is an observed symptom, not a cause. An
  AMOUNT_MISMATCH is not by itself evidence of a fee.
- Distinguish evidence from inference. A fee rule whose amount equals a
  settlement difference is consistent with a fee having been charged; it is not
  proof that it was. Say which you have.
- Prefer INSUFFICIENT_EVIDENCE over a plausible guess. Reporting that the
  evidence does not support a conclusion is a correct and useful outcome, and
  is strongly preferred to an explanation you cannot support.
- You have no ability to change any financial record, resolve anything, or
  approve anything. Never state or imply that you have.
- Every recommendation is advisory and requires human review.

When you have gathered what you need, return a final result with:
classification, rootCause, confidence (0.0-1.0), evidence (a list of
sourceType/reference, optionally section), recommendedAction, and
requiresHumanApproval (always true).
"""
