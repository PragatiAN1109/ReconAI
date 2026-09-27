---
document_id: POL-RECON-001
title: Reconciliation Operations Manual
version: "1.0"
synthetic: true
---

# Reconciliation Operations Manual

> Synthetic demonstration document. Not a real operations manual.

## Detection Is Not Explanation

Reconciliation compares authoritative transaction and settlement records and reports
where they disagree. It reports what disagrees, never why. An analyst investigating a
discrepancy begins from a detected difference and must establish the cause from
evidence.

The deterministic exception types are AMOUNT_MISMATCH, MISSING_SETTLEMENT,
DUPLICATE_SETTLEMENT and CURRENCY_MISMATCH. These describe the disagreement. Business
explanations such as a processor fee, a processor delay, or a currency conversion are
root causes established during investigation, and are never recorded as detection
results.

## Investigating an Amount Mismatch

An amount mismatch means the expected settlement amount and the settled amount differ.
The difference is defined as expected minus settled: a positive difference means less
money arrived than expected, and a negative difference means more arrived.

A recommended order of enquiry:

1. Confirm the transaction and its settlements from authoritative records.
2. Check whether an active fee rule applies to the merchant and processor.
3. Compare the difference against the applicable fee amounts.
4. Review the merchant's recent settlements for the same pattern.
5. Consult the fee schedule and settlement policy before concluding.

A single matching amount is suggestive, not conclusive. A recurring difference of the
same amount across a merchant's settlements is considerably stronger evidence than one
isolated match.

## Investigating a Missing Settlement

Confirm the settlement window has elapsed, confirm no settlement exists in any status,
and confirm the transaction reached a state that should settle. A transaction still in
AUTHORIZED status may simply not have been captured.

## Evidence Standards

Every material conclusion must cite the evidence supporting it, identifying the record
or document it came from. Where evidence is insufficient, escalation is the correct
outcome. An analyst must not select the most plausible-sounding explanation in the
absence of supporting evidence.
