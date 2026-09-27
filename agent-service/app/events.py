"""The Kafka contract published by the financial core.

This mirrors the Java record
``com.reconai.exception.ReconciliationExceptionEvent`` exactly. The financial
core owns this contract; this service only reads it.
"""

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class ExceptionType(StrEnum):
    """The deterministic discrepancy types the financial core can detect.

    Mirrors the Java ``ExceptionType`` enum and the database check constraint
    behind it. ``PROCESSOR_FEE`` is deliberately absent from all three: it is a
    root-cause classification that investigation may later propose, never a
    discrepancy the financial core detects.
    """

    MISSING_SETTLEMENT = "MISSING_SETTLEMENT"
    DUPLICATE_SETTLEMENT = "DUPLICATE_SETTLEMENT"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    AMOUNT_MISMATCH = "AMOUNT_MISMATCH"


class ReconciliationExceptionEvent(BaseModel):
    """A newly detected reconciliation exception.

    Published once per created exception, after the producing database
    transaction commits. Reusing an existing exception publishes nothing, so one
    event means one newly detected discrepancy.

    The payload carries identity only — no amounts, no settlement, no merchant
    and no internal UUID. Authoritative detail is fetched through controlled
    interfaces when investigation is built, so this event never becomes a second
    copy of financial data.

    Field names are snake_case in Python and camelCase on the wire; the aliases
    bridge the two without either side compromising.

    ``extra="forbid"`` is deliberate. The producer contract is exactly these
    four fields, and both sides of it are owned here. An event carrying anything
    else means producer and consumer have drifted apart, which should surface
    immediately as a validation failure rather than be quietly tolerated.
    Extending the contract — with ``eventId``, ``eventVersion`` or
    ``correlationId``, for instance — is a deliberate, coordinated change across
    the Spring producer, this model, the tests and the documentation.
    """

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        frozen=True,
    )

    exception_id: str = Field(alias="exceptionId", min_length=1)
    transaction_id: str = Field(alias="transactionId", min_length=1)
    type: ExceptionType
    # AwareDatetime rejects a timestamp with no offset. An instant without a
    # timezone is ambiguous, and quietly assuming UTC would be a guess about
    # when a financial discrepancy was detected.
    detected_at: AwareDatetime = Field(alias="detectedAt")
