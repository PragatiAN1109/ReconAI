# ReconAI — API Contract

## 1. Purpose

This document defines the V1 API boundaries between:

- the React Operations Console;
- the Spring Boot Financial Core;
- the Python Investigation Agent Service; and
- internal agent tools.

The API design follows the core ReconAI principle:

> **Deterministic systems detect. AI investigates. Humans authorize.**

The Spring Boot Financial Core remains the authoritative interface for financial-domain data.

The AI agent does not receive unrestricted database access and interacts with financial information only through explicitly defined read-only tools.

---

# 2. Service Boundaries

ReconAI V1 contains two primary backend services.

## Financial Core

```text
Technology:
Java 21 + Spring Boot 3

Responsibilities:
- transactions
- settlements
- reconciliation
- reconciliation exceptions
- fee rules
- human approvals
- audit events
- agent-facing financial APIs
```

Base path:

```text
/api/v1
```

---

## Investigation Agent Service

```text
Technology:
Python + FastAPI

Responsibilities:
- investigation orchestration
- LLM interaction
- tool selection
- policy retrieval
- evidence collection
- recommendation generation
- evaluation support
```

Internal base path:

```text
/internal/v1
```

The Agent Service is not intended to become the authoritative financial API.

---

# 3. API Consumers

Three logical consumers exist.

```text
React UI
    |
    v
Spring Boot Financial Core


Kafka
    |
    v
Investigation Agent


Investigation Agent
    |
    v
Restricted Financial Core APIs
```

The React frontend should normally communicate with the Financial Core.

The Agent Service uses restricted read-only interfaces for financial information.

---

# 4. Common Response Conventions

## Content Type

```http
Content-Type: application/json
```

---

## Timestamp Format

All API timestamps use ISO 8601 UTC.

Example:

```text
2026-09-26T14:32:00Z
```

---

## Monetary Values

Monetary values are serialized as JSON numbers with decimal precision.

Example:

```json
{
  "amount": 1247.50,
  "currency": "USD"
}
```

Backend financial calculations must use decimal types.

---

# 5. Error Format

Errors should use a consistent structure.

Example:

```json
{
  "timestamp": "2026-09-26T14:32:00Z",
  "status": 404,
  "error": "NOT_FOUND",
  "message": "Transaction TX-48291 was not found.",
  "path": "/api/v1/transactions/TX-48291",
  "correlationId": "CORR-89123"
}
```

Initial error categories:

```text
VALIDATION_ERROR
NOT_FOUND
CONFLICT
INVALID_STATE_TRANSITION
INTERNAL_ERROR
DEPENDENCY_UNAVAILABLE
```

---

# 6. Transaction APIs

## 6.1 Create Transaction

```http
POST /api/v1/transactions
```

### Consumer

```text
Frontend / Seed Data / Demo
```

### Request

```json
{
  "merchantId": "MERCHANT-104",
  "amount": 1247.50,
  "expectedSettlementAmount": 1247.50,
  "currency": "USD",
  "transactionType": "PURCHASE",
  "status": "POSTED",
  "transactionTimestamp": "2026-09-26T13:45:00Z"
}
```

### Response

```http
201 Created
```

```json
{
  "transactionId": "TX-48291",
  "merchantId": "MERCHANT-104",
  "amount": 1247.50,
  "expectedSettlementAmount": 1247.50,
  "currency": "USD",
  "transactionType": "PURCHASE",
  "status": "POSTED",
  "transactionTimestamp": "2026-09-26T13:45:00Z",
  "createdAt": "2026-09-26T13:45:02Z"
}
```

---

## 6.2 Get Transaction

```http
GET /api/v1/transactions/{transactionId}
```

Example:

```http
GET /api/v1/transactions/TX-48291
```

### Response

```json
{
  "transactionId": "TX-48291",
  "merchantId": "MERCHANT-104",
  "amount": 1247.50,
  "expectedSettlementAmount": 1247.50,
  "currency": "USD",
  "transactionType": "PURCHASE",
  "status": "POSTED",
  "transactionTimestamp": "2026-09-26T13:45:00Z"
}
```

---

# 7. Settlement APIs

## 7.1 Create Settlement

```http
POST /api/v1/settlements
```

### Request

```json
{
  "transactionId": "TX-48291",
  "processor": "NORTHSTAR_PAYMENTS",
  "settledAmount": 1217.50,
  "currency": "USD",
  "status": "COMPLETED",
  "settlementTimestamp": "2026-09-26T14:00:00Z"
}
```

### Response

```http
201 Created
```

```json
{
  "settlementId": "SET-8821",
  "transactionId": "TX-48291",
  "processor": "NORTHSTAR_PAYMENTS",
  "settledAmount": 1217.50,
  "currency": "USD",
  "status": "COMPLETED",
  "settlementTimestamp": "2026-09-26T14:00:00Z"
}
```

---

## 7.2 Get Settlements for Transaction

```http
GET /api/v1/transactions/{transactionId}/settlements
```

### Response

```json
{
  "transactionId": "TX-48291",
  "settlements": [
    {
      "settlementId": "SET-8821",
      "processor": "NORTHSTAR_PAYMENTS",
      "settledAmount": 1217.50,
      "currency": "USD",
      "status": "COMPLETED",
      "settlementTimestamp": "2026-09-26T14:00:00Z"
    }
  ]
}
```

The response is an array because multiple settlement records may exist.

This is required for duplicate-settlement detection.

---

# 8. Reconciliation APIs

## 8.1 Reconcile Transaction

```http
POST /api/v1/reconciliation/transactions/{transactionId}
```

Example:

```http
POST /api/v1/reconciliation/transactions/TX-48291
```

The request contains no AI instructions.

The Financial Core retrieves authoritative financial records and performs deterministic reconciliation.

### Successful Reconciliation

```json
{
  "transactionId": "TX-10001",
  "reconciled": true,
  "exceptions": [],
  "reconciledAt": "2026-09-26T14:32:00Z"
}
```

### Reconciliation With Exception

```json
{
  "transactionId": "TX-48291",
  "reconciled": false,
  "exceptions": [
    {
      "exceptionId": "EX-1042",
      "exceptionType": "AMOUNT_MISMATCH",
      "expectedValue": "1247.50",
      "observedValue": "1217.50",
      "differenceAmount": 30.00,
      "currency": "USD",
      "status": "OPEN"
    }
  ],
  "reconciledAt": "2026-09-26T14:32:00Z"
}
```

The endpoint returns after deterministic reconciliation.

It does not wait for AI investigation.

---

## 8.2 Run Batch Reconciliation

Optional V1 endpoint:

```http
POST /api/v1/reconciliation/run
```

### Request

```json
{
  "transactionIds": [
    "TX-48291",
    "TX-48292",
    "TX-48293"
  ]
}
```

### Response

```http
202 Accepted
```

```json
{
  "requested": 3,
  "message": "Reconciliation started."
}
```

This endpoint should remain secondary to the single-transaction demo workflow.

---

# 9. Reconciliation Exception APIs

## 9.1 List Exceptions

```http
GET /api/v1/exceptions
```

Optional filters:

```text
status
exceptionType
transactionId
```

Example:

```http
GET /api/v1/exceptions?status=AWAITING_REVIEW
```

### Response

```json
{
  "items": [
    {
      "exceptionId": "EX-1042",
      "transactionId": "TX-48291",
      "settlementId": "SET-8821",
      "exceptionType": "AMOUNT_MISMATCH",
      "differenceAmount": 30.00,
      "currency": "USD",
      "status": "AWAITING_REVIEW",
      "detectedAt": "2026-09-26T14:32:00Z"
    }
  ],
  "total": 1
}
```

---

## 9.2 Get Exception

```http
GET /api/v1/exceptions/{exceptionId}
```

### Response

```json
{
  "exceptionId": "EX-1042",
  "transactionId": "TX-48291",
  "settlementId": "SET-8821",
  "exceptionType": "AMOUNT_MISMATCH",
  "expectedValue": "1247.50",
  "observedValue": "1217.50",
  "differenceAmount": 30.00,
  "currency": "USD",
  "status": "AWAITING_REVIEW",
  "detectedAt": "2026-09-26T14:32:00Z"
}
```

---

# 10. Kafka Event Contract

When an exception is created, the Financial Core publishes an event.

## Topic

```text
reconciliation.exceptions
```

## Event

```json
{
  "eventId": "EVT-1001",
  "eventType": "RECONCILIATION_EXCEPTION_DETECTED",
  "eventVersion": "1",
  "exceptionId": "EX-1042",
  "transactionId": "TX-48291",
  "exceptionType": "AMOUNT_MISMATCH",
  "detectedAt": "2026-09-26T14:32:00Z",
  "correlationId": "CORR-89123"
}
```

### Important

Kafka events should contain identifiers and necessary routing context.

They should not contain unnecessary customer or financial information.

The investigation service should retrieve authoritative details through tools.

---

# 11. Agent Financial Tool APIs

These endpoints exist specifically for the Investigation Agent.

They must be read-only.

Suggested base path:

```text
/api/v1/agent-tools
```

These endpoints should never expose arbitrary SQL capability.

---

# 12. get_transaction Tool

## Endpoint

```http
GET /api/v1/agent-tools/transactions/{transactionId}
```

### Response

```json
{
  "transactionId": "TX-48291",
  "merchantId": "MERCHANT-104",
  "amount": 1247.50,
  "expectedSettlementAmount": 1247.50,
  "currency": "USD",
  "transactionType": "PURCHASE",
  "status": "POSTED",
  "transactionTimestamp": "2026-09-26T13:45:00Z"
}
```

This endpoint returns only fields necessary for investigation.

---

# 13. get_settlement Tool

## Endpoint

```http
GET /api/v1/agent-tools/transactions/{transactionId}/settlements
```

### Response

```json
{
  "transactionId": "TX-48291",
  "settlements": [
    {
      "settlementId": "SET-8821",
      "processor": "NORTHSTAR_PAYMENTS",
      "settledAmount": 1217.50,
      "currency": "USD",
      "status": "COMPLETED",
      "settlementTimestamp": "2026-09-26T14:00:00Z"
    }
  ]
}
```

---

# 14. get_fee_rules Tool

## Implemented Endpoint

```http
GET /api/v1/fee-rules
```

Optional filters:

```text
merchantId
processor
currency
active
```

Example:

```http
GET /api/v1/fee-rules?merchantId=MERCHANT-104&processor=NORTHSTAR_PAYMENTS&active=true
```

### Response

```json
{
  "items": [
    {
      "ruleId": "FR-14",
      "merchantId": "MERCHANT-104",
      "processor": "NORTHSTAR_PAYMENTS",
      "feeType": "PROCESSING",
      "feeAmount": 50.00,
      "currency": "USD",
      "description": "Cross-network settlement processing fee applied per settled purchase.",
      "active": true
    }
  ],
  "total": 1
}
```

Read-only: there is no create, update or delete path. A `merchantId` filter also returns
rules with no merchant, since those apply to every merchant on the processor.

The envelope is `{items, total}`, matching the other list endpoints in the implemented
API rather than the merchant-keyed shape sketched below. The endpoint also sits on the
normal `/api/v1` path rather than an `agent-tools` prefix, consistent with the
transaction and settlement reads the investigation service already uses.

## Originally Sketched Endpoint

The following was the original sketch, retained for reference. It has not been
implemented.

```http
GET /api/v1/agent-tools/merchants/{merchantId}/fee-rules
```

Example:

```http
GET /api/v1/agent-tools/merchants/MERCHANT-104/fee-rules
```

### Response

```json
{
  "merchantId": "MERCHANT-104",
  "rules": [
    {
      "ruleId": "FR-14",
      "processor": "NORTHSTAR_PAYMENTS",
      "feeType": "PROCESSING",
      "feeAmount": 30.00,
      "currency": "USD",
      "description": "Tier B cross-network settlement processing fee.",
      "effectiveFrom": "2026-01-01T00:00:00Z",
      "effectiveTo": null,
      "active": true
    }
  ]
}
```

---

# 15. get_transaction_history Tool

## Endpoint

```http
GET /api/v1/agent-tools/merchants/{merchantId}/transaction-history
```

Optional query parameters:

```text
limit
processor
```

Example:

```http
GET /api/v1/agent-tools/merchants/MERCHANT-104/transaction-history?limit=10
```

### Response

```json
{
  "merchantId": "MERCHANT-104",
  "transactions": [
    {
      "transactionId": "TX-38182",
      "expectedSettlementAmount": 830.00,
      "settledAmount": 800.00,
      "differenceAmount": 30.00,
      "currency": "USD",
      "processor": "NORTHSTAR_PAYMENTS"
    },
    {
      "transactionId": "TX-19281",
      "expectedSettlementAmount": 930.00,
      "settledAmount": 900.00,
      "differenceAmount": 30.00,
      "currency": "USD",
      "processor": "NORTHSTAR_PAYMENTS"
    }
  ]
}
```

---

# 16. Policy Search Tool

Policy retrieval belongs to the Investigation Agent Service.

Logical tool:

```text
search_policy_documents(query)
```

Internal endpoint:

```http
POST /internal/v1/policies/search
```

### Request

```json
{
  "query": "processor settlement fees for Tier B merchants",
  "topK": 5
}
```

### Response

```json
{
  "results": [
    {
      "documentId": "POL-1001",
      "title": "Settlement Processing Policy",
      "version": "1.2",
      "section": "4.2 Processor Fees",
      "content": "Tier B cross-network settlements may incur...",
      "retrievalScore": 0.91
    }
  ]
}
```

The exact interpretation of retrieval score depends on the embedding/vector implementation.

It should not be presented as probability or model confidence.

---

# 17. Investigation APIs

## 17.1 Get Investigation for Exception — not implemented

```http
GET /api/v1/exceptions/{exceptionId}/investigations
```

Not implemented. The plural response shape assumed an exception could have
several investigations; it has at most one, enforced by a unique constraint on
`exception_id` (see `docs/data-model.md` section 6).

Were it added, it would live on the Investigation Service — which owns
investigations — and return a single object or `404`, not a list.

---

## 17.2 Get Investigation Detail

```http
GET /api/v1/investigations/{investigationId}
```

Served by the **Investigation Service**.

### Response

```json
{
  "investigation_id": "INV-1001",
  "exception_id": "EX-1042",
  "transaction_id": "TX-48291",
  "exception_type": "AMOUNT_MISMATCH",
  "status": "AWAITING_REVIEW",
  "detected_at": "2026-09-26T14:32:00Z",
  "created_at": "2026-09-26T14:32:01Z",
  "updated_at": "2026-09-26T14:32:11Z"
}
```

The recommendation is a separate resource rather than an embedded object,
because it does not exist until an investigation has run. Embedding it would
mean either a null field that clients must special-case, or an empty object that
invites rendering a conclusion nobody reached.

---

## 17.3 Get Recommendation

```http
GET /api/v1/investigations/{investigationId}/recommendation
```

### Response

```json
{
  "recommendation_id": "REC-3001",
  "investigation_id": "INV-1001",
  "classification": "PROCESSOR_FEE",
  "root_cause": "An active 50.00 USD processing fee is consistent with the difference.",
  "confidence": "0.8600",
  "confidence_note": "Model self-reported, not a calibrated probability. Used only as an ordering signal against a configured review threshold.",
  "recommended_action": "Review and classify the discrepancy as a processor fee adjustment.",
  "requires_human_approval": true,
  "model_provider": "anthropic",
  "model_name": "claude-sonnet-5",
  "prompt_version": "v1",
  "created_at": "2026-09-26T14:32:11Z",
  "evidence": [
    {"source_type": "FEE_RULE", "reference": "FR-14", "section": null, "excerpt": null},
    {"source_type": "POLICY_DOCUMENT", "reference": "POL-FEE-001",
     "section": "Cross-Network Settlement Fees",
     "excerpt": "Settlement processing fees are deducted at settlement time."},
    {"source_type": "SETTLEMENT", "reference": "SET-8008", "section": null, "excerpt": null}
  ]
}
```

`confidence` is serialised as a string so JSON cannot round it, and
`confidence_note` is present on every response carrying it. It is a model
self-report, **not a calibrated probability**: 0.86 does not mean 86% of such
conclusions are correct. Clients must not present it as a statistical claim.

Every entry in `evidence` was verified against what the tools actually returned
before the recommendation was stored. `excerpt` is populated only for policy
evidence, so a reviewer sees the text the investigation saw rather than whatever
the corpus says today.

`404` while an investigation is `PENDING` or `RUNNING`: there is genuinely no
conclusion yet.

---

## 17.4 Run Investigation

```http
POST /api/v1/investigations/{investigationId}/run
```

Served by the **Investigation Service**, which owns investigations. Runs the
bounded tool loop, validates the result against the evidence actually retrieved,
stores it, and routes it by deterministic guardrail.

### Response

```json
{
  "investigation_id": "INV-1001",
  "status": "AWAITING_REVIEW",
  "guardrail_reason": "Reported confidence 0.8600 meets the review threshold 0.85 with 3 verified evidence reference(s); awaiting human approval.",
  "recommendation": { "...": "as in 17.3" },
  "evidence_retrieved": {
    "transactions": ["TX-10009"],
    "settlements": ["SET-8008"],
    "feeRules": ["FR-14", "FR-15"],
    "policyDocuments": ["POL-FEE-001"]
  }
}
```

`status` is either `AWAITING_REVIEW` or `ESCALATED`. **It is never
`COMPLETED`** — a run cannot complete an investigation, only a human approving
one can.

`guardrail_reason` is generated by deterministic application code, not by the
model, so it can be trusted as a description of why the investigation was routed
where it was. The threshold and evidence minimum are configurable
(`RECONAI_AGENT_REVIEW_CONFIDENCE_THRESHOLD`,
`RECONAI_AGENT_REVIEW_MINIMUM_EVIDENCE`).

`evidence_retrieved` is the application's own record of what the tools returned,
reported alongside the model's citations so a reviewer can see the difference
between what was available and what was cited.

This endpoint approves nothing and modifies no financial record.

### Status codes

| Code | Meaning |
|---:|---|
| 200 | The investigation ran; the result is stored and routed |
| 404 | No such investigation |
| 409 | Not `PENDING` — already running, already concluded, or already reviewed |
| 422 | The result was malformed or cited evidence that was never retrieved |
| 503 | No model provider is configured, or the provider could not be reached |

A `422` stores no recommendation at all. A failed investigation has no
conclusion, and none is invented to fill the gap; the investigation moves to
`FAILED` and the reason is recorded in the audit trail.

`409` is what makes a run idempotent in the way that matters: a second call
cannot produce a second conclusion.

---

# 18. Internal Agent Result API — not implemented

An earlier draft had the Agent Service POST its result back to the Financial
Core, which would validate and persist it:

```http
POST /api/v1/internal/investigations/{investigationId}/result
```

**This endpoint does not exist, and will not.** The Investigation Service owns
investigation results and stores them in the `investigation` schema it owns. See
`docs/data-model.md` section 23.

The original reasoning — "the Agent Service should not directly write to
Financial Core tables" — is correct and is fully preserved. It is satisfied more
strictly by the implemented design: the Investigation Service writes only to its
own schema, and issues no write of any kind to the Financial Core. Routing AI
results *through* the Financial Core would have made it the store of record for
probabilistic output, which is the opposite of what this system is for.

Section 19's failure endpoint is likewise not implemented. A failed
investigation is recorded as `FAILED` by the service that ran it, with the
reason in its own audit trail.

---

# 19. Investigation Failure API — not implemented

See section 18. Failures are recorded locally by the Investigation Service.

The original constraint still holds and is enforced: a failure record must not
expose secrets, API keys, stack traces, or provider details. What is stored is
the exception type and message produced by this service's own code — never a
provider payload.

---

# 20. Review APIs

These endpoints represent **human** actions.

The Investigation Agent has no access to them. Not as a tool, and not by any
reachable code path: the tool allowlist contains exactly four read-only
evidence tools, and nothing else is callable.

Served by the Investigation Service, addressed by investigation rather than by
recommendation — one investigation has at most one recommendation and at most
one decision, so the investigation is the stable handle.

## What approval does not do

**Approval calls nothing on the Financial Core.** No POST, PUT, PATCH or DELETE.
The review service holds no client to it at all, so this is structural rather
than a rule to remember.

Recording an approval does not resolve the exception, alter a settlement, or
move money. It records that a human judged an explanation acceptable. Acting on
that is a separate, deliberate step outside this service.

## Authentication

**There is none in V1.** `reviewed_by` is whatever the caller sends, stored
verbatim and returned with a `reviewer_note` saying so. It is demo attribution,
not identity. Nothing should be built on it that assumes otherwise.

---

## 20.1 Approve Recommendation

```http
POST /api/v1/investigations/{investigationId}/approve
```

### Request

```json
{
  "reviewed_by": "analyst-01",
  "comment": "Fee rule and settlement policy support the recommendation."
}
```

`reviewed_by` is required and must be non-empty. Unauthenticated is not the same
as anonymous: someone must be named.

### Response

```json
{
  "review_id": "REV-7001",
  "investigation_id": "INV-1001",
  "recommendation_id": "REC-3001",
  "decision": "APPROVED",
  "reviewed_by": "analyst-01",
  "reviewer_note": "Reviewer identity is caller-supplied and unverified: this service has no authentication.",
  "comment": "Fee rule and settlement policy support the recommendation.",
  "decided_at": "2026-09-26T14:40:00Z"
}
```

The investigation becomes `COMPLETED`.

---

## 20.2 Reject Recommendation

```http
POST /api/v1/investigations/{investigationId}/reject
```

Same request shape. `decision` is `REJECTED` and the investigation becomes
`ESCALATED`, not resolved: rejecting an explanation does not make the underlying
discrepancy disappear. It still exists and still needs a human.

---

## 20.3 Escalate Recommendation

```http
POST /api/v1/investigations/{investigationId}/escalate
```

Same request shape. `decision` is `ESCALATED` and the investigation becomes
`ESCALATED`.

Distinct from rejection: the explanation is not being judged wrong, it is being
judged above this reviewer's authority. The two say different things to whoever
picks it up next, which is why both are recorded.

---

## 20.4 Review status codes

| Code | Meaning |
|---:|---|
| 200 | The decision was recorded |
| 404 | No such investigation |
| 409 | Not `AWAITING_REVIEW`, or already reviewed |
| 422 | `reviewed_by` missing or empty |

`409` rather than `400` for a state conflict: the request is well-formed, and it
is the state of the investigation that makes it impossible.

A decision is recorded **at most once**. A second reviewer cannot overwrite the
first, and two reviewers submitting simultaneously result in exactly one stored
decision — enforced by a unique constraint on `investigation_id`, not by an
application check that both could pass.

Only an investigation that is `AWAITING_REVIEW` can be decided. An `ESCALATED`
one cannot be approved: the guardrail already routed it away from recommendation
review toward a human investigating it directly, which is a different activity.

---

# 21. Audit API

## Get Audit Trail

```http
GET /api/v1/investigations/{investigationId}/audit
```

Served by the Investigation Service. Scoped to one investigation rather than
globally filterable: every event in this trail is about exactly one
investigation, and the way it is read is as that investigation's timeline.

### Response

```json
{
  "investigation_id": "INV-1001",
  "items": [
    {
      "event_id": "AUD-9001",
      "investigation_id": "INV-1001",
      "event_type": "INVESTIGATION_STARTED",
      "actor_type": "SYSTEM",
      "actor_id": null,
      "metadata": {"model_provider": "anthropic", "model_name": "claude-sonnet-5"},
      "occurred_at": "2026-09-26T14:32:04Z"
    },
    {
      "event_id": "AUD-9002",
      "investigation_id": "INV-1001",
      "event_type": "AI_RESULT_GENERATED",
      "actor_type": "AI",
      "actor_id": null,
      "metadata": {
        "recommendation_id": "REC-3001",
        "classification": "PROCESSOR_FEE",
        "confidence": "0.8600",
        "evidence_count": 3,
        "model_name": "claude-sonnet-5",
        "prompt_version": "v1"
      },
      "occurred_at": "2026-09-26T14:32:11Z"
    },
    {
      "event_id": "AUD-9003",
      "investigation_id": "INV-1001",
      "event_type": "INVESTIGATION_AWAITING_REVIEW",
      "actor_type": "SYSTEM",
      "actor_id": null,
      "metadata": {
        "recommendation_id": "REC-3001",
        "reason": "Reported confidence 0.8600 meets the review threshold 0.85 with 3 verified evidence reference(s); awaiting human approval.",
        "confidence_threshold": "0.85",
        "minimum_evidence": 1
      },
      "occurred_at": "2026-09-26T14:32:11Z"
    },
    {
      "event_id": "AUD-9004",
      "investigation_id": "INV-1001",
      "event_type": "REVIEW_APPROVED",
      "actor_type": "HUMAN",
      "actor_id": "analyst-01",
      "metadata": {
        "review_id": "REV-7001",
        "recommendation_id": "REC-3001",
        "decision": "APPROVED",
        "resulting_status": "COMPLETED",
        "has_comment": true
      },
      "occurred_at": "2026-09-26T14:40:00Z"
    }
  ],
  "total": 4
}
```

Ordered oldest-first, by an integer sequence rather than by timestamp or
identifier text: several events are written in one transaction and would tie on
time, and `AUD-10001` sorts before `AUD-9001` as a string.

`actor_type` is `SYSTEM`, `AI` or `HUMAN`. `actor_id` is set only for `HUMAN`
events, and carries the same unauthenticated caller-supplied name as
`reviewed_by`.

### Append-only

There is no endpoint to create, amend or delete an audit event — `POST`, `PUT`
and `DELETE` on this path all return `405`. Append-only is enforced by the
absence of a write path rather than by a check that could be bypassed.

Each event is written in the same transaction as the state change it describes,
so the trail cannot record something that was rolled back.

### What metadata never contains

No prompt, no provider request or response, no credential, and nothing
resembling model reasoning. Private chain-of-thought is never requested,
persisted or exposed anywhere in this system.

---

# 22. Dashboard API

To avoid forcing the frontend to calculate operational statistics from raw records:

```http
GET /api/v1/dashboard/summary
```

### Response

```json
{
  "transactions": 200,
  "reconciled": 165,
  "exceptions": 35,
  "investigating": 4,
  "awaitingReview": 7,
  "resolved": 21,
  "escalated": 3
}
```

This endpoint is intended for the Operations Console.

---

# 23. Agent Tool Boundary

The agent's allowed financial tools are:

```text
get_transaction

get_settlement

get_fee_rules

get_transaction_history

search_policy_documents
```

The agent must not have tools such as:

```text
update_transaction

delete_transaction

update_settlement

delete_settlement

modify_ledger

resolve_exception

approve_recommendation

reject_recommendation

initiate_payment

execute_sql
```

This boundary must be enforced in code rather than relying only on prompt instructions.

---

# 24. Agent Data Access Principle

The preferred architecture is:

```text
Agent
  |
  v
Controlled Tool
  |
  v
Financial Core API
  |
  v
Authoritative Data
```

Not:

```text
Agent
  |
  v
Raw Database
```

The agent should receive the minimum information necessary for each investigation.

---

# 25. Correlation IDs

Requests and asynchronous events should support a correlation identifier.

Example:

```text
CORR-89123
```

A single reconciliation workflow may therefore be traced across:

```text
HTTP request
    |
    v
Spring Boot
    |
    v
Reconciliation
    |
    v
Kafka Event
    |
    v
Agent
    |
    v
Tool Calls
    |
    v
Recommendation
```

The correlation ID should appear in application logs and relevant audit events.

---

# 26. Idempotency

The architecture should avoid duplicate processing where practical.

### Reconciliation

Repeated reconciliation of unchanged records should not create unlimited identical open exceptions.

### Kafka Consumer

The investigation consumer should handle duplicate event delivery safely.

### Running an Investigation

Running the same investigation repeatedly cannot create duplicate
recommendations. `POST /run` claims the investigation with
`UPDATE ... WHERE status = 'PENDING'`, so a second concurrent call is refused
with `409` before any model call is made — and `UNIQUE(investigation_id)` on
`recommendations` is the backstop if application logic were ever wrong.

### Reviewing

Deciding the same investigation twice is refused with `409`, and
`UNIQUE(investigation_id)` on `reviews` guarantees at most one stored decision
under concurrent requests.

These are database constraints rather than application-level checks, because two
concurrent requests can both pass a check and only one can win a constraint.

---

# 27. API Security Assumptions

V1 uses synthetic data and is intended as a portfolio application.

Full enterprise authentication is outside the initial implementation scope.

However, API boundaries should still reflect future security requirements.

Conceptually:

```text
Public/User API
    |
    v
Frontend-authenticated access


Internal API
    |
    v
Service-to-service access


Agent Tool API
    |
    v
Read-only restricted access
```

Internal endpoints should not be treated as normal public frontend APIs.

A production system would add:

- OAuth/OIDC;
- role-based authorization;
- service identities;
- scoped tokens;
- secrets management; and
- network-level restrictions.

---

# 28. V1 End-to-End Contract

The primary demonstration follows this sequence.

### 1. Create Transaction

```http
POST /api/v1/transactions
```

### 2. Create Settlement

```http
POST /api/v1/settlements
```

### 3. Run Reconciliation

```http
POST /api/v1/reconciliation/transactions/TX-48291
```

Financial Core detects:

```text
AMOUNT_MISMATCH
$30.00
```

### 4. Publish Event

```text
reconciliation.exceptions
```

### 5. Agent Consumes Event

```text
EX-1042
```

### 6. Agent Calls

```text
get_transaction()

get_settlements()

get_fee_rules()

search_policies()
```

Exactly four tools, all read-only. `get_transaction_history()` is not
implemented — see section 15.

### 7. Agent Determines

```text
Likely root cause:
PROCESSOR_FEE

Self-reported confidence:
0.86
```

Every citation is then checked against what the tools actually returned. A
result citing anything unverifiable is rejected in full.

### 8. The Investigation Service Stores the Result

In its own schema. There is no POST back to the Financial Core; see section 18.

A deterministic guardrail then routes it:

```text
confidence >= threshold, conclusive classification, evidence present
        -> AWAITING_REVIEW

anything else
        -> ESCALATED
```

### 9. Analyst Opens the Investigation

```http
GET /api/v1/investigations/INV-2041
GET /api/v1/investigations/INV-2041/recommendation
```

### 10. Analyst Approves

```http
POST /api/v1/investigations/INV-2041/approve
```

```json
{"reviewed_by": "analyst-01", "comment": "Fee rule supports the recommendation."}
```

The investigation becomes `COMPLETED`. Nothing is written to the Financial Core.

### 11. System Records

```text
REVIEW_APPROVED
```

in the audit trail, in the same transaction as the status change.

---

# 29. Contract Invariants

The following API rules must remain true.

1. Reconciliation endpoints do not invoke the LLM synchronously.
2. Kafka events contain identifiers rather than unnecessary sensitive records.
3. Agent financial tools are read-only.
4. The agent does not receive arbitrary SQL access.
5. AI-generated recommendations do not directly modify financial records.
6. Human review endpoints are not available as agent tools.
7. Investigation results contain structured output.
8. Recommendations reference supporting evidence, and every reference is
   verified against what the tools actually returned before the result is
   stored.
9. Failed investigations do not prevent deterministic reconciliation.
10. Important operations are traceable through correlation IDs and audit events.
11. No endpoint on the Investigation Service issues a write to the Financial
    Core. Approving a recommendation records a human judgement; it does not
    resolve an exception or move money.
12. Only a human transition reaches `COMPLETED`. The guardrail has exactly two
    outcomes, `AWAITING_REVIEW` and `ESCALATED`, and neither completes anything.
13. `requiresHumanApproval` is always true on a stored recommendation, enforced
    by a database CHECK constraint as well as by the response schema.
14. Model confidence is a self-report, never presented as a calibrated
    probability.
15. No prompt, provider payload, or model reasoning is persisted or exposed.

---

# 30. V1 Implementation Priority

Implement APIs in this order:

```text
1. Transaction APIs

2. Settlement APIs

3. Reconciliation API

4. Exception APIs

5. Kafka exception event

6. Agent read-only tool APIs

7. Investigation result API

8. Recommendation/approval APIs

9. Audit API

10. Dashboard summary API
```

Do not implement the entire API surface simultaneously.

The first implementation milestone is:

```text
Transaction
    +
Settlement
    |
    v
Reconciliation
    |
    v
Exception
    |
    v
Kafka Event
```

Only after this deterministic flow is tested should the AI investigation layer be introduced.