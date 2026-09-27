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
    v
Recommendation
    |
    +------> RecommendationEvidence
    |
    v
Review (human decision)

Policy corpus (Markdown on disk)

All investigation lifecycle events
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
TX-48291     transaction        (Financial Core)
SET-8821     settlement         (Financial Core)
EX-1042      exception          (Financial Core)
FR-14        fee rule           (Financial Core)
INV-2041     investigation      (Investigation Service)
REC-3011     recommendation     (Investigation Service)
REV-7001     review             (Investigation Service)
AUD-9001     audit event        (Investigation Service)
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

Represents the durable investigation **case** for a reconciliation exception.

An Investigation is the ongoing question "why does this discrepancy exist?" — not a
single attempt at answering it. Processing the same exception again does not create
another Investigation; it continues the existing one.

The Investigation is separate from the exception because:

- an exception is deterministic, while its explanation is not;
- an exception states what disagrees, and an investigation pursues why;
- an investigation has a lifecycle of its own that outlives any one attempt; and
- an investigation is advisory, and must never alter the exception it examines.

## Table

```text
investigations
```

## Fields

| Field | Type | Required | Description |
|---|---|---:|---|
| id | UUID | Yes | Internal primary key |
| investigation_id | VARCHAR(50) | Yes | Unique business ID, e.g. `INV-1001` |
| exception_id | VARCHAR(50) | Yes | Exception being investigated; **UNIQUE** |
| transaction_id | VARCHAR(50) | Yes | Transaction the exception concerns |
| exception_type | VARCHAR(50) | Yes | Deterministic discrepancy type, carried from the event |
| status | VARCHAR(30) | Yes | Investigation lifecycle |
| detected_at | TIMESTAMPTZ | Yes | When the financial core detected the discrepancy |
| created_at | TIMESTAMPTZ | Yes | When this record was created |
| updated_at | TIMESTAMPTZ | Yes | Last modification time |

`exception_id` and `transaction_id` are business identifiers belonging to the financial
core. They are stored as plain references, never as foreign keys: authoritative detail is
retrieved through the financial core's API, not by joining across an ownership boundary.

`detected_at` is deliberately distinct from `created_at`. The first is when the
discrepancy existed; the second is when this service happened to hear about it.

### Fields not present

Model provider, model name, prompt version, confidence, start and completion times, and
failure reason are **not** fields of Investigation. They describe a single execution
attempt or its result, not the case, and belong to whichever execution and recommendation
concepts are designed later.

## InvestigationStatus

```text
PENDING
RUNNING
COMPLETED
FAILED
ESCALATED
```

Only `PENDING` is reachable today: the status is set when the case is opened, and nothing
yet advances it.

## Relationship

```text
ReconciliationException 1 ---- 0..1 Investigation
```

One reconciliation exception has **zero or one** Investigation.

This is a deliberate choice, and the database enforces it with a unique constraint on
`exception_id` rather than leaving it to application logic. Event delivery is
at-least-once and duplicate deliveries can arrive concurrently, so a check-then-insert
would let two through.

The consequences are intentional:

1. **A reconciliation exception has at most one Investigation.**
2. **Redelivery or reprocessing reuses the existing Investigation** rather than opening a
   second case for the same discrepancy.
3. **Investigation identity is stable.** `INV-1001` refers to the same case for the
   lifetime of the exception, however many times the event is processed, so anything that
   references an investigation — a log line, an audit record, an analyst's note — stays
   valid.

### If execution history is needed later

Retries, model versions and per-attempt timings are real concerns, and this model does not
discard them. It says they are not *Investigations*. A second execution of an
investigation is another attempt at one case, not a second case.

Should that history be required, it belongs in a separate concept — `InvestigationRun` or
`InvestigationAttempt` — related as:

```text
Investigation 1 ---- 0..* InvestigationRun
```

with the per-attempt fields listed under "Fields not present" living there. Creating
several Investigation records for one exception would fragment the case, break the
stability described above, and make "the investigation into EX-1042" ambiguous.

**This is not implemented.** It is recorded here so the extension point is deliberate
rather than discovered.

---

# 7. InvestigationEvidence — superseded

**Not implemented.** Superseded by section 10, `RecommendationEvidence`.

Evidence is only ever retrieved in the course of producing one recommendation,
and only evidence that passed grounding validation is persisted at all, so a
separate evidence table joined to recommendations had no second side to its
many-to-many relationship. The two tables would have held the same rows.

Retained as a numbered heading so the surrounding section numbers, which are
referenced elsewhere, do not shift.

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
| merchant_id | VARCHAR(50) | No | Merchant-specific rule; null applies to all merchants on the processor |
| processor | VARCHAR(100) | Yes | Applicable processor |
| fee_type | VARCHAR(50) | Yes | Type of fee |
| fee_amount | NUMERIC(19,4) | Yes | Fixed fee |
| currency | VARCHAR(3) | Yes | Currency of the fee |
| description | TEXT | Yes | Rule description |
| active | BOOLEAN | Yes | Whether rule is active |
| created_at | TIMESTAMPTZ | Yes | Creation time |

### Fields not implemented

`fee_percentage`, `effective_from` and `effective_to` are deliberately absent from the
implemented schema.

V1 models fixed-amount fees only, which is why `fee_amount` and `currency` are required
here where a percentage-capable model would make both optional. Adding percentage rules
later means relaxing those two columns and adding `fee_percentage`.

Temporal validity is represented by `active` alone. Effective-date windows imply
point-in-time rule resolution — deciding which rule applied on the date a settlement
occurred — which is real logic with real edge cases and no current use. `active`
answers the only question asked today: is this fee currently charged.

## FeeType

```text
FIXED
PERCENTAGE
NETWORK
CROSS_BORDER
PROCESSING
```

A fee rule is **evidence**, not a verdict. It records that a fee of this shape applies
to a processor. It never records that a particular transaction was charged one:
establishing that requires the transaction, its settlements and a judgement, none of
which live here. Deterministic reconciliation does not read this table, and a
difference that happens to equal a fee amount is still an `AMOUNT_MISMATCH`.

This table allows:

```text
get_fee_rules(merchant_id)
```

to return structured authoritative information without requiring the LLM to extract every fee from documents.

---

# 9. Recommendation

## Purpose

Represents the structured conclusion produced by an investigation.

Recommendations are advisory. They do not modify authoritative financial
records, and nothing in the system acts on one without a human decision.

At most one per investigation. A second would make "the AI's conclusion" an
ambiguous phrase, so `UNIQUE(investigation_id)` settles it in the database
rather than in application code that two concurrent runs could both pass.

## Table

```text
investigation.recommendations
```

## Fields

| Field | Type | Required | Description |
|---|---|---:|---|
| id | UUID | Yes | Internal primary key |
| recommendation_id | VARCHAR(50) | Yes | Unique business ID (`REC-`) |
| investigation_id | VARCHAR(50) | Yes | Related investigation; **UNIQUE** |
| classification | VARCHAR(50) | Yes | Proposed root cause |
| root_cause | TEXT | Yes | Explanation |
| confidence | NUMERIC(5,4) | Yes | Model self-reported confidence — see below |
| recommended_action | TEXT | Yes | Proposed analyst action |
| requires_human_approval | BOOLEAN | Yes | CHECK-constrained to true |
| model_provider | VARCHAR(50) | No | Which provider produced this |
| model_name | VARCHAR(100) | No | Which model produced this |
| prompt_version | VARCHAR(50) | No | Which instructions produced this |
| created_at | TIMESTAMPTZ | Yes | Creation time |

`should_escalate` from the earlier draft is absent, deliberately. Whether
something is escalated is decided by the deterministic guardrail, not proposed
by the model, and it is recorded as the investigation's status. A model-supplied
boolean next to it would be a second, contradictable answer to a question the
policy already owns.

### Confidence is not a probability

`confidence` is the model's own self-report. It is **not calibrated**: 0.9 does
not mean nine such conclusions in ten are correct. It is used only as an
ordering signal compared against an operator-configured threshold, and every API
response carrying it says so. Nothing should present it as a statistical claim.

It is `NUMERIC(5,4)` and never a float, so a stored confidence reads back as
what was written and a threshold comparison cannot turn on binary rounding.

### Database-enforced invariants

```text
CHECK requires_human_approval = true
CHECK confidence BETWEEN 0 AND 1
CHECK classification IN (the seven RootCauseClassification values)
UNIQUE (investigation_id)
```

The first is the one that matters most: a recommendation that did not require
human approval would be an autonomous decision, and the database refuses to
store one even if every layer of application code were wrong.

### What is deliberately not stored

The prompt, the provider's raw request or response, and anything resembling
model reasoning. Only the structured result a reviewer needs, plus enough
operational metadata to know what produced it.

## RootCauseClassification

```text
PROCESSOR_FEE
PROCESSOR_DELAY
DUPLICATE_PROCESSING
CURRENCY_CONVERSION
PROCESSOR_ERROR
UNKNOWN
INSUFFICIENT_EVIDENCE
```

This enum is intentionally different from `ExceptionType`, and both differences
are enforced by database CHECK constraints in their respective schemas:
`PROCESSOR_FEE` can never be a detected exception type, and `AMOUNT_MISMATCH`
can never be a root-cause classification.

For example:

```text
ExceptionType:

AMOUNT_MISMATCH

        ↓ investigation

RootCauseClassification:

PROCESSOR_FEE
```

`INSUFFICIENT_EVIDENCE` is a first-class successful outcome, not a failure. An
investigation reporting that the evidence does not support a conclusion has done
its job; it is escalated to a human rather than recorded as having failed.

---

# 10. RecommendationEvidence

## Purpose

Records exactly which evidence items support a recommendation.

This matters because a recommendation must not merely contain prose claiming
that evidence exists. "According to the fee schedule" cannot be checked;
`FEE_RULE / FR-14` can be, and is.

**Only references that passed grounding validation are stored here.** During an
investigation the application keeps an in-memory ledger of what the tools
actually returned, and every citation in a proposed result is checked against
it. A result citing anything unverifiable is rejected in full — no conclusion is
stored with the bad citation dropped. So the question "what evidence did the AI
actually use?" has an answer that can be trusted later.

## Table

```text
investigation.recommendation_evidence
```

## Fields

| Field | Type | Required | Description |
|---|---|---:|---|
| id | UUID | Yes | Internal primary key |
| recommendation_id | VARCHAR(50) | Yes | Recommendation this supports (FK) |
| source_type | VARCHAR(50) | Yes | `TRANSACTION`, `SETTLEMENT`, `FEE_RULE`, `POLICY_DOCUMENT` |
| reference | VARCHAR(255) | Yes | Business identifier of the cited record |
| section | VARCHAR(255) | No | Which section, for policy evidence |
| excerpt | TEXT | No | The retrieved text, for policy evidence |
| created_at | TIMESTAMPTZ | Yes | Creation time |

`excerpt` is stored only for policy evidence, so a reviewer reads the words the
investigation actually saw rather than whatever the corpus says by the time they
look. Financial records are cited by identifier and re-read from the Financial
Core, which remains their source of truth — copying their values here would
create a second one that silently goes stale.

`recommendation_id` is a real foreign key: both tables belong to this service,
and evidence without its recommendation is meaningless. It is the only foreign
key in the schema, and it points inward. There are none across the service
boundary in either direction.

## Consolidation from the earlier draft

The earlier draft modelled this as two tables — `investigation_evidence` holding
evidence, and a `recommendation_evidence` join table linking it to
recommendations. As implemented there is one table.

Evidence is only ever retrieved in the course of producing one recommendation,
and only grounded evidence is persisted at all, so the many-to-many relationship
had no second side. Two tables would have held the same rows with a distinction
nothing consumed. Section 7 (`InvestigationEvidence`) is superseded by this one.

---

# 11. Review (implemented; formerly "Approval")

## Purpose

Represents the human decision applied to an AI-generated recommendation.

Named `reviews` rather than `approvals` because a review records whichever
decision was made. Naming the table after one outcome made rejection read like
an exception to the normal path, when it is an equally normal path.

## Table

```text
investigation.reviews
```

Owned by the Python Investigation Service. See section 23.

## Fields

| Field | Type | Required | Description |
|---|---|---:|---|
| id | UUID | Yes | Internal primary key |
| review_id | VARCHAR(50) | Yes | Unique business ID (`REV-`) |
| investigation_id | VARCHAR(50) | Yes | Investigation being decided; UNIQUE |
| recommendation_id | VARCHAR(50) | Yes | Recommendation being reviewed (FK) |
| decision | VARCHAR(30) | Yes | Human decision |
| reviewed_by | VARCHAR(255) | Yes | Caller-supplied reviewer name — see below |
| comment | TEXT | No | Optional explanation |
| decided_at | TIMESTAMPTZ | Yes | Decision timestamp |

`UNIQUE(investigation_id)` is the important one: a decision that can be
overwritten is not a decision, and two reviewers submitting at once must not
both be recorded. The constraint, not application code, is what makes that true.

## ReviewDecision

```text
APPROVED
REJECTED
ESCALATED
```

There is deliberately no value meaning "approved without a human". The database
CHECK constraint enforces the set.

## Resulting investigation status

| Decision | Investigation becomes | Why |
|---|---|---|
| APPROVED | COMPLETED | A human accepted the explanation. |
| REJECTED | ESCALATED | The explanation was not accepted, but the discrepancy still exists and still needs a human. |
| ESCALATED | ESCALATED | The reviewer declined to decide and passed it on. |

## Important invariants

An AI agent must never create a review. Reviews are created only by the human
review endpoints.

**Approval does not act.** Recording an approval writes to this service's own
tables and makes no call to the Financial Core — no POST, PUT, PATCH or DELETE.
No exception is resolved, no settlement is altered, and no money moves. Acting
on an approved recommendation is a separate, deliberate step outside this
service.

**`reviewed_by` is not an identity.** V1 has no authentication. The value is
whatever the caller sent, recorded verbatim as the claim it is. Every API
response carrying it says so. Do not build anything on it that assumes
otherwise.

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

Append-only. There is no update path and no delete path — not in the service,
not in the API. A trail that can be revised is not evidence of anything.

## Table

```text
investigation.audit_events
```

Owned by the Python Investigation Service. See section 23 for why the AI
lifecycle trail lives here rather than in the Financial Core.

## Fields

| Field | Type | Required | Description |
|---|---|---:|---|
| id | UUID | Yes | Internal primary key |
| event_id | VARCHAR(50) | Yes | Unique business ID (`AUD-`) |
| sequence_no | BIGINT | Yes | Sequence value behind `event_id`; UNIQUE |
| investigation_id | VARCHAR(50) | Yes | Investigation this event belongs to |
| event_type | VARCHAR(50) | Yes | Audit event classification |
| actor_type | VARCHAR(30) | Yes | Source of action |
| actor_id | VARCHAR(255) | No | Specific actor; set only for HUMAN events |
| metadata | JSONB | No | Small non-sensitive summary |
| occurred_at | TIMESTAMPTZ | Yes | Event timestamp (`clock_timestamp()`) |

`sequence_no` exists because the trail needs a **total** order and neither
alternative provides one: several events are committed in a single transaction,
and ordering by `event_id` as text would sort `AUD-10001` before `AUD-9001`.
`occurred_at` uses `clock_timestamp()` rather than `now()` so it records when
the event happened rather than when its transaction began.

`resource_type` / `resource_id` / `correlation_id` from the earlier draft are
absent. Every event in this table is about exactly one investigation, so a
generic resource reference would only ever hold the same value that
`investigation_id` already holds.

## What metadata may contain

A classification, a confidence, an evidence count, a guardrail reason, a failure
category, the threshold that was applied. Enough to re-derive why something was
routed the way it was.

**Never** a prompt, a provider request or response, a credential, or anything
resembling model reasoning. This table is read by humans reviewing decisions; it
is not a debugging sink, and private chain-of-thought is neither requested,
persisted, nor exposed anywhere in the system.

## ActorType

```text
SYSTEM    application logic: lifecycle transitions and guardrail routing
AI        the model produced a result
HUMAN     a person decided
```

## AuditEventType

Implemented values, all of them investigation-lifecycle events:

```text
INVESTIGATION_CREATED

INVESTIGATION_STARTED

AI_RESULT_GENERATED

INVESTIGATION_AWAITING_REVIEW

INVESTIGATION_ESCALATED

INVESTIGATION_FAILED

REVIEW_APPROVED

REVIEW_REJECTED
```

Deliberately few. `TOOL_CALLED` and `EVIDENCE_RETRIEVED` from the earlier draft
are absent: a durable row per tool call would bury the events a reviewer
actually needs, and what the tools returned is already recorded, verified, as
`recommendation_evidence`. Tool activity is logged, not audited.

The `TRANSACTION_*`, `SETTLEMENT_*` and `RECONCILIATION_*` values are also
absent. Those are Financial Core state changes; this service does not observe
them and must not claim to have recorded them.

## Transactional guarantee

Each event is written in the same transaction as the state change it describes.
An audit entry that survived a rolled-back transition would be a record of
something that never happened.

---

# 15. Entity Relationships

The primary domain relationships are:

```text
=========== Financial Core (schema: public) ===========

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

======== no foreign keys cross this line ==============

========= Investigation Service (investigation) ========

ReconciliationException.exception_id
    |                       (a string reference, not a FK)
    | 0..1
    v
Investigation
    |
    | 1
    |
    | 0..1
    v
Recommendation
    |                       \
    | 1                      \ 1
    |                         \
    | 0..*                     \ 0..1
    v                           v
RecommendationEvidence        Review


AuditEvent  --> Investigation  (by investigation_id; append-only)
```

## The boundary

Nothing in the `investigation` schema holds a foreign key into `public`, and
nothing in `public` holds one into `investigation`. The Investigation Service
stores Financial Core business identifiers as plain string columns and resolves
them through the Financial Core's read-only API when it needs the records.

This is deliberate. A foreign key would couple one service's writes to another
service's schema and make the ownership boundary decorative. The only foreign
keys in the `investigation` schema point at `recommendations`, which the same
service owns.

## Cardinality notes

`Investigation` is 0..1 per exception, not 0..*. Reprocessing an exception
continues the existing case rather than opening another; see section 6.

`Recommendation` is 0..1 per investigation and `Review` is 0..1 per
investigation. Both are enforced by unique constraints, because both are
questions that must have exactly one answer.

`AuditEvent` is many-per-investigation, ordered by `sequence_no`, and references
its investigation by business identifier rather than generically — every event
in the table is about exactly one investigation.

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
  "requiresHumanApproval": true
}
```

The recommendation is linked to the evidence it was validated against. Each row
is a reference the application confirmed a tool really returned:

```text
REC-3011
   |
   +---- FEE_RULE / FR-14
   |
   +---- POLICY_DOCUMENT / POL-SETTLEMENT-001 §4.2
   |
   +---- SETTLEMENT / SET-8821
```

A citation that could not be traced to a retrieval would have caused the whole
result to be rejected, so nothing in this list is a claim about something
nobody read.

---

# 18. Example Review

The analyst reviews the recommendation and selects:

```text
APPROVED
```

A Review record is created:

```json
{
  "reviewId": "REV-7001",
  "investigationId": "INV-2041",
  "recommendationId": "REC-3011",
  "decision": "APPROVED",
  "reviewedBy": "analyst-01",
  "comment": "Fee configuration and settlement policy support the recommendation.",
  "decidedAt": "2026-09-27T12:04:00Z"
}
```

The investigation becomes `COMPLETED`, and an audit event records that a human
made the decision.

## What approval does not do

Approval records a human judgement about an explanation. It does not:

- resolve the reconciliation exception;
- modify any transaction or settlement;
- move money; or
- issue any write of any kind to the Financial Core.

The Investigation Service has no write path to the Financial Core — the review
service holds no client to it at all. Acting on an approved recommendation is a
separate, deliberate step outside this service.

`reviewedBy` is caller-supplied and unverified. V1 has no authentication.

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
   |  run: claimed by a conditional UPDATE
   v
RUNNING
   |
   +---------------------------+---------------------+
   |                           |                     |
   |  guardrail: confident     |  guardrail:         |  execution
   |  and grounded             |  anything weaker    |  failure
   v                           v                     v
AWAITING_REVIEW            ESCALATED              FAILED
   |
   +-------------+----------------+
   |             |                |
   | approved    | rejected       | escalated
   v             v                v
COMPLETED    ESCALATED        ESCALATED
```

Every transition is made by application code. The model proposes an explanation
and never decides what state the workflow is in.

**Only a human reaches COMPLETED.** No confidence value is a shortcut: the
deterministic guardrail has exactly two outcomes, `AWAITING_REVIEW` and
`ESCALATED`, and neither completes anything.

`RUNNING` is claimed with `UPDATE ... WHERE status = 'PENDING' RETURNING`, so
concurrent run requests are resolved by the database rather than by a
read-then-write that both would pass. A losing request is refused before any
model call is made.

A crash between claiming and recording leaves an investigation in `RUNNING`.
That is deliberate: the work was started and its outcome is unknown, which is an
honest state. There is no background scheduler and no distributed lock to
quietly undo it; recovering is an operational decision.

`ESCALATED` is reached three ways — by the guardrail, by rejection, or by a
reviewer explicitly passing it on — and is terminal in this service. Rejection
escalates rather than resolving because a rejected explanation does not make the
underlying discrepancy go away.

---

## Transaction boundaries

A model call is slow, external, and cannot participate in a database
transaction. The run path is therefore three short transactions with the call
between them, and nothing is held open while it runs:

```text
TX1   claim PENDING -> RUNNING, audit INVESTIGATION_STARTED        commit
      |
      |   (no transaction open)
      v
      agent: bounded tool loop, model call, grounding validation
      |
      v
TX2   recommendation + evidence + guardrail status + audit         commit
```

On failure, a third transaction records `FAILED` and the reason.

Database and model are not atomic together, and nothing here pretends
otherwise. What *is* atomic is TX2: the recommendation, its evidence, the
resulting status and the audit events commit together, because a recommendation
with no status change is invisible and a status change with no recommendation is
unexplainable.

---

# 20. Important Data Invariants

The implementation must preserve the following rules.

### Financial Precision

Monetary values must never use floating-point arithmetic.

### Detection/Investigation Separation

`ReconciliationException.exception_type` must represent a deterministic discrepancy.

It must not contain speculative AI conclusions.

### Evidence Traceability

Recommendations must reference the evidence used to support them, and every
reference must be verified against what the tools actually returned before the
result is accepted. A result citing anything unverifiable is rejected in full;
no conclusion is stored with the bad citation removed.

### Human Approval

`Review` records must originate from human-facing application actions, not agent
tools. No agent tool can create one, and no code path reachable from a model can.

Approval is a judgement, not an action: recording one writes only to the
Investigation Service's own tables and issues no request to the Financial Core.

`reviewed_by` is caller-supplied and unauthenticated in V1. It records a claim,
not a verified identity, and every response carrying it says so.

### Deterministic Guardrails

Routing between human review and escalation is decided by application code from
the stored result, never by the model. The same result must always route the
same way, and the reason must be stored alongside it so the decision can be
re-derived later.

### Confidence Is Not Calibrated

`Recommendation.confidence` is a model self-report used only as an ordering
signal against a configured threshold. It must never be presented as a
probability.

### No Persisted Reasoning

Private chain-of-thought is never requested, persisted, or exposed. Stored
artifacts hold the structured result and operational metadata only — never a
prompt, a provider payload, or model reasoning.

### Read-Only Agent Access

Agent tools must not provide mutation operations against transactions or settlements.

### Auditability

Important state transitions must produce audit events.

### Investigation Identity

A reconciliation exception has at most one Investigation, enforced by a unique constraint
on `exception_id`. Reprocessing an exception continues the existing case rather than
opening another, so an investigation identifier means the same thing forever.

Retrying execution is a separate matter from creating a case; see section 6.

### Model Traceability

The model, provider and prompt version used to produce a recommendation must be recorded.
They describe an execution rather than the investigation case, so they belong to the
execution or recommendation concept, not to the Investigation record.

### Policy Traceability

Policy evidence should retain document version and section information.

---

# 21. Recommended V1 Indexes

## Financial Core (schema: public)

```text
transactions(transaction_id)

transactions(merchant_id)

settlements(settlement_id)

settlements(transaction_id)

reconciliation_exceptions(exception_id)

reconciliation_exceptions(transaction_id)

reconciliation_exceptions(status)

fee_rules(merchant_id)
```

## Investigation Service (schema: investigation)

```text
investigations(investigation_id)        unique constraint

investigations(exception_id)            unique constraint — the idempotency guarantee

investigations(status)

investigations(transaction_id)

recommendations(recommendation_id)      unique constraint

recommendations(investigation_id)       unique constraint — one conclusion per investigation

recommendation_evidence(recommendation_id)

reviews(review_id)                      unique constraint

reviews(investigation_id)               unique constraint — one decision per investigation

audit_events(event_id)                  unique constraint

audit_events(sequence_no)               unique constraint

audit_events(investigation_id, sequence_no)
```

Several of these are unique constraints rather than plain indexes, and that is
the point: each one is a correctness guarantee that concurrent requests would
otherwise be able to violate, and it happens to index the column as a side
effect.

The composite `(investigation_id, sequence_no)` serves the only way the trail is
read — one investigation's timeline, in order.

No index on `policy_chunks`: the policy corpus is Markdown on disk in V1, with
no embeddings and no vector database. For a corpus this size, lexical search is
correct, explainable and fast enough, and an unexplainable retrieval step would
undercut the evidence guarantees the rest of the system rests on.

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
Spring Boot Financial Core owns (schema: public):

transactions
settlements
reconciliation_exceptions
fee_rules
```

```text
Python Investigation Service owns (schema: investigation):

investigations
recommendations
recommendation_evidence
reviews
audit_events
```

Policy knowledge is not a database concern in V1: the corpus is a directory of
Markdown documents on disk, searched lexically. See `policies/README.md`.

For V1, both schemas reside within the same PostgreSQL instance.

Shared physical storage does not imply shared application ownership. The
separate schema is what makes the boundary enforceable rather than conventional,
and there are no foreign keys across it in either direction.

## Why human review and audit moved to the Investigation Service

An earlier draft of this section assigned `approvals` and `audit_events` to the
Financial Core. As implemented they belong to the Investigation Service, for two
reasons:

1. **What is being reviewed is an AI recommendation, not a financial record.** A
   review decides whether an explanation is acceptable. It does not resolve an
   exception, settle anything, or move money — no approval path in this system
   writes to the Financial Core. Storing that decision next to the
   recommendation it judges keeps the two from drifting apart, and lets a single
   unique constraint guarantee one decision per investigation.

2. **The audit trail being recorded is the AI lifecycle.** Its events are
   "investigation started", "result generated", "escalated by policy",
   "approved by a human" — all of which happen in the Investigation Service and
   none of which the Financial Core observes. Writing them from the service that
   causes them is what lets each event commit in the same transaction as the
   state change it describes.

If the Financial Core later needs its own audit trail for its own state changes,
that is a separate table owned by it, recording different events. The two are
not the same concept and should not be merged.

## Terminology

The implemented table is `reviews`, not `approvals`. A review records whichever
decision a human made — approved, rejected, or escalated — and naming the table
after only one of those outcomes made rejection look like an afterthought.

`recommendation_evidence` replaces the earlier pair of `investigation_evidence`
and `recommendation_evidence`. Evidence is only ever retrieved in the course of
producing one recommendation, so two tables would have held the same rows with a
distinction nothing consumed.

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
     v                          deterministic: rule precedence, BigDecimal
Deterministic Discrepancy
     |
     v
ReconciliationException
     |
     v                          published on Kafka after commit
AI Investigation
     |
     v                          bounded tool loop; only allowlisted reads
Retrieved Evidence
     |
     v                          every citation verified against what was retrieved
Recommendation
     |
     v                          deterministic guardrail: review or escalate
Human Decision
     |
     v                          append-only, one transaction per state change
Audit Trail
```

Each arrow narrows what the next stage is allowed to assert. The deterministic
engine decides *that* something is wrong; the investigation may only cite
evidence it actually retrieved; the guardrail — not the model — decides what a
human sees; and only a human completes anything.

This separation allows ReconAI to use probabilistic AI capabilities without making probabilistic outputs authoritative financial facts.

The database therefore reflects the project's primary architectural principle:

> **Deterministic systems detect. AI investigates. Humans authorize.**