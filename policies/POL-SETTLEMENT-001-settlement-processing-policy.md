---
document_id: POL-SETTLEMENT-001
title: Settlement Processing Policy
version: "2.1"
synthetic: true
---

# Settlement Processing Policy

> Synthetic demonstration document. Not a real settlement policy.

## Settlement Windows

A settled purchase is normally reported by the processor within one business day of
authorisation. A settlement that has not been reported within three business days is
considered overdue and should be investigated as a missing settlement.

## Settlement Status

Only a settlement in COMPLETED status represents money that has moved. PENDING
settlements have been acknowledged but not executed. FAILED settlements did not
execute. REVERSED settlements were executed and then undone.

Reconciliation considers only COMPLETED settlements. A transaction whose only
settlement is PENDING or FAILED has not been settled, and is treated the same as a
transaction with no settlement at all.

## Expected Versus Settled Amounts

The expected settlement amount is what the internal system anticipates receiving, and
already accounts for deductions known at authorisation time. It is not necessarily
equal to the transaction amount.

A difference between the expected settlement amount and the settled amount is a
reconciliation discrepancy regardless of its cause. Establishing the cause is a separate
activity from detecting the difference.

## Duplicate Settlements

A transaction should be settled exactly once. Where more than one COMPLETED settlement
exists for a single transaction, the duplicate must not be resolved by selecting one and
disregarding the others. Common causes include processor retry after a timeout, a
correction issued as a second settlement rather than a reversal, and genuine duplicate
payment. These require different remediation, so the cause must be established before
any adjustment.

## Reversals

A reversal is recorded as a settlement in REVERSED status and does not offset a
COMPLETED settlement automatically. A transaction showing both a COMPLETED and a
REVERSED settlement requires review.
