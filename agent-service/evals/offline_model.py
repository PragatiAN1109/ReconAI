"""A deterministic provider that needs no network, no key and no money.

It satisfies the production ``InvestigationModel`` protocol, so the real agent
cannot tell it apart from Anthropic: same transcript in, same ``AssistantTurn``
out, same tool loop, same grounding validation afterwards.

**What offline mode measures is the harness, not a language model.** This
provider replays a script written next to the scenario. If the script says
PROCESSOR_FEE, the "classification accuracy" of that run is a fact about the
script. Every report this framework emits says so at the top, because a number
labelled "accuracy" is otherwise read as a claim about Claude.

What it *does* measure honestly: that scenarios are well-formed, that evidence
is retrievable, that grounding rejects unbacked citations, that the guardrail
routes as documented, and that every metric computes correctly — including on
deliberately wrong behaviour.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from app.investigation_model import AssistantTurn, ToolCall, TranscriptEntry
from app.investigation_models import InvestigationContext

from evals.scenario import Scenario


class OfflineInvestigationModel:
    """Replays a scenario's scripted tool calls, then submits its scripted result."""

    def __init__(self, scenario: Scenario) -> None:
        self._script = scenario.offline
        self._scenario_id = scenario.scenario_id
        self.calls = 0

    async def next_turn(
        self,
        context: InvestigationContext,
        transcript: Sequence[TranscriptEntry],
        system_prompt: str,
        tools: Sequence[Mapping[str, Any]],
    ) -> AssistantTurn:
        index = self.calls
        self.calls += 1

        if index < len(self._script.tool_calls):
            tool = self._script.tool_calls[index]
            return AssistantTurn(
                tool_calls=(
                    ToolCall(
                        call_id=f"{self._scenario_id}-{index}",
                        tool=tool,
                        arguments=_arguments_for(tool, context),
                    ),
                )
            )

        return AssistantTurn(
            final_result={
                "classification": self._script.classification.value,
                "rootCause": self._script.root_cause,
                "confidence": self._script.confidence,
                "evidence": [
                    {"sourceType": source, "reference": reference}
                    | ({"section": section} if section else {})
                    for source, reference, section in self._script.citations
                ],
                "recommendedAction": self._script.recommended_action,
                "requiresHumanApproval": True,
            }
        )


def _arguments_for(tool: str, context: InvestigationContext) -> dict[str, Any]:
    """Plausible arguments for each tool, derived from the context.

    The arguments are validated by the production tool layer, so a wrong shape
    here would surface as a real ``ToolError`` rather than being silently
    accepted — which is the behaviour worth having.
    """
    match tool:
        case "get_transaction" | "get_settlements":
            return {"transaction_id": context.transaction_id}
        case "get_fee_rules":
            return {}
        case _:
            return {"query": "settlement fee policy amount mismatch"}
