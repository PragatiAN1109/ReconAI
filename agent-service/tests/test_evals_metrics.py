"""Tests for the metric calculations and report rendering.

Metrics are computed from hand-built outcomes rather than from real runs, so
each one is checked against a case whose answer is known by construction. A
metric that is only ever exercised on real data is a metric nobody has verified.

No network, no provider, no credentials.
"""

import json
from decimal import Decimal

import pytest

from app.evidence_ledger import EvidenceLedger
from app.guardrails import GuardrailDecision
from app.investigation_models import InvestigationResult, RootCauseClassification
from app.models import InvestigationStatus

from evals import metrics as M
from evals.report import build_report, render_markdown, write_reports
from evals.runner import RunConfig, ScenarioOutcome, ToolInvocation
from tests.test_evals_scenarios import scenario

T = RootCauseClassification


def result(classification=T.PROCESSOR_FEE, confidence=0.9, citations=()) -> InvestigationResult:
    return InvestigationResult.model_validate(
        {
            "classification": classification.value,
            "rootCause": "Because.",
            "confidence": confidence,
            "evidence": [
                {"sourceType": source, "reference": reference} for source, reference in citations
            ],
            "recommendedAction": "Review.",
            "requiresHumanApproval": True,
        }
    )


def ledger(settlements=(), fee_rules=()) -> EvidenceLedger:
    built = EvidenceLedger()
    built.settlements.update(settlements)
    built.fee_rules.update(fee_rules)
    return built


def outcome(
    *,
    scenario_kwargs=None,
    res=None,
    led=None,
    escalated=False,
    tools=(),
    latency=1.0,
    failure_kind=None,
    model_calls=2,
) -> ScenarioOutcome:
    decision = None
    if res is not None:
        decision = GuardrailDecision(
            InvestigationStatus.ESCALATED if escalated else InvestigationStatus.AWAITING_REVIEW,
            "because",
        )
    return ScenarioOutcome(
        scenario=scenario(**(scenario_kwargs or {})),
        result=res,
        ledger=led or EvidenceLedger(),
        decision=decision,
        tool_calls=tuple(tools),
        model_calls=model_calls,
        latency_seconds=latency,
        failure="failed" if failure_kind else None,
        failure_kind=failure_kind,
    )


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def test_an_exact_match_counts_as_strict_and_acceptable() -> None:
    m = M.classification_metrics([outcome(res=result(T.PROCESSOR_FEE))])

    assert m.strict_correct == 1
    assert m.acceptable_correct == 1
    assert m.strict_accuracy == 1.0


def test_a_declared_alternative_counts_as_acceptable_but_not_strict() -> None:
    """The distinction the report must never blur."""
    m = M.classification_metrics(
        [
            outcome(
                scenario_kwargs={
                    "expected_classification": T.PROCESSOR_FEE,
                    "acceptable_classifications": frozenset({T.PROCESSOR_ERROR}),
                },
                res=result(T.PROCESSOR_ERROR),
            )
        ]
    )

    assert m.strict_correct == 0
    assert m.acceptable_correct == 1
    assert m.strict_accuracy == 0.0
    assert m.acceptable_accuracy == 1.0


def test_an_undeclared_alternative_counts_as_neither() -> None:
    m = M.classification_metrics(
        [outcome(scenario_kwargs={"expected_classification": T.PROCESSOR_FEE},
                 res=result(T.DUPLICATE_PROCESSING))]
    )

    assert m.strict_correct == 0
    assert m.acceptable_correct == 0


def test_a_run_with_no_result_is_counted_and_surfaced() -> None:
    m = M.classification_metrics([outcome(res=None, failure_kind="InvestigationFailed")])

    assert m.no_result == 1
    assert m.strict_accuracy == 0.0


def test_accuracy_of_an_empty_run_is_zero_not_an_error() -> None:
    assert M.classification_metrics([]).strict_accuracy == 0.0


# ---------------------------------------------------------------------------
# Grounding
# ---------------------------------------------------------------------------


def test_grounded_citations_are_counted_against_the_ledger() -> None:
    m = M.grounding_metrics(
        [
            outcome(
                res=result(citations=(("SETTLEMENT", "SET-1"), ("FEE_RULE", "FR-1"))),
                led=ledger(settlements={"SET-1"}, fee_rules={"FR-1"}),
            )
        ]
    )

    assert m.total_citations == 2
    assert m.grounded_citations == 2
    assert m.citation_grounding_rate == 1.0
    assert m.unsupported_citation_rate == 0.0


def test_a_rejected_result_is_counted_as_ungrounded() -> None:
    """The validator rejects whole results, so that is where the signal is."""
    m = M.grounding_metrics([outcome(res=None, failure_kind="UngroundedResultError")])

    assert m.ungrounded_results == 1
    assert m.fully_grounded_results == 0
    assert m.result_grounding_rate == 0.0


def test_a_non_grounding_failure_is_not_counted_as_ungrounded() -> None:
    """A provider outage is not a fabrication and must not inflate the metric."""
    m = M.grounding_metrics([outcome(res=None, failure_kind="InvestigationModelError")])

    assert m.ungrounded_results == 0


def test_grounding_is_rechecked_rather_than_assumed() -> None:
    """A citation the ledger cannot vouch for is counted even if a result survived.

    Independent of the code that produced the result, on purpose: this metric
    exists to catch the validator being wrong, not to restate its output.
    """
    m = M.grounding_metrics(
        [outcome(res=result(citations=(("FEE_RULE", "FR-GHOST"),)), led=ledger())]
    )

    assert m.total_citations == 1
    assert m.grounded_citations == 0
    assert m.unsupported_citation_rate == 1.0


# ---------------------------------------------------------------------------
# Evidence coverage
# ---------------------------------------------------------------------------


def test_coverage_counts_expected_identifiers_that_were_cited() -> None:
    from app.investigation_models import EvidenceSource
    from evals.scenario import ExpectedEvidence

    expected = (
        ExpectedEvidence(source_type=EvidenceSource.SETTLEMENT, reference="SET-T1", why="x"),
    )
    m = M.evidence_coverage_metrics(
        [outcome(scenario_kwargs={"expected_evidence": expected},
                 res=result(citations=(("SETTLEMENT", "SET-T1"),)))]
    )

    assert m.expected_total == 1
    assert m.cited_total == 1
    assert m.coverage == 1.0


def test_coverage_ignores_scenarios_that_name_no_identifiers() -> None:
    """Categorical requirements are excluded rather than guessed at."""
    m = M.evidence_coverage_metrics([outcome(res=result())])

    assert m.scenarios_scored == 0
    assert m.coverage == 0.0


def test_coverage_is_separate_from_grounding() -> None:
    """A result can be fully grounded and still miss what mattered."""
    from app.investigation_models import EvidenceSource
    from evals.scenario import ExpectedEvidence

    expected = (
        ExpectedEvidence(source_type=EvidenceSource.SETTLEMENT, reference="SET-T1", why="x"),
    )
    only = outcome(
        scenario_kwargs={"expected_evidence": expected},
        res=result(citations=(("FEE_RULE", "FR-1"),)),
        led=ledger(fee_rules={"FR-1"}),
    )

    assert M.grounding_metrics([only]).citation_grounding_rate == 1.0
    assert M.evidence_coverage_metrics([only]).coverage == 0.0


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def test_required_tool_recall_counts_required_tools_actually_called() -> None:
    m = M.tool_metrics(
        [
            outcome(
                scenario_kwargs={
                    "required_tools": frozenset({"get_settlements", "get_fee_rules"})
                },
                tools=[ToolInvocation("get_settlements", ("transaction_id",), True)],
                res=result(),
            )
        ]
    )

    assert m.required_expected == 2
    assert m.required_called == 1
    assert m.required_tool_recall == 0.5


def test_optional_tools_are_never_penalised() -> None:
    m = M.tool_metrics(
        [
            outcome(
                scenario_kwargs={
                    "required_tools": frozenset({"get_settlements"}),
                    "optional_tools": frozenset({"search_policy_documents"}),
                },
                tools=[
                    ToolInvocation("get_settlements", (), True),
                    ToolInvocation("search_policy_documents", ("query",), True),
                ],
                res=result(),
            )
        ]
    )

    assert m.required_tool_recall == 1.0
    assert m.forbidden_call_count == 0


def test_forbidden_tool_calls_are_counted() -> None:
    m = M.tool_metrics(
        [
            outcome(
                scenario_kwargs={"forbidden_tools": frozenset({"get_fee_rules"})},
                tools=[
                    ToolInvocation("get_fee_rules", (), True),
                    ToolInvocation("get_fee_rules", (), True),
                ],
                res=result(),
            )
        ]
    )

    assert m.forbidden_call_count == 2
    assert m.scenarios_with_forbidden_calls == 1


def test_failed_tool_calls_are_counted_separately() -> None:
    m = M.tool_metrics(
        [outcome(tools=[ToolInvocation("get_settlements", (), False)], res=result())]
    )

    assert m.failed_tool_calls == 1
    assert m.total_tool_calls == 1


def test_recall_is_one_when_nothing_was_required() -> None:
    """Vacuously satisfied, not a division by zero."""
    assert M.tool_metrics([outcome(res=result())]).required_tool_recall == 1.0


# ---------------------------------------------------------------------------
# Escalation
# ---------------------------------------------------------------------------


def test_the_escalation_confusion_matrix_fills_every_cell() -> None:
    m = M.escalation_metrics(
        [
            outcome(scenario_kwargs={"should_escalate": True,
                                     "expected_classification": T.UNKNOWN},
                    res=result(), escalated=True),
            outcome(scenario_kwargs={"should_escalate": True,
                                     "expected_classification": T.UNKNOWN},
                    res=result(), escalated=False),
            outcome(scenario_kwargs={"should_escalate": False}, res=result(), escalated=True),
            outcome(scenario_kwargs={"should_escalate": False}, res=result(), escalated=False),
        ]
    )

    assert m.expected_escalate_actual_escalate == 1
    assert m.expected_escalate_actual_review == 1
    assert m.expected_review_actual_escalate == 1
    assert m.expected_review_actual_review == 1
    assert m.accuracy == 0.5


def test_the_unsafe_cell_is_reported_separately() -> None:
    """A case needing a human routed as a finding is the dangerous failure."""
    m = M.escalation_metrics(
        [
            outcome(scenario_kwargs={"should_escalate": True,
                                     "expected_classification": T.UNKNOWN},
                    res=result(), escalated=False)
        ]
    )

    assert m.unsafe_review_count == 1


def test_a_run_without_a_decision_is_excluded_not_scored_as_wrong() -> None:
    m = M.escalation_metrics(
        [outcome(res=None, failure_kind="InvestigationFailed"),
         outcome(scenario_kwargs={"should_escalate": False}, res=result(), escalated=False)]
    )

    assert m.no_decision == 1
    assert m.scored == 1
    assert m.accuracy == 1.0


def test_escalation_is_not_derived_from_classification() -> None:
    """A correct classification held at low confidence is still escalated."""
    m = M.escalation_metrics(
        [outcome(scenario_kwargs={"should_escalate": False},
                 res=result(T.PROCESSOR_FEE, confidence=0.2), escalated=True)]
    )

    assert m.expected_review_actual_escalate == 1
    assert m.accuracy == 0.0


# ---------------------------------------------------------------------------
# Latency
# ---------------------------------------------------------------------------


def test_latency_aggregates_are_computed() -> None:
    m = M.latency_metrics(
        [outcome(res=result(), latency=seconds) for seconds in (1.0, 2.0, 3.0, 4.0)], "offline"
    )

    assert m.count == 4
    assert m.mean_seconds == 2.5
    assert m.median_seconds == 2.5
    assert m.max_seconds == 4.0


def test_p95_uses_nearest_rank() -> None:
    """Always a value that actually occurred, never an interpolated fiction."""
    assert M.percentile([1.0, 2.0, 3.0, 4.0, 5.0], 0.95) == 5.0
    assert M.percentile(list(range(1, 101)), 0.95) == 95
    assert M.percentile([7.0], 0.95) == 7.0
    assert M.percentile([], 0.95) == 0.0


def test_latency_is_labelled_by_mode() -> None:
    """Offline and live latency must not be read as comparable."""
    assert "OFFLINE" in M.latency_metrics([outcome(res=result())], "offline").label
    assert "LIVE" in M.latency_metrics([outcome(res=result())], "live").label


# ---------------------------------------------------------------------------
# Tokens and cost
# ---------------------------------------------------------------------------


def test_offline_token_usage_is_not_observable() -> None:
    m = M.token_metrics([outcome(res=result())], "offline")

    assert m.observable is False
    assert m.input_tokens is None


def test_live_tokens_report_not_observable_rather_than_zero() -> None:
    """Zero would be a fabricated measurement; None is the truth."""
    m = M.token_metrics([outcome(res=result())], "live")

    assert m.observable is False
    assert m.input_tokens is None
    assert "NOT OBSERVABLE" in m.note


def test_cost_is_not_calculated_without_supplied_rates() -> None:
    tokens = M.TokenMetrics(observable=True, input_tokens=1000, output_tokens=500)

    assert M.cost_metrics(tokens, None, None).calculated is False
    assert M.cost_metrics(tokens, 3.0, None).calculated is False


def test_cost_is_not_calculated_without_observable_tokens() -> None:
    tokens = M.TokenMetrics(observable=False)

    cost = M.cost_metrics(tokens, 3.0, 15.0)
    assert cost.calculated is False
    assert "NOT CALCULATED" in cost.note


def test_cost_uses_only_supplied_rates() -> None:
    tokens = M.TokenMetrics(observable=True, input_tokens=1_000_000, output_tokens=1_000_000)

    cost = M.cost_metrics(tokens, 3.0, 15.0)
    assert cost.calculated is True
    assert cost.estimated_usd == pytest.approx(18.0)
    assert "USER-SUPPLIED RATES" in cost.note


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


def config(mode: str = "offline") -> RunConfig:
    from app.config import Settings

    return RunConfig(
        mode=mode,
        settings=Settings(_env_file=None),
        confidence_threshold=Decimal("0.85"),
        minimum_evidence=1,
    )


def test_the_json_report_serialises() -> None:
    outcomes = [outcome(res=result(citations=(("FEE_RULE", "FR-1"),)), led=ledger(fee_rules={"FR-1"}))]
    report = build_report(outcomes, M.summarise(outcomes, "offline"), config(), "run-1")

    encoded = json.dumps(report)
    assert json.loads(encoded)["run_id"] == "run-1"
    assert report["scenario_count"] == 1
    assert report["metrics"]["classification"]["strict_accuracy"] == 1.0


def test_the_markdown_report_renders_and_labels_offline_runs() -> None:
    outcomes = [outcome(res=result())]
    report = build_report(outcomes, M.summarise(outcomes, "offline"), config(), "run-2")

    markdown = render_markdown(report)
    assert "# ReconAI Evaluation" in markdown
    assert "not evidence of Claude" in markdown.replace("**", "")
    assert "OFFLINE HARNESS LATENCY" in markdown
    assert "NOT OBSERVABLE" in markdown


def test_the_markdown_report_labels_live_runs_differently() -> None:
    outcomes = [outcome(res=result())]
    report = build_report(outcomes, M.summarise(outcomes, "live"), config("live"), "run-3")

    markdown = render_markdown(report)
    assert "Live run against a real provider" in markdown
    assert "LIVE MODEL LATENCY" in markdown


def test_the_report_contains_no_single_composite_score() -> None:
    """Averaging these metrics would hide the trade-offs they exist to expose."""
    outcomes = [outcome(res=result())]
    report = build_report(outcomes, M.summarise(outcomes, "offline"), config(), "run-4")

    assert "overall_score" not in json.dumps(report)
    assert "composite" not in json.dumps(report)


def test_reports_are_written_to_disk(tmp_path) -> None:
    outcomes = [outcome(res=result())]
    report = build_report(outcomes, M.summarise(outcomes, "offline"), config(), "run-5")

    json_path, markdown_path = write_reports(report, tmp_path)

    assert json_path.exists() and markdown_path.exists()
    assert json.loads(json_path.read_text())["run_id"] == "run-5"
    assert markdown_path.read_text().startswith("# ReconAI Evaluation")
