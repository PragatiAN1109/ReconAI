# ReconAI — Data Model

## 1. Purpose

This document defines the V1 domain model for ReconAI.

The model supports the complete workflow:

```text
Transaction
    |
    v
Settlement
    |
    v
Deterministic Reconciliation
    |
    v
ReconciliationException
    |
    v
Investigation
    |
    +------> InvestigationEvidence
    |
    v
Recommendation
    |
    v
Approval

PolicyDocument
    |
    v
PolicyChunk

All major operations
    |
    v
AuditEvent
```

The model intentionally separates:

- authoritative financial records;
- deterministic reconciliation results;
- AI-generated investigation artifacts;
- human decisions; and
- audit records.

---

# 2. General Modeling Rules

## 2.1 Internal IDs

Database entities use UUID primary keys.

Example:

```text
id = 550e8400-e29b-41d4-a716-446655440000
```

UUIDs are internal identifiers.

---

## 2.2 Business IDs

Important domain objects also expose readable business identifiers.

Examples:

```text
TX-48291
SET-8821
EX-1042
INV-2041
REC-3011
```

These identifiers are useful in:

- APIs;
- logs;
- Kafka events;
- UI;
- audit records; and
- demonstrations.

Business IDs must be unique.

---

## 2.3 Monetary Values

Financial amounts must use decimal arithmetic.

Java:

```text
BigDecimal
```

PostgreSQL:

```text
NUMERIC(19,4)
```

Do not use:

```text
float
double
```

for monetary calculations.

---

## 2.4 Currency

Currency values use ISO 4217 currency codes.

Examples:

```text
USD
EUR
GBP
INR
```

V1 stores currency as:

```text
VARCHAR(3)
```

---

## 2.5 Timestamps

Application timestamps are stored in UTC.

PostgreSQL:

```text
TIMESTAMP WITH TIME ZONE
```

Application services should use timezone-aware timestamp types.

---

# 3. Transaction

## Purpose

Represents an authoritative internal payment transaction.

This record describes what the internal financial system expects to be settled.

## Table

```text
transactions
```

## Fields

| Field | Type | Required | Description |
|---|---|---:|---|
| id | UUID | Yes | Internal primary key |
| transaction_id | VARCHAR(50) | Yes | Unique business ID, e.g. `TX-48291` |
| merchant_id | VARCHAR(50) | Yes | Merchant associated with transaction |
| amount | NUMERIC(19,4) | Yes | Original transaction amount |
| expected_settlement_amount | NUMERIC(19,4) | Yes | Amount expected during settlement |
| currency | VARCHAR(3) | Yes | ISO currency code |
| transaction_type | VARCHAR(30) | Yes | Transaction classification |
| status | VARCHAR(30) | Yes | Current transaction status |
| transaction_timestamp | TIMESTAMPTZ | Yes | Time transaction occurred |
| created_at | TIMESTAMPTZ | Yes | Record creation time |
| updated_at | TIMESTAMPTZ | Yes | Last modification time |

## TransactionType

Initial values:

```text
PURCHASE
REFUND
REVERSAL
```

## TransactionStatus

Initial values:

```text
AUTHORIZED
POSTED
SETTLED
REVERSED
```

## Constraints

```text
transaction_id UNIQUE
amount >= 0
expected_settlement_amount >= 0
currency length = 3
```

---

# 4. Settlement

## Purpose

Represents settlement information received from an external processor or settlement system.

A transaction may have zero, one, or multiple settlement records.

Multiple records are allowed because duplicate settlement detection is one of the reconciliation scenarios.

## Table

```text
settlements
```

## Fields

| Field | Type | Required | Description |
|---|---|---:|---|
| id | UUID | Yes | Internal primary key |
| settlement_id | VARCHAR(50) | Yes | Unique business identifier |
| transaction_id | VARCHAR(50) | Yes | Related transaction business ID |
| processor | VARCHAR(100) | Yes | Settlement processor |
| settled_amount | NUMERIC(19,4) | Yes | Amount reported by processor |
| currency | VARCHAR(3) | Yes | Settlement currency |
| status | VARCHAR(30) | Yes | Settlement status |
| settlement_timestamp | TIMESTAMPTZ | Yes | Settlement time |
| created_at | TIMESTAMPTZ | Yes | Record creation time |

## SettlementStatus

```text
PENDING
COMPLETED
REVERSED
FAILED
```

## Relationship

```text
Transaction 1 ---- 0..* Settlement
```

V1 may associate records using the transaction business identifier.

---

# 5. ReconciliationException

## Purpose

Represents a discrepancy discovered by deterministic reconciliation logic.

A reconciliation exception records **what is inconsistent**.

It does not necessarily describe **why the inconsistency occurred**.

## Table

```text
reconciliation_exceptions
```

## Fields

| Field | Type | Required | Description |
|---|---|---:|---|
| id | UUID | Yes | Internal primary key |
| exception_id | VARCHAR(50) | Yes | Unique business ID |
| transaction_id | VARCHAR(50) | Yes | Related transaction |
| settlement_id | VARCHAR(50) | No | Relevant settlement when applicable |
| exception_type | VARCHAR(50) | Yes | Deterministically detected discrepancy |
| expected_value | VARCHAR(255) | No | Expected value |
| observed_value | VARCHAR(255) | No | Observed value |
| difference_amount | NUMERIC(19,4) | No | Monetary difference when relevant |
| currency | VARCHAR(3) | No | Currency associated with difference |
| status | VARCHAR(30) | Yes | Exception lifecycle status |
| detected_at | TIMESTAMPTZ | Yes | Detection time |
| created_at | TIMESTAMPTZ | Yes | Record creation time |
| updated_at | TIMESTAMPTZ | Yes | Last update |

## ExceptionType

V1 deterministic exception types:

```text
AMOUNT_MISMATCH
MISSING_SETTLEMENT
DUPLICATE_SETTLEMENT
CURRENCY_MISMATCH
```

Important:

```text
PROCESSOR_FEE
```

is **not** a deterministic V1 exception type.

It is an investigation/root-cause classification.

Example:

```text
Detected:

AMOUNT_MISMATCH

Investigated as:

PROCESSOR_FEE
```

## ExceptionStatus

```text
OPEN
INVESTIGATING
AWAITING_REVIEW
RESOLVED
ESCALATED
```

---

# 6. Investigation

## Purpose

Represents an AI-assisted investigation of a reconciliation exception.

The Investigation is separate from the exception because:

- an exception is deterministic;
- an investigation may fail;
- an investigation may be retried;
- future versions may support multiple investigations;
- model/configuration versions may change.

## Table

```text
investigations
```

## Fields

| Field | Type | Required | Description |
|---|---|---:|---|
| id | UUID | Yes | Internal primary key |
| investigation_id | VARCHAR(50) | Yes | Unique business ID |
| exception_id | VARCHAR(50) | Yes | Exception being investigated |
| status | VARCHAR(30) | Yes | Investigation lifecycle |
| model_provider | VARCHAR(50) | No | Model provider used |
| model_name | VARCHAR(100) | No | Model used |
| prompt_version | VARCHAR(50) | No | Investigation prompt version |
| confidence | NUMERIC(5,4) | No | Final confidence value |
| started_at | TIMESTAMPTZ | No | Investigation start |
| completed_at | TIMESTAMPTZ | No | Investigation completion |
| failure_reason | TEXT | No | Failure information |
| created_at | TIMESTAMPTZ | Yes | Record creation time |

## InvestigationStatus

```text
PENDING
RUNNING
COMPLETED
FAILED
ESCALATED
```

## Relationship

```text
ReconciliationException 1 ---- 0..* Investigation
```

V1 normally creates one successful investigation per exception, but the data model allows retries.

---

# 7. InvestigationEvidence

## Purpose

Stores evidence collected by the investigation agent.

Evidence allows recommendations to be traced back to authoritative sources.

## Table

```text
investigation_evidence
```

## Fields

| Field | Type | Required | Description |
|---|---|---:|---|
| id | UUID | Yes | Internal primary key |
| evidence_id | VARCHAR(50) | Yes | Unique business ID |
| investigation_id | VARCHAR(50) | Yes | Related investigation |
| source_type | VARCHAR(50) | Yes | Type of evidence |
| source_reference | VARCHAR(255) | Yes | Source business identifier |
| summary | TEXT | Yes | Human-readable evidence summary |
| content | TEXT | No | Relevant retrieved content |
| retrieval_score | NUMERIC(6,5) | No | Retrieval score if applicable |
| tool_name | VARCHAR(100) | Yes | Tool that retrieved evidence |
| retrieved_at | TIMESTAMPTZ | Yes | Retrieval time |

## EvidenceSourceType

```text
TRANSACTION
SETTLEMENT
FEE_RULE
HISTORICAL_TRANSACTION
POLICY_DOCUMENT
```

Example:

```text
evidence_id:
EVD-5001

source_type:
FEE_RULE

source_reference:
FR-14

summary:
Tier B cross-network settlements incur a $30 processing fee.

tool_name:
get_fee_rules
```

---

# 8. FeeRule

## Purpose

Represents structured merchant or processor fee configuration.

Fee rules are stored separately from policy documents because they represent structured operational configuration rather than unstructured documentation.

## Table

```text
fee_rules
```

## Fields

| Field | Type | Required | Description |
|---|---|---:|---|
| id | UUID | Yes | Internal primary key |
| rule_id | VARCHAR(50) | Yes | Unique business ID, e.g. `FR-14` |
| merchant_id | VARCHAR(50) | No | Merchant-specific rule |
| processor | VARCHAR(100) | Yes | Applicable processor |
| fee_type | VARCHAR(50) | Yes | Type of fee |
| fee_amount | NUMERIC(19,4) | No | Fixed fee |
| fee_percentage | NUMERIC(8,5) | No | Percentage fee |
| currency | VARCHAR(3) | No | Currency for fixed fee |
| description | TEXT | Yes | Rule description |
| effective_from | TIMESTAMPTZ | Yes | Effective start |
| effective_to | TIMESTAMPTZ | No | Optional expiration |
| active | BOOLEAN | Yes | Whether rule is active |
| created_at | TIMESTAMPTZ | Yes | Creation time |

## FeeType

```text
FIXED
PERCENTAGE
NETWORK
CROSS_BORDER
PROCESSING
```

This table allows:

```text
get_fee_rules(merchant_id)
```

to return structured authoritative information without requiring the LLM to extract every fee from documents.

---

# 9. Recommendation

## Purpose

Represents the structured conclusion produced by an investigation.

Recommendations are advisory.

They do not modify authoritative financial records.

## Table

```text
recommendations
```

## Fields

| Field | Type | Required | Description |
|---|---|---:|---|
| id | UUID | Yes | Internal primary key |
| recommendation_id | VARCHAR(50) | Yes | Unique business ID |
| investigation_id | VARCHAR(50) | Yes | Related investigation |
| classification | VARCHAR(50) | Yes | Proposed root cause |
| root_cause | TEXT | Yes | Explanation |
| confidence | NUMERIC(5,4) | Yes | Agent confidence |
| recommended_action | TEXT | Yes | Proposed analyst action |
| requires_human_approval | BOOLEAN | Yes | Must be true for V1 |
| should_escalate | BOOLEAN | Yes | Whether manual escalation is recommended |
| created_at | TIMESTAMPTZ | Yes | Creation time |

## RootCauseClassification

Initial values:

```text
PROCESSOR_FEE
PROCESSOR_DELAY
DUPLICATE_PROCESSING
CURRENCY_CONVERSION
PROCESSOR_ERROR
UNKNOWN
INSUFFICIENT_EVIDENCE
```

This enum is intentionally different from `ExceptionType`.

For example:

```text
ExceptionType:

AMOUNT_MISMATCH

        ↓ investigation

RootCauseClassification:

PROCESSOR_FEE
```

---

# 10. RecommendationEvidence

## Purpose

Creates an explicit many-to-many relationship between recommendations and the evidence supporting them.

This is important because a recommendation should not merely contain prose claiming that evidence exists.

The database should record exactly which evidence items support the recommendation.

## Table

```text
recommendation_evidence
```

## Fields

| Field | Type | Required |
|---|---|---:|
| recommendation_id | UUID | Yes |
| evidence_id | UUID | Yes |

Composite primary key:

```text
(recommendation_id, evidence_id)
```

## Relationship

```text
Recommendation * ---- * InvestigationEvidence
```

---

# 11. Approval

## Purpose

Represents the human decision applied to an AI-generated recommendation.

## Table

```text
approvals
```

## Fields

| Field | Type | Required | Description |
|---|---|---:|---|
| id | UUID | Yes | Internal primary key |
| approval_id | VARCHAR(50) | Yes | Unique business ID |
| recommendation_id | VARCHAR(50) | Yes | Recommendation being reviewed |
| decision | VARCHAR(30) | Yes | Human decision |
| reviewer_id | VARCHAR(100) | Yes | Analyst/user identifier |
| reviewer_comment | TEXT | No | Optional explanation |
| decided_at | TIMESTAMPTZ | Yes | Decision timestamp |
| created_at | TIMESTAMPTZ | Yes | Record creation time |

## ApprovalDecision

```text
APPROVED
REJECTED
ESCALATED
```

## Important Invariant

An AI agent must never create an approval decision.

Approval endpoints represent human actions.

---

# 12. PolicyDocument

## Purpose

Represents a financial or operational document available to the RAG system.

## Table

```text
policy_documents
```

## Fields

| Field | Type | Required | Description |
|---|---|---:|---|
| id | UUID | Yes | Internal primary key |
| document_id | VARCHAR(50) | Yes | Unique business ID |
| title | VARCHAR(255) | Yes | Document title |
| document_type | VARCHAR(50) | Yes | Policy classification |
| version | VARCHAR(30) | Yes | Document version |
| effective_from | TIMESTAMPTZ | Yes | Effective date |
| effective_to | TIMESTAMPTZ | No | Expiration |
| content_hash | VARCHAR(64) | Yes | SHA-256 content hash |
| active | BOOLEAN | Yes | Active status |
| created_at | TIMESTAMPTZ | Yes | Creation time |

## PolicyDocumentType

```text
MERCHANT_FEE_POLICY
SETTLEMENT_POLICY
RECONCILIATION_POLICY
CURRENCY_POLICY
EXCEPTION_HANDLING_POLICY
```

---

# 13. PolicyChunk

## Purpose

Stores retrievable sections of policy documents for semantic search.

## Table

```text
policy_chunks
```

## Fields

| Field | Type | Required | Description |
|---|---|---:|---|
| id | UUID | Yes | Internal primary key |
| chunk_id | VARCHAR(50) | Yes | Unique business ID |
| document_id | UUID | Yes | Parent policy document |
| section | VARCHAR(255) | No | Human-readable section |
| chunk_index | INTEGER | Yes | Position within document |
| content | TEXT | Yes | Chunk content |
| embedding | VECTOR | Yes | Semantic embedding |
| created_at | TIMESTAMPTZ | Yes | Creation time |

## Relationship

```text
PolicyDocument 1 ---- * PolicyChunk
```

A retrieved chunk must retain its document and section information so the UI can display meaningful evidence such as:

```text
Settlement Processing Policy
Section 4.2
```

rather than merely:

```text
Chunk 18
```

---

# 14. AuditEvent

## Purpose

Records significant system actions so an investigation can be reconstructed.

Audit records should be append-only from the application's perspective.

## Table

```text
audit_events
```

## Fields

| Field | Type | Required | Description |
|---|---|---:|---|
| id | UUID | Yes | Internal primary key |
| event_id | VARCHAR(50) | Yes | Unique business ID |
| event_type | VARCHAR(50) | Yes | Audit event classification |
| actor_type | VARCHAR(30) | Yes | Source of action |
| actor_id | VARCHAR(100) | No | Specific actor |
| resource_type | VARCHAR(50) | Yes | Affected resource type |
| resource_id | VARCHAR(100) | Yes | Affected resource |
| correlation_id | VARCHAR(100) | No | Cross-service correlation identifier |
| metadata | JSONB | No | Additional structured information |
| occurred_at | TIMESTAMPTZ | Yes | Event timestamp |

## ActorType

```text
SYSTEM
AGENT
USER
```

## AuditEventType

Initial values:

```text
TRANSACTION_CREATED

SETTLEMENT_CREATED

RECONCILIATION_STARTED

RECONCILIATION_COMPLETED

EXCEPTION_DETECTED

EXCEPTION_PUBLISHED

INVESTIGATION_STARTED

TOOL_CALLED

EVIDENCE_RETRIEVED

RECOMMENDATION_GENERATED

INVESTIGATION_FAILED

INVESTIGATION_ESCALATED

RECOMMENDATION_APPROVED

RECOMMENDATION_REJECTED
```

---

# 15. Entity Relationships

The primary domain relationships are:

```text
Transaction
    |
    | 1
    |
    | 0..*
    v
Settlement


Transaction
    |
    | 1
    |
    | 0..*
    v
ReconciliationException
    |
    | 1
    |
    | 0..*
    v
Investigation
    |
    | 1
    |
    | 0..*
    v
InvestigationEvidence


Investigation
    |
    | 1
    |
    | 0..1
    v
Recommendation
    |
    | 1
    |
    | 0..1
    v
Approval


Recommendation
      *
      |
      |
      *
InvestigationEvidence


PolicyDocument
    |
    | 1
    |
    | *
    v
PolicyChunk
```

`AuditEvent` references resources generically rather than requiring a foreign key to every domain table.

---

# 16. Example End-to-End Data

## Transaction

```json
{
  "transactionId": "TX-48291",
  "merchantId": "MERCHANT-104",
  "amount": 1247.50,
  "expectedSettlementAmount": 1247.50,
  "currency": "USD",
  "transactionType": "PURCHASE",
  "status": "POSTED"
}
```

## Settlement

```json
{
  "settlementId": "SET-8821",
  "transactionId": "TX-48291",
  "processor": "NORTHSTAR_PAYMENTS",
  "settledAmount": 1217.50,
  "currency": "USD",
  "status": "COMPLETED"
}
```

## Deterministic Exception

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
  "status": "OPEN"
}
```

Notice that this record says nothing about processor fees.

The financial core has established only:

```text
$1,247.50 != $1,217.50
```

---

# 17. Example Investigation

The agent retrieves:

```text
Transaction TX-48291
Settlement SET-8821
Fee Rule FR-14
Settlement Processing Policy §4.2
Historical Transactions TX-38182 and TX-19281
```

Evidence records are created for the relevant retrieved information.

The investigation produces:

```json
{
  "classification": "PROCESSOR_FEE",
  "rootCause": "A $30 processor settlement fee was applied.",
  "confidence": 0.94,
  "recommendedAction": "Classify the discrepancy as a processor fee adjustment.",
  "requiresHumanApproval": true,
  "shouldEscalate": false
}
```

The recommendation is linked to evidence:

```text
REC-3011
   |
   +---- EVD-5001 → Fee Rule FR-14
   |
   +---- EVD-5002 → Settlement Policy §4.2
   |
   +---- EVD-5003 → Historical Transaction TX-38182
```

---

# 18. Example Approval

The analyst reviews the recommendation and selects:

```text
APPROVED
```

An Approval record is created:

```json
{
  "approvalId": "APR-9011",
  "recommendationId": "REC-3011",
  "decision": "APPROVED",
  "reviewerId": "analyst-01",
  "reviewerComment": "Fee configuration and historical settlements support the recommendation."
}
```

An audit event is then recorded:

```text
RECOMMENDATION_APPROVED
```

The reconciliation exception may transition:

```text
AWAITING_REVIEW
        ↓
RESOLVED
```

---

# 19. Status Transitions

## ReconciliationException

```text
OPEN
 |
 v
INVESTIGATING
 |
 v
AWAITING_REVIEW
 |
 +----------+-----------+
 |          |           |
 v          v           v
RESOLVED  OPEN      ESCALATED
          (rejected)
```

A rejected recommendation does not automatically mean the underlying exception disappears.

It may return to manual investigation.

---

## Investigation

```text
PENDING
   |
   v
RUNNING
   |
   +-------------+
   |             |
   v             v
COMPLETED      FAILED
   |
   v
ESCALATED
(if insufficient evidence)
```

---

# 20. Important Data Invariants

The implementation must preserve the following rules.

### Financial Precision

Monetary values must never use floating-point arithmetic.

### Detection/Investigation Separation

`ReconciliationException.exception_type` must represent a deterministic discrepancy.

It must not contain speculative AI conclusions.

### Evidence Traceability

Recommendations should reference the evidence used to support them.

### Human Approval

`Approval` records must originate from human-facing application actions, not agent tools.

### Read-Only Agent Access

Agent tools must not provide mutation operations against transactions or settlements.

### Auditability

Important state transitions must produce audit events.

### Retry Support

Multiple investigations may exist for the same reconciliation exception.

### Model Traceability

Investigations should record the model, provider, and prompt version used to produce a recommendation.

### Policy Traceability

Policy evidence should retain document version and section information.

---

# 21. Recommended V1 Indexes

The following indexes should be created initially:

```text
transactions(transaction_id)

transactions(merchant_id)

settlements(settlement_id)

settlements(transaction_id)

reconciliation_exceptions(exception_id)

reconciliation_exceptions(transaction_id)

reconciliation_exceptions(status)

investigations(investigation_id)

investigations(exception_id)

investigation_evidence(investigation_id)

fee_rules(merchant_id)

recommendations(investigation_id)

approvals(recommendation_id)

policy_documents(document_id)

policy_chunks(document_id)

audit_events(resource_id)

audit_events(correlation_id)

audit_events(occurred_at)
```

A vector index for `policy_chunks.embedding` may be introduced once the RAG implementation is established.

For the small V1 corpus, correctness and simplicity take priority over vector-index optimization.

---

# 22. V1 Seed Data Requirements

Development seed data should contain enough variation to demonstrate reconciliation and investigation behavior.

Target:

```text
~200 transactions
```

with a distribution approximately resembling:

```text
165 normally reconciled transactions

10 processor-fee scenarios
8 unexplained amount mismatches
6 missing settlements
4 duplicate settlements
4 currency mismatches
3 ambiguous / escalation scenarios
```

These numbers are illustrative rather than contractual.

The important requirement is that the dataset contains:

- straightforward successful reconciliations;
- explainable exceptions;
- ambiguous exceptions;
- cases with sufficient evidence;
- cases with conflicting evidence; and
- cases where escalation is the correct behavior.

This prevents the demonstration dataset from being designed exclusively around cases the AI can easily solve.

---

# 23. V1 Data Ownership

The system should maintain clear ownership boundaries.

```text
Spring Boot Financial Core owns:

transactions
settlements
reconciliation_exceptions
fee_rules
approvals
audit_events
```

The investigation layer is responsible for producing:

```text
investigations
investigation_evidence
recommendations
```

Policy ingestion manages:

```text
policy_documents
policy_chunks
```

For V1, these entities may reside within the same PostgreSQL instance.

Shared physical storage does not imply shared application ownership.

The agent should still access financial-domain information through controlled APIs/tools rather than direct unrestricted SQL access.

---

# 24. V1 Data Model Summary

The model intentionally creates a traceable chain:

```text
Financial Fact
     |
     v
Transaction + Settlement
     |
     v
Deterministic Discrepancy
     |
     v
ReconciliationException
     |
     v
AI Investigation
     |
     v
Retrieved Evidence
     |
     v
Recommendation
     |
     v
Human Decision
     |
     v
Audit Trail
```

This separation allows ReconAI to use probabilistic AI capabilities without making probabilistic outputs authoritative financial facts.

The database therefore reflects the project's primary architectural principle:

> **Deterministic systems detect. AI investigates. Humans authorize.**