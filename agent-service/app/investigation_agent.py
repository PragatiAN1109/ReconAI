"""The investigation workflow: a bounded loop, then grounding validation.

One investigator, not a team of them. It asks for evidence through the
controlled tools, reasons over what comes back, and proposes an explanation
that the application then checks against what was actually retrieved.

The loop is bounded. An agent that can call tools indefinitely is an agent that
can spend indefinitely, and a model that cannot reach a conclusion should fail
visibly rather than circle.
"""

import json
import logging
from collections.abc import Callable

from pydantic import ValidationError

from app.evidence_ledger import EvidenceLedger
from app.financial_core_client import FinancialCoreClient
from app.investigation_model import (
    SUBMIT_RESULT_TOOL,
    SYSTEM_PROMPT,
    AssistantTurn,
    InvestigationModel,
    ToolResult,
    ToolResults,
    TranscriptEntry,
)
from app.investigation_models import (
    InvestigationContext,
    InvestigationResult,
    describe_payload,
)
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


class ResultSchemaInvalid(Exception):
    """A submitted result does not satisfy the structured contract.

    Internal to this module and never raised out of it. It carries the field
    problems so they can be handed back to the model once, and is deliberately
    *not* an ``InvestigationFailed``: whether a schema failure ends the
    investigation depends on whether a correction has already been tried.
    """

    def __init__(self, problems: list[dict[str, str]]) -> None:
        super().__init__(f"{len(problems)} field problem(s)")
        self.problems = problems


def _repair_feedback(problems: list[dict[str, str]]) -> str:
    """The message handed back after a rejected submission.

    Structural facts only: which fields were wrong and why, plus a statement
    that this is the one correction available. It deliberately carries no part
    of the submitted payload, no prompt, no narrative and no evidence content —
    the model already holds its own conversation, and echoing financial detail
    back through a repair path would widen what a single malformed result can
    expose.
    """
    return json.dumps(
        {
            "accepted": False,
            "validationErrors": problems,
            "instruction": (
                "Your result was rejected before it was stored. Submit a complete "
                "corrected investigation result with every required field present. "
                "Cite only evidence the tools returned in this conversation. This is "
                "the only correction available; a second rejection ends the "
                "investigation with no recommendation."
            ),
        }
    )


class InvestigationAgent:
    """Runs one investigation to a validated, grounded result."""

    def __init__(
        self,
        model: InvestigationModel,
        financial_core: FinancialCoreClient,
        policies: PolicySearch,
        max_tool_rounds: int = DEFAULT_MAX_TOOL_ROUNDS,
        repair_permit: Callable[[], bool] | None = None,
    ) -> None:
        self._model = model
        self._financial_core = financial_core
        self._policies = policies
        self._max_tool_rounds = max_tool_rounds
        # Asked before the one correction call, which is another paid provider
        # request and must be metered like any other. A plain callable rather
        # than the limiter itself: the agent needs to know whether it may spend,
        # not how spending is counted. Defaults to permitting, so a caller with
        # no budget to enforce — every unit test — behaves unchanged.
        self._repair_permit = repair_permit or (lambda: True)

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

        #: At most one correction per investigation, for the whole investigation
        #: rather than per round. This flag is the entire enforcement: once set,
        #: the next schema failure is terminal, so there is no path to a
        #: correction loop however the model behaves.
        repair_used = False

        for round_number in range(1, self._max_tool_rounds + 1):
            turn = await self._model.next_turn(
                context, transcript, SYSTEM_PROMPT, TOOL_SPECIFICATIONS
            )
            transcript.append(turn)

            if turn.is_final:
                try:
                    result = self._parse(context, turn)
                except ResultSchemaInvalid as invalid:
                    # A malformed submission. The payload is never patched here:
                    # the model submits a complete replacement or the
                    # investigation fails. See _repair_feedback.
                    feedback = self._offer_repair(
                        context, turn, invalid, repair_used=repair_used
                    )
                    repair_used = True
                    transcript.append(feedback)
                    continue

                self._check_grounding(context, result, ledger)
                logger.info(
                    "Investigation completed [investigation_id=%s classification=%s "
                    "confidence=%.2f citations=%s rounds=%d repaired=%s]",
                    context.investigation_id,
                    result.classification.value,
                    result.confidence,
                    result.cites(),
                    round_number,
                    repair_used,
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

    def _offer_repair(
        self,
        context: InvestigationContext,
        turn: AssistantTurn,
        invalid: ResultSchemaInvalid,
        *,
        repair_used: bool,
    ) -> ToolResults:
        """Decide whether a rejected submission gets a correction, and build it.

        Three ways this ends as a terminal failure instead:

        - a correction has already been used, so the budget of one is spent;
        - the provider gave the submission no identifier, so a correction cannot
          be addressed to it at all;
        - the shared AI budget has no room for another provider call.

        The last is the interesting one. A correction is another paid call, so
        it is metered like any other. When it is refused the investigation still
        **fails** rather than reverting to PENDING: the run happened, tools were
        called, money was spent and the model produced a conclusion — an
        unusable one. Recording that as "not yet run" would be false about all
        of it. The message says why, so the trail distinguishes "we did not try
        to correct it" from "the correction also failed".
        """
        if repair_used:
            logger.error(
                "Rejecting a second malformed result; the one correction was already used "
                "[investigation_id=%s problems=%s]",
                context.investigation_id,
                invalid.problems,
            )
            raise InvestigationFailed(
                f"Investigation {context.investigation_id} returned a malformed result "
                f"again after its single correction attempt"
            ) from invalid

        if not turn.is_correctable:
            logger.error(
                "Cannot request a correction: the submission carried no identifier "
                "[investigation_id=%s]",
                context.investigation_id,
            )
            raise InvestigationFailed(
                f"Investigation {context.investigation_id} returned a malformed result"
            ) from invalid

        if not self._repair_permit():
            logger.error(
                "Not requesting a correction: the AI budget for this window is exhausted "
                "[investigation_id=%s problems=%s]",
                context.investigation_id,
                invalid.problems,
            )
            raise InvestigationFailed(
                f"Investigation {context.investigation_id} returned a malformed result and "
                f"the single correction attempt could not be made because the AI budget "
                f"for this window is exhausted"
            ) from invalid

        logger.warning(
            "Requesting one corrected result [investigation_id=%s problems=%s]",
            context.investigation_id,
            invalid.problems,
        )
        return ToolResults(
            results=(
                ToolResult(
                    call_id=turn.final_call_id or "",
                    tool=SUBMIT_RESULT_TOOL,
                    content=_repair_feedback(invalid.problems),
                    # Marked as an error so the provider presents it as a
                    # rejection rather than as data the model may have asked for.
                    failed=True,
                ),
            )
        )

    def _parse(
        self, context: InvestigationContext, turn: AssistantTurn
    ) -> InvestigationResult:
        """Validate a submitted result against the schema, and nothing more.

        Separate from grounding so a schema failure can be answered with
        feedback while a fabricated citation stays terminal. Those are different
        kinds of wrong: one is a malformed message, the other is a claim about
        evidence nobody retrieved.

        :raises ResultSchemaInvalid: the payload does not satisfy the contract
        """
        assert turn.final_result is not None

        try:
            return InvestigationResult.model_validate(dict(turn.final_result))
        except ValidationError as error:
            problems = [
                {"field": ".".join(str(part) for part in item["loc"]), "problem": item["msg"]}
                for item in error.errors()
            ]
            # The field names and reasons, plus the two scalar values that
            # identify which conclusion was rejected. Never the payload itself:
            # rootCause and the evidence list carry financial detail, and this
            # line has to stay safe to read in an aggregated log.
            logger.error(
                "Investigation result failed schema validation "
                "[investigation_id=%s problems=%s submitted=%s]",
                context.investigation_id,
                problems,
                describe_payload(turn.final_result),
            )
            raise ResultSchemaInvalid(problems) from error

    def _check_grounding(
        self, context: InvestigationContext, result: InvestigationResult, ledger: EvidenceLedger
    ) -> InvestigationResult:
        """Check every citation against what the tools actually returned.

        Terminal, and deliberately not repairable. A schema failure is a badly
        formed message; a citation to evidence no tool returned is a claim about
        the world that was not true. Offering to correct the second would invite
        a second guess at which identifier sounds plausible.
        """
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
