---
document_id: POL-FEE-001
title: Merchant Fee Schedule
version: "1.3"
synthetic: true
---

# Merchant Fee Schedule

> Synthetic demonstration document. Not a real fee schedule.

## Scope

This schedule lists fees that processors may deduct from settlement amounts before
funds reach a merchant. Authoritative per-merchant amounts are held as structured fee
rules and referenced by rule identifier, for example `FR-14`. This document explains
how those rules are meant to be applied; it does not restate their amounts.

## Cross-Network Settlement Fees

A cross-network settlement processing fee applies when a purchase is settled through a
processor that is not the acquiring network. The fee is a fixed amount per settled
transaction, deducted at settlement rather than invoiced separately.

Because the deduction happens before funds arrive, the settled amount is lower than the
expected settlement amount by exactly the fee. This is the most common benign cause of
a small, consistent settlement shortfall.

NORTHSTAR_PAYMENTS applies a fixed cross-network processing fee to settled purchases.
The current amount is defined by the active fee rule for that processor.

## Network Access Fees

A per-settlement network access fee may apply to all merchants on a processor,
independent of any merchant-specific arrangement. Network access fees are small and
apply uniformly, so they affect every settlement for that processor rather than
isolated transactions.

## Fee Rule Precedence

Where both a merchant-specific rule and a processor-wide rule exist, both may apply:
the merchant-specific rule does not supersede a network access fee. Where two rules of
the same type exist for one merchant and processor, only the active rule applies. A
withdrawn rule is retained for historical reference and must not be used to explain a
current settlement.

## Verifying a Fee Explanation

A fee only explains a settlement difference when the fee rule was active, applies to
the processor that settled the transaction, applies to the merchant or to all merchants
on that processor, is denominated in the settlement currency, and the amounts reconcile.
A matching amount alone is not sufficient evidence.
