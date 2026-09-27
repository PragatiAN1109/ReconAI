"""Running one scenario through the production investigation path.

The runner orchestrates; it does not reason. Every judgement in the pipeline is
made by the code that runs in production:

* ``InvestigationAgent``     — the bounded tool loop
* ``InvestigationTools``     — the allowlist and argument validation
* ``EvidenceLedger``         — what was actually retrieved
* the agent's own validator  — schema and grounding
* ``guardrails.evaluate``    — the routing decision

The only things the runner supplies are the evidence source (this scenario's
records) and the provider (scripted offline, real Anthropic live). Measuring a
reimplementation would measure the reimplementation.

Nothing here persists anything, touches Kafka, or writes to the Financial Core.
An evaluation is an observation.
"""

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from app.config import Settings
from app.evidence_ledger import EvidenceLedger
from app.guardrails import GuardrailDecision, evaluate
from app.investigation_agent import InvestigationAgent, InvestigationFailed
from app.investigation_model import (
    InvestigationModel,
    InvestigationModelError,
    ToolResults,
)
from app.investigation_models import InvestigationContext, InvestigationResult
from app.models import InvestigationStatus

from evals.evidence import financial_core_for, policy_search_for
from evals.offline_model import OfflineInvestigationModel
from evals.scenario import Scenario

logger = logging.getLogger(__name__)


@dataclass
class ToolInvocation:
    """One observed tool request. Names and argument keys only."""

    tool: str
    argument_keys: tuple[str, ...]
    succeeded: bool


@dataclass
class ScenarioOutcome:
    """Everything observable about one run.

    Deliberately excludes anything resembling model reasoning. What is recorded
    is what the system *did*: which tools it called, what came back, what it
    concluded, and how the deterministic layers responded.
    """

    scenario: Scenario
    #: None when the investigation failed before producing a result.
    result: InvestigationResult | None
    #: What the tools returned. **Empty when the run failed**: the agent builds
    #: the ledger internally and returns it only alongside a successful result,
    #: so a rejected investigation's retrieved evidence is not recoverable from
    #: here. ``ledger_available`` says which case this is, so an empty ledger is
    #: never misread as "nothing was retrieved".
    ledger: EvidenceLedger
    decision: GuardrailDecision | None
    tool_calls: tuple[ToolInvocation, ...]
    model_calls: int
    latency_seconds: float
    #: Set when the run failed. Grounding rejections land here too.
    failure: str | None = None
    failure_kind: str | None = None
    #: False when the run failed and the ledger could not be recovered.
    ledger_available: bool = True
    #: Provider usage, when the adapter exposes it. See the framework README:
    #: the Anthropic adapter does not currently surface `response.usage`, so
    #: this stays None on live runs rather than being reported as zero.
    input_tokens: int | None = None
    output_tokens: int | None = None

    @property
    def classification(self) -> str | None:
        return self.result.classification.value if self.result else None

    @property
    def escalated(self) -> bool | None:
        """Whether the real guardrail escalated. None if there was no result."""
        if self.decision is None:
            return None
        return self.decision.status is InvestigationStatus.ESCALATED

    @property
    def grounded(self) -> bool:
        """True when a result survived the production grounding validator.

        A failed investigation is not grounded: the validator is precisely what
        rejected it.
        """
        return self.result is not None

    @property
    def citations(self) -> list[str]:
        return self.result.cites() if self.result else []


@dataclass
class RunConfig:
    """How a set of scenarios should be run."""

    mode: str  # "offline" | "live"
    settings: Settings
    #: Live only. Offline builds its own provider per scenario.
    live_model: InvestigationModel | None = None
    max_tool_rounds: int = 8
    confidence_threshold: Decimal = field(default_factory=lambda: Decimal("0.85"))
    minimum_evidence: int = 1

    @property
    def provider(self) -> str:
        return self.settings.llm_provider if self.mode == "live" else "offline-deterministic"

    @property
    def model_name(self) -> str:
        return self.settings.llm_model if self.mode == "live" else "scripted"


async def run_scenario(scenario: Scenario, config: RunConfig) -> ScenarioOutcome:
    """Run one scenario and observe what happened."""
    # Deliberately NOT core.open(): that method builds a real connection pool
    # and would replace the scenario transport, sending evaluation traffic to
    # whatever is listening on the configured Financial Core port. The client
    # returned here is already open against this scenario's evidence.
    core = financial_core_for(scenario, config.settings)

    if config.mode == "live":
        if config.live_model is None:
            raise RuntimeError("live mode requires a configured provider")
        model: InvestigationModel = config.live_model
    else:
        model = OfflineInvestigationModel(scenario)

    observed: list[ToolInvocation] = []
    observer = _ObservingModel(model, observed)
    agent = InvestigationAgent(
        observer,
        core,
        policy_search_for(config.settings),
        max_tool_rounds=config.max_tool_rounds,
    )

    context = InvestigationContext(
        investigationId=f"EVAL-{scenario.scenario_id}",
        exceptionId=f"EVAL-EX-{scenario.scenario_id}",
        transactionId=scenario.transaction["transactionId"],
        exceptionType=scenario.exception_type,
    )

    started = time.perf_counter()
    result: InvestigationResult | None = None
    ledger = EvidenceLedger()
    failure: str | None = None
    failure_kind: str | None = None

    try:
        result, ledger = await agent.investigate(context)
    except InvestigationFailed as error:
        # Includes ungrounded citations. Recorded as an outcome, not raised:
        # a model that fabricates a reference is a measurement, and a run that
        # aborted the whole suite would measure nothing.
        failure, failure_kind = str(error), type(error).__name__
    except InvestigationModelError as error:
        failure, failure_kind = str(error), type(error).__name__
    finally:
        latency = time.perf_counter() - started
        await core.close()

    decision = (
        evaluate(
            result,
            confidence_threshold=config.confidence_threshold,
            minimum_evidence=config.minimum_evidence,
        )
        if result is not None
        else None
    )

    return ScenarioOutcome(
        scenario=scenario,
        result=result,
        ledger=ledger,
        decision=decision,
        ledger_available=result is not None,
        tool_calls=tuple(observed),
        # Counted at the boundary, so it is exact for any provider rather
        # than inferred from tool counts.
        model_calls=observer.calls,
        latency_seconds=latency,
        failure=failure,
        failure_kind=failure_kind,
    )


async def run_scenarios(
    scenarios: Sequence[Scenario], config: RunConfig
) -> list[ScenarioOutcome]:
    """Run scenarios one at a time.

    Sequential on purpose. Live mode talks to a rate-limited, billed API, and
    concurrency there buys a little wall-clock in exchange for unpredictable
    spend and 429s. Offline is fast enough that it does not matter.
    """
    outcomes: list[ScenarioOutcome] = []
    for index, scenario in enumerate(scenarios, start=1):
        logger.info(
            "Evaluating [%d/%d] %s (%s)",
            index,
            len(scenarios),
            scenario.scenario_id,
            scenario.difficulty.value,
        )
        outcomes.append(await run_scenario(scenario, config))
    return outcomes


class _ObservingModel:
    """Wraps a provider to record which tools it asked for and how they fared.

    A wrapper rather than log parsing: the tool sequence is a measured output,
    and deriving it from log text would make the metric depend on log format.

    Outcomes are backfilled from the transcript. The agent executes tools and
    feeds the results back on the following turn, so by the time this is called
    again the previous round's successes and failures are visible as real
    ``ToolResult`` entries — the agent's own record, not a guess.
    """

    def __init__(self, inner: InvestigationModel, sink: list[ToolInvocation]) -> None:
        self._inner = inner
        self._sink = sink
        self.calls = 0

    async def next_turn(self, context, transcript, system_prompt, tools):
        self._record_outcomes(transcript)
        self.calls += 1
        turn = await self._inner.next_turn(context, transcript, system_prompt, tools)
        for call in turn.tool_calls:
            self._sink.append(
                ToolInvocation(
                    tool=call.tool,
                    argument_keys=tuple(sorted(call.arguments)),
                    # Provisional; corrected from the transcript next turn, and
                    # by _finalise() for the last round.
                    succeeded=True,
                )
            )
        return turn

    def _record_outcomes(self, transcript) -> None:
        """Mark the most recent round's requests with what actually happened."""
        for entry in reversed(transcript):
            if isinstance(entry, ToolResults):
                failures = {r.tool for r in entry.results if r.failed}
                for invocation in reversed(self._sink[-len(entry.results):]):
                    if invocation.tool in failures:
                        invocation.succeeded = False
                return
