"""The evaluation dataset.

Thirty-six synthetic scenarios spanning every implemented classification, built
so that a system which pattern-matches amounts rather than reasoning about
evidence will score visibly worse than one that reasons.

Several cases are adversarial by construction: the amount matches a fee rule
that is inactive; the amount matches but the currency does not; two rules sum to
the difference while neither matches alone; a fee explains part of a difference
and nothing explains the rest. In each, the superficially obvious answer is
wrong, and the dataset says why in ``rationale``.

**Synthetic.** Every merchant, processor, amount and identifier is invented.
None resembles a real institution's data, and nothing here is customer
information. That also bounds what these numbers can claim — see the
"Limitations" section of ``evals/README.md``.

Offline scripts live alongside each scenario. They are harness input, not
ground truth, and a handful deliberately misbehave so the metrics are exercised
against known-wrong behaviour rather than only against success.
"""

from typing import Any

from app.events import ExceptionType
from app.investigation_models import EvidenceSource, RootCauseClassification

from evals.scenario import Difficulty, ExpectedEvidence, OfflineScript, Scenario

MERCHANT = "MERCHANT-EVAL-001"
PROCESSOR = "NORTHSTAR_PAYMENTS"
OTHER_PROCESSOR = "ATLAS_CLEARING"

T = RootCauseClassification
S = EvidenceSource


def _transaction(
    transaction_id: str,
    *,
    expected: str,
    amount: str | None = None,
    currency: str = "USD",
    merchant: str = MERCHANT,
    status: str = "POSTED",
    timestamp: str = "2026-09-20T10:00:00Z",
) -> dict[str, Any]:
    return {
        "transactionId": transaction_id,
        "merchantId": merchant,
        "amount": amount or expected,
        "expectedSettlementAmount": expected,
        "currency": currency,
        "transactionType": "PURCHASE",
        "status": status,
        "transactionTimestamp": timestamp,
        "createdAt": timestamp,
    }


def _settlement(
    settlement_id: str,
    transaction_id: str,
    *,
    settled: str,
    currency: str = "USD",
    processor: str = PROCESSOR,
    status: str = "COMPLETED",
    timestamp: str = "2026-09-20T22:00:00Z",
) -> dict[str, Any]:
    return {
        "settlementId": settlement_id,
        "transactionId": transaction_id,
        "processor": processor,
        "settledAmount": settled,
        "currency": currency,
        "status": status,
        "settlementTimestamp": timestamp,
    }


def _fee(
    rule_id: str,
    *,
    amount: str,
    fee_type: str = "PROCESSING",
    currency: str = "USD",
    merchant: str | None = MERCHANT,
    processor: str = PROCESSOR,
    active: bool = True,
    description: str = "Settlement processing fee.",
) -> dict[str, Any]:
    return {
        "ruleId": rule_id,
        "merchantId": merchant,
        "processor": processor,
        "feeType": fee_type,
        "feeAmount": amount,
        "currency": currency,
        "description": description,
        "active": active,
    }


def _cite(*refs: tuple[str, str]) -> tuple[tuple[str, str, str | None], ...]:
    return tuple((source, reference, None) for source, reference in refs)


#: A correct, well-evidenced offline script for a fee case. Most scenarios use
#: a variant of this; the interesting ones deliberately do not.
def _script(
    classification: RootCauseClassification,
    confidence: float,
    citations: tuple[tuple[str, str, str | None], ...],
    tools: tuple[str, ...] = (
        "get_transaction",
        "get_settlements",
        "get_fee_rules",
        "search_policy_documents",
    ),
) -> OfflineScript:
    return OfflineScript(
        tool_calls=tools,
        classification=classification,
        confidence=confidence,
        citations=citations,
    )


def _build() -> list[Scenario]:
    scenarios: list[Scenario] = []
    add = scenarios.append

    # ---------------------------------------------------------------
    # PROCESSOR_FEE — the well-evidenced cases
    # ---------------------------------------------------------------

    add(Scenario(
        scenario_id="fee-exact-match",
        title="Fee rule exactly matches the discrepancy",
        description="Expected 2500.00, settled 2450.00. One active 50.00 processing fee applies.",
        exception_type=ExceptionType.AMOUNT_MISMATCH,
        transaction=_transaction("TX-E1001", expected="2500.00"),
        settlements=[_settlement("SET-E1001", "TX-E1001", settled="2450.00")],
        fee_rules=[_fee("FR-E101", amount="50.00")],
        expected_classification=T.PROCESSOR_FEE,
        expected_evidence=(
            ExpectedEvidence(source_type=S.SETTLEMENT, reference="SET-E1001",
                             why="The settled amount is the observed fact."),
            ExpectedEvidence(source_type=S.FEE_RULE, reference="FR-E101",
                             why="The rule that accounts for the difference."),
        ),
        required_tools=frozenset({"get_transaction", "get_settlements", "get_fee_rules"}),
        optional_tools=frozenset({"search_policy_documents"}),
        should_escalate=False,
        rationale=(
            "An active, merchant-and-processor-matched rule in the settlement currency "
            "equals the difference exactly. Nothing contradicts it."
        ),
        difficulty=Difficulty.CLEAR,
        offline=_script(T.PROCESSOR_FEE, 0.92,
                        _cite(("SETTLEMENT", "SET-E1001"), ("FEE_RULE", "FR-E101"))),
    ))

    add(Scenario(
        scenario_id="fee-plus-network-fee",
        title="Two fees sum to the difference",
        description="Expected 2500.00, settled 2447.50. A 50.00 merchant fee and a 2.50 network fee.",
        exception_type=ExceptionType.AMOUNT_MISMATCH,
        transaction=_transaction("TX-E1002", expected="2500.00"),
        settlements=[_settlement("SET-E1002", "TX-E1002", settled="2447.50")],
        fee_rules=[
            _fee("FR-E102", amount="50.00"),
            _fee("FR-E103", amount="2.50", fee_type="NETWORK", merchant=None,
                 description="Per-settlement network access fee for all merchants."),
        ],
        expected_classification=T.PROCESSOR_FEE,
        expected_evidence=(
            ExpectedEvidence(source_type=S.FEE_RULE, reference="FR-E102",
                             why="Merchant-specific component of the difference."),
            ExpectedEvidence(source_type=S.FEE_RULE, reference="FR-E103",
                             why="Processor-wide component; both must be cited to reach 52.50."),
        ),
        required_tools=frozenset({"get_settlements", "get_fee_rules"}),
        optional_tools=frozenset({"get_transaction", "search_policy_documents"}),
        should_escalate=False,
        rationale=(
            "Neither rule matches 52.50 alone. Policy states a merchant rule does not "
            "supersede a network access fee, so both apply and together they account for it."
        ),
        difficulty=Difficulty.MODERATE,
        offline=_script(T.PROCESSOR_FEE, 0.88,
                        _cite(("FEE_RULE", "FR-E102"), ("FEE_RULE", "FR-E103"))),
    ))

    add(Scenario(
        scenario_id="fee-inactive-rule-only",
        title="Matching fee rule is withdrawn",
        description="Expected 2500.00, settled 2450.00. The only 50.00 rule is inactive.",
        exception_type=ExceptionType.AMOUNT_MISMATCH,
        transaction=_transaction("TX-E1003", expected="2500.00"),
        settlements=[_settlement("SET-E1003", "TX-E1003", settled="2450.00")],
        fee_rules=[
            _fee("FR-E104", amount="50.00", active=False,
                 description="Withdrawn processing fee. Superseded."),
        ],
        expected_classification=T.UNKNOWN,
        acceptable_classifications=frozenset({T.INSUFFICIENT_EVIDENCE}),
        expected_evidence=(
            ExpectedEvidence(source_type=S.FEE_RULE, reference="FR-E104",
                             why="Must be cited to explain why it does NOT apply."),
        ),
        required_tools=frozenset({"get_settlements", "get_fee_rules"}),
        optional_tools=frozenset({"get_transaction", "search_policy_documents"}),
        should_escalate=True,
        rationale=(
            "ADVERSARIAL: the amount matches exactly, which is the trap. Policy states a "
            "withdrawn rule must not be used to explain a current settlement, so the "
            "matching amount is not evidence and the cause is unexplained."
        ),
        difficulty=Difficulty.ADVERSARIAL,
        offline=_script(T.UNKNOWN, 0.35, _cite(("FEE_RULE", "FR-E104"))),
    ))

    add(Scenario(
        scenario_id="fee-wrong-currency",
        title="Fee amount matches but currency does not",
        description="USD settlement short by 12.00; the only 12.00 rule is denominated in EUR.",
        exception_type=ExceptionType.AMOUNT_MISMATCH,
        transaction=_transaction("TX-E1004", expected="900.00"),
        settlements=[_settlement("SET-E1004", "TX-E1004", settled="888.00")],
        fee_rules=[
            _fee("FR-E105", amount="12.00", currency="EUR", fee_type="CROSS_BORDER",
                 merchant=None, description="Cross-border handling fee for EUR settlements."),
        ],
        expected_classification=T.UNKNOWN,
        acceptable_classifications=frozenset({T.INSUFFICIENT_EVIDENCE}),
        expected_evidence=(
            ExpectedEvidence(source_type=S.FEE_RULE, reference="FR-E105",
                             why="Cited to explain why the currency mismatch disqualifies it."),
        ),
        required_tools=frozenset({"get_settlements", "get_fee_rules"}),
        optional_tools=frozenset({"get_transaction", "search_policy_documents"}),
        should_escalate=True,
        rationale=(
            "ADVERSARIAL: numeric equality across currencies is coincidence. Policy requires "
            "the rule to be denominated in the settlement currency."
        ),
        difficulty=Difficulty.ADVERSARIAL,
        offline=_script(T.UNKNOWN, 0.4, _cite(("FEE_RULE", "FR-E105"))),
    ))


    add(Scenario(
        scenario_id="fee-partial-explanation",
        title="Fee explains part of the difference",
        description="Expected 2500.00, settled 2400.00. A 50.00 fee explains half the 100.00 gap.",
        exception_type=ExceptionType.AMOUNT_MISMATCH,
        transaction=_transaction("TX-E1006", expected="2500.00"),
        settlements=[_settlement("SET-E1006", "TX-E1006", settled="2400.00")],
        fee_rules=[_fee("FR-E107", amount="50.00")],
        expected_classification=T.UNKNOWN,
        acceptable_classifications=frozenset({T.INSUFFICIENT_EVIDENCE}),
        expected_evidence=(
            ExpectedEvidence(source_type=S.FEE_RULE, reference="FR-E107",
                             why="Accounts for part of the gap; the remainder is unexplained."),
        ),
        required_tools=frozenset({"get_settlements", "get_fee_rules"}),
        optional_tools=frozenset({"get_transaction", "search_policy_documents"}),
        should_escalate=True,
        rationale=(
            "A partially explained difference is not an explained one. 50.00 of the 100.00 "
            "gap has a cause; concluding PROCESSOR_FEE would imply the whole gap is accounted for."
        ),
        difficulty=Difficulty.ADVERSARIAL,
        offline=_script(T.UNKNOWN, 0.45, _cite(("FEE_RULE", "FR-E107"))),
    ))

    add(Scenario(
        scenario_id="fee-active-and-inactive-duplicate",
        title="Active and withdrawn rules with identical amounts",
        description="Two 50.00 rules; one active, one withdrawn. The active one applies.",
        exception_type=ExceptionType.AMOUNT_MISMATCH,
        transaction=_transaction("TX-E1007", expected="3000.00"),
        settlements=[_settlement("SET-E1007", "TX-E1007", settled="2950.00")],
        fee_rules=[
            _fee("FR-E108", amount="50.00", description="Current processing fee."),
            _fee("FR-E109", amount="50.00", active=False, description="Withdrawn; superseded by FR-E108."),
        ],
        expected_classification=T.PROCESSOR_FEE,
        expected_evidence=(
            ExpectedEvidence(source_type=S.FEE_RULE, reference="FR-E108",
                             why="The active rule is the one that applies."),
        ),
        required_tools=frozenset({"get_settlements", "get_fee_rules"}),
        optional_tools=frozenset({"get_transaction", "search_policy_documents"}),
        should_escalate=False,
        rationale=(
            "An active rule fully accounts for the difference. The withdrawn twin is a "
            "distractor: citing it instead would be wrong, but its presence does not make "
            "the case ambiguous."
        ),
        difficulty=Difficulty.MODERATE,
        offline=_script(T.PROCESSOR_FEE, 0.9, _cite(("FEE_RULE", "FR-E108"))),
    ))

    add(Scenario(
        scenario_id="fee-processor-wide-only",
        title="Only a processor-wide rule applies",
        description="No merchant-specific rule; a 2.50 network fee explains the difference.",
        exception_type=ExceptionType.AMOUNT_MISMATCH,
        transaction=_transaction("TX-E1008", expected="500.00"),
        settlements=[_settlement("SET-E1008", "TX-E1008", settled="497.50")],
        fee_rules=[
            _fee("FR-E110", amount="2.50", fee_type="NETWORK", merchant=None,
                 description="Per-settlement network access fee for all merchants."),
        ],
        expected_classification=T.PROCESSOR_FEE,
        expected_evidence=(
            ExpectedEvidence(source_type=S.FEE_RULE, reference="FR-E110",
                             why="Processor-wide rules apply to every merchant."),
        ),
        required_tools=frozenset({"get_settlements", "get_fee_rules"}),
        optional_tools=frozenset({"get_transaction", "search_policy_documents"}),
        should_escalate=False,
        rationale="A rule naming no merchant applies to all merchants on the processor.",
        difficulty=Difficulty.CLEAR,
        offline=_script(T.PROCESSOR_FEE, 0.89, _cite(("FEE_RULE", "FR-E110"))),
    ))

    add(Scenario(
        scenario_id="fee-near-miss-amount",
        title="Fee rule almost matches but not exactly",
        description="Difference is 50.25; the nearest rule is 50.00.",
        exception_type=ExceptionType.AMOUNT_MISMATCH,
        transaction=_transaction("TX-E1009", expected="1000.00"),
        settlements=[_settlement("SET-E1009", "TX-E1009", settled="949.75")],
        fee_rules=[_fee("FR-E111", amount="50.00")],
        expected_classification=T.UNKNOWN,
        acceptable_classifications=frozenset({T.INSUFFICIENT_EVIDENCE}),
        expected_evidence=(
            ExpectedEvidence(source_type=S.FEE_RULE, reference="FR-E111",
                             why="Close but not equal; the 0.25 remainder is unexplained."),
        ),
        required_tools=frozenset({"get_settlements", "get_fee_rules"}),
        optional_tools=frozenset({"get_transaction", "search_policy_documents"}),
        should_escalate=True,
        rationale=(
            "ADVERSARIAL: 'close enough' is not reconciliation. Policy requires the amounts "
            "to reconcile, and 0.25 remains unaccounted for."
        ),
        difficulty=Difficulty.ADVERSARIAL,
        offline=_script(T.UNKNOWN, 0.4, _cite(("FEE_RULE", "FR-E111"))),
    ))

    add(Scenario(
        scenario_id="fee-no-rules-at-all",
        title="Amount mismatch with no fee rules configured",
        description="Expected 750.00, settled 700.00, and the processor has no rules.",
        exception_type=ExceptionType.AMOUNT_MISMATCH,
        transaction=_transaction("TX-E1010", expected="750.00"),
        settlements=[_settlement("SET-E1010", "TX-E1010", settled="700.00")],
        fee_rules=[],
        expected_classification=T.UNKNOWN,
        acceptable_classifications=frozenset({T.INSUFFICIENT_EVIDENCE}),
        expected_evidence=(),
        required_tools=frozenset({"get_settlements", "get_fee_rules"}),
        optional_tools=frozenset({"get_transaction", "search_policy_documents"}),
        should_escalate=True,
        rationale="No candidate explanation exists. The difference is real and unexplained.",
        difficulty=Difficulty.CLEAR,
        offline=_script(T.UNKNOWN, 0.25, ()),
    ))

    # ---------------------------------------------------------------
    # PROCESSOR_DELAY — timing, not amount
    # ---------------------------------------------------------------

    add(Scenario(
        scenario_id="delay-settled-late-correct-amount",
        title="Settlement arrived late but for the full amount",
        description="Missing at detection; a later COMPLETED settlement matches exactly.",
        exception_type=ExceptionType.MISSING_SETTLEMENT,
        transaction=_transaction("TX-E2001", expected="1500.00", timestamp="2026-09-18T09:00:00Z"),
        settlements=[_settlement("SET-E2001", "TX-E2001", settled="1500.00",
                                 timestamp="2026-09-21T09:00:00Z")],
        fee_rules=[],
        expected_classification=T.PROCESSOR_DELAY,
        expected_evidence=(
            ExpectedEvidence(source_type=S.SETTLEMENT, reference="SET-E2001",
                             why="Its timestamp versus the transaction's is the whole case."),
        ),
        required_tools=frozenset({"get_transaction", "get_settlements"}),
        optional_tools=frozenset({"get_fee_rules", "search_policy_documents"}),
        should_escalate=False,
        rationale=(
            "The amount is correct, so nothing is financially wrong. The settlement simply "
            "arrived outside the expected window: a timing cause, not an amount cause."
        ),
        difficulty=Difficulty.CLEAR,
        offline=_script(T.PROCESSOR_DELAY, 0.9, _cite(("SETTLEMENT", "SET-E2001")),
                        tools=("get_transaction", "get_settlements", "search_policy_documents")),
    ))

    add(Scenario(
        scenario_id="delay-pending-settlement",
        title="Settlement exists but is still PENDING",
        description="A PENDING settlement for the full amount is present.",
        exception_type=ExceptionType.MISSING_SETTLEMENT,
        transaction=_transaction("TX-E2002", expected="820.00"),
        settlements=[_settlement("SET-E2002", "TX-E2002", settled="820.00", status="PENDING")],
        fee_rules=[],
        expected_classification=T.PROCESSOR_DELAY,
        expected_evidence=(
            ExpectedEvidence(source_type=S.SETTLEMENT, reference="SET-E2002",
                             why="PENDING status is the evidence of incompleteness, not absence."),
        ),
        required_tools=frozenset({"get_settlements"}),
        optional_tools=frozenset({"get_transaction", "get_fee_rules", "search_policy_documents"}),
        should_escalate=False,
        rationale=(
            "Reconciliation only considers COMPLETED settlements, so a PENDING one reads as "
            "missing. The record exists and is in flight."
        ),
        difficulty=Difficulty.MODERATE,
        offline=_script(T.PROCESSOR_DELAY, 0.87, _cite(("SETTLEMENT", "SET-E2002")),
                        tools=("get_settlements", "search_policy_documents")),
    ))

    add(Scenario(
        scenario_id="delay-no-timing-evidence",
        title="Missing settlement with no timing evidence at all",
        description="No settlement record of any kind exists.",
        exception_type=ExceptionType.MISSING_SETTLEMENT,
        transaction=_transaction("TX-E2003", expected="640.00"),
        settlements=[],
        fee_rules=[],
        expected_classification=T.INSUFFICIENT_EVIDENCE,
        acceptable_classifications=frozenset({T.UNKNOWN}),
        expected_evidence=(),
        required_tools=frozenset({"get_settlements"}),
        optional_tools=frozenset({"get_transaction", "get_fee_rules", "search_policy_documents"}),
        should_escalate=True,
        rationale=(
            "Absence of a settlement is consistent with delay, with processor failure, and "
            "with never having been sent. Nothing distinguishes them, so concluding "
            "PROCESSOR_DELAY would be a guess dressed as a finding."
        ),
        difficulty=Difficulty.AMBIGUOUS,
        offline=_script(T.INSUFFICIENT_EVIDENCE, 0.2, (),
                        tools=("get_settlements", "search_policy_documents")),
    ))

    # ---------------------------------------------------------------
    # DUPLICATE_PROCESSING
    # ---------------------------------------------------------------

    add(Scenario(
        scenario_id="duplicate-identical-settlements",
        title="Two identical completed settlements",
        description="Same processor, same amount, minutes apart.",
        exception_type=ExceptionType.DUPLICATE_SETTLEMENT,
        transaction=_transaction("TX-E3001", expected="1800.00"),
        settlements=[
            _settlement("SET-E3001", "TX-E3001", settled="1800.00", timestamp="2026-09-20T22:00:00Z"),
            _settlement("SET-E3002", "TX-E3001", settled="1800.00", timestamp="2026-09-20T22:04:00Z"),
        ],
        fee_rules=[],
        expected_classification=T.DUPLICATE_PROCESSING,
        expected_evidence=(
            ExpectedEvidence(source_type=S.SETTLEMENT, reference="SET-E3001", why="First settlement."),
            ExpectedEvidence(source_type=S.SETTLEMENT, reference="SET-E3002",
                             why="The duplicate; both must be cited to show the pair."),
        ),
        required_tools=frozenset({"get_settlements"}),
        optional_tools=frozenset({"get_transaction", "get_fee_rules", "search_policy_documents"}),
        should_escalate=False,
        rationale="Two COMPLETED settlements of identical amount minutes apart is the textbook case.",
        difficulty=Difficulty.CLEAR,
        offline=_script(T.DUPLICATE_PROCESSING, 0.93,
                        _cite(("SETTLEMENT", "SET-E3001"), ("SETTLEMENT", "SET-E3002")),
                        tools=("get_settlements", "search_policy_documents")),
    ))

    add(Scenario(
        scenario_id="duplicate-one-reversed",
        title="Two settlements, one reversed",
        description="A duplicate exists but was reversed, leaving one effective settlement.",
        exception_type=ExceptionType.DUPLICATE_SETTLEMENT,
        transaction=_transaction("TX-E3002", expected="1800.00"),
        settlements=[
            _settlement("SET-E3003", "TX-E3002", settled="1800.00"),
            _settlement("SET-E3004", "TX-E3002", settled="1800.00", status="REVERSED",
                        timestamp="2026-09-20T23:00:00Z"),
        ],
        fee_rules=[],
        expected_classification=T.DUPLICATE_PROCESSING,
        acceptable_classifications=frozenset({T.PROCESSOR_ERROR}),
        expected_evidence=(
            ExpectedEvidence(source_type=S.SETTLEMENT, reference="SET-E3004",
                             why="Its REVERSED status is what makes this already-corrected."),
        ),
        required_tools=frozenset({"get_settlements"}),
        optional_tools=frozenset({"get_transaction", "get_fee_rules", "search_policy_documents"}),
        should_escalate=False,
        rationale=(
            "A duplicate did occur and was already reversed. The classification is still "
            "DUPLICATE_PROCESSING; the reversal is remediation, not a different cause."
        ),
        difficulty=Difficulty.MODERATE,
        offline=_script(T.DUPLICATE_PROCESSING, 0.85,
                        _cite(("SETTLEMENT", "SET-E3003"), ("SETTLEMENT", "SET-E3004")),
                        tools=("get_settlements", "search_policy_documents")),
    ))

    add(Scenario(
        scenario_id="duplicate-legitimate-split",
        title="Two settlements that legitimately sum to the expected amount",
        description="Two partial settlements of 900.00 each for an expected 1800.00.",
        exception_type=ExceptionType.DUPLICATE_SETTLEMENT,
        transaction=_transaction("TX-E3003", expected="1800.00"),
        settlements=[
            _settlement("SET-E3005", "TX-E3003", settled="900.00"),
            _settlement("SET-E3006", "TX-E3003", settled="900.00", timestamp="2026-09-21T02:00:00Z"),
        ],
        fee_rules=[],
        expected_classification=T.UNKNOWN,
        acceptable_classifications=frozenset({T.INSUFFICIENT_EVIDENCE, T.DUPLICATE_PROCESSING}),
        expected_evidence=(
            ExpectedEvidence(source_type=S.SETTLEMENT, reference="SET-E3005", why="First part."),
            ExpectedEvidence(source_type=S.SETTLEMENT, reference="SET-E3006", why="Second part."),
        ),
        required_tools=frozenset({"get_transaction", "get_settlements"}),
        optional_tools=frozenset({"get_fee_rules", "search_policy_documents"}),
        should_escalate=True,
        rationale=(
            "ADVERSARIAL: two settlements looks like duplication, but they sum exactly to the "
            "expected amount, which is what a legitimate split settlement looks like. Nothing "
            "available distinguishes a split from a double-pay, so it needs a human."
        ),
        difficulty=Difficulty.ADVERSARIAL,
        offline=_script(T.UNKNOWN, 0.45,
                        _cite(("SETTLEMENT", "SET-E3005"), ("SETTLEMENT", "SET-E3006")),
                        tools=("get_transaction", "get_settlements")),
    ))

    add(Scenario(
        scenario_id="duplicate-different-processors",
        title="Duplicate settlements from two different processors",
        description="Identical amounts settled by NORTHSTAR and ATLAS.",
        exception_type=ExceptionType.DUPLICATE_SETTLEMENT,
        transaction=_transaction("TX-E3004", expected="450.00"),
        settlements=[
            _settlement("SET-E3007", "TX-E3004", settled="450.00"),
            _settlement("SET-E3008", "TX-E3004", settled="450.00", processor=OTHER_PROCESSOR,
                        timestamp="2026-09-20T22:10:00Z"),
        ],
        fee_rules=[],
        expected_classification=T.DUPLICATE_PROCESSING,
        acceptable_classifications=frozenset({T.PROCESSOR_ERROR}),
        expected_evidence=(
            ExpectedEvidence(source_type=S.SETTLEMENT, reference="SET-E3007", why="NORTHSTAR settlement."),
            ExpectedEvidence(source_type=S.SETTLEMENT, reference="SET-E3008", why="ATLAS settlement."),
        ),
        required_tools=frozenset({"get_settlements"}),
        optional_tools=frozenset({"get_transaction", "get_fee_rules", "search_policy_documents"}),
        should_escalate=False,
        rationale=(
            "One transaction settled twice by two processors is duplication regardless of who "
            "did it. PROCESSOR_ERROR is defensible since cross-processor routing is itself a fault."
        ),
        difficulty=Difficulty.MODERATE,
        offline=_script(T.DUPLICATE_PROCESSING, 0.88,
                        _cite(("SETTLEMENT", "SET-E3007"), ("SETTLEMENT", "SET-E3008")),
                        tools=("get_settlements",)),
    ))

    # ---------------------------------------------------------------
    # CURRENCY_CONVERSION
    # ---------------------------------------------------------------

    add(Scenario(
        scenario_id="currency-eur-settlement-with-policy",
        title="EUR settlement against a USD transaction",
        description="Expected 1000.00 USD, settled 920.00 EUR.",
        exception_type=ExceptionType.CURRENCY_MISMATCH,
        transaction=_transaction("TX-E4001", expected="1000.00", currency="USD"),
        settlements=[_settlement("SET-E4001", "TX-E4001", settled="920.00", currency="EUR")],
        fee_rules=[],
        expected_classification=T.CURRENCY_CONVERSION,
        expected_evidence=(
            ExpectedEvidence(source_type=S.SETTLEMENT, reference="SET-E4001",
                             why="Its currency differs from the transaction's."),
            ExpectedEvidence(source_type=S.POLICY_DOCUMENT, reference="POL-FX-001",
                             why="The policy governing settlement currency."),
        ),
        required_tools=frozenset({"get_transaction", "get_settlements", "search_policy_documents"}),
        optional_tools=frozenset({"get_fee_rules"}),
        should_escalate=False,
        rationale="The currencies plainly differ and the FX policy is the relevant authority.",
        difficulty=Difficulty.CLEAR,
        offline=OfflineScript(
            tool_calls=("get_transaction", "get_settlements", "search_policy_documents"),
            classification=T.CURRENCY_CONVERSION, confidence=0.9,
            citations=(("SETTLEMENT", "SET-E4001", None),
                       ("POLICY_DOCUMENT", "POL-FX-001", "Currency Mismatch")),
        ),
    ))

    add(Scenario(
        scenario_id="currency-no-fx-evidence",
        title="Currency mismatch with no conversion evidence",
        description="Settled in GBP; nothing indicates an agreed conversion.",
        exception_type=ExceptionType.CURRENCY_MISMATCH,
        transaction=_transaction("TX-E4002", expected="2000.00", currency="USD"),
        settlements=[_settlement("SET-E4002", "TX-E4002", settled="1580.00", currency="GBP")],
        fee_rules=[],
        expected_classification=T.CURRENCY_CONVERSION,
        acceptable_classifications=frozenset({T.INSUFFICIENT_EVIDENCE, T.PROCESSOR_ERROR}),
        expected_evidence=(
            ExpectedEvidence(source_type=S.SETTLEMENT, reference="SET-E4002",
                             why="The mismatched currency is the observable fact."),
        ),
        required_tools=frozenset({"get_settlements"}),
        optional_tools=frozenset({"get_transaction", "get_fee_rules", "search_policy_documents"}),
        should_escalate=True,
        rationale=(
            "The mismatch is visible but no rate, agreement or FX record supports the "
            "specific amount. The cause is plausible and unproven, so a human decides."
        ),
        difficulty=Difficulty.AMBIGUOUS,
        offline=_script(T.CURRENCY_CONVERSION, 0.55, _cite(("SETTLEMENT", "SET-E4002")),
                        tools=("get_settlements", "search_policy_documents")),
    ))

    add(Scenario(
        scenario_id="currency-same-currency-amount-gap",
        title="Currencies match; only the amount differs",
        description="Detected as CURRENCY_MISMATCH but both records are USD.",
        exception_type=ExceptionType.CURRENCY_MISMATCH,
        transaction=_transaction("TX-E4003", expected="700.00", currency="USD"),
        settlements=[_settlement("SET-E4003", "TX-E4003", settled="680.00", currency="USD")],
        fee_rules=[_fee("FR-E401", amount="20.00")],
        expected_classification=T.PROCESSOR_FEE,
        acceptable_classifications=frozenset({T.PROCESSOR_ERROR}),
        expected_evidence=(
            ExpectedEvidence(source_type=S.FEE_RULE, reference="FR-E401",
                             why="A 20.00 fee accounts for the whole difference."),
        ),
        required_tools=frozenset({"get_settlements", "get_fee_rules"}),
        optional_tools=frozenset({"get_transaction", "search_policy_documents"}),
        should_escalate=False,
        rationale=(
            "ADVERSARIAL: the deterministic label says CURRENCY_MISMATCH, but the currencies "
            "are identical. The investigator must reason from the records, not the label."
        ),
        difficulty=Difficulty.ADVERSARIAL,
        offline=_script(T.PROCESSOR_FEE, 0.86, _cite(("FEE_RULE", "FR-E401")),
                        tools=("get_settlements", "get_fee_rules")),
    ))

    add(Scenario(
        scenario_id="currency-cross-border-fee",
        title="Cross-border fee in the settlement currency",
        description="EUR settlement short by exactly the 12.00 EUR cross-border fee.",
        exception_type=ExceptionType.CURRENCY_MISMATCH,
        transaction=_transaction("TX-E4004", expected="600.00", currency="EUR"),
        settlements=[_settlement("SET-E4004", "TX-E4004", settled="588.00", currency="EUR")],
        fee_rules=[
            _fee("FR-E402", amount="12.00", currency="EUR", fee_type="CROSS_BORDER", merchant=None,
                 description="Cross-border settlement handling fee for euro settlements."),
        ],
        expected_classification=T.PROCESSOR_FEE,
        acceptable_classifications=frozenset({T.CURRENCY_CONVERSION}),
        expected_evidence=(
            ExpectedEvidence(source_type=S.FEE_RULE, reference="FR-E402",
                             why="Denominated in the settlement currency and matches exactly."),
        ),
        required_tools=frozenset({"get_settlements", "get_fee_rules"}),
        optional_tools=frozenset({"get_transaction", "search_policy_documents"}),
        should_escalate=False,
        rationale=(
            "Both records are EUR, so there is no conversion. A cross-border fee in the "
            "settlement currency matches exactly."
        ),
        difficulty=Difficulty.MODERATE,
        offline=_script(T.PROCESSOR_FEE, 0.87, _cite(("FEE_RULE", "FR-E402")),
                        tools=("get_settlements", "get_fee_rules")),
    ))

    # ---------------------------------------------------------------
    # PROCESSOR_ERROR
    # ---------------------------------------------------------------

    add(Scenario(
        scenario_id="error-settled-more-than-expected",
        title="Processor settled more than expected",
        description="Expected 400.00, settled 4000.00.",
        exception_type=ExceptionType.AMOUNT_MISMATCH,
        transaction=_transaction("TX-E5001", expected="400.00"),
        settlements=[_settlement("SET-E5001", "TX-E5001", settled="4000.00")],
        fee_rules=[_fee("FR-E501", amount="5.00")],
        expected_classification=T.PROCESSOR_ERROR,
        acceptable_classifications=frozenset({T.UNKNOWN}),
        expected_evidence=(
            ExpectedEvidence(source_type=S.SETTLEMENT, reference="SET-E5001",
                             why="Settling ten times the expected amount is the fault itself."),
        ),
        required_tools=frozenset({"get_transaction", "get_settlements"}),
        optional_tools=frozenset({"get_fee_rules", "search_policy_documents"}),
        should_escalate=False,
        rationale=(
            "A fee can only reduce a settlement. Settling an order of magnitude too much "
            "is a processing fault, and no fee rule could explain an overpayment."
        ),
        difficulty=Difficulty.MODERATE,
        offline=_script(T.PROCESSOR_ERROR, 0.88, _cite(("SETTLEMENT", "SET-E5001")),
                        tools=("get_transaction", "get_settlements")),
    ))

    add(Scenario(
        scenario_id="error-failed-settlement-status",
        title="Settlement recorded as FAILED",
        description="The only settlement carries FAILED status.",
        exception_type=ExceptionType.MISSING_SETTLEMENT,
        transaction=_transaction("TX-E5002", expected="980.00"),
        settlements=[_settlement("SET-E5002", "TX-E5002", settled="0.00", status="FAILED")],
        fee_rules=[],
        expected_classification=T.PROCESSOR_ERROR,
        acceptable_classifications=frozenset({T.PROCESSOR_DELAY}),
        expected_evidence=(
            ExpectedEvidence(source_type=S.SETTLEMENT, reference="SET-E5002",
                             why="FAILED status is direct evidence of a processing failure."),
        ),
        required_tools=frozenset({"get_settlements"}),
        optional_tools=frozenset({"get_transaction", "get_fee_rules", "search_policy_documents"}),
        should_escalate=False,
        rationale="The processor recorded an explicit failure, which is a stated cause rather than an inference.",
        difficulty=Difficulty.CLEAR,
        offline=_script(T.PROCESSOR_ERROR, 0.9, _cite(("SETTLEMENT", "SET-E5002")),
                        tools=("get_settlements",)),
    ))

    add(Scenario(
        scenario_id="error-negative-direction-fee",
        title="Settlement exceeds expected by exactly a fee amount",
        description="Settled 25.00 ABOVE expected, and a 25.00 fee rule exists.",
        exception_type=ExceptionType.AMOUNT_MISMATCH,
        transaction=_transaction("TX-E5003", expected="1000.00"),
        settlements=[_settlement("SET-E5003", "TX-E5003", settled="1025.00")],
        fee_rules=[_fee("FR-E502", amount="25.00")],
        expected_classification=T.PROCESSOR_ERROR,
        acceptable_classifications=frozenset({T.UNKNOWN, T.INSUFFICIENT_EVIDENCE}),
        expected_evidence=(
            ExpectedEvidence(source_type=S.SETTLEMENT, reference="SET-E5003",
                             why="The direction of the difference is the decisive fact."),
        ),
        required_tools=frozenset({"get_transaction", "get_settlements", "get_fee_rules"}),
        optional_tools=frozenset({"search_policy_documents"}),
        should_escalate=False,
        rationale=(
            "ADVERSARIAL: the magnitude matches a fee exactly, but the sign is wrong. Fees are "
            "deducted, so a fee cannot make a settlement larger. Matching on magnitude alone "
            "yields PROCESSOR_FEE, which is incorrect."
        ),
        difficulty=Difficulty.ADVERSARIAL,
        offline=_script(T.PROCESSOR_ERROR, 0.82, _cite(("SETTLEMENT", "SET-E5003"))),
    ))

    # ---------------------------------------------------------------
    # UNKNOWN / INSUFFICIENT_EVIDENCE / conflicting evidence
    # ---------------------------------------------------------------

    add(Scenario(
        scenario_id="unknown-conflicting-fee-rules",
        title="Two active rules each matching a different reading",
        description="Difference 30.00; two active rules of 30.00 with contradictory scope.",
        exception_type=ExceptionType.AMOUNT_MISMATCH,
        transaction=_transaction("TX-E6001", expected="1300.00"),
        settlements=[_settlement("SET-E6001", "TX-E6001", settled="1270.00")],
        fee_rules=[
            _fee("FR-E601", amount="30.00", description="Merchant processing fee."),
            _fee("FR-E602", amount="30.00", fee_type="NETWORK", merchant=None,
                 description="Processor-wide network fee applied to all merchants."),
        ],
        expected_classification=T.UNKNOWN,
        acceptable_classifications=frozenset({T.PROCESSOR_FEE, T.INSUFFICIENT_EVIDENCE}),
        expected_evidence=(
            ExpectedEvidence(source_type=S.FEE_RULE, reference="FR-E601", why="One candidate."),
            ExpectedEvidence(source_type=S.FEE_RULE, reference="FR-E602", why="The other candidate."),
        ),
        required_tools=frozenset({"get_settlements", "get_fee_rules"}),
        optional_tools=frozenset({"get_transaction", "search_policy_documents"}),
        should_escalate=True,
        rationale=(
            "Policy says both a merchant rule and a network fee may apply, which would give "
            "60.00. Only 30.00 was deducted, so exactly one was charged and nothing says which."
        ),
        difficulty=Difficulty.AMBIGUOUS,
        offline=_script(T.UNKNOWN, 0.4,
                        _cite(("FEE_RULE", "FR-E601"), ("FEE_RULE", "FR-E602"))),
    ))


    add(Scenario(
        scenario_id="insufficient-no-records-at-all",
        title="Transaction with no settlements and no rules",
        description="Nothing exists beyond the transaction itself.",
        exception_type=ExceptionType.MISSING_SETTLEMENT,
        transaction=_transaction("TX-E6003", expected="55.00"),
        settlements=[],
        fee_rules=[],
        expected_classification=T.INSUFFICIENT_EVIDENCE,
        acceptable_classifications=frozenset({T.UNKNOWN}),
        expected_evidence=(),
        required_tools=frozenset({"get_settlements"}),
        optional_tools=frozenset({"get_transaction", "get_fee_rules", "search_policy_documents"}),
        should_escalate=True,
        rationale="There is nothing to reason from. Reporting that is the correct outcome.",
        difficulty=Difficulty.CLEAR,
        offline=_script(T.INSUFFICIENT_EVIDENCE, 0.15, (),
                        tools=("get_settlements", "get_fee_rules")),
    ))

    add(Scenario(
        scenario_id="insufficient-reversed-only",
        title="Only a reversed settlement exists",
        description="One REVERSED settlement and nothing else.",
        exception_type=ExceptionType.MISSING_SETTLEMENT,
        transaction=_transaction("TX-E6004", expected="410.00"),
        settlements=[_settlement("SET-E6003", "TX-E6004", settled="410.00", status="REVERSED")],
        fee_rules=[],
        expected_classification=T.INSUFFICIENT_EVIDENCE,
        acceptable_classifications=frozenset({T.UNKNOWN, T.PROCESSOR_ERROR}),
        expected_evidence=(
            ExpectedEvidence(source_type=S.SETTLEMENT, reference="SET-E6003",
                             why="The reversal is the only fact available."),
        ),
        required_tools=frozenset({"get_settlements"}),
        optional_tools=frozenset({"get_transaction", "get_fee_rules", "search_policy_documents"}),
        should_escalate=True,
        rationale=(
            "A reversal says the settlement was undone but not why, and no replacement exists. "
            "Cause requires information the tools cannot reach."
        ),
        difficulty=Difficulty.AMBIGUOUS,
        offline=_script(T.INSUFFICIENT_EVIDENCE, 0.25, _cite(("SETTLEMENT", "SET-E6003")),
                        tools=("get_settlements",)),
    ))

    add(Scenario(
        scenario_id="unknown-fee-matches-but-inactive-and-active-differs",
        title="Active rule is the wrong amount; inactive one matches",
        description="Difference 40.00. Active rule is 15.00; the 40.00 rule is withdrawn.",
        exception_type=ExceptionType.AMOUNT_MISMATCH,
        transaction=_transaction("TX-E6005", expected="880.00"),
        settlements=[_settlement("SET-E6004", "TX-E6005", settled="840.00")],
        fee_rules=[
            _fee("FR-E604", amount="15.00", description="Current processing fee."),
            _fee("FR-E605", amount="40.00", active=False, description="Withdrawn legacy fee."),
        ],
        expected_classification=T.UNKNOWN,
        acceptable_classifications=frozenset({T.INSUFFICIENT_EVIDENCE}),
        expected_evidence=(
            ExpectedEvidence(source_type=S.FEE_RULE, reference="FR-E605",
                             why="The tempting match, which must be rejected as withdrawn."),
        ),
        required_tools=frozenset({"get_settlements", "get_fee_rules"}),
        optional_tools=frozenset({"get_transaction", "search_policy_documents"}),
        should_escalate=True,
        rationale=(
            "ADVERSARIAL: the exact match is withdrawn and the active rule is the wrong amount. "
            "Neither explains 40.00."
        ),
        difficulty=Difficulty.ADVERSARIAL,
        offline=_script(T.UNKNOWN, 0.35, _cite(("FEE_RULE", "FR-E605"))),
    ))

    # ---------------------------------------------------------------
    # Harness-exercising scenarios: deliberately misbehaving scripts.
    # These measure the METRICS, not the domain.
    # ---------------------------------------------------------------

    add(Scenario(
        scenario_id="harness-ungrounded-citation",
        title="Harness check: scripted provider fabricates a fee rule",
        description="The script cites FR-E999, which no tool returns.",
        exception_type=ExceptionType.AMOUNT_MISMATCH,
        transaction=_transaction("TX-E7001", expected="500.00"),
        settlements=[_settlement("SET-E7001", "TX-E7001", settled="450.00")],
        fee_rules=[_fee("FR-E701", amount="50.00")],
        expected_classification=T.PROCESSOR_FEE,
        expected_evidence=(
            ExpectedEvidence(source_type=S.FEE_RULE, reference="FR-E701", why="The real rule."),
        ),
        required_tools=frozenset({"get_settlements", "get_fee_rules"}),
        optional_tools=frozenset({"get_transaction", "search_policy_documents"}),
        should_escalate=False,
        rationale=(
            "Exists to prove the production grounding validator rejects a fabricated citation "
            "during an eval run, and that the metrics count it as an ungrounded result rather "
            "than crashing the suite."
        ),
        difficulty=Difficulty.CLEAR,
        offline=OfflineScript(
            tool_calls=("get_settlements", "get_fee_rules"),
            classification=T.PROCESSOR_FEE, confidence=0.95,
            citations=(("FEE_RULE", "FR-E999", None),),
        ),
    ))

    add(Scenario(
        scenario_id="harness-wrong-classification",
        title="Harness check: scripted provider answers incorrectly",
        description="A clear fee case where the script says DUPLICATE_PROCESSING.",
        exception_type=ExceptionType.AMOUNT_MISMATCH,
        transaction=_transaction("TX-E7002", expected="600.00"),
        settlements=[_settlement("SET-E7002", "TX-E7002", settled="580.00")],
        fee_rules=[_fee("FR-E702", amount="20.00")],
        expected_classification=T.PROCESSOR_FEE,
        expected_evidence=(
            ExpectedEvidence(source_type=S.FEE_RULE, reference="FR-E702", why="Explains the difference."),
        ),
        required_tools=frozenset({"get_settlements", "get_fee_rules"}),
        optional_tools=frozenset({"get_transaction", "search_policy_documents"}),
        should_escalate=False,
        rationale=(
            "Exists so strict accuracy is provably below 100% offline. A dataset whose script "
            "always agrees with the answer key measures nothing."
        ),
        difficulty=Difficulty.CLEAR,
        offline=OfflineScript(
            tool_calls=("get_settlements", "get_fee_rules"),
            classification=T.DUPLICATE_PROCESSING, confidence=0.9,
            citations=(("SETTLEMENT", "SET-E7002", None),),
        ),
    ))

    add(Scenario(
        scenario_id="harness-missing-required-tool",
        title="Harness check: scripted provider skips a required tool",
        description="Concludes without ever calling get_fee_rules.",
        exception_type=ExceptionType.AMOUNT_MISMATCH,
        transaction=_transaction("TX-E7003", expected="700.00"),
        settlements=[_settlement("SET-E7003", "TX-E7003", settled="690.00")],
        fee_rules=[_fee("FR-E703", amount="10.00")],
        expected_classification=T.PROCESSOR_FEE,
        expected_evidence=(
            ExpectedEvidence(source_type=S.FEE_RULE, reference="FR-E703", why="Would have explained it."),
        ),
        required_tools=frozenset({"get_settlements", "get_fee_rules"}),
        optional_tools=frozenset({"get_transaction", "search_policy_documents"}),
        should_escalate=False,
        rationale=(
            "Exists so required_tool_recall is provably below 1.0 offline, and to show that "
            "skipping evidence gathering degrades evidence coverage too."
        ),
        difficulty=Difficulty.CLEAR,
        offline=OfflineScript(
            tool_calls=("get_settlements",),
            classification=T.PROCESSOR_FEE, confidence=0.9,
            citations=(("SETTLEMENT", "SET-E7003", None),),
        ),
    ))

    add(Scenario(
        scenario_id="harness-low-confidence-correct-answer",
        title="Harness check: correct classification held at low confidence",
        description="Right answer, confidence 0.40, so the guardrail escalates it.",
        exception_type=ExceptionType.AMOUNT_MISMATCH,
        transaction=_transaction("TX-E7004", expected="800.00"),
        settlements=[_settlement("SET-E7004", "TX-E7004", settled="760.00")],
        fee_rules=[_fee("FR-E704", amount="40.00")],
        expected_classification=T.PROCESSOR_FEE,
        expected_evidence=(
            ExpectedEvidence(source_type=S.FEE_RULE, reference="FR-E704", why="Explains the difference."),
        ),
        required_tools=frozenset({"get_settlements", "get_fee_rules"}),
        optional_tools=frozenset({"get_transaction", "search_policy_documents"}),
        should_escalate=False,
        rationale=(
            "Demonstrates that classification and escalation are independent measurements: "
            "this is classified correctly and escalated anyway, which the confusion matrix "
            "must record as expected-review/actual-escalate rather than a classification miss."
        ),
        difficulty=Difficulty.CLEAR,
        offline=OfflineScript(
            tool_calls=("get_settlements", "get_fee_rules"),
            classification=T.PROCESSOR_FEE, confidence=0.40,
            citations=(("FEE_RULE", "FR-E704", None),),
        ),
    ))

    add(Scenario(
        scenario_id="harness-forbidden-tool-call",
        title="Harness check: scripted provider calls a tool flagged unnecessary",
        description="A pure duplicate-settlement case where fee rules are irrelevant.",
        exception_type=ExceptionType.DUPLICATE_SETTLEMENT,
        transaction=_transaction("TX-E7005", expected="150.00"),
        settlements=[
            _settlement("SET-E7005", "TX-E7005", settled="150.00"),
            _settlement("SET-E7006", "TX-E7005", settled="150.00", timestamp="2026-09-20T22:05:00Z"),
        ],
        fee_rules=[],
        expected_classification=T.DUPLICATE_PROCESSING,
        expected_evidence=(
            ExpectedEvidence(source_type=S.SETTLEMENT, reference="SET-E7005", why="First."),
            ExpectedEvidence(source_type=S.SETTLEMENT, reference="SET-E7006", why="Duplicate."),
        ),
        required_tools=frozenset({"get_settlements"}),
        forbidden_tools=frozenset({"get_fee_rules"}),
        should_escalate=False,
        rationale=(
            "Two identical settlements need no fee lookup; the amounts are equal. Exists so "
            "forbidden_tool_call_count is provably non-zero offline."
        ),
        difficulty=Difficulty.CLEAR,
        offline=OfflineScript(
            tool_calls=("get_settlements", "get_fee_rules"),
            classification=T.DUPLICATE_PROCESSING, confidence=0.9,
            citations=(("SETTLEMENT", "SET-E7005", None), ("SETTLEMENT", "SET-E7006", None)),
        ),
    ))

    # ---------------------------------------------------------------
    # Remaining coverage
    # ---------------------------------------------------------------

    add(Scenario(
        scenario_id="fee-policy-cited-correctly",
        title="Fee case where policy grounds the reasoning",
        description="Clear fee case; the fee policy states deduction happens at settlement.",
        exception_type=ExceptionType.AMOUNT_MISMATCH,
        transaction=_transaction("TX-E8001", expected="2200.00"),
        settlements=[_settlement("SET-E8001", "TX-E8001", settled="2150.00")],
        fee_rules=[_fee("FR-E801", amount="50.00")],
        expected_classification=T.PROCESSOR_FEE,
        expected_evidence=(
            ExpectedEvidence(source_type=S.FEE_RULE, reference="FR-E801", why="The applicable rule."),
            ExpectedEvidence(source_type=S.POLICY_DOCUMENT, reference="POL-FEE-001",
                             why="States that fees are deducted at settlement."),
        ),
        required_tools=frozenset({"get_settlements", "get_fee_rules", "search_policy_documents"}),
        optional_tools=frozenset({"get_transaction"}),
        should_escalate=False,
        rationale="Tests whether policy evidence is gathered and cited, not just fee records.",
        difficulty=Difficulty.CLEAR,
        offline=OfflineScript(
            tool_calls=("get_settlements", "get_fee_rules", "search_policy_documents"),
            classification=T.PROCESSOR_FEE, confidence=0.91,
            citations=(("FEE_RULE", "FR-E801", None),
                       ("POLICY_DOCUMENT", "POL-FEE-001", "Cross-Network Settlement Fees")),
        ),
    ))

    add(Scenario(
        scenario_id="fee-many-rules-one-applies",
        title="Five rules returned; one applies",
        description="A crowded fee table where only one rule fits every criterion.",
        exception_type=ExceptionType.AMOUNT_MISMATCH,
        transaction=_transaction("TX-E8002", expected="1600.00"),
        settlements=[_settlement("SET-E8002", "TX-E8002", settled="1585.00")],
        fee_rules=[
            _fee("FR-E802", amount="15.00", description="Applicable merchant processing fee."),
            _fee("FR-E803", amount="15.00", active=False, description="Withdrawn twin."),
            _fee("FR-E804", amount="15.00", currency="EUR", merchant=None, description="EUR fee."),
            _fee("FR-E805", amount="15.00", processor=OTHER_PROCESSOR, merchant=None,
                 description="Other processor fee."),
            _fee("FR-E806", amount="99.00", description="Large unrelated fee."),
        ],
        expected_classification=T.PROCESSOR_FEE,
        expected_evidence=(
            ExpectedEvidence(source_type=S.FEE_RULE, reference="FR-E802",
                             why="The only rule active, in-currency and on the right processor."),
        ),
        required_tools=frozenset({"get_settlements", "get_fee_rules"}),
        optional_tools=frozenset({"get_transaction", "search_policy_documents"}),
        should_escalate=False,
        rationale=(
            "Four of five rules match on amount alone. Only one satisfies active, currency, "
            "processor and merchant together. Amount-matching alone picks a wrong rule."
        ),
        difficulty=Difficulty.ADVERSARIAL,
        offline=_script(T.PROCESSOR_FEE, 0.88, _cite(("FEE_RULE", "FR-E802"))),
    ))




    add(Scenario(
        scenario_id="error-settlement-for-other-merchant-amount",
        title="Settlement amount matches a different merchant's fee pattern",
        description="Difference 7.00; the only 7.00 rule names another merchant.",
        exception_type=ExceptionType.AMOUNT_MISMATCH,
        transaction=_transaction("TX-E8006", expected="207.00"),
        settlements=[_settlement("SET-E8007", "TX-E8006", settled="200.00")],
        fee_rules=[
            _fee("FR-E809", amount="7.00", merchant="MERCHANT-EVAL-OTHER",
                 description="Processing fee for a different merchant."),
        ],
        expected_classification=T.UNKNOWN,
        acceptable_classifications=frozenset({T.INSUFFICIENT_EVIDENCE}),
        expected_evidence=(),
        required_tools=frozenset({"get_settlements", "get_fee_rules"}),
        optional_tools=frozenset({"get_transaction", "search_policy_documents"}),
        should_escalate=True,
        rationale=(
            "ADVERSARIAL: a merchant-scoped rule for a different merchant does not apply here, "
            "however exactly the amount matches."
        ),
        difficulty=Difficulty.ADVERSARIAL,
        offline=_script(T.UNKNOWN, 0.35, ()),
    ))



    add(Scenario(
        scenario_id="insufficient-policy-only-evidence",
        title="Policy is relevant but no financial evidence explains the gap",
        description="A 33.00 difference where policy describes fees but no rule exists.",
        exception_type=ExceptionType.AMOUNT_MISMATCH,
        transaction=_transaction("TX-E8009", expected="433.00"),
        settlements=[_settlement("SET-E8010", "TX-E8009", settled="400.00")],
        fee_rules=[],
        expected_classification=T.INSUFFICIENT_EVIDENCE,
        acceptable_classifications=frozenset({T.UNKNOWN}),
        expected_evidence=(),
        required_tools=frozenset({"get_settlements", "get_fee_rules"}),
        optional_tools=frozenset({"get_transaction", "search_policy_documents"}),
        should_escalate=True,
        rationale=(
            "Policy describing how fees work is not evidence that a fee was charged. Citing "
            "policy alone to conclude PROCESSOR_FEE would be unsupported."
        ),
        difficulty=Difficulty.MODERATE,
        offline=_script(T.INSUFFICIENT_EVIDENCE, 0.2, (),
                        tools=("get_settlements", "get_fee_rules", "search_policy_documents")),
    ))

    return scenarios


SCENARIOS: list[Scenario] = _build()
