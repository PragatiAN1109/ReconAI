"""The investigation workflow: a bounded loop, then grounding validation.

One investigator, not a team of them. It asks for evidence through the
controlled tools, reasons over what comes back, and proposes an explanation
that the application then checks against what was actually retrieved.

The loop is bounded. An agent that can call tools indefinitely is an agent that
can spend indefinitely, and a model that cannot reach a conclusion should fail
visibly rather than circle.
"""

import logging

from pydantic import ValidationError

from app.evidence_ledger import EvidenceLedger
from app.financial_core_client import FinancialCoreClient
from app.investigation_model import (
    SYSTEM_PROMPT,
    AssistantTurn,
    InvestigationModel,
    ToolResult,
    ToolResults,
    TranscriptEntry,
)
from app.investigation_models import InvestigationContext, InvestigationResult
from app.investigation_tools import TOOL_SPECIFICATIONS, InvestigationTools, ToolError
from app.policy_search import PolicySearch

logger = logging.getLogger(__name__)

#: Enough rounds to fetch a transaction, its settlements, fee rules and a policy
#: search, with room to follow up. Small enough that a model going in circles
#: stops quickly.
DEFAULT_MAX_TOOL_ROUNDS = 8


class InvestigationFailed(RuntimeError):
    """The investigation could not produce a usable result.

    Raised rather than returning a degraded result. An investigation that did
    not reach a grounded conclusion has no conclusion, and inventing a
    placeholder would put an unsupported explanation into the system wearing a
    schema-valid shape.
    """


class UngroundedResultError(InvestigationFailed):
    """The model cited evidence that no tool returned.

    The reason the ledger exists. A citation that cannot be traced to a
    retrieval is a fabrication, however plausible the identifier looks.
    """


class InvestigationAgent:
    """Runs one investigation to a validated, grounded result."""

    def __init__(
        self,
        model: InvestigationModel,
        financial_core: FinancialCoreClient,
        policies: PolicySearch,
        max_tool_rounds: int = DEFAULT_MAX_TOOL_ROUNDS,
    ) -> None:
        self._model = model
        self._financial_core = financial_core
        self._policies = policies
        self._max_tool_rounds = max_tool_rounds

    async def investigate(
        self, context: InvestigationContext
    ) -> tuple[InvestigationResult, EvidenceLedger]:
        """Investigate one exception and return its result with the evidence behind it.

        The ledger comes back alongside the result so a caller can see what the
        conclusion actually rests on, not merely what it claims to.

        :raises UngroundedResultError: the result cited evidence never retrieved
        :raises InvestigationFailed: no result within the bound, or a malformed one
        """
        ledger = EvidenceLedger()
        tools = InvestigationTools(self._financial_core, self._policies, ledger)
        transcript: list[TranscriptEntry] = []

        logger.info(
            "Investigation started [investigation_id=%s exception_id=%s transaction_id=%s "
            "exception_type=%s max_tool_rounds=%d]",
            context.investigation_id,
            context.exception_id,
            context.transaction_id,
            context.exception_type.value,
            self._max_tool_rounds,
        )

        for round_number in range(1, self._max_tool_rounds + 1):
            turn = await self._model.next_turn(
                context, transcript, SYSTEM_PROMPT, TOOL_SPECIFICATIONS
            )
            transcript.append(turn)

            if turn.is_final:
                result = self._validate(context, turn, ledger)
                logger.info(
                    "Investigation completed [investigation_id=%s classification=%s "
                    "confidence=%.2f citations=%s rounds=%d]",
                    context.investigation_id,
                    result.classification.value,
                    result.confidence,
                    result.cites(),
                    round_number,
                )
                return result, ledger

            if not turn.tool_calls:
                raise InvestigationFailed(
                    f"Investigation {context.investigation_id} produced a turn with neither "
                    f"tool requests nor a result"
                )

            results = await self._run_tools(context, tools, turn)
            transcript.append(ToolResults(results=results))

        # The bound is a safety limit, not a suggestion. Nothing is fabricated
        # to fill the gap.
        logger.error(
            "Investigation abandoned after exhausting its tool budget "
            "[investigation_id=%s max_tool_rounds=%d evidence=%s]",
            context.investigation_id,
            self._max_tool_rounds,
            ledger.summary(),
        )
        raise InvestigationFailed(
            f"Investigation {context.investigation_id} did not reach a conclusion within "
            f"{self._max_tool_rounds} tool rounds"
        )

    async def _run_tools(
        self,
        context: InvestigationContext,
        tools: InvestigationTools,
        turn: AssistantTurn,
    ) -> tuple[ToolResult, ...]:
        """Execute the requested tools, turning failures into results.

        A refused or failed tool is reported back to the model rather than
        raised. The model asked for something it could not have; that is
        information it should act on, not a reason to end the investigation.
        """
        results: list[ToolResult] = []

        for call in turn.tool_calls:
            logger.info(
                "Tool requested [investigation_id=%s tool=%s arguments=%s]",
                context.investigation_id,
                call.tool,
                sorted(call.arguments),
            )
            try:
                content = await tools.execute(call.tool, call.arguments)
            except ToolError as error:
                logger.warning(
                    "Tool failed [investigation_id=%s tool=%s reason=%s]",
                    context.investigation_id,
                    call.tool,
                    error,
                )
                results.append(
                    ToolResult(
                        call_id=call.call_id, tool=call.tool, content=str(error), failed=True
                    )
                )
                continue

            logger.info(
                "Tool completed [investigation_id=%s tool=%s]",
                context.investigation_id,
                call.tool,
            )
            results.append(ToolResult(call_id=call.call_id, tool=call.tool, content=content))

        return tuple(results)

    def _validate(
        self, context: InvestigationContext, turn: AssistantTurn, ledger: EvidenceLedger
    ) -> InvestigationResult:
        """Parse the model's result, then check every citation against the ledger."""
        assert turn.final_result is not None

        try:
            result = InvestigationResult.model_validate(dict(turn.final_result))
        except ValidationError as error:
            problems = [
                {"field": ".".join(str(part) for part in item["loc"]), "problem": item["msg"]}
                for item in error.errors()
            ]
            logger.error(
                "Investigation result failed schema validation "
                "[investigation_id=%s problems=%s]",
                context.investigation_id,
                problems,
            )
            raise InvestigationFailed(
                f"Investigation {context.investigation_id} returned a malformed result"
            ) from error

        ungrounded = ledger.ungrounded(result.evidence)
        if ungrounded:
            cited = [reference.describe() for reference in ungrounded]
            logger.error(
                "Investigation result cited evidence that no tool returned "
                "[investigation_id=%s ungrounded=%s retrieved=%s]",
                context.investigation_id,
                cited,
                ledger.summary(),
            )
            raise UngroundedResultError(
                f"Investigation {context.investigation_id} cited evidence that was never "
                f"retrieved: {', '.join(cited)}"
            )

        logger.info(
            "Evidence grounding verified [investigation_id=%s citations=%d]",
            context.investigation_id,
            len(result.evidence),
        )
        return result
