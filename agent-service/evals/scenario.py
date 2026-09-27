"""What a single evaluation case asserts.

A scenario is **domain truth**, not a model fixture. It describes a financial
situation and what a competent investigator should conclude from it, expressed
only in terms this repository already owns: the deterministic exception types,
the root-cause taxonomy, the four controlled tools, and the evidence sources.

Nothing here mentions a provider. The same scenario drives the deterministic
offline harness and a live Anthropic run, and neither can influence what the
scenario says is correct.

The financial payloads are held in the Financial Core's own **API JSON shape**
rather than as loose fields. Validation parses them with the production evidence
models, so a malformed amount or currency in the dataset fails here rather than
surfacing as a confusing tool error mid-run.
"""

from collections.abc import Sequence
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.evidence_models import FeeRuleEvidence, SettlementEvidence, TransactionEvidence
from app.events import ExceptionType
from app.investigation_models import EvidenceSource, RootCauseClassification
from app.investigation_tools import ALLOWED_TOOLS

_STRICT = ConfigDict(extra="forbid", frozen=True)

#: Classifications that are an admission rather than a conclusion. The
#: production guardrail escalates both regardless of confidence, so a scenario
#: expecting one of these must also expect escalation.
NON_CONCLUSIVE = frozenset(
    {RootCauseClassification.INSUFFICIENT_EVIDENCE, RootCauseClassification.UNKNOWN}
)


class Difficulty(StrEnum):
    """How hard the case is, so results can be read by band rather than in bulk.

    An aggregate score hides the thing worth knowing. A system that is perfect
    on CLEAR cases and wrong on ADVERSARIAL ones is a specific, actionable
    finding; "82% accurate" is not.
    """

    #: One explanation fits and nothing contradicts it.
    CLEAR = "CLEAR"
    #: Requires combining several pieces of evidence correctly.
    MODERATE = "MODERATE"
    #: The evidence genuinely underdetermines the answer.
    AMBIGUOUS = "AMBIGUOUS"
    #: A superficial pattern match gives the *wrong* answer. These are the
    #: cases that distinguish reasoning from pattern completion.
    ADVERSARIAL = "ADVERSARIAL"


class ExpectedEvidence(BaseModel):
    """One thing the conclusion ought to rest on.

    ``reference`` is an identifier when a specific record is the point
    (``FR-14`` is *the* rule that explains the difference). It is ``None`` when
    the requirement is categorical — "some policy document" — because demanding
    an exact section would measure phrasing rather than reasoning.
    """

    model_config = _STRICT

    source_type: EvidenceSource
    reference: str | None = None
    #: Why this matters, for the report. A requirement nobody can justify is a
    #: requirement that should not be scored.
    why: str = Field(min_length=1)


class OfflineScript(BaseModel):
    """What the deterministic offline provider does for this scenario.

    **This is harness input, not ground truth.** It exists so the offline mode
    can exercise the real agent, the real grounding validator and the real
    guardrail without a network — and so the metrics themselves can be tested
    against known-wrong behaviour.

    Deliberately allowed to diverge from the scenario's expectations. A dataset
    whose scripted provider always answers correctly would report 100% accuracy
    and prove only that the script was copied from the answer key. Several
    scenarios script a wrong classification, a missing tool, or an unsupported
    citation precisely so the metrics demonstrably discriminate.
    """

    model_config = _STRICT

    #: Tools to request, in order, one per round. Names are validated against
    #: the production allowlist.
    tool_calls: tuple[str, ...] = ()
    classification: RootCauseClassification
    confidence: float = Field(ge=0.0, le=1.0)
    #: Citations the scripted provider emits, as ``(source_type, reference,
    #: section)``. A reference no tool returned will be rejected by the real
    #: grounding validator — which is the point for the scenarios that do it.
    citations: tuple[tuple[str, str, str | None], ...] = ()
    root_cause: str = "Scripted offline behaviour for harness evaluation."
    recommended_action: str = "Scripted offline behaviour for harness evaluation."

    @model_validator(mode="after")
    def _tools_must_be_real(self) -> "OfflineScript":
        unknown = set(self.tool_calls) - ALLOWED_TOOLS
        if unknown:
            raise ValueError(f"offline script requests unknown tools: {sorted(unknown)}")
        return self


class Scenario(BaseModel):
    """One evaluation case."""

    model_config = _STRICT

    scenario_id: str = Field(min_length=1, pattern=r"^[a-z0-9-]+$")
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)

    #: What deterministic reconciliation detected. The investigator is told this
    #: and must not treat it as the cause.
    exception_type: ExceptionType

    #: Financial Core API payloads, served to the real client over a mock
    #: transport so the production parsing path runs unchanged.
    transaction: dict[str, Any]
    settlements: list[dict[str, Any]] = Field(default_factory=list)
    fee_rules: list[dict[str, Any]] = Field(default_factory=list)

    expected_classification: RootCauseClassification
    #: Other classifications a competent analyst could defend on this evidence.
    #: Reported separately from strict accuracy, never folded into it.
    acceptable_classifications: frozenset[RootCauseClassification] = frozenset()

    expected_evidence: tuple[ExpectedEvidence, ...] = ()

    required_tools: frozenset[str] = frozenset()
    optional_tools: frozenset[str] = frozenset()
    #: Tools whose use would indicate a misread of the case. Kept small: most
    #: tool use is cheap and harmless, and penalising curiosity would be wrong.
    forbidden_tools: frozenset[str] = frozenset()

    should_escalate: bool
    #: Why the expectation above is the correct one. Required: an expectation
    #: nobody can justify is an expectation that should not be in a benchmark.
    rationale: str = Field(min_length=1)
    difficulty: Difficulty

    offline: OfflineScript

    # -- validation ------------------------------------------------------

    @model_validator(mode="after")
    def _check(self) -> "Scenario":
        self._check_tools()
        self._check_payloads()
        self._check_escalation()
        self._check_evidence()
        return self

    def _check_tools(self) -> None:
        for name, tools in (
            ("required_tools", self.required_tools),
            ("optional_tools", self.optional_tools),
            ("forbidden_tools", self.forbidden_tools),
        ):
            unknown = set(tools) - ALLOWED_TOOLS
            if unknown:
                raise ValueError(f"{self.scenario_id}: {name} names unknown tools {sorted(unknown)}")

        overlap = self.required_tools & self.forbidden_tools
        if overlap:
            raise ValueError(
                f"{self.scenario_id}: tools are both required and forbidden: {sorted(overlap)}"
            )
        optional_overlap = self.optional_tools & self.forbidden_tools
        if optional_overlap:
            raise ValueError(
                f"{self.scenario_id}: tools are both optional and forbidden: "
                f"{sorted(optional_overlap)}"
            )

    def _check_payloads(self) -> None:
        """Parse with the production models, so bad data fails here.

        The production evidence models accept any string as a currency — the
        Financial Core validates that on write, so the read model does not
        repeat it. A benchmark authored by hand has no such upstream guard, so
        the ISO-4217 shape is checked here. This constrains the dataset only;
        no production model is changed.
        """
        try:
            TransactionEvidence.model_validate(self.transaction)
        except Exception as error:
            raise ValueError(f"{self.scenario_id}: transaction payload is invalid: {error}") from error
        for settlement in self.settlements:
            try:
                SettlementEvidence.model_validate(settlement)
            except Exception as error:
                raise ValueError(
                    f"{self.scenario_id}: settlement payload is invalid: {error}"
                ) from error
        for rule in self.fee_rules:
            try:
                FeeRuleEvidence.model_validate(rule)
            except Exception as error:
                raise ValueError(
                    f"{self.scenario_id}: fee rule payload is invalid: {error}"
                ) from error

        for label, payload in [("transaction", self.transaction)] + [
            ("settlement", s) for s in self.settlements
        ] + [("fee rule", r) for r in self.fee_rules]:
            currency = payload.get("currency")
            if not (isinstance(currency, str) and len(currency) == 3 and currency.isalpha()):
                raise ValueError(
                    f"{self.scenario_id}: {label} payload is invalid: currency "
                    f"{currency!r} is not a three-letter ISO 4217 code"
                )

        transaction_id = self.transaction.get("transactionId")
        for settlement in self.settlements:
            if settlement.get("transactionId") != transaction_id:
                raise ValueError(
                    f"{self.scenario_id}: settlement {settlement.get('settlementId')!r} "
                    f"references a different transaction"
                )

    def _check_escalation(self) -> None:
        """A non-conclusive expectation must expect escalation.

        Not a style rule: the production guardrail escalates UNKNOWN and
        INSUFFICIENT_EVIDENCE unconditionally. A scenario expecting one of those
        *and* expecting review would be unsatisfiable, and would show up as a
        permanent failure that no model change could fix.
        """
        if self.expected_classification in NON_CONCLUSIVE and not self.should_escalate:
            raise ValueError(
                f"{self.scenario_id}: expects {self.expected_classification.value} but "
                f"should_escalate is False; the guardrail always escalates that classification"
            )

    def _check_evidence(self) -> None:
        """Every named identifier must exist in this scenario's evidence."""
        available = self.available_identifiers()
        for expected in self.expected_evidence:
            if expected.reference is None:
                continue
            if expected.source_type is EvidenceSource.POLICY_DOCUMENT:
                # Policy documents come from the real corpus, checked separately
                # by the dataset validator which can see the corpus.
                continue
            if expected.reference not in available:
                raise ValueError(
                    f"{self.scenario_id}: expects evidence {expected.reference!r} which no "
                    f"tool in this scenario could return (available: {sorted(available)})"
                )

    # -- helpers ---------------------------------------------------------

    def available_identifiers(self) -> set[str]:
        """Financial identifiers this scenario's tools can actually return."""
        identifiers = {self.transaction["transactionId"]}
        identifiers |= {s["settlementId"] for s in self.settlements}
        identifiers |= {s["transactionId"] for s in self.settlements}
        identifiers |= {r["ruleId"] for r in self.fee_rules}
        return identifiers

    def expected_identifiers(self) -> list[str]:
        """Named identifiers the conclusion should cite, for coverage scoring."""
        return [e.reference for e in self.expected_evidence if e.reference is not None]

    def is_acceptable(self, classification: RootCauseClassification) -> bool:
        return (
            classification == self.expected_classification
            or classification in self.acceptable_classifications
        )


def validate_dataset(scenarios: Sequence[Scenario], policy_documents: set[str]) -> None:
    """Check the dataset as a whole. Fails fast and loudly.

    A bad benchmark is worse than no benchmark: it produces numbers that look
    like measurement and are not. These checks are the difference.

    :raises ValueError: the dataset is unusable
    """
    problems: list[str] = []

    seen: dict[str, int] = {}
    for scenario in scenarios:
        seen[scenario.scenario_id] = seen.get(scenario.scenario_id, 0) + 1
    duplicates = sorted(sid for sid, count in seen.items() if count > 1)
    if duplicates:
        problems.append(f"duplicate scenario_id: {duplicates}")

    for scenario in scenarios:
        for expected in scenario.expected_evidence:
            if (
                expected.source_type is EvidenceSource.POLICY_DOCUMENT
                and expected.reference is not None
                and expected.reference not in policy_documents
            ):
                problems.append(
                    f"{scenario.scenario_id}: expects policy {expected.reference!r} which is "
                    f"not in the corpus ({sorted(policy_documents)})"
                )

    if problems:
        raise ValueError("Evaluation dataset is invalid:\n  - " + "\n  - ".join(problems))
