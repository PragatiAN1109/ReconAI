"""Typed financial evidence returned by the Financial Core.

These models mirror the Spring DTOs exactly:
``com.reconai.transaction.dto.TransactionResponse`` and
``com.reconai.settlement.dto.SettlementResponse``. The financial core owns those
contracts; this service only reads them.

Money is ``Decimal`` throughout and never ``float``. A binary float cannot
represent 1247.50 exactly, and evidence that has been rounded on the way in is
not evidence.
"""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

# Both sides of these contracts are owned in this repository, so an unexpected
# field means producer and consumer have drifted and should say so loudly rather
# than be quietly dropped. Same reasoning as the Kafka event model.
_STRICT = ConfigDict(extra="forbid", populate_by_name=True, frozen=True)


class TransactionType(StrEnum):
    """Mirrors ``com.reconai.transaction.TransactionType``."""

    PURCHASE = "PURCHASE"
    REFUND = "REFUND"
    REVERSAL = "REVERSAL"


class TransactionStatus(StrEnum):
    """Mirrors ``com.reconai.transaction.TransactionStatus``."""

    AUTHORIZED = "AUTHORIZED"
    POSTED = "POSTED"
    SETTLED = "SETTLED"
    REVERSED = "REVERSED"


class SettlementStatus(StrEnum):
    """Mirrors ``com.reconai.settlement.SettlementStatus``."""

    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    REVERSED = "REVERSED"
    FAILED = "FAILED"


class FeeType(StrEnum):
    """Mirrors ``com.reconai.feerule.FeeType``."""

    FIXED = "FIXED"
    PERCENTAGE = "PERCENTAGE"
    NETWORK = "NETWORK"
    CROSS_BORDER = "CROSS_BORDER"
    PROCESSING = "PROCESSING"


class TransactionEvidence(BaseModel):
    """What the financial system expected to settle.

    ``amount`` and ``expected_settlement_amount`` are different numbers whenever
    a deduction is already anticipated, and reconciliation compares against the
    expected amount. Any investigation reading this evidence has to respect the
    same distinction.
    """

    model_config = _STRICT

    transaction_id: str = Field(alias="transactionId")
    merchant_id: str = Field(alias="merchantId")
    amount: Decimal
    expected_settlement_amount: Decimal = Field(alias="expectedSettlementAmount")
    currency: str
    transaction_type: TransactionType = Field(alias="transactionType")
    status: TransactionStatus
    transaction_timestamp: AwareDatetime = Field(alias="transactionTimestamp")
    created_at: AwareDatetime = Field(alias="createdAt")


class SettlementEvidence(BaseModel):
    """What a processor reported settling."""

    model_config = _STRICT

    settlement_id: str = Field(alias="settlementId")
    transaction_id: str = Field(alias="transactionId")
    processor: str
    settled_amount: Decimal = Field(alias="settledAmount")
    currency: str
    status: SettlementStatus
    settlement_timestamp: AwareDatetime = Field(alias="settlementTimestamp")


class FeeRuleEvidence(BaseModel):
    """A fee that applies to a processor, optionally narrowed to one merchant.

    This is context, not a conclusion. A rule whose amount happens to equal a
    settlement difference is evidence that such a fee exists — not a finding
    that this transaction was charged it. Drawing that inference is
    investigation's job and needs more than a matching number.

    ``merchant_id`` is absent when the rule applies to every merchant on the
    processor.
    """

    model_config = _STRICT

    rule_id: str = Field(alias="ruleId")
    merchant_id: str | None = Field(default=None, alias="merchantId")
    processor: str
    fee_type: FeeType = Field(alias="feeType")
    fee_amount: Decimal = Field(alias="feeAmount")
    currency: str
    description: str
    active: bool


class FeeRuleList(BaseModel):
    """The financial core's fee rule list envelope."""

    model_config = _STRICT

    items: list[FeeRuleEvidence]
    total: int


class TransactionSettlements(BaseModel):
    """The settlements recorded against one transaction.

    Always a list, possibly empty. A transaction may have none, one, or several,
    and which of those it is decides between MISSING_SETTLEMENT,
    DUPLICATE_SETTLEMENT and everything else — so this must never be flattened
    into a single optional settlement.
    """

    model_config = _STRICT

    transaction_id: str = Field(alias="transactionId")
    settlements: list[SettlementEvidence]
