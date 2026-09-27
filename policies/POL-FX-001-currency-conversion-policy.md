---
document_id: POL-FX-001
title: Currency Conversion Policy
version: "1.1"
synthetic: true
---

# Currency Conversion Policy

> Synthetic demonstration document. Not a real currency policy.

## Settlement Currency

A transaction is normally settled in the currency in which it was authorised. The
expected settlement currency is the transaction currency unless a conversion
arrangement is in place for that merchant and processor.

## Currency Mismatch

A currency mismatch occurs where the settlement currency differs from the transaction
currency. It is reported whether or not the amounts also differ, because amounts in
different currencies are not comparable and any difference between them is meaningless.

A currency mismatch must not be resolved by converting one amount to the other at a
current rate. The rate applied at settlement, if any, is the only rate that explains the
figures.

## Permitted Conversion

Conversion at settlement is permitted only where the merchant has a multi-currency
settlement arrangement. Where conversion is expected, the applied rate and any
conversion fee must be evidenced from the processor's settlement advice.

Where no conversion arrangement exists, a settlement in another currency is an error on
the processor's side and must be escalated rather than adjusted.

## Cross-Border Fees

A cross-border handling fee may apply to settlements denominated in a currency other
than the merchant's home currency. Such a fee is defined by a fee rule denominated in
the settlement currency. A fee rule in one currency does not explain a difference in
another.
