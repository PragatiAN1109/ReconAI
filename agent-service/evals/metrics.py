"""Turning observed outcomes into numbers that mean something.

Two principles shape this module.

**No single score.** There is no "ReconAI score". Classification accuracy,
grounding, evidence coverage, tool selection and escalation accuracy measure
different properties that trade off against each other, and averaging them into
one number would hide exactly the trade-offs worth seeing. A system that
escalates everything scores perfectly on safety and uselessly on accuracy; one
number cannot say that.

**Nothing is inferred that was not observed.** Where the architecture cannot
measure something — arbitrary prose claims, provider token usage — the metric
reports that it is not observable rather than substituting zero. A zero is a
measurement; "not observable" is the truth.
"""

import statistics
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field

from app.investigation_models import RootCauseClassification

from evals.runner import ScenarioOutcome


@dataclass(frozen=True)
class ClassificationMetrics:
    """How often the investigator named the right root cause.

    ``strict`` counts only exact matches. ``acceptable`` additionally counts
    classifications a scenario explicitly declared defensible. They are reported
    separately and never merged: an alternative being defensible is a judgement
    the dataset author made, and folding it into "accuracy" would quietly
    inflate the headline number.
    """

    total: int
    strict_correct: int
    acceptable_correct: int
    #: Runs that produced no result at all (grounding rejection, malformed
    #: result, provider failure). Counted as incorrect, and surfaced.
    no_result: int
    by_expected: dict[str, dict[str, int]] = field(default_factory=dict)

    @property
    def strict_accuracy(self) -> float:
        return self.strict_correct / self.total if self.total else 0.0

    @property
    def acceptable_accuracy(self) -> float:
        return self.acceptable_correct / self.total if self.total else 0.0


@dataclass(frozen=True)
class GroundingMetrics:
    """Whether citations were backed by evidence actually retrieved.

    The production validator is authoritative and is not re-implemented here: a
    run that reached a result has already passed it. What this adds is the
    aggregate — how many results survived, and how many citations they carried.

    ``ungrounded_results`` counts runs the validator rejected. On a live run
    that number is the fabrication rate, and it is the single most important
    number this framework produces.
    """

    results_produced: int
    fully_grounded_results: int
    ungrounded_results: int
    total_citations: int
    grounded_citations: int

    @property
    def citation_grounding_rate(self) -> float:
        return self.grounded_citations / self.total_citations if self.total_citations else 0.0

    @property
    def result_grounding_rate(self) -> float:
        attempted = self.fully_grounded_results + self.ungrounded_results
        return self.fully_grounded_results / attempted if attempted else 0.0

    @property
    def unsupported_citation_rate(self) -> float:
        """Citations the ledger could not vouch for, over all citations.

        Necessarily 0.0 among *surviving* results, because the validator
        rejects any result containing one. The signal lives in
        ``ungrounded_results``: rejection is all-or-nothing by design, so a
        fabricated reference costs the whole result rather than one citation.
        """
        return 1.0 - self.citation_grounding_rate


@dataclass(frozen=True)
class EvidenceCoverageMetrics:
    """Whether the conclusion rested on the evidence that mattered.

    Distinct from grounding, and the distinction is the point: a result can cite
    only real records and still miss the one that explains the case. Grounding
    asks "is this true?"; coverage asks "is this enough?".

    Only scenarios that name specific identifiers are scored. Categorical
    requirements ("some policy document") are excluded rather than guessed at.
    """

    scenarios_scored: int
    expected_total: int
    cited_total: int

    @property
    def coverage(self) -> float:
        return self.cited_total / self.expected_total if self.expected_total else 0.0


@dataclass(frozen=True)
class ToolMetrics:
    """Which tools were used against what the scenario expected.

    Optional tools are never penalised. Gathering evidence that turns out not to
    matter is normal investigative behaviour, and a metric that discouraged it
    would train the wrong habit.
    """

    required_expected: int
    required_called: int
    forbidden_call_count: int
    scenarios_with_forbidden_calls: int
    failed_tool_calls: int
    total_tool_calls: int
    calls_by_tool: dict[str, int] = field(default_factory=dict)

    @property
    def required_tool_recall(self) -> float:
        return self.required_called / self.required_expected if self.required_expected else 1.0


@dataclass(frozen=True)
class EscalationMetrics:
    """Whether the deterministic guardrail routed each case as it should.

    Computed from the **real** ``guardrails.evaluate`` outcome, never from the
    classification. Those are different questions: a correct classification held
    at low confidence is still correctly escalated.

    ``expected_escalate_actual_review`` is the dangerous cell — a case that
    needed a human was routed as a confident finding. It deserves more weight
    than the raw accuracy figure gives it.
    """

    total: int
    expected_escalate_actual_escalate: int
    expected_escalate_actual_review: int
    expected_review_actual_escalate: int
    expected_review_actual_review: int
    #: No result, so the guardrail never ran. Excluded from accuracy and
    #: reported, rather than silently scored as a miss.
    no_decision: int

    @property
    def scored(self) -> int:
        return self.total - self.no_decision

    @property
    def accuracy(self) -> float:
        correct = self.expected_escalate_actual_escalate + self.expected_review_actual_review
        return correct / self.scored if self.scored else 0.0

    @property
    def unsafe_review_count(self) -> int:
        """Cases needing a human that were routed as findings."""
        return self.expected_escalate_actual_review


@dataclass(frozen=True)
class LatencyMetrics:
    """Wall-clock per scenario.

    ``label`` distinguishes offline harness timing from live model timing. They
    differ by orders of magnitude and mean entirely different things; presenting
    them under one heading would invite exactly the wrong comparison.
    """

    label: str
    count: int
    mean_seconds: float
    median_seconds: float
    p95_seconds: float
    max_seconds: float


@dataclass(frozen=True)
class TokenMetrics:
    """Provider usage, when the adapter exposes it.

    ``observable`` is false whenever the provider abstraction does not surface
    usage. In that case the counts are None, not zero: reporting zero tokens for
    a run that certainly consumed some would be a fabricated metric.
    """

    observable: bool
    input_tokens: int | None = None
    output_tokens: int | None = None
    provider_calls: int = 0
    note: str = ""


@dataclass(frozen=True)
class CostMetrics:
    """Estimated spend, only when rates were supplied explicitly.

    No pricing is hardcoded. Provider prices change, differ by model and tier,
    and a guessed rate baked into a repository becomes a confidently wrong
    number that outlives whoever guessed it.
    """

    calculated: bool
    estimated_usd: float | None = None
    input_cost_per_million: float | None = None
    output_cost_per_million: float | None = None
    note: str = ""


def percentile(values: Sequence[float], fraction: float) -> float:
    """Nearest-rank percentile.

    Chosen over interpolation because it always returns a value that actually
    occurred. With the handful of samples an eval run produces, an interpolated
    p95 reports a latency nothing experienced.
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, min(len(ordered), int(-(-fraction * len(ordered) // 1))))
    return ordered[rank - 1]


def classification_metrics(outcomes: Sequence[ScenarioOutcome]) -> ClassificationMetrics:
    strict = acceptable = no_result = 0
    by_expected: dict[str, dict[str, int]] = {}

    for outcome in outcomes:
        expected = outcome.scenario.expected_classification
        bucket = by_expected.setdefault(expected.value, {})
        actual = outcome.classification or "NO_RESULT"
        bucket[actual] = bucket.get(actual, 0) + 1

        if outcome.result is None:
            no_result += 1
            continue
        if outcome.result.classification == expected:
            strict += 1
            acceptable += 1
        elif outcome.scenario.is_acceptable(outcome.result.classification):
            acceptable += 1

    return ClassificationMetrics(
        total=len(outcomes),
        strict_correct=strict,
        acceptable_correct=acceptable,
        no_result=no_result,
        by_expected=by_expected,
    )


def grounding_metrics(outcomes: Sequence[ScenarioOutcome]) -> GroundingMetrics:
    produced = fully = ungrounded = total_citations = grounded_citations = 0

    for outcome in outcomes:
        if outcome.result is None:
            # UngroundedResultError is the validator rejecting a fabrication.
            # Other failures are not grounding failures and are not counted here.
            if outcome.failure_kind == "UngroundedResultError":
                ungrounded += 1
            continue

        produced += 1
        fully += 1
        citations = outcome.result.evidence
        total_citations += len(citations)
        # Re-checked against the ledger rather than assumed: the assertion that
        # a surviving result is fully grounded is worth verifying independently
        # of the code that produced it.
        grounded_citations += sum(1 for c in citations if outcome.ledger.supports(c))

    return GroundingMetrics(
        results_produced=produced,
        fully_grounded_results=fully,
        ungrounded_results=ungrounded,
        total_citations=total_citations,
        grounded_citations=grounded_citations,
    )


def evidence_coverage_metrics(
    outcomes: Sequence[ScenarioOutcome],
) -> EvidenceCoverageMetrics:
    scored = expected_total = cited_total = 0

    for outcome in outcomes:
        expected = outcome.scenario.expected_identifiers()
        if not expected:
            continue
        scored += 1
        expected_total += len(expected)
        cited = {reference.reference for reference in (outcome.result.evidence if outcome.result else [])}
        cited_total += sum(1 for identifier in expected if identifier in cited)

    return EvidenceCoverageMetrics(
        scenarios_scored=scored, expected_total=expected_total, cited_total=cited_total
    )


def tool_metrics(outcomes: Sequence[ScenarioOutcome]) -> ToolMetrics:
    required_expected = required_called = 0
    forbidden_calls = scenarios_with_forbidden = failed = total = 0
    by_tool: dict[str, int] = {}

    for outcome in outcomes:
        used = {call.tool for call in outcome.tool_calls}
        required_expected += len(outcome.scenario.required_tools)
        required_called += len(outcome.scenario.required_tools & used)

        forbidden_here = sum(
            1 for call in outcome.tool_calls if call.tool in outcome.scenario.forbidden_tools
        )
        forbidden_calls += forbidden_here
        if forbidden_here:
            scenarios_with_forbidden += 1

        for call in outcome.tool_calls:
            total += 1
            by_tool[call.tool] = by_tool.get(call.tool, 0) + 1
            if not call.succeeded:
                failed += 1

    return ToolMetrics(
        required_expected=required_expected,
        required_called=required_called,
        forbidden_call_count=forbidden_calls,
        scenarios_with_forbidden_calls=scenarios_with_forbidden,
        failed_tool_calls=failed,
        total_tool_calls=total,
        calls_by_tool=dict(sorted(by_tool.items())),
    )


def escalation_metrics(outcomes: Sequence[ScenarioOutcome]) -> EscalationMetrics:
    ee = er = re_ = rr = none = 0

    for outcome in outcomes:
        escalated = outcome.escalated
        if escalated is None:
            none += 1
            continue
        if outcome.scenario.should_escalate:
            if escalated:
                ee += 1
            else:
                er += 1
        elif escalated:
            re_ += 1
        else:
            rr += 1

    return EscalationMetrics(
        total=len(outcomes),
        expected_escalate_actual_escalate=ee,
        expected_escalate_actual_review=er,
        expected_review_actual_escalate=re_,
        expected_review_actual_review=rr,
        no_decision=none,
    )


def latency_metrics(outcomes: Sequence[ScenarioOutcome], mode: str) -> LatencyMetrics:
    values = [outcome.latency_seconds for outcome in outcomes]
    label = "LIVE MODEL LATENCY" if mode == "live" else "OFFLINE HARNESS LATENCY"
    if not values:
        return LatencyMetrics(label, 0, 0.0, 0.0, 0.0, 0.0)
    return LatencyMetrics(
        label=label,
        count=len(values),
        mean_seconds=statistics.fmean(values),
        median_seconds=statistics.median(values),
        p95_seconds=percentile(values, 0.95),
        max_seconds=max(values),
    )


def token_metrics(outcomes: Sequence[ScenarioOutcome], mode: str) -> TokenMetrics:
    calls = sum(outcome.model_calls for outcome in outcomes)
    reported = [o for o in outcomes if o.input_tokens is not None or o.output_tokens is not None]

    if mode != "live":
        return TokenMetrics(
            observable=False,
            provider_calls=calls,
            note="Offline runs make no provider calls; token usage does not apply.",
        )

    if not reported:
        return TokenMetrics(
            observable=False,
            provider_calls=calls,
            note=(
                "NOT OBSERVABLE. The Anthropic adapter does not currently surface "
                "response.usage through the provider abstraction. See evals/README.md "
                "for the proposed backward-compatible addition."
            ),
        )

    return TokenMetrics(
        observable=True,
        input_tokens=sum(o.input_tokens or 0 for o in reported),
        output_tokens=sum(o.output_tokens or 0 for o in reported),
        provider_calls=calls,
    )


def cost_metrics(
    tokens: TokenMetrics,
    input_cost_per_million: float | None,
    output_cost_per_million: float | None,
) -> CostMetrics:
    if input_cost_per_million is None or output_cost_per_million is None:
        return CostMetrics(
            calculated=False,
            note="NOT CALCULATED. No pricing supplied; rates are never assumed.",
        )
    if not tokens.observable:
        return CostMetrics(
            calculated=False,
            input_cost_per_million=input_cost_per_million,
            output_cost_per_million=output_cost_per_million,
            note="NOT CALCULATED. Token usage is not observable, so cost cannot be derived.",
        )

    estimated = (tokens.input_tokens or 0) / 1_000_000 * input_cost_per_million + (
        tokens.output_tokens or 0
    ) / 1_000_000 * output_cost_per_million
    return CostMetrics(
        calculated=True,
        estimated_usd=round(estimated, 6),
        input_cost_per_million=input_cost_per_million,
        output_cost_per_million=output_cost_per_million,
        note="ESTIMATED COST USING USER-SUPPLIED RATES.",
    )


@dataclass(frozen=True)
class Summary:
    """Every metric for one run. Deliberately not reduced to a score."""

    classification: ClassificationMetrics
    grounding: GroundingMetrics
    coverage: EvidenceCoverageMetrics
    tools: ToolMetrics
    escalation: EscalationMetrics
    latency: LatencyMetrics
    tokens: TokenMetrics
    cost: CostMetrics

    def as_dict(self) -> dict:
        return {
            "classification": asdict(self.classification)
            | {
                "strict_accuracy": self.classification.strict_accuracy,
                "acceptable_accuracy": self.classification.acceptable_accuracy,
            },
            "grounding": asdict(self.grounding)
            | {
                "citation_grounding_rate": self.grounding.citation_grounding_rate,
                "result_grounding_rate": self.grounding.result_grounding_rate,
                "unsupported_citation_rate": self.grounding.unsupported_citation_rate,
            },
            "evidence_coverage": asdict(self.coverage) | {"coverage": self.coverage.coverage},
            "tools": asdict(self.tools)
            | {"required_tool_recall": self.tools.required_tool_recall},
            "escalation": asdict(self.escalation)
            | {
                "accuracy": self.escalation.accuracy,
                "scored": self.escalation.scored,
                "unsafe_review_count": self.escalation.unsafe_review_count,
            },
            "latency": asdict(self.latency),
            "tokens": asdict(self.tokens),
            "cost": asdict(self.cost),
        }


def summarise(
    outcomes: Sequence[ScenarioOutcome],
    mode: str,
    input_cost_per_million: float | None = None,
    output_cost_per_million: float | None = None,
) -> Summary:
    tokens = token_metrics(outcomes, mode)
    return Summary(
        classification=classification_metrics(outcomes),
        grounding=grounding_metrics(outcomes),
        coverage=evidence_coverage_metrics(outcomes),
        tools=tool_metrics(outcomes),
        escalation=escalation_metrics(outcomes),
        latency=latency_metrics(outcomes, mode),
        tokens=tokens,
        cost=cost_metrics(tokens, input_cost_per_million, output_cost_per_million),
    )
