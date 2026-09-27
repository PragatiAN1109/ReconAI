"""Serving one scenario's evidence to the real production clients.

The point of this module is what it *doesn't* do: it does not reimplement
evidence retrieval. A scenario's payloads are served over an in-memory HTTP
transport to the genuine ``FinancialCoreClient``, so the production path runs
end to end — URL construction, query parameters, status handling, strict
response validation into typed evidence models, and the ledger recording that
grounding later depends on.

A hand-written stub returning `TransactionEvidence` objects directly would skip
all of that, and would therefore measure a system nobody runs.

Fee-rule filtering is reimplemented here, and only here, because it lives in
Spring rather than in Python. It mirrors the documented contract: omitted
filters do not constrain, and a merchant filter also returns rules that name no
merchant, since those apply to every merchant on the processor.
"""

import json
from typing import Any

import httpx

from app.config import Settings
from app.financial_core_client import FinancialCoreClient
from app.policy_search import PolicySearch

from evals.scenario import Scenario


def _match(rule: dict[str, Any], params: httpx.QueryParams) -> bool:
    """Mirror the Financial Core's documented fee-rule filter semantics."""
    merchant = params.get("merchantId")
    if merchant is not None:
        # A merchant filter also returns processor-wide rules: those apply to
        # every merchant, so excluding them would hide applicable evidence.
        if rule.get("merchantId") not in (merchant, None):
            return False
    processor = params.get("processor")
    if processor is not None and rule.get("processor") != processor:
        return False
    currency = params.get("currency")
    if currency is not None and rule.get("currency") != currency:
        return False
    active = params.get("active")
    if active is not None and rule.get("active") is not (active.lower() == "true"):
        return False
    return True


def financial_core_for(scenario: Scenario, settings: Settings) -> FinancialCoreClient:
    """A real client whose transport answers from this scenario.

    Unknown identifiers return 404 rather than a fabricated record, so a model
    that guesses an identifier gets the same "no such thing" the real system
    would give it.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path

        def ok(payload: Any) -> httpx.Response:
            return httpx.Response(
                200,
                content=json.dumps(payload),
                headers={"content-type": "application/json"},
            )

        if path == "/api/v1/fee-rules":
            items = [r for r in scenario.fee_rules if _match(r, request.url.params)]
            return ok({"items": items, "total": len(items)})

        if path.endswith("/settlements"):
            requested = path.split("/")[-2]
            if requested != scenario.transaction["transactionId"]:
                return httpx.Response(404, json={"message": f"Unknown transaction {requested}"})
            return ok({"transactionId": requested, "settlements": scenario.settlements})

        if path.startswith("/api/v1/transactions/"):
            requested = path.rsplit("/", 1)[-1]
            if requested != scenario.transaction["transactionId"]:
                return httpx.Response(404, json={"message": f"Unknown transaction {requested}"})
            return ok(scenario.transaction)

        return httpx.Response(404, json={"message": f"No such endpoint {path}"})

    client = FinancialCoreClient(settings)
    # The one seam: the transport. Everything above it is production code.
    #
    # Returned already open. Callers must NOT call `open()` on it — that builds
    # a real pool and would silently replace this transport, pointing evaluation
    # traffic at whatever is listening on the configured Financial Core port.
    # An eval that quietly reads production data is worse than one that fails.
    client._client = httpx.AsyncClient(  # noqa: SLF001
        base_url="http://financial-core.eval", transport=httpx.MockTransport(handler)
    )
    return client


def policy_search_for(settings: Settings) -> PolicySearch:
    """The real policy corpus, unmodified.

    Policies are the same for every scenario because they are the same in
    production: a corpus tailored per case would measure the tailoring.
    """
    return PolicySearch(settings.policy_corpus_path)
