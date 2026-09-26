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

## Endpoint

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

## 17.1 Get Investigation for Exception

```http
GET /api/v1/exceptions/{exceptionId}/investigations
```

### Response

```json
{
  "exceptionId": "EX-1042",
  "investigations": [
    {
      "investigationId": "INV-2041",
      "status": "COMPLETED",
      "confidence": 0.94,
      "startedAt": "2026-09-26T14:32:04Z",
      "completedAt": "2026-09-26T14:32:11Z"
    }
  ]
}
```

---

## 17.2 Get Investigation Detail

```http
GET /api/v1/investigations/{investigationId}
```

### Response

```json
{
  "investigationId": "INV-2041",
  "exceptionId": "EX-1042",
  "status": "COMPLETED",
  "modelProvider": "configured-provider",
  "modelName": "configured-model",
  "promptVersion": "v1",
  "confidence": 0.94,
  "startedAt": "2026-09-26T14:32:04Z",
  "completedAt": "2026-09-26T14:32:11Z",
  "evidence": [
    {
      "evidenceId": "EVD-5001",
      "sourceType": "FEE_RULE",
      "sourceReference": "FR-14",
      "summary": "A $30 processing fee applies to this merchant and processor."
    },
    {
      "evidenceId": "EVD-5002",
      "sourceType": "POLICY_DOCUMENT",
      "sourceReference": "Settlement Processing Policy §4.2",
      "summary": "The policy permits the applicable processor fee."
    }
  ],
  "recommendation": {
    "recommendationId": "REC-3011",
    "classification": "PROCESSOR_FEE",
    "rootCause": "A $30 processor settlement fee was applied.",
    "confidence": 0.94,
    "recommendedAction": "Classify the discrepancy as a processor fee adjustment.",
    "requiresHumanApproval": true,
    "shouldEscalate": false
  }
}
```

---

# 18. Internal Agent Result API

After investigation, the Agent Service must return its structured result to the Financial Core.

## Endpoint

```http
POST /api/v1/internal/investigations/{investigationId}/result
```

This endpoint is service-to-service only.

### Request

```json
{
  "status": "COMPLETED",
  "modelProvider": "configured-provider",
  "modelName": "configured-model",
  "promptVersion": "v1",
  "classification": "PROCESSOR_FEE",
  "rootCause": "A $30 processor settlement fee was applied.",
  "confidence": 0.94,
  "recommendedAction": "Classify the discrepancy as a processor fee adjustment.",
  "requiresHumanApproval": true,
  "shouldEscalate": false,
  "evidence": [
    {
      "sourceType": "FEE_RULE",
      "sourceReference": "FR-14",
      "summary": "A $30 processing fee applies.",
      "toolName": "get_fee_rules"
    },
    {
      "sourceType": "POLICY_DOCUMENT",
      "sourceReference": "Settlement Processing Policy §4.2",
      "summary": "The applicable policy supports the fee.",
      "toolName": "search_policy_documents"
    }
  ]
}
```

### Response

```http
201 Created
```

```json
{
  "investigationId": "INV-2041",
  "recommendationId": "REC-3011",
  "exceptionStatus": "AWAITING_REVIEW"
}
```

The Financial Core validates and persists the result.

The Agent Service should not directly write to Financial Core tables.

---

# 19. Investigation Failure API

If an investigation fails:

```http
POST /api/v1/internal/investigations/{investigationId}/failure
```

### Request

```json
{
  "errorType": "MODEL_PROVIDER_UNAVAILABLE",
  "message": "Investigation could not be completed.",
  "retryable": true
}
```

The failure response must not expose secrets, API keys, stack traces, or sensitive provider details.

---

# 20. Approval APIs

These endpoints represent human actions.

The Investigation Agent must not have access to them as tools.

---

## 20.1 Approve Recommendation

```http
POST /api/v1/recommendations/{recommendationId}/approve
```

### Request

```json
{
  "reviewerId": "analyst-01",
  "comment": "Fee rule and settlement policy support the recommendation."
}
```

### Response

```json
{
  "approvalId": "APR-9011",
  "recommendationId": "REC-3011",
  "decision": "APPROVED",
  "exceptionStatus": "RESOLVED",
  "decidedAt": "2026-09-26T14:40:00Z"
}
```

---

## 20.2 Reject Recommendation

```http
POST /api/v1/recommendations/{recommendationId}/reject
```

### Request

```json
{
  "reviewerId": "analyst-01",
  "comment": "The historical evidence does not sufficiently support this conclusion."
}
```

### Response

```json
{
  "approvalId": "APR-9012",
  "recommendationId": "REC-3011",
  "decision": "REJECTED",
  "exceptionStatus": "OPEN",
  "decidedAt": "2026-09-26T14:41:00Z"
}
```

---

## 20.3 Escalate Recommendation

```http
POST /api/v1/recommendations/{recommendationId}/escalate
```

### Request

```json
{
  "reviewerId": "analyst-01",
  "comment": "Requires processor investigation."
}
```

### Response

```json
{
  "approvalId": "APR-9013",
  "recommendationId": "REC-3011",
  "decision": "ESCALATED",
  "exceptionStatus": "ESCALATED",
  "decidedAt": "2026-09-26T14:42:00Z"
}
```

---

# 21. Audit APIs

## Get Audit Trail

```http
GET /api/v1/audit
```

Supported filters:

```text
resourceId
correlationId
eventType
```

Example:

```http
GET /api/v1/audit?resourceId=EX-1042
```

### Response

```json
{
  "events": [
    {
      "eventId": "AUD-1001",
      "eventType": "EXCEPTION_DETECTED",
      "actorType": "SYSTEM",
      "resourceType": "RECONCILIATION_EXCEPTION",
      "resourceId": "EX-1042",
      "occurredAt": "2026-09-26T14:32:00Z"
    },
    {
      "eventId": "AUD-1002",
      "eventType": "INVESTIGATION_STARTED",
      "actorType": "AGENT",
      "resourceType": "INVESTIGATION",
      "resourceId": "INV-2041",
      "occurredAt": "2026-09-26T14:32:04Z"
    },
    {
      "eventId": "AUD-1003",
      "eventType": "RECOMMENDATION_APPROVED",
      "actorType": "USER",
      "actorId": "analyst-01",
      "resourceType": "RECOMMENDATION",
      "resourceId": "REC-3011",
      "occurredAt": "2026-09-26T14:40:00Z"
    }
  ]
}
```

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

### Agent Result

Submitting the same completed investigation result repeatedly should not create duplicate recommendations.

V1 may implement simple uniqueness constraints and application-level checks.

Production improvements may introduce stronger event-processing guarantees.

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

get_settlement()

get_fee_rules()

search_policy_documents()

get_transaction_history()
```

### 7. Agent Determines

```text
Likely root cause:
PROCESSOR_FEE

Confidence:
0.94
```

### 8. Agent Submits Result

```http
POST /api/v1/internal/investigations/INV-2041/result
```

### 9. Analyst Opens Investigation

```http
GET /api/v1/investigations/INV-2041
```

### 10. Analyst Approves

```http
POST /api/v1/recommendations/REC-3011/approve
```

### 11. System Records

```text
RECOMMENDATION_APPROVED
```

in the audit trail.

---

# 29. Contract Invariants

The following API rules must remain true.

1. Reconciliation endpoints do not invoke the LLM synchronously.
2. Kafka events contain identifiers rather than unnecessary sensitive records.
3. Agent financial tools are read-only.
4. The agent does not receive arbitrary SQL access.
5. AI-generated recommendations do not directly modify financial records.
6. Human approval endpoints are not available as agent tools.
7. Investigation results contain structured output.
8. Recommendations reference supporting evidence.
9. Failed investigations do not prevent deterministic reconciliation.
10. Important operations are traceable through correlation IDs and audit events.

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