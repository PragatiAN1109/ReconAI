"""The controlled read-only interface to the Financial Core.

This is the *only* way the Investigation Service reaches authoritative financial
data. It is deliberately narrow: two named operations, both GET, no generic
request method and no arbitrary-URL escape hatch. A future investigation agent
gets this object's public methods as its allowlist, so anything added here
becomes a capability the agent has.

The financial core remains the system of record. Nothing in this module writes,
and there is no method through which it could.
"""

import logging
from decimal import Decimal
from types import TracebackType

import httpx
from pydantic import ValidationError

from app.config import Settings
from app.evidence_models import SettlementEvidence, TransactionEvidence, TransactionSettlements

logger = logging.getLogger(__name__)


class FinancialCoreError(RuntimeError):
    """Base for every failure at the evidence boundary.

    Four subclasses, because an investigation has to tell them apart: a
    transaction that does not exist is a finding, while a financial core that
    cannot be reached is an outage. Collapsing either into empty evidence would
    let an investigation draw conclusions from an absence it never verified.
    """


class FinancialCoreNotFound(FinancialCoreError):
    """The financial core says the resource does not exist."""


class FinancialCoreUnavailable(FinancialCoreError):
    """The financial core could not be reached, or returned a server error."""


class FinancialCoreTimeout(FinancialCoreError):
    """The financial core did not answer in time."""


class FinancialCoreContractError(FinancialCoreError):
    """The response did not match the contract this service expects.

    Raised for unparseable bodies and for schema drift — a missing field, an
    unknown enum value, or an unexpected one. Treated as an error rather than
    tolerated, because both sides of this contract are owned here.
    """


class FinancialCoreClient:
    """Retrieves financial evidence over HTTP.

    The public surface is exactly two operations:

    * :meth:`get_transaction`
    * :meth:`get_settlements`

    There is no ``request(method, path)``, no ``fetch_url`` and no write
    operation. That is the safety boundary, not an oversight: a generic method
    here would hand a future agent the ability to reach anything the financial
    core exposes, including the endpoints that create and reconcile records.

    One client is shared for the life of the application so connections are
    pooled rather than reopened per call.
    """

    def __init__(self, settings: Settings) -> None:
        self._base_url = settings.financial_core_base_url
        self._timeout = settings.financial_core_timeout_seconds
        self._client: httpx.AsyncClient | None = None

    # -- lifecycle ---------------------------------------------------------

    async def open(self) -> None:
        """Create the connection pool. Does not contact the financial core.

        Deliberately no reachability check: the financial core is needed when
        evidence is retrieved, not continuously, so an outage at startup should
        surface at the call that needs it rather than delay or block startup.
        """
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout,
        )
        logger.info(
            "Financial Core client ready [base_url=%s timeout=%ss]",
            self._base_url,
            self._timeout,
        )

    async def close(self) -> None:
        """Release the connection pool. Safe whether or not it was opened."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
            logger.info("Financial Core client closed")

    async def __aenter__(self) -> "FinancialCoreClient":
        await self.open()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()

    # -- controlled evidence operations ------------------------------------

    async def get_transaction(self, transaction_id: str) -> TransactionEvidence:
        """Retrieve one transaction.

        :raises FinancialCoreNotFound: no such transaction. This is meaningful
            during an investigation and is never softened into ``None``.
        :raises FinancialCoreUnavailable: the financial core could not be reached
        :raises FinancialCoreTimeout: the financial core did not answer in time
        :raises FinancialCoreContractError: the response did not match the contract
        """
        payload = await self._get(
            f"/api/v1/transactions/{transaction_id}",
            resource=f"Transaction {transaction_id}",
        )
        return self._validate(payload, TransactionEvidence, resource=f"Transaction {transaction_id}")

    async def get_settlements(self, transaction_id: str) -> list[SettlementEvidence]:
        """Retrieve every settlement recorded against one transaction.

        Plural, and empty is a legitimate answer. A transaction with no
        settlements is exactly the MISSING_SETTLEMENT case, and one with several
        is the DUPLICATE_SETTLEMENT case; both are lost if this is modelled as a
        single optional settlement.

        An unknown transaction still raises :class:`FinancialCoreNotFound` — "no
        settlements" and "no such transaction" are different facts.
        """
        payload = await self._get(
            f"/api/v1/transactions/{transaction_id}/settlements",
            resource=f"Settlements for transaction {transaction_id}",
        )
        response = self._validate(
            payload,
            TransactionSettlements,
            resource=f"Settlements for transaction {transaction_id}",
        )
        return list(response.settlements)

    # -- internals ---------------------------------------------------------

    async def _get(self, path: str, *, resource: str) -> object:
        """Perform one GET and turn transport and status failures into domain errors.

        Private on purpose. Exposing it would be the generic HTTP tool this
        design exists to avoid.
        """
        if self._client is None:
            raise FinancialCoreUnavailable("Financial Core client is not open")

        try:
            response = await self._client.get(path)
        except httpx.TimeoutException as error:
            raise FinancialCoreTimeout(
                f"{resource} could not be retrieved: the Financial Core timed out "
                f"after {self._timeout}s"
            ) from error
        except httpx.HTTPError as error:
            # Covers connection refusal, DNS failure, protocol errors — anything
            # that stopped a response from arriving.
            raise FinancialCoreUnavailable(
                f"{resource} could not be retrieved: the Financial Core is unreachable"
            ) from error

        if response.status_code == httpx.codes.NOT_FOUND:
            raise FinancialCoreNotFound(f"{resource} was not found in the Financial Core")

        if response.status_code >= httpx.codes.INTERNAL_SERVER_ERROR:
            raise FinancialCoreUnavailable(
                f"{resource} could not be retrieved: the Financial Core returned "
                f"{response.status_code}"
            )

        if response.status_code != httpx.codes.OK:
            raise FinancialCoreContractError(
                f"{resource} could not be retrieved: unexpected status "
                f"{response.status_code} from the Financial Core"
            )

        try:
            # parse_float=Decimal is load-bearing. Without it json would produce
            # binary floats and 1247.50 would already have lost exactness before
            # Pydantic ever saw it.
            return response.json(parse_float=Decimal)
        except ValueError as error:
            raise FinancialCoreContractError(
                f"{resource} could not be retrieved: the Financial Core returned "
                f"a body that is not valid JSON"
            ) from error

    @staticmethod
    def _validate[T](payload: object, model: type[T], *, resource: str) -> T:
        try:
            return model.model_validate(payload)  # type: ignore[attr-defined]
        except ValidationError as error:
            problems = [
                {"field": ".".join(str(part) for part in item["loc"]), "problem": item["msg"]}
                for item in error.errors()
            ]
            logger.error(
                "Financial Core response did not match the expected contract "
                "[resource=%s problems=%s]",
                resource,
                problems,
            )
            raise FinancialCoreContractError(
                f"{resource} could not be retrieved: the Financial Core response did "
                f"not match the expected contract"
            ) from error
