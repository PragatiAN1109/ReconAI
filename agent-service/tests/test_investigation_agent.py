"""Tests for the investigation workflow.

No provider, no network, no API key. The Financial Core is faked with httpx's
MockTransport and the model with a scripted fake, so what is under test is the
workflow: the allowlist, the bounded loop, the evidence ledger and grounding
validation.
"""

import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.events import ExceptionType
from app.evidence_ledger import EvidenceLedger
from app.financial_core_client import FinancialCoreClient
from app.investigation_agent import (
    DEFAULT_MAX_TOOL_ROUNDS,
    InvestigationAgent,
    InvestigationFailed,
    UngroundedResultError,
)
from app.investigation_model import AssistantTurn, ToolCall, ToolResults
from app.investigation_models import (
    EvidenceReference,
    EvidenceSource,
    InvestigationContext,
    RootCauseClassification,
)
from app.investigation_tools import ALLOWED_TOOLS
from app.policy_search import PolicySearch
from tests.fake_model import FakeModel, final_turn, tool_turn

CONTEXT = InvestigationContext(
    investigationId="INV-1001",
    exceptionId="EX-1008",
    transactionId="TX-10009",
    exceptionType=ExceptionType.AMOUNT_MISMATCH,
)

TRANSACTION = {
    "transactionId": "TX-10009",
    "merchantId": "MERCHANT-PHASE43-DEMO",
    "amount": 2500.00,
    "expectedSettlementAmount": 2500.00,
    "currency": "USD",
    "transactionType": "PURCHASE",
    "status": "POSTED",
    "createdAt": "2026-09-27T02:49:59.479010Z",
    "transactionTimestamp": "2026-09-27T11:00:00Z",
}
SETTLEMENTS = {
    "transactionId": "TX-10009",
    "settlements": [
        {
            "settlementId": "SET-8008",
            "transactionId": "TX-10009",
            "processor": "NORTHSTAR_PAYMENTS",
            "settledAmount": 2450.00,
            "currency": "USD",
            "status": "COMPLETED",
            "settlementTimestamp": "2026-09-27T11:30:00Z",
        }
    ],
}
FEE_RULES = {
    "items": [
        {
            "ruleId": "FR-14",
            "merchantId": "MERCHANT-PHASE43-DEMO",
            "processor": "NORTHSTAR_PAYMENTS",
            "feeType": "PROCESSING",
            "feeAmount": 50.00,
            "currency": "USD",
            "description": "Cross-network settlement processing fee.",
            "active": True,
        }
    ],
    "total": 1,
}
EMPTY_FEE_RULES = {"items": [], "total": 0}

POLICY_CORPUS = {
    "fees.md": """---
document_id: POL-FEE-001
title: Merchant Fee Schedule
---

# Merchant Fee Schedule

## Cross-Network Settlement Fees

A cross-network settlement processing fee is deducted from the settled amount.
""",
}


def financial_core(handler: Callable[[httpx.Request], httpx.Response]) -> FinancialCoreClient:
    core = FinancialCoreClient(Settings(_env_file=None))
    core._client = httpx.AsyncClient(  # noqa: SLF001
        base_url="http://financial-core.test", transport=httpx.MockTransport(handler)
    )
    return core


def routing_core(
    *, fee_rules: dict | None = None, fail: bool = False
) -> FinancialCoreClient:
    """A Financial Core answering the three read endpoints from fixtures."""

    def handler(request: httpx.Request) -> httpx.Response:
        if fail:
            raise httpx.ConnectError("refused", request=request)
        path = request.url.path
        if path.endswith("/settlements"):
            payload = SETTLEMENTS
        elif path == "/api/v1/fee-rules":
            payload = FEE_RULES if fee_rules is None else fee_rules
        else:
            payload = TRANSACTION
        return httpx.Response(
            200, content=json.dumps(payload), headers={"content-type": "application/json"}
        )

    return financial_core(handler)


@pytest.fixture
def policies(tmp_path: Path) -> PolicySearch:
    for name, content in POLICY_CORPUS.items():
        (tmp_path / name).write_text(content, encoding="utf-8")
    return PolicySearch(tmp_path)


def agent(model: FakeModel, policies: PolicySearch, **kwargs) -> InvestigationAgent:
    return InvestigationAgent(model, routing_core(**kwargs), policies)


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


async def test_the_model_can_gather_evidence_then_conclude(policies: PolicySearch) -> None:
    model = FakeModel(
        [
            tool_turn("get_transaction", {"transaction_id": "TX-10009"}),
            tool_turn("get_settlements", {"transaction_id": "TX-10009"}),
            tool_turn(
                "get_fee_rules",
                {
                    "merchant_id": "MERCHANT-PHASE43-DEMO",
                    "processor": "NORTHSTAR_PAYMENTS",
                    "currency": "USD",
                    "active": True,
                },
            ),
            tool_turn("search_policy_documents", {"query": "settlement processing fee"}),
            final_turn(
                evidence=[
                    {"sourceType": "TRANSACTION", "reference": "TX-10009"},
                    {"sourceType": "SETTLEMENT", "reference": "SET-8008"},
                    {"sourceType": "FEE_RULE", "reference": "FR-14"},
                    {
                        "sourceType": "POLICY_DOCUMENT",
                        "reference": "POL-FEE-001",
                        "section": "Cross-Network Settlement Fees",
                    },
                ]
            ),
        ]
    )

    result, ledger = await agent(model, policies).investigate(CONTEXT)

    assert result.classification is RootCauseClassification.PROCESSOR_FEE
    assert result.requires_human_approval is True
    assert ledger.transactions == {"TX-10009"}
    assert ledger.settlements == {"SET-8008"}
    assert ledger.fee_rules == {"FR-14"}
    assert ledger.policy_documents == {"POL-FEE-001"}


async def test_the_model_receives_the_context_prompt_and_allowlist(
    policies: PolicySearch,
) -> None:
    model = FakeModel([final_turn(evidence=[])])

    await agent(model, policies).investigate(CONTEXT)

    assert "INSUFFICIENT_EVIDENCE" in model.seen_system_prompts[0]
    assert {spec["name"] for spec in model.seen_tools[0]} == ALLOWED_TOOLS


async def test_tool_results_reach_the_model_in_a_serialisable_form(
    policies: PolicySearch,
) -> None:
    model = FakeModel(
        [tool_turn("get_transaction", {"transaction_id": "TX-10009"}), final_turn(evidence=[])]
    )

    await agent(model, policies).investigate(CONTEXT)

    returned = model.seen_transcripts[-1]
    tool_results = [entry for entry in returned if isinstance(entry, ToolResults)]
    payload = json.loads(tool_results[0].results[0].content)
    assert payload["transactionId"] == "TX-10009"
    assert tool_results[0].results[0].failed is False


async def test_several_tools_may_be_requested_in_one_turn(policies: PolicySearch) -> None:
    model = FakeModel(
        [
            AssistantTurn(
                tool_calls=(
                    ToolCall("a", "get_transaction", {"transaction_id": "TX-10009"}),
                    ToolCall("b", "get_settlements", {"transaction_id": "TX-10009"}),
                )
            ),
            final_turn(evidence=[{"sourceType": "SETTLEMENT", "reference": "SET-8008"}]),
        ]
    )

    _, ledger = await agent(model, policies).investigate(CONTEXT)

    assert ledger.transactions == {"TX-10009"}
    assert ledger.settlements == {"SET-8008"}


# ---------------------------------------------------------------------------
# The tool allowlist
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("get_transaction", {"transaction_id": "TX-10009"}),
        ("get_settlements", {"transaction_id": "TX-10009"}),
        ("get_fee_rules", {"processor": "NORTHSTAR_PAYMENTS"}),
        ("search_policy_documents", {"query": "fee"}),
    ],
)
async def test_every_allowed_tool_can_be_requested(
    policies: PolicySearch, tool: str, arguments: dict
) -> None:
    model = FakeModel([tool_turn(tool, arguments), final_turn(evidence=[])])

    result, _ = await agent(model, policies).investigate(CONTEXT)

    assert result is not None
    assert not [entry for entry in model.seen_transcripts[-1] if _failed(entry)]


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("http_request", {"url": "https://example.com"}),
        ("fetch_url", {"url": "http://169.254.169.254/latest/meta-data/"}),
        ("read_file", {"path": "/etc/passwd"}),
        ("list_directory", {"path": "/"}),
        ("execute_sql", {"query": "SELECT * FROM transactions"}),
        ("run_python", {"code": "import os; os.system('id')"}),
        ("shell", {"command": "cat ~/.aws/credentials"}),
        ("update_transaction", {"transaction_id": "TX-10009", "amount": 0}),
        ("delete_settlement", {"settlement_id": "SET-8008"}),
        ("approve_recommendation", {"id": "REC-1"}),
    ],
)
async def test_a_tool_outside_the_allowlist_is_refused(
    policies: PolicySearch, tool: str, arguments: dict
) -> None:
    """Refused before anything is looked up, and reported back to the model."""
    model = FakeModel([tool_turn(tool, arguments), final_turn(evidence=[])])

    result, ledger = await agent(model, policies).investigate(CONTEXT)

    failures = _failures(model.seen_transcripts[-1])
    assert failures
    assert "Unknown tool" in failures[0].content
    assert ledger.is_empty
    assert result is not None


async def test_the_allowlist_contains_only_the_four_evidence_tools() -> None:
    assert ALLOWED_TOOLS == {
        "get_transaction",
        "get_settlements",
        "get_fee_rules",
        "search_policy_documents",
    }


async def test_malformed_tool_arguments_fail_safely(policies: PolicySearch) -> None:
    model = FakeModel(
        [
            tool_turn("get_transaction", {"wrong_argument": "TX-10009"}),
            final_turn(evidence=[]),
        ]
    )

    result, ledger = await agent(model, policies).investigate(CONTEXT)

    failures = _failures(model.seen_transcripts[-1])
    assert failures and "invalid" in failures[0].content
    assert ledger.is_empty
    assert result is not None


async def test_an_unexpected_tool_argument_is_refused(policies: PolicySearch) -> None:
    """Arguments are strict, so a smuggled extra cannot reach an operation."""
    model = FakeModel(
        [
            tool_turn(
                "get_transaction", {"transaction_id": "TX-10009", "limit": "99; DROP TABLE"}
            ),
            final_turn(evidence=[]),
        ]
    )

    await agent(model, policies).investigate(CONTEXT)

    assert _failures(model.seen_transcripts[-1])


async def test_a_failing_evidence_source_is_reported_not_silently_empty(
    policies: PolicySearch,
) -> None:
    """An outage must not look like an absence of facts."""
    model = FakeModel(
        [tool_turn("get_transaction", {"transaction_id": "TX-10009"}), final_turn(evidence=[])]
    )

    result, ledger = await agent(model, policies, fail=True).investigate(CONTEXT)

    failures = _failures(model.seen_transcripts[-1])
    assert failures and "could not be completed" in failures[0].content
    assert ledger.is_empty
    assert result is not None


# ---------------------------------------------------------------------------
# Grounding
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "reference"),
    [
        ("SETTLEMENT", "SET-9999"),
        ("FEE_RULE", "FR-999"),
        ("POLICY_DOCUMENT", "POL-INVENTED-001"),
        ("TRANSACTION", "TX-00000"),
    ],
)
async def test_a_hallucinated_citation_is_rejected(
    policies: PolicySearch, source: str, reference: str
) -> None:
    model = FakeModel(
        [
            tool_turn("get_transaction", {"transaction_id": "TX-10009"}),
            final_turn(evidence=[{"sourceType": source, "reference": reference}]),
        ]
    )

    with pytest.raises(UngroundedResultError) as failure:
        await agent(model, policies).investigate(CONTEXT)

    assert reference in str(failure.value)


async def test_a_citation_from_a_tool_that_was_never_called_is_rejected(
    policies: PolicySearch,
) -> None:
    """FR-14 exists, but this investigation never asked for fee rules."""
    model = FakeModel(
        [
            tool_turn("get_transaction", {"transaction_id": "TX-10009"}),
            final_turn(evidence=[{"sourceType": "FEE_RULE", "reference": "FR-14"}]),
        ]
    )

    with pytest.raises(UngroundedResultError):
        await agent(model, policies).investigate(CONTEXT)


async def test_a_real_document_with_an_invented_section_is_rejected(
    policies: PolicySearch,
) -> None:
    """Right document, wrong section, is still a claim about unread text."""
    model = FakeModel(
        [
            tool_turn("search_policy_documents", {"query": "fee"}),
            final_turn(
                evidence=[
                    {
                        "sourceType": "POLICY_DOCUMENT",
                        "reference": "POL-FEE-001",
                        "section": "Section 9.4 Invented",
                    }
                ]
            ),
        ]
    )

    with pytest.raises(UngroundedResultError):
        await agent(model, policies).investigate(CONTEXT)


async def test_one_bad_citation_invalidates_an_otherwise_grounded_result(
    policies: PolicySearch,
) -> None:
    model = FakeModel(
        [
            tool_turn("get_settlements", {"transaction_id": "TX-10009"}),
            final_turn(
                evidence=[
                    {"sourceType": "SETTLEMENT", "reference": "SET-8008"},
                    {"sourceType": "FEE_RULE", "reference": "FR-999"},
                ]
            ),
        ]
    )

    with pytest.raises(UngroundedResultError) as failure:
        await agent(model, policies).investigate(CONTEXT)

    assert "FR-999" in str(failure.value)
    assert "SET-8008" not in str(failure.value)


async def test_a_result_citing_nothing_is_accepted(policies: PolicySearch) -> None:
    """Citing nothing is not a grounding failure; it is a weak conclusion."""
    model = FakeModel([final_turn(classification="UNKNOWN", evidence=[])])

    result, _ = await agent(model, policies).investigate(CONTEXT)

    assert result.evidence == []


async def test_the_ledger_records_only_what_tools_actually_returned(
    policies: PolicySearch,
) -> None:
    model = FakeModel(
        [
            tool_turn("get_fee_rules", {"processor": "NOBODY"}),
            final_turn(evidence=[]),
        ]
    )

    _, ledger = await agent(model, policies, fee_rules=EMPTY_FEE_RULES).investigate(CONTEXT)

    assert ledger.fee_rules == set()
    assert ledger.is_empty


# ---------------------------------------------------------------------------
# Insufficient evidence
# ---------------------------------------------------------------------------


async def test_insufficient_evidence_is_a_successful_outcome(policies: PolicySearch) -> None:
    model = FakeModel(
        [
            tool_turn("get_transaction", {"transaction_id": "TX-10009"}),
            final_turn(
                classification="INSUFFICIENT_EVIDENCE",
                rootCause="No fee rule or policy accounts for the difference.",
                confidence=0.2,
                evidence=[{"sourceType": "TRANSACTION", "reference": "TX-10009"}],
                recommendedAction="Escalate for manual review.",
            ),
        ]
    )

    result, _ = await agent(model, policies).investigate(CONTEXT)

    assert result.classification is RootCauseClassification.INSUFFICIENT_EVIDENCE
    assert result.requires_human_approval is True


async def test_no_matching_fee_rule_does_not_force_a_processor_fee_conclusion(
    policies: PolicySearch,
) -> None:
    model = FakeModel(
        [
            tool_turn("get_settlements", {"transaction_id": "TX-10009"}),
            tool_turn("get_fee_rules", {"processor": "NORTHSTAR_PAYMENTS"}),
            final_turn(
                classification="INSUFFICIENT_EVIDENCE",
                rootCause="No active fee rule matches this processor.",
                confidence=0.15,
                evidence=[{"sourceType": "SETTLEMENT", "reference": "SET-8008"}],
                recommendedAction="Escalate for manual review.",
            ),
        ]
    )

    result, ledger = await agent(model, policies, fee_rules=EMPTY_FEE_RULES).investigate(CONTEXT)

    assert result.classification is RootCauseClassification.INSUFFICIENT_EVIDENCE
    assert ledger.fee_rules == set()


async def test_an_empty_policy_search_cannot_be_cited(policies: PolicySearch) -> None:
    model = FakeModel(
        [
            tool_turn("search_policy_documents", {"query": "cryptocurrency custody"}),
            final_turn(evidence=[{"sourceType": "POLICY_DOCUMENT", "reference": "POL-FEE-001"}]),
        ]
    )

    with pytest.raises(UngroundedResultError):
        await agent(model, policies).investigate(CONTEXT)


# ---------------------------------------------------------------------------
# Result validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "payload"),
    [
        ("unknown classification", {"classification": "PROCESSOR_GREED"}),
        ("confidence below zero", {"confidence": -0.1}),
        ("confidence above one", {"confidence": 1.5}),
        ("approval waived", {"requiresHumanApproval": False}),
        ("empty root cause", {"rootCause": ""}),
        ("unknown evidence source", {"evidence": [{"sourceType": "RUMOUR", "reference": "X"}]}),
        ("evidence missing a reference", {"evidence": [{"sourceType": "SETTLEMENT"}]}),
        ("unexpected field", {"shouldEscalate": True}),
    ],
)
async def test_a_malformed_result_is_rejected(
    policies: PolicySearch, name: str, payload: dict
) -> None:
    model = FakeModel([final_turn(**payload)])

    with pytest.raises(InvestigationFailed):
        await agent(model, policies).investigate(CONTEXT)


async def test_a_turn_with_neither_tools_nor_a_result_fails(policies: PolicySearch) -> None:
    model = FakeModel([AssistantTurn()])

    with pytest.raises(InvestigationFailed):
        await agent(model, policies).investigate(CONTEXT)


# ---------------------------------------------------------------------------
# The bounded loop
# ---------------------------------------------------------------------------


async def test_the_loop_stops_at_the_round_limit(policies: PolicySearch) -> None:
    """The fake never concludes, which is exactly what the bound is for."""
    model = FakeModel([])

    with pytest.raises(InvestigationFailed) as failure:
        await InvestigationAgent(
            model, routing_core(), policies, max_tool_rounds=3
        ).investigate(CONTEXT)

    assert "did not reach a conclusion" in str(failure.value)
    assert model.calls == 3


async def test_exhausting_the_budget_fabricates_no_result(policies: PolicySearch) -> None:
    model = FakeModel([])

    with pytest.raises(InvestigationFailed):
        await InvestigationAgent(
            model, routing_core(), policies, max_tool_rounds=2
        ).investigate(CONTEXT)


async def test_a_conclusion_on_the_final_permitted_round_is_accepted(
    policies: PolicySearch,
) -> None:
    model = FakeModel(
        [tool_turn("get_transaction", {"transaction_id": "TX-10009"}), final_turn(evidence=[])]
    )

    result, _ = await InvestigationAgent(
        model, routing_core(), policies, max_tool_rounds=2
    ).investigate(CONTEXT)

    assert result is not None


async def test_the_default_bound_is_small() -> None:
    assert 1 < DEFAULT_MAX_TOOL_ROUNDS <= 20


# ---------------------------------------------------------------------------
# Financial safety
# ---------------------------------------------------------------------------


async def test_no_tool_specification_mentions_a_write_operation() -> None:
    from app.investigation_tools import TOOL_SPECIFICATIONS  # noqa: PLC0415

    rendered = json.dumps(TOOL_SPECIFICATIONS).lower()
    for forbidden in ("post", "put", "patch", "delete", "create", "update", "approve", "resolve"):
        assert forbidden not in json.dumps(
            [spec["name"] for spec in TOOL_SPECIFICATIONS]
        ).lower(), forbidden
    assert "url" not in rendered
    assert "sql" not in rendered
    assert "path" not in rendered


async def test_the_result_carries_no_mechanism_to_act_on_itself(
    policies: PolicySearch,
) -> None:
    model = FakeModel([final_turn(evidence=[])])

    result, _ = await agent(model, policies).investigate(CONTEXT)

    for method in ("execute", "apply", "approve", "resolve", "commit", "save"):
        assert not hasattr(result, method)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _failures(transcript) -> list:
    return [result for entry in transcript if isinstance(entry, ToolResults)
            for result in entry.results if result.failed]


def _failed(entry) -> bool:
    return isinstance(entry, ToolResults) and any(result.failed for result in entry.results)


def test_the_ledger_distinguishes_sections_of_one_document() -> None:
    ledger = EvidenceLedger()
    ledger.policy_documents.add("POL-FEE-001")
    ledger.policy_sections.add(("POL-FEE-001", "Cross-Network Settlement Fees"))

    real = EvidenceReference(
        sourceType=EvidenceSource.POLICY_DOCUMENT,
        reference="POL-FEE-001",
        section="Cross-Network Settlement Fees",
    )
    invented = EvidenceReference(
        sourceType=EvidenceSource.POLICY_DOCUMENT,
        reference="POL-FEE-001",
        section="Never Retrieved",
    )

    assert ledger.supports(real)
    assert not ledger.supports(invented)
