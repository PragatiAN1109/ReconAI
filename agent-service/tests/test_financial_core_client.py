"""Tests for the controlled evidence boundary.

The HTTP boundary is faked with httpx's own ``MockTransport`` — no server, no
Spring, no network, and no mocking framework.
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from app.config import Settings
from app.evidence_models import (
    SettlementEvidence,
    SettlementStatus,
    TransactionEvidence,
    TransactionStatus,
    TransactionType,
)
from app.financial_core_client import (
    FinancialCoreClient,
    FinancialCoreContractError,
    FinancialCoreError,
    FinancialCoreNotFound,
    FinancialCoreTimeout,
    FinancialCoreUnavailable,
)

# Copied from a real response of the running Financial Core.
TRANSACTION_JSON = {
    "transactionId": "TX-10009",
    "merchantId": "MERCHANT-PHASE43-DEMO",
    "amount": 2500.00,
    "expectedSettlementAmount": 2500.00,
    "currency": "USD",
    "transactionType": "PURCHASE",
    "status": "POSTED",
    "transactionTimestamp": "2026-09-27T11:00:00Z",
    "createdAt": "2026-09-27T02:49:59.123456Z",
}

SETTLEMENT_JSON = {
    "settlementId": "SET-8009",
    "transactionId": "TX-10009",
    "processor": "NORTHSTAR_PAYMENTS",
    "settledAmount": 2450.00,
    "currency": "USD",
    "status": "COMPLETED",
    "settlementTimestamp": "2026-09-27T11:30:00Z",
}


def client_with(handler: Callable[[httpx.Request], httpx.Response]) -> FinancialCoreClient:
    """A client whose transport is a function instead of a socket."""
    core = FinancialCoreClient(Settings(_env_file=None))
    core._client = httpx.AsyncClient(  # noqa: SLF001 - substituting the transport
        base_url="http://financial-core.test",
        transport=httpx.MockTransport(handler),
    )
    return core


def responds(payload: object, status_code: int = 200) -> Callable[[httpx.Request], httpx.Response]:
    # json.dumps rather than the json= kwarg, so the numbers stay exactly as
    # written and the client's own Decimal parsing is what is under test.
    return lambda _: httpx.Response(
        status_code, content=json.dumps(payload), headers={"content-type": "application/json"}
    )


# ---------------------------------------------------------------------------
# get_transaction
# ---------------------------------------------------------------------------


async def test_get_transaction_calls_the_documented_endpoint() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=TRANSACTION_JSON)

    await client_with(handler).get_transaction("TX-10009")

    assert seen[0].url.path == "/api/v1/transactions/TX-10009"
    assert seen[0].method == "GET"


async def test_a_transaction_response_maps_to_typed_evidence() -> None:
    evidence = await client_with(responds(TRANSACTION_JSON)).get_transaction("TX-10009")

    assert isinstance(evidence, TransactionEvidence)
    assert evidence.transaction_id == "TX-10009"
    assert evidence.merchant_id == "MERCHANT-PHASE43-DEMO"
    assert evidence.currency == "USD"
    assert evidence.transaction_type is TransactionType.PURCHASE
    assert evidence.status is TransactionStatus.POSTED


async def test_transaction_money_stays_decimal_and_exact() -> None:
    evidence = await client_with(responds(TRANSACTION_JSON)).get_transaction("TX-10009")

    assert isinstance(evidence.amount, Decimal)
    assert isinstance(evidence.expected_settlement_amount, Decimal)
    assert evidence.amount == Decimal("2500.00")


async def test_money_that_a_float_would_corrupt_survives_intact() -> None:
    """1247.50 and 0.1 have no exact binary float representation."""
    payload = {**TRANSACTION_JSON, "amount": 1247.50, "expectedSettlementAmount": 0.1}

    evidence = await client_with(responds(payload)).get_transaction("TX-10009")

    assert evidence.amount == Decimal("1247.50")
    assert evidence.expected_settlement_amount == Decimal("0.1")
    assert str(evidence.expected_settlement_amount) == "0.1"


async def test_transaction_timestamps_are_timezone_aware() -> None:
    evidence = await client_with(responds(TRANSACTION_JSON)).get_transaction("TX-10009")

    assert evidence.transaction_timestamp.tzinfo is not None
    assert evidence.created_at.tzinfo is not None
    assert evidence.transaction_timestamp == datetime(2026, 9, 27, 11, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# get_settlements
# ---------------------------------------------------------------------------


async def test_get_settlements_calls_the_documented_endpoint() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"transactionId": "TX-10009", "settlements": []})

    await client_with(handler).get_settlements("TX-10009")

    assert seen[0].url.path == "/api/v1/transactions/TX-10009/settlements"
    assert seen[0].method == "GET"


async def test_a_transaction_with_no_settlements_returns_an_empty_list() -> None:
    """The MISSING_SETTLEMENT case. Empty is an answer, not a failure."""
    payload = {"transactionId": "TX-10009", "settlements": []}

    settlements = await client_with(responds(payload)).get_settlements("TX-10009")

    assert settlements == []


async def test_a_single_settlement_maps_to_typed_evidence() -> None:
    payload = {"transactionId": "TX-10009", "settlements": [SETTLEMENT_JSON]}

    settlements = await client_with(responds(payload)).get_settlements("TX-10009")

    assert len(settlements) == 1
    settlement = settlements[0]
    assert isinstance(settlement, SettlementEvidence)
    assert settlement.settlement_id == "SET-8009"
    assert settlement.processor == "NORTHSTAR_PAYMENTS"
    assert settlement.status is SettlementStatus.COMPLETED


async def test_several_settlements_are_all_preserved() -> None:
    """The DUPLICATE_SETTLEMENT case. Nothing may collapse this to one."""
    payload = {
        "transactionId": "TX-10009",
        "settlements": [
            SETTLEMENT_JSON,
            {**SETTLEMENT_JSON, "settlementId": "SET-8010"},
            {**SETTLEMENT_JSON, "settlementId": "SET-8011", "status": "FAILED"},
        ],
    }

    settlements = await client_with(responds(payload)).get_settlements("TX-10009")

    assert [s.settlement_id for s in settlements] == ["SET-8009", "SET-8010", "SET-8011"]
    assert settlements[2].status is SettlementStatus.FAILED


async def test_settlement_money_stays_decimal_and_exact() -> None:
    payload = {
        "transactionId": "TX-10009",
        "settlements": [{**SETTLEMENT_JSON, "settledAmount": 1217.50}],
    }

    settlements = await client_with(responds(payload)).get_settlements("TX-10009")

    assert isinstance(settlements[0].settled_amount, Decimal)
    assert settlements[0].settled_amount == Decimal("1217.50")


async def test_settlement_timestamps_are_timezone_aware() -> None:
    payload = {"transactionId": "TX-10009", "settlements": [SETTLEMENT_JSON]}

    settlements = await client_with(responds(payload)).get_settlements("TX-10009")

    assert settlements[0].settlement_timestamp.tzinfo is not None


# ---------------------------------------------------------------------------
# Error semantics
# ---------------------------------------------------------------------------


async def test_a_missing_transaction_raises_not_found_rather_than_returning_none() -> None:
    """A transaction that does not exist is a finding, not an empty result."""
    core = client_with(responds({"status": 404, "error": "NOT_FOUND"}, status_code=404))

    with pytest.raises(FinancialCoreNotFound) as failure:
        await core.get_transaction("TX-NOPE")

    assert "TX-NOPE" in str(failure.value)


async def test_settlements_for_a_missing_transaction_raise_not_found_not_empty() -> None:
    """"No settlements" and "no such transaction" are different facts."""
    core = client_with(responds({"status": 404}, status_code=404))

    with pytest.raises(FinancialCoreNotFound):
        await core.get_settlements("TX-NOPE")


async def test_a_connection_failure_maps_to_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(FinancialCoreUnavailable):
        await client_with(handler).get_transaction("TX-10009")


async def test_a_timeout_maps_to_the_timeout_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    with pytest.raises(FinancialCoreTimeout):
        await client_with(handler).get_transaction("TX-10009")


async def test_a_timeout_is_not_reported_as_unavailable() -> None:
    """Slow and absent are different operational problems."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("too slow", request=request)

    with pytest.raises(FinancialCoreTimeout):
        await client_with(handler).get_settlements("TX-10009")


async def test_a_server_error_maps_to_unavailable() -> None:
    with pytest.raises(FinancialCoreUnavailable):
        await client_with(responds({}, status_code=503)).get_transaction("TX-10009")


async def test_a_body_that_is_not_json_maps_to_a_contract_error() -> None:
    core = client_with(lambda _: httpx.Response(200, content=b"<html>not json</html>"))

    with pytest.raises(FinancialCoreContractError):
        await core.get_transaction("TX-10009")


@pytest.mark.parametrize(
    ("name", "payload"),
    [
        ("missing field", {k: v for k, v in TRANSACTION_JSON.items() if k != "currency"}),
        ("unknown transaction type", {**TRANSACTION_JSON, "transactionType": "GIFT"}),
        ("unknown status", {**TRANSACTION_JSON, "status": "NOT_A_STATUS"}),
        ("naive timestamp", {**TRANSACTION_JSON, "transactionTimestamp": "2026-09-27T11:00:00"}),
        ("wrong shape", {"unexpected": "payload"}),
    ],
)
async def test_a_response_that_breaks_the_contract_maps_to_a_contract_error(
    name: str, payload: dict[str, object]
) -> None:
    with pytest.raises(FinancialCoreContractError):
        await client_with(responds(payload)).get_transaction("TX-10009")


async def test_an_unexpected_field_is_rejected_as_contract_drift() -> None:
    """Both sides of this contract are owned here, so drift must surface."""
    payload = {**TRANSACTION_JSON, "riskScore": 0.9}

    with pytest.raises(FinancialCoreContractError):
        await client_with(responds(payload)).get_transaction("TX-10009")


async def test_a_malformed_settlement_maps_to_a_contract_error() -> None:
    payload = {
        "transactionId": "TX-10009",
        "settlements": [{k: v for k, v in SETTLEMENT_JSON.items() if k != "settledAmount"}],
    }

    with pytest.raises(FinancialCoreContractError):
        await client_with(responds(payload)).get_settlements("TX-10009")


async def test_every_boundary_error_shares_one_base_so_callers_can_catch_broadly() -> None:
    for error in (
        FinancialCoreNotFound,
        FinancialCoreUnavailable,
        FinancialCoreTimeout,
        FinancialCoreContractError,
    ):
        assert issubclass(error, FinancialCoreError)


async def test_using_the_client_before_it_is_open_fails_loudly() -> None:
    core = FinancialCoreClient(Settings(_env_file=None))

    with pytest.raises(FinancialCoreUnavailable):
        await core.get_transaction("TX-10009")


# ---------------------------------------------------------------------------
# The safety boundary
# ---------------------------------------------------------------------------


def test_the_client_exposes_only_the_two_read_operations() -> None:
    """The public surface is the allowlist a future agent would receive."""
    public = {
        name
        for name in dir(FinancialCoreClient)
        if not name.startswith("_") and callable(getattr(FinancialCoreClient, name))
    }

    assert public == {"get_transaction", "get_settlements", "open", "close"}


def test_no_write_operation_is_exposed() -> None:
    forbidden = ("post", "put", "patch", "delete", "create", "update", "save", "reconcile")
    public = {name.lower() for name in dir(FinancialCoreClient) if not name.startswith("_")}

    assert not [name for name in public if any(word in name for word in forbidden)]


def test_no_arbitrary_url_or_generic_request_tool_is_exposed() -> None:
    """A generic method here would hand a future agent the whole API."""
    public = {name.lower() for name in dir(FinancialCoreClient) if not name.startswith("_")}

    assert not public & {"request", "fetch", "fetch_url", "call", "send", "get", "http"}


async def test_only_get_requests_are_ever_issued() -> None:
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if request.url.path.endswith("/settlements"):
            return httpx.Response(200, json={"transactionId": "TX-1", "settlements": []})
        return httpx.Response(200, json=TRANSACTION_JSON)

    core = client_with(handler)
    await core.get_transaction("TX-10009")
    await core.get_settlements("TX-10009")

    assert set(methods) == {"GET"}


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


async def test_opening_the_client_contacts_nothing() -> None:
    """A financial core outage must not affect this service's startup."""
    core = FinancialCoreClient(Settings(_env_file=None, financial_core_base_url="http://127.0.0.1:1"))

    await core.open()
    await core.close()


async def test_the_client_can_be_used_as_a_context_manager() -> None:
    async with FinancialCoreClient(Settings(_env_file=None)) as core:
        assert core._client is not None  # noqa: SLF001

    assert core._client is None  # noqa: SLF001


async def test_closing_twice_is_harmless() -> None:
    core = FinancialCoreClient(Settings(_env_file=None))
    await core.open()
    await core.close()
    await core.close()


async def test_the_configured_base_url_and_timeout_are_applied() -> None:
    settings = Settings(
        _env_file=None,
        financial_core_base_url="http://financial-core.internal:9000",
        financial_core_timeout_seconds=2.5,
    )
    core = FinancialCoreClient(settings)
    await core.open()

    assert str(core._client.base_url) == "http://financial-core.internal:9000"  # noqa: SLF001
    assert core._client.timeout.read == 2.5  # noqa: SLF001
    await core.close()
