# ReconAI — System Architecture

## 1. Architecture Overview

ReconAI is an event-driven payment reconciliation and exception-investigation platform.

The system deliberately separates financial reconciliation from AI-assisted investigation.

The core architectural principle is:

> **Deterministic systems detect. AI investigates. Humans authorize.**

The deterministic financial core is responsible for identifying discrepancies between authoritative transaction and settlement records.

When an exception is detected, an asynchronous event triggers an AI-assisted investigation. The investigation agent retrieves information through explicitly defined tools, gathers supporting evidence, searches relevant financial policies, and produces a structured recommendation.

The recommendation is advisory and requires human review before the exception can be considered resolved.

---

## 2. High-Level Architecture

```text
                           ┌───────────────────────┐
                           │       React UI        │
                           │  Operations Console   │
                           └───────────┬───────────┘
                                       │
                                    REST API
                                       │
                                       ▼
                           ┌───────────────────────┐
                           │     Spring Boot       │
                           │    Financial Core     │
                           └───────────┬───────────┘
                                       │
                 ┌─────────────────────┼──────────────────────┐
                 │                     │                      │
                 ▼                     ▼                      ▼
          Transactions            Settlements        Reconciliation
                                                            │
                                                     Exception detected
                                                            │
                                                            ▼
                                                          Kafka
                                              reconciliation.exceptions
                                                            │
                                                            ▼
                                                ┌────────────────────┐
                                                │ Python / FastAPI   │
                                                │ Investigation      │
                                                │ Agent Service      │
                                                └─────────┬──────────┘
                                                          │
                       ┌──────────────────────────────────┼───────────────────┐
                       │                  │               │                   │
                       ▼                  ▼               ▼                   ▼
               Transaction Tool    Settlement Tool   Fee Rule Tool      History Tool
                       │                  │               │                   │
                       └──────────────────┴───────┬───────┴───────────────────┘
                                                  │
                                                  ▼
                                           Policy Search
                                               (RAG)
                                                  │
                                                  ▼
                                          PostgreSQL/pgvector
                                                  │
                                                  ▼
                                       Evidence-backed Result
                                                  │
                                                  ▼
                                            Human Review
                                           Approve / Reject
                                                  │
                                                  ▼
                                             Audit Trail
```

---

## 3. Major Components

### 3.1 Operations Console

**Technology:** React + TypeScript + Vite

The Operations Console provides the user interface for reconciliation analysts.

Primary responsibilities:

- display reconciliation statistics;
- display the exception queue;
- show transaction and settlement details;
- display investigation progress;
- display AI-generated recommendations;
- show supporting evidence;
- allow analysts to approve, reject, or escalate recommendations; and
- display audit history.

The frontend does not communicate directly with the database, Kafka, or the LLM.

All application operations are performed through backend APIs.

---

## 3.2 Financial Core

**Technology:** Java 21 + Spring Boot 3

The Financial Core is the authoritative application layer for financial records and reconciliation.

Primary responsibilities:

- manage transactions;
- manage settlements;
- execute deterministic reconciliation;
- create reconciliation exceptions;
- expose REST APIs;
- publish reconciliation-exception events;
- manage investigation state;
- process human approval decisions; and
- persist audit events.

The Financial Core must not depend on the AI service to determine whether financial records reconcile.

If the AI service is unavailable, deterministic reconciliation must continue to function.

---

## 4. Deterministic Reconciliation Engine

The Reconciliation Engine compares authoritative transaction and settlement data using explicit business rules.

Example:

```text
Transaction
Expected Settlement = $1,247.50

Settlement
Actual Settlement = $1,217.50
```

The engine calculates:

```text
Difference = $30.00
```

and produces:

```text
Exception Type = AMOUNT_MISMATCH
```

This process does not involve an LLM.

### Initial Detection Rules

The V1 engine supports deterministic detection of:

```text
AMOUNT_MISMATCH
MISSING_SETTLEMENT
DUPLICATE_SETTLEMENT
CURRENCY_MISMATCH
```

`PROCESSOR_FEE` is primarily an investigation/root-cause classification rather than a deterministic discrepancy type.

For example:

```text
AMOUNT_MISMATCH
      |
      v
AI Investigation
      |
      v
PROCESSOR_FEE
```

This separation prevents assumptions about business causes from being embedded into basic financial comparison logic.

---

## 5. Event-Driven Investigation

AI investigations are intentionally asynchronous.

When the reconciliation engine detects an exception, the Financial Core publishes an event to Kafka.

### Topic

```text
reconciliation.exceptions
```

Example event:

```json
{
  "eventId": "EVT-1001",
  "exceptionId": "EX-1042",
  "transactionId": "TX-48291",
  "exceptionType": "AMOUNT_MISMATCH",
  "detectedAt": "2026-09-26T14:32:00Z"
}
```

The investigation service consumes this event and begins an investigation.

### Why Asynchronous Processing?

LLM-based investigation introduces:

- variable response latency;
- external API dependencies;
- potentially expensive operations;
- independent failure modes; and
- retry requirements.

The financial reconciliation workflow should not wait for an AI investigation to complete.

Kafka therefore decouples:

```text
Financial Reconciliation
```

from:

```text
AI Investigation
```

If the AI service becomes unavailable, reconciliation can continue and exceptions can remain queued for later investigation or manual review.

---

### 5.1 Implemented Publication Flow

The financial core publishes; it never consumes.

```text
ReconciliationService
        |
        v
ReconciliationException persisted
        |
        v
AFTER_COMMIT event
        |
        v
Kafka: reconciliation.exceptions
        |
        v
Investigation Service consumer
        |
        v
Pydantic validation
        |
        v
(investigation: not yet implemented)
```

**Why Kafka exists.** AI investigation has variable latency and independent failure modes. Placing a queue between detection and investigation means a slow, failing or entirely absent investigation service cannot affect whether the financial core establishes that two authoritative records disagree.

The Python service consumes the topic under the fixed group `reconai-investigation-service`, with `auto.offset.reset=latest` and manual commits after each record. Delivery is at-least-once, so investigation handling must be idempotent by `exceptionId` once it exists. Today the consumer validates the event against the contract and logs it; nothing is investigated, fetched or persisted. See `agent-service/README.md` for the consumer's offset, readiness and error-handling behaviour.

---

### 5.2 Why Publication Happens After Commit

An event is raised in-process at the moment a new `ReconciliationException` row is created, inside the same database transaction. A `@TransactionalEventListener(phase = AFTER_COMMIT)` holds it until that transaction commits, and only then forwards it to Kafka.

The rule this protects: **never investigate an exception that rolled back.** Publishing beside the insert would announce discrepancies that never existed, and an investigation agent would spend real time and real model budget on a row no longer in the database.

`fallbackExecution` is left at its default of `false`. With no transaction in progress there is nothing to commit, and nothing is published.

Two distinct failure modes follow, and they are deliberately not treated alike:

```text
Database transaction rolls back
    -> no AFTER_COMMIT callback
    -> no Kafka event
    -> nothing was persisted, so nothing may be investigated

Database commit succeeds, Kafka publication then fails
    -> the financial exception remains persisted and readable
    -> the publication failure is logged
    -> V1 may lose the investigation trigger
```

A Kafka failure must never roll back or invalidate an already committed financial exception. Financial correctness does not depend on message delivery.

---

### 5.3 Publication Is Once Per Detection, Not Once Per Request

Reconciliation is idempotent: re-running it over unchanged records reuses the existing unresolved exception. Publication follows creation, not the API call, so:

```text
first reconciliation   -> creates EX-1001 -> one event
second reconciliation  -> reuses EX-1001  -> no event
third reconciliation   -> reuses EX-1001  -> no event

successful reconciliation            -> no exception -> no event
changed discrepancy                  -> new exception -> new event
resolved exception, same discrepancy
recurring                            -> new exception -> new event
```

Without this, polling the reconciliation endpoint would repeatedly re-trigger investigation of one discrepancy.

The transaction business ID is the message key, so all events concerning one transaction share a partition and reach a consumer in order.

---

### 5.4 V1 Delivery Limitation

A database commit and a Kafka publication are **not atomic**. If the process dies after the commit and before the send, the event is lost: a real discrepancy sits persisted in the database with no investigation ever triggered. It remains visible through the exception APIs and can be re-detected, but nothing automatic will notice it.

The reconciliation request does not wait for Kafka acknowledgement. Producer-side synchronous blocking is bounded using `max.block.ms`, while send completion or failure is handled asynchronously for logging. The guarantee being protected is that financial reconciliation does not wait for AI investigation.

**Production hardening path: transactional outbox.** Write the event to an outbox table in the same transaction as the exception, then relay it to Kafka separately with at-least-once delivery and an idempotent consumer. That is deliberately not built in V1, where the added machinery would outweigh the risk carried by synthetic data.

---

### 5.5 Implemented Event Payload

```json
{
  "exceptionId": "EX-1042",
  "transactionId": "TX-48291",
  "type": "AMOUNT_MISMATCH",
  "detectedAt": "2026-09-26T14:32:00Z"
}
```

Identity and classification only. No internal UUID, no settlement, no amounts, no merchant, and nothing from the investigation layer. A consumer that needs authoritative detail retrieves it through the controlled read APIs, so the event never becomes a second, drifting copy of financial data.

No `__TypeId__` header is attached: Java class names are meaningless to a Python consumer and leak internal structure. Timestamps are serialised by the application's own `ObjectMapper`, so the message and the REST API render an instant identically.

Note that this payload is narrower than the illustrative examples earlier in this section and in `api-contract.md` section 10, which also show `eventId`, `eventType`, `eventVersion` and `correlationId`. Those are envelope concerns worth adding when a consumer exists to need them; V1 carries the minimum a consumer must have to start an investigation.

---

## 6. Investigation Agent Service

**Technology:** Python + FastAPI

The Investigation Agent Service is responsible for investigating detected reconciliation exceptions.

It receives an exception identifier and determines what information is required to investigate the discrepancy.

The agent does not receive unrestricted access to application databases.

Instead, it interacts with the system through explicitly defined tools.

---

## 7. Agent Tools

### 7.1 get_transaction

```text
get_transaction(transaction_id)
```

Retrieves authoritative transaction information.

Example response:

```json
{
  "transactionId": "TX-48291",
  "merchantId": "MERCHANT-104",
  "expectedSettlementAmount": 1247.50,
  "currency": "USD"
}
```

---

### 7.2 get_settlement

```text
get_settlement(transaction_id)
```

Retrieves settlement records associated with a transaction.

Example response:

```json
{
  "settlementId": "SET-8821",
  "transactionId": "TX-48291",
  "settledAmount": 1217.50,
  "currency": "USD"
}
```

---

### 7.3 get_fee_rules

```text
get_fee_rules(merchant_id)
```

Retrieves applicable merchant or processor fee configurations.

This tool may provide evidence explaining an amount discrepancy.

---

### 7.4 get_transaction_history

```text
get_transaction_history(merchant_id)
```

Retrieves relevant historical transaction and settlement information.

This allows the investigation agent to identify patterns such as recurring settlement adjustments.

---

### 7.5 search_policy_documents

```text
search_policy_documents(query)
```

Searches relevant operational and financial policies using semantic retrieval.

The tool returns evidence containing:

```text
document
section
content
retrieval score
```

The investigation agent should use these references when supporting policy-related conclusions.

---

## 8. Retrieval-Augmented Generation

Financial policies represent external domain knowledge that may change independently of application code or model training.

ReconAI therefore uses Retrieval-Augmented Generation rather than expecting the language model to know financial policies.

### V1 Knowledge Base

The initial synthetic knowledge base includes:

```text
Merchant Fee Schedule
Settlement Processing Policy
Reconciliation Operations Manual
Currency Conversion Policy
Exception Handling Policy
```

Documents are:

```text
Document
   ↓
Chunking
   ↓
Embedding
   ↓
Vector Storage
   ↓
Semantic Retrieval
```

### Vector Storage

V1 uses:

```text
PostgreSQL + pgvector
```

This avoids introducing a separate vector database while the dataset remains relatively small.

The architecture allows a dedicated vector database to be introduced later if scale or retrieval requirements justify it.

---

## 9. Investigation Workflow

A typical investigation proceeds as follows:

```text
AMOUNT_MISMATCH
        |
        v
Investigation Created
        |
        v
get_transaction()
        |
        v
get_settlement()
        |
        v
Difference confirmed
        |
        v
get_fee_rules()
        |
        v
Potential matching fee found
        |
        v
search_policy_documents()
        |
        v
Applicable policy retrieved
        |
        v
get_transaction_history()
        |
        v
Historical behavior examined
        |
        v
Evidence evaluated
        |
        v
Structured Recommendation
```

Different exception types may produce different tool-call sequences.

This is one reason investigation is modeled as an agentic workflow rather than a single fixed prompt.

---

## 10. Investigation Output

The agent must return structured output.

Example:

```json
{
  "classification": "PROCESSOR_FEE",
  "rootCause": "A $30 processor settlement fee was applied.",
  "confidence": 0.94,
  "evidence": [
    {
      "sourceType": "FEE_RULE",
      "reference": "FR-14"
    },
    {
      "sourceType": "POLICY",
      "reference": "Settlement Processing Policy §4.2"
    },
    {
      "sourceType": "HISTORICAL_TRANSACTION",
      "reference": "TX-38182"
    }
  ],
  "recommendedAction": "Classify the discrepancy as a processor fee adjustment.",
  "requiresHumanApproval": true,
  "shouldEscalate": false
}
```

Free-form text may be included for analyst readability, but application behavior should rely on structured fields.

---

## 11. Human-in-the-Loop Control

AI recommendations are advisory.

An investigation enters:

```text
AWAITING_REVIEW
```

after the agent completes its work.

An authorized analyst can then select:

```text
APPROVE

REJECT

ESCALATE
```

The agent cannot perform these operations itself.

### State Flow

```text
OPEN
  |
  v
INVESTIGATING
  |
  v
AWAITING_REVIEW
  |
  +-------------------+
  |         |         |
  v         v         v
APPROVED  REJECTED  ESCALATED
```

---

## 12. Guardrails

### 12.1 Restricted Tool Access

The agent may only invoke explicitly registered tools.

It does not receive arbitrary SQL execution capability.

---

### 12.2 Read-Only Financial Access

Agent tools interacting with authoritative financial information are read-only.

The agent cannot:

```text
UPDATE transactions

DELETE settlements

modify ledger balances

approve recommendations

initiate payments
```

---

### 12.3 Evidence Requirement

A material root-cause conclusion should be supported by retrieved evidence.

When sufficient evidence is unavailable, the preferred result is:

```text
INSUFFICIENT_EVIDENCE
```

rather than an unsupported explanation.

---

### 12.4 Confidence-Based Escalation

Initial V1 thresholds may be configured approximately as:

```text
confidence >= 0.85
    recommendation available for review

confidence >= 0.60 and < 0.85
    recommendation + elevated review warning

confidence < 0.60
    escalate / insufficient evidence
```

These thresholds are configurable operational policies, not intrinsic measures of truth.

Their effectiveness should be evaluated against the project's evaluation dataset.

---

### 12.5 Data Minimization

Only information necessary for investigation should be included in model context.

Unnecessary personally identifiable or sensitive financial information should be excluded or redacted before external model calls.

---

## 13. Audit Architecture

ReconAI records significant actions as audit events.

Example events:

```text
EXCEPTION_DETECTED

EXCEPTION_PUBLISHED

INVESTIGATION_STARTED

TOOL_CALLED

EVIDENCE_RETRIEVED

RECOMMENDATION_GENERATED

INVESTIGATION_ESCALATED

RECOMMENDATION_APPROVED

RECOMMENDATION_REJECTED
```

An audit event should contain:

```text
event ID
timestamp
actor
event type
resource type
resource ID
relevant metadata
```

The goal is to make an investigation reconstructable after the fact.

---

## 14. Failure Isolation

One of ReconAI's primary architectural requirements is that AI failure must not become financial-system failure.

### LLM Failure

If the model provider is unavailable:

```text
Reconciliation
    ✓ continues

Exception detection
    ✓ continues

AI investigation
    ✗ temporarily unavailable

Exception
    → remains available for retry/manual investigation
```

---

### Investigation Failure

Failed investigations should be retryable.

A production architecture may introduce:

```text
Retry Policy
     |
     v
Dead-Letter Queue
     |
     v
Manual Review / Replay
```

V1 may implement simplified retry behavior while documenting the production design.

---

### Kafka Failure

A production system should prevent the loss of reconciliation exceptions when publishing fails.

Potential future approaches include:

- transactional outbox;
- producer retries;
- idempotent consumers; and
- dead-letter topics.

The V1 implementation may use simpler publishing semantics while explicitly documenting this limitation.

---

## 15. Data Architecture

The initial relational model contains:

```text
Transaction
     |
     | 1
     |
     | *
Settlement


Transaction
     |
     | 1
     |
     | *
ReconciliationException
     |
     | 1
     |
     | *
Investigation
     |
     | 1
     |
     | *
InvestigationEvidence
     |
     | 1
     |
     | 0..1
Recommendation
     |
     | 1
     |
     | 0..1
Approval


PolicyDocument
     |
     | 1
     |
     | *
PolicyChunk


AuditEvent
→ references relevant domain resources
```

Detailed schemas will be defined separately from this architecture document.

---

## 16. Local Development Architecture

Local development uses Docker Compose.

Target services:

```text
reconai-frontend

reconai-backend

reconai-agent

postgres

kafka
```

The desired developer workflow is:

```bash
docker compose up
```

followed by access to the local Operations Console.

---

## 17. AWS Deployment Architecture

The portfolio deployment targets AWS.

```text
                           Internet
                              |
                              v
                        CloudFront
                              |
                              v
                             S3
                       React Frontend
                              |
                              v
                       Backend Endpoint
                              |
                              v
                        ECS / Fargate
                         Spring Boot
                              |
                    +---------+---------+
                    |                   |
                    v                   v
               RDS PostgreSQL      Event Infrastructure
                    |                   |
                    |                   v
                    |             Agent Worker
                    |              ECS/Fargate
                    |                   |
                    +-------------------+
                              |
                              v
                           LLM API
```

### AWS Services

Initial target:

```text
S3
CloudFront
ECR
ECS/Fargate
RDS PostgreSQL
CloudWatch
```

Kafka deployment will be selected based on the simplest reliable portfolio deployment.

A production architecture could use Amazon MSK or another managed Kafka provider.

The project should not introduce complex cloud infrastructure solely to reproduce the local environment.

---

## 18. Observability

The system should expose sufficient information to understand failures and investigation performance.

Initial observability targets include:

```text
reconciliation exceptions created

Kafka events published

investigations started

investigations completed

investigations failed

agent execution latency

tool-call failures

LLM API failures

API errors
```

AWS deployments should send application logs to CloudWatch.

Future versions may introduce distributed tracing and more detailed AI-specific observability.

---

## 19. Evaluation Architecture

The agent must be evaluated independently of whether its responses appear plausible.

ReconAI will maintain a synthetic evaluation dataset containing known investigation scenarios.

Each scenario may define:

```text
transaction

settlement

fee configuration

applicable policy

expected root cause

expected evidence

expected escalation behavior
```

Initial evaluation metrics include:

```text
Root-Cause Classification Accuracy

Evidence Grounding

Tool Selection

Unsupported Claim Rate

Escalation Accuracy

Investigation Latency

Token Usage

Estimated Investigation Cost
```

Evaluation results should be generated from actual system behavior rather than manually claimed.

---

## 20. Key Architectural Decisions

### Java for Financial Core

Java/Spring Boot provides a strongly typed, mature environment for transactional backend systems and deterministic financial business logic.

### Python for AI Layer

Python provides a mature ecosystem for LLM orchestration, retrieval, evaluation, and experimentation.

Keeping the AI layer separate prevents model-specific implementation concerns from contaminating the financial domain layer.

### Kafka Between Reconciliation and Investigation

AI execution has different latency, scaling, and failure characteristics from reconciliation.

Kafka provides asynchronous decoupling between these workloads.

### PostgreSQL for Operational Data

The domain is strongly relational and benefits from transactional consistency.

### pgvector for V1 Retrieval

The initial policy corpus is small enough that introducing separate vector infrastructure would add unnecessary operational complexity.

### Human Authorization Boundary

AI may investigate and recommend but cannot authorize consequential financial actions.

This boundary is intentional rather than an implementation limitation.

---

## 21. Architecture Invariants

The following rules should remain true even as ReconAI evolves:

1. Reconciliation does not depend on an LLM.
2. Financial discrepancies are detected deterministically.
3. AI does not directly modify authoritative financial records.
4. Agent access to enterprise data occurs through controlled tools.
5. Material AI conclusions must be evidence-backed.
6. Insufficient evidence is a valid investigation outcome.
7. Consequential recommendations require human review.
8. AI failures must not prevent deterministic reconciliation.
9. Investigation activity must be auditable.
10. Evaluation is required before agent behavior is considered reliable.

---

## 22. V1 Architecture Goal

The V1 architecture is successful when the following complete path works:

```text
Synthetic Transaction
        +
Synthetic Settlement
        |
        v
Spring Boot Reconciliation
        |
        v
AMOUNT_MISMATCH
        |
        v
Kafka Event
        |
        v
Python Investigation Agent
        |
        +---- get_transaction()
        |
        +---- get_settlement()
        |
        +---- get_fee_rules()
        |
        +---- search_policy_documents()
        |
        +---- get_transaction_history()
        |
        v
Evidence-Backed Recommendation
        |
        v
React Operations Console
        |
        v
Human Approve / Reject / Escalate
        |
        v
Audit Trail
```

This vertical slice is the primary implementation target before additional functionality is introduced.