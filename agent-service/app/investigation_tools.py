"""The four things an investigation is allowed to do.

This module is the boundary between a language model and everything else. The
model may *request* one of these operations; the application decides whether the
request is allowed, validates the arguments, executes it, and records what came
back.

What is deliberately absent matters more than what is here. There is no generic
HTTP request, no SQL, no filesystem read, no shell, no code execution and no URL
parameter anywhere in these schemas. A model cannot ask for something that is
not on this list, and asking for something unrecognised is refused rather than
improvised.
"""

import json
import logging
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.evidence_ledger import EvidenceLedger
from app.financial_core_client import FinancialCoreClient, FinancialCoreError
from app.policy_search import PolicySearch

logger = logging.getLogger(__name__)

GET_TRANSACTION = "get_transaction"
GET_SETTLEMENTS = "get_settlements"
GET_FEE_RULES = "get_fee_rules"
SEARCH_POLICY_DOCUMENTS = "search_policy_documents"

#: The complete allowlist. Membership here is the capability.
ALLOWED_TOOLS = frozenset(
    {GET_TRANSACTION, GET_SETTLEMENTS, GET_FEE_RULES, SEARCH_POLICY_DOCUMENTS}
)


class ToolError(RuntimeError):
    """A tool request could not be honoured.

    Carried back to the model as a result rather than raised out of the loop: a
    model that asked for something impossible should learn that and try
    something else, not crash the investigation.
    """


# -- Argument schemas ------------------------------------------------------
#
# Every tool's arguments are a strict model. This is what stops a plausible-
# looking request from turning into an unintended operation.


class _GetTransactionArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transaction_id: str = Field(min_length=1, max_length=50)


class _GetSettlementsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transaction_id: str = Field(min_length=1, max_length=50)


class _GetFeeRulesArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    merchant_id: str | None = Field(default=None, max_length=50)
    processor: str | None = Field(default=None, max_length=100)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    active: bool | None = None


class _SearchPolicyArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=500)


#: Descriptions handed to the model. Deliberately factual: what the tool
#: returns, not how to reason about it.
TOOL_SPECIFICATIONS: tuple[dict[str, Any], ...] = (
    {
        "name": GET_TRANSACTION,
        "description": (
            "Retrieve one authoritative transaction by its business identifier "
            "(for example TX-10009). Returns the merchant, the original amount, "
            "the amount expected to settle, currency, type and status."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "transaction_id": {"type": "string", "description": "e.g. TX-10009"}
            },
            "required": ["transaction_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": GET_SETTLEMENTS,
        "description": (
            "Retrieve every settlement recorded against a transaction. A "
            "transaction may have none, one, or several; an empty list is a "
            "legitimate answer and means nothing was settled."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "transaction_id": {"type": "string", "description": "e.g. TX-10009"}
            },
            "required": ["transaction_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": GET_FEE_RULES,
        "description": (
            "Retrieve fee configuration. All filters are optional. A merchant "
            "filter also returns rules that apply to every merchant on the "
            "processor. A fee rule states that a fee of that shape exists; it "
            "does not state that any particular transaction was charged one."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "merchant_id": {"type": "string"},
                "processor": {"type": "string"},
                "currency": {"type": "string", "description": "ISO 4217, e.g. USD"},
                "active": {"type": "boolean"},
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "name": SEARCH_POLICY_DOCUMENTS,
        "description": (
            "Search the operational policy corpus. Returns excerpts with the "
            "document identifier and section they came from, which are what a "
            "citation must reference."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    },
)


class InvestigationTools:
    """Executes allowed tool requests and records what they returned.

    Holds the already-narrow clients from earlier phases — the Financial Core
    client exposes only reads, and policy search exposes only a query. This
    class narrows further still, to four named operations with validated
    arguments.
    """

    def __init__(
        self,
        financial_core: FinancialCoreClient,
        policies: PolicySearch,
        ledger: EvidenceLedger,
    ) -> None:
        self._financial_core = financial_core
        self._policies = policies
        self._ledger = ledger

    async def execute(self, tool: str, arguments: Mapping[str, Any]) -> str:
        """Run one tool request and return its result as JSON for the model.

        :raises ToolError: the tool is not allowed, the arguments do not match
            its schema, or the underlying operation failed
        """
        if tool not in ALLOWED_TOOLS:
            # Refused before anything is looked up. The name is logged so an
            # attempt at something unavailable is visible.
            logger.warning("Refusing unknown tool request [tool=%s]", tool)
            raise ToolError(
                f"Unknown tool {tool!r}. Available tools: {', '.join(sorted(ALLOWED_TOOLS))}."
            )

        try:
            match tool:
                case "get_transaction":
                    return await self._get_transaction(arguments)
                case "get_settlements":
                    return await self._get_settlements(arguments)
                case "get_fee_rules":
                    return await self._get_fee_rules(arguments)
                case _:
                    return self._search_policy_documents(arguments)
        except ValidationError as error:
            raise ToolError(
                f"Arguments for {tool} are invalid: "
                + "; ".join(
                    f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
                    for item in error.errors()
                )
            ) from error
        except FinancialCoreError as error:
            # The evidence source is unreachable or disagrees with us. Reported
            # to the model as a failed tool, not as a silent empty result: an
            # investigation must not mistake an outage for an absence of facts.
            raise ToolError(f"{tool} could not be completed: {error}") from error

    async def _get_transaction(self, arguments: Mapping[str, Any]) -> str:
        parsed = _GetTransactionArgs.model_validate(dict(arguments))
        transaction = await self._financial_core.get_transaction(parsed.transaction_id)
        self._ledger.record_transaction(transaction)
        return transaction.model_dump_json(by_alias=True)

    async def _get_settlements(self, arguments: Mapping[str, Any]) -> str:
        parsed = _GetSettlementsArgs.model_validate(dict(arguments))
        settlements = await self._financial_core.get_settlements(parsed.transaction_id)
        self._ledger.record_settlements(settlements)
        return json.dumps(
            {
                "transactionId": parsed.transaction_id,
                "settlements": [
                    json.loads(settlement.model_dump_json(by_alias=True))
                    for settlement in settlements
                ],
            }
        )

    async def _get_fee_rules(self, arguments: Mapping[str, Any]) -> str:
        parsed = _GetFeeRulesArgs.model_validate(dict(arguments))
        rules = await self._financial_core.get_fee_rules(
            merchant_id=parsed.merchant_id,
            processor=parsed.processor,
            currency=parsed.currency,
            active=parsed.active,
        )
        self._ledger.record_fee_rules(rules)
        return json.dumps(
            {"feeRules": [json.loads(rule.model_dump_json(by_alias=True)) for rule in rules]}
        )

    def _search_policy_documents(self, arguments: Mapping[str, Any]) -> str:
        parsed = _SearchPolicyArgs.model_validate(dict(arguments))
        results = self._policies.search(parsed.query)
        self._ledger.record_policies(results)
        return json.dumps(
            {"results": [json.loads(result.model_dump_json()) for result in results]}
        )
