# ReconAI — System Architecture

## 1. Architecture Overview

ReconAI is an event-driven payment reconciliation and exception-investigation platform.

The system deliberately separates financial reconciliation from AI-assisted investigation.

The core architectural principle is:

> **Deterministic systems detect. AI investigates. Humans authorize.**

The deterministic financial core is responsible for identifying discrepancies between authoritative transaction and settlement records.

When an exception is detected, an asynchronous event triggers an AI-assisted investigation. The investigation agent retrieves information through explicitly defined tools, gathers supporting evidence, searches relevant financial policies, and produces a structured recommendation.

The recommendation is advisory and requires human review before the exception can be considered resolved.

### The implemented pipeline

```text
Financial Core (Spring)
    ↓  deterministic reconciliation — rule precedence, BigDecimal
ReconciliationException
    ↓  Kafka, published after the database transaction commits
Investigation (PENDING)
    ↓  claimed PENDING → RUNNING
AI investigator (bounded tool loop)
    ↓  four read-only tools, nothing else callable
Evidence Ledger — what the tools actually returned
    ↓
structured InvestigationResult
    ↓  grounding validation: every citation checked, or the result is rejected
durable recommendation + grounded evidence
    ↓  deterministic guardrail (application code, never the model)
AWAITING_REVIEW  /  ESCALATED
    ↓  human decision
COMPLETED  /  ESCALATED
    ↓
append-only audit trail
```

Each step narrows what the next may assert. The deterministic engine establishes
*that* two records disagree; the investigator may cite only evidence it actually
retrieved; the guardrail — not the model — decides what a human sees; and only a
human reaches `COMPLETED`.

Everything from `Investigation (PENDING)` down is owned by the Python
Investigation Service. It reads from the Financial Core and **never writes to
it**.

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
              get_transaction    get_settlements   get_fee_rules   search_policy_documents
                       │                  │               │                   │
                       │   (read-only HTTP to Financial Core)    (local Markdown corpus,
                       │                  │               │       deterministic lexical search)
                       └──────────────────┴───────┬───────┴───────────────────┘
                                                  │
                                                  ▼
                                          Evidence Ledger
                                    (what the tools actually returned)
                                                  │
                                                  ▼
                                      Grounding validation
                                 (every citation checked, or rejected)
                                                  │
                                                  ▼
                                  Durable result + grounded evidence
                                                  │
                                                  ▼
                                      Deterministic guardrail
                                                  │
                                    ┌─────────────┴─────────────┐
                                    ▼                           ▼
                             AWAITING_REVIEW                ESCALATED
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
It is implemented and deployed; see the screenshots in the repository README.

In production it is served as static assets from S3 through CloudFront, which
also proxies the two API path prefixes so the browser stays same-origin. Locally
it is served by nginx in Docker Compose over the same prefixes, so the API
contract is identical in both.

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
- manage fee rules;
- execute deterministic reconciliation;
- create reconciliation exceptions;
- expose read APIs used as controlled evidence sources; and
- publish reconciliation-exception events after commit.

**Not** the Financial Core's responsibilities: investigation state, AI
execution, recommendations, human review decisions and the investigation audit
trail all belong to the Python Investigation Service, which owns the
`investigation` schema. An earlier draft of this list assigned them here; see
`docs/data-model.md` section 23 for why they moved.

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

`PROCESSOR_FEE` is **not** a deterministic discrepancy type. It is an
investigation root-cause classification, and it appears in a different enum
(`RootCauseClassification`) that the reconciliation engine never produces.
Both halves of that separation are enforced by database CHECK constraints, not
by convention.

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

Event (the whole contract — see section 5.5):

```json
{
  "exceptionId": "EX-1042",
  "transactionId": "TX-48291",
  "type": "AMOUNT_MISMATCH",
  "detectedAt": "2026-09-26T14:32:00Z"
}
```

The investigation service consumes this event and records a `PENDING`
investigation.

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
Investigation persisted (PENDING)
        |
        v
run: claimed PENDING -> RUNNING, then investigated
```

**Why Kafka exists.** AI investigation has variable latency and independent failure modes. Placing a queue between detection and investigation means a slow, failing or entirely absent investigation service cannot affect whether the financial core establishes that two authoritative records disagree.

The Python service consumes the topic under the fixed group `reconai-investigation-service`, with `auto.offset.reset=latest` and manual commits after each record. Delivery is at-least-once, so investigation handling must be idempotent by `exceptionId` once it exists. A validated event becomes a `PENDING` investigation, at most one per `exceptionId` — enforced by a unique constraint rather than an application check, since duplicate deliveries can arrive concurrently. A record's offset is committed only once it has been recorded or judged permanently unusable; a valid event that cannot be stored leaves its offset uncommitted so it is redelivered rather than lost.

Consumption stops there. Ingestion creates a `PENDING` investigation and nothing more: no evidence is fetched and no model is called on the consumer path. Investigating is a separate, explicitly triggered step, so a broker backlog cannot turn into a burst of model calls.

The investigation service owns the `investigation` schema and writes nowhere else. It never reads or writes `transactions`, `settlements` or `reconciliation_exceptions`, and holds no foreign keys into them; `exceptionId` and `transactionId` are resolved through the financial core's API instead.

That resolution is implemented as a deliberately narrow read-only client exposing three operations — `get_transaction`, `get_settlements` and `get_fee_rules`, against `GET /api/v1/transactions/{id}`, `GET /api/v1/transactions/{id}/settlements` and `GET /api/v1/fee-rules`. There is no generic request method and no write operation, because these methods are the allowlist a future investigation agent receives: anything added here becomes a capability that agent has. Monetary evidence is carried as decimal and never as floating point.

Policy evidence comes from a separate tool, `search_policy_documents`, over a small corpus of synthetic Markdown documents in `policies/`. V1 retrieval is deterministic lexical matching — no embeddings, no vector store, no pgvector. Results cite the document identifier and section they came from, so a later conclusion can be traced to its source. That tool exposes a query and nothing else: no file reading, no directory listing and no path argument.

An investigation can now be run. A language model receives the four controlled tools and nothing else — no generic HTTP, no SQL, no filesystem, no code execution — and works within a bounded loop, at most eight tool rounds, after which the investigation fails rather than fabricating a conclusion. The model may request a tool; the application validates the name and arguments, executes it, and records what came back.

Grounding is decided by the application rather than asserted by the model. Every identifier a tool returns is written to an in-memory evidence ledger scoped to that run, and every citation in the final result is checked against it. A citation the ledger cannot vouch for invalidates the whole result: a reference that cannot be traced to a retrieval is a fabrication, however plausible the identifier looks. `INSUFFICIENT_EVIDENCE` is a first-class successful outcome, preferred to a plausible guess.

The result is advisory and is **not persisted**. Running an investigation does not change the investigation record, does not resolve anything, and cannot modify a financial record. `requiresHumanApproval` is pinned true so a result cannot describe itself as needing no review. Persistence, lifecycle and human review are later phases.

Fee rules are structured configuration owned by the financial core and are **evidence only**. Reconciliation never reads them: a settlement difference is an `AMOUNT_MISMATCH` whether or not a fee rule of the same amount exists. Nothing in either service concludes `PROCESSOR_FEE`; that remains a root-cause classification for investigation to propose and a human to authorise. When automatic investigation is enabled, consuming an exception event schedules the controlled tool loop without anyone asking — the tools are invoked automatically, the conclusion is still only ever a proposal, and human review is unchanged.

A provider's tool `input_schema` guides how a model builds its call; it does not validate one. Investigation INV-1004 submitted a final result without `confidence` while `confidence` was already listed as required, which is why `InvestigationResult` — not the schema — is the acceptance boundary. A rejected submission earns exactly one corrected resubmission, validated identically and grounded identically; the application never supplies a missing value, and a second rejection ends the investigation with no recommendation stored.

Automatic execution is bounded by a shared AI budget covering manual runs, automatic runs and that one correction. An investigation whose automatic run was paused by an exhausted budget stays `PENDING` and **does not resume when the window resets**: V1 has no scheduler, and `POST /investigations/{id}/run` is the operator recovery path rather than a public control. A crash while an investigation is `RUNNING` leaves it there, which remains a known limitation recovered deliberately rather than by a background process. See `agent-service/README.md` for offset, readiness, migration and evidence detail.

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

These four fields are the entire contract, asserted in Spring's own `KafkaDeliveryIntegrationTest` and mirrored by the Python consumer, which rejects unknown fields. Earlier drafts of this document and of `api-contract.md` also showed `eventId`, `eventType`, `eventVersion` and `correlationId`; none of those exist. They are envelope concerns worth adding when a consumer needs them, and adding one is a coordinated change across the Spring record, the Python model, the tests and the docs — not a gap to be quietly filled.

---

## 6. Investigation Service

**Technology:** Python + FastAPI

The Investigation Service is responsible for investigating detected reconciliation exceptions.

It receives an exception identifier and determines what information is required to investigate the discrepancy.

The agent does not receive unrestricted access to application databases.

Instead, it interacts with the system through explicitly defined tools.

The service owns the `investigation` schema and everything the AI lifecycle produces:
the investigation case, the recommendation, the evidence behind it, the human review
decision, and the audit trail. It owns no financial records, reads them only through the
Financial Core's read-only API, and **issues no write to the Financial Core of any
kind**.

It is also where the deterministic guardrail lives — the policy deciding whether a
result reaches a human or is escalated. That decision is made by application code from
the stored result, never by the model.

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

### 7.4 get_transaction_history — not implemented

```text
get_transaction_history(merchant_id)
```

**This tool does not exist.** It is not in the allowlist, there is no Financial
Core endpoint behind it, and the agent cannot call it.

It is retained here as a candidate capability because pattern evidence — "this
merchant is adjusted like this every month" — would be genuinely useful. It is
absent for a reason worth stating: `HISTORICAL_TRANSACTION` is correspondingly
absent from the evidence source enum, because a citation type with no tool
behind it could only ever be produced from imagination.

Implementing it means adding the endpoint, the tool, the evidence type and the
ledger support together.

---

### 7.5 search_policy_documents

```text
search_policy_documents(query)
```

Searches the synthetic policy corpus using **deterministic lexical retrieval**.
There is no embedding step and no vector store; section 8 describes the scoring
rule and why it was chosen.

The tool returns evidence containing:

```text
document_id
title
section
excerpt
score
```

`score` is a lexical relevance score, not a probability and not model confidence.

The investigation agent should use these references when supporting policy-related
conclusions. A policy citation naming a section must match a section that was
actually returned, which is what makes the citation checkable.

---

## 8. Policy Knowledge Retrieval

Financial policies represent external domain knowledge that may change
independently of application code or model training. ReconAI therefore retrieves
policy text and supplies it as evidence, rather than expecting the language model
to know financial policies.

### V1 Knowledge Base

Five synthetic Markdown documents in `policies/`:

```text
Merchant Fee Schedule            POL-FEE-001
Settlement Processing Policy     POL-SETTLEMENT-001
Reconciliation Operations Manual POL-RECON-001
Currency Conversion Policy       POL-FX-001
Exception Handling Policy        POL-EXCEPTION-001
```

They are synthetic, describing no real processor, bank, network or regulator.
See `policies/README.md`.

### Deterministic lexical search — no embeddings, no vector store

```text
Markdown documents on disk
   ↓
parsed into (document, section) units at startup
   ↓
lexical term matching with a fixed scoring rule
   ↓
ranked excerpts, each carrying document ID and section
```

**There is no embedding step, no vector database and no pgvector.** Retrieval is
term matching: how many of the query's distinct terms a section contains,
weighted by frequency damped for length, with extra weight for terms in the
heading. Ties break by document ID then position.

This is a deliberate choice, not a placeholder. For a corpus this size, lexical
search is correct, fast, and — most importantly — **explainable**: the same
query always returns the same sections in the same order, and why a section
matched can be read off the query. An unexplainable retrieval step underneath
evidence that the rest of the system treats as verifiable would undercut the
grounding guarantees everything else rests on.

Swapping in semantic retrieval later is a change behind the `search_policy_documents`
tool contract; callers would not change. It is not implemented today.

### Filesystem boundary

`PolicySearch` exposes one public method, `search(query)`. There is no
`read_file`, no directory listing, and no path or filename argument. The corpus
directory is fixed at construction and only `*.md` files within it are read, so
a query is search terms and never a path.

---

## 9. Investigation Workflow

A typical investigation proceeds as follows:

```text
AMOUNT_MISMATCH
        |
        v
Investigation Created (PENDING, from the Kafka event)
        |
        v
Claimed: PENDING -> RUNNING          [transaction 1, committed]
        |
        v
get_transaction()
        |
        v                             no transaction is open here
get_settlements()
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
Evidence evaluated
        |
        v
Structured result proposed
        |
        v
Grounding validation: every citation checked against
what the tools actually returned
        |
        v
Deterministic guardrail: AWAITING_REVIEW or ESCALATED
        |
        v
Recommendation + evidence + status + audit    [transaction 2, committed]
        |
        v
Human decision                                [transaction 3, committed]
```

Different exception types produce different tool-call sequences. This is one
reason investigation is modeled as an agentic workflow rather than a single
fixed prompt.

### The loop is bounded

An agent that can call tools indefinitely is an agent that can spend
indefinitely. The number of tool rounds is capped
(`RECONAI_AGENT_INVESTIGATION_MAX_TOOL_ROUNDS`, default 8), and a model that
cannot reach a conclusion within the bound fails visibly rather than circling.

### Why three transactions rather than one

A model call is slow, external, and cannot participate in a database
transaction. Wrapping the whole workflow in one would mean holding a row lock
open across a network call to a third party — so the database work is split
around the call instead, and nothing is held open while it runs.

The database and the model are not atomic together, and nothing in the design
pretends otherwise. What *is* atomic is the recording step: the recommendation,
its evidence, the resulting status and the audit events commit together, because
a recommendation with no status change is invisible and a status change with no
recommendation is unexplainable.

A crash between claiming and recording leaves the investigation in `RUNNING` —
a visible, honest state meaning "started, outcome unknown". There is no
background scheduler and no distributed lock to quietly resolve it.

### Concurrency

Claiming is a conditional `UPDATE ... WHERE status = 'PENDING' RETURNING`. Two
simultaneous run requests both pass any read-based check, but only one can win
the update, because the database serialises the row. The loser is refused with
`409` **before** a model call is made, so a rejected run costs nothing.

---

## 10. Investigation Output

The agent must return structured output. Free-form prose may be included for
analyst readability, but no application behaviour depends on it.

Example:

```json
{
  "classification": "PROCESSOR_FEE",
  "rootCause": "An active 50.00 USD processing fee is consistent with the difference.",
  "confidence": 0.86,
  "evidence": [
    {"sourceType": "FEE_RULE", "reference": "FR-14"},
    {"sourceType": "SETTLEMENT", "reference": "SET-8008"},
    {"sourceType": "POLICY_DOCUMENT", "reference": "POL-FEE-001",
     "section": "Cross-Network Settlement Fees"}
  ],
  "recommendedAction": "Classify the discrepancy as a processor fee adjustment.",
  "requiresHumanApproval": true
}
```

### Evidence is verified, not trusted

A model asked for its sources will produce plausible ones whether or not it saw
them — `FR-999` looks exactly like `FR-14`. So the application keeps its own
ledger of what the tools actually returned during the run, and checks every
citation against it.

A result citing anything unverifiable is **rejected in full**. Not downgraded,
not stored with the bad citation removed: an investigation that fabricated one
reference has not demonstrated that it did not fabricate the reasoning. The
investigation moves to `FAILED` and no recommendation is stored.

`sourceType` is one of `TRANSACTION`, `SETTLEMENT`, `FEE_RULE`,
`POLICY_DOCUMENT` — one per controlled tool. `HISTORICAL_TRANSACTION` from the
earlier draft is absent because no tool retrieves it; a source with no tool
behind it could only ever be cited from imagination.

### `shouldEscalate` is not a model output

Whether something is escalated is decided by the deterministic guardrail from
the stored result, and recorded as the investigation's status. A model-supplied
boolean would be a second, contradictable answer to a question the policy owns.

### `requiresHumanApproval` is always true

Pinned by the result schema, and independently enforced by a database CHECK
constraint. A result cannot describe itself as needing no review — not even a
malformed one that slipped past a validator.

### `confidence` is a self-report

It is not a calibrated probability, and is used only as an ordering signal
against a configured threshold. Every API response carrying it says so.

---

## 11. Human-in-the-Loop Control

AI recommendations are advisory. This is the boundary the whole system is built
around, so it is worth being precise about what it does and does not mean.

An investigation enters:

```text
AWAITING_REVIEW
```

when the agent has produced a grounded result that the deterministic guardrail
judged fit for review. Anything weaker goes to `ESCALATED` instead.

An analyst can then select:

```text
APPROVE     the explanation is accepted; investigation -> COMPLETED
REJECT      the explanation is not accepted; investigation -> ESCALATED
ESCALATE    the reviewer passes it on; investigation -> ESCALATED
```

The agent cannot perform these operations. Not as a tool, and not by any
reachable code path.

### What approval does

It records that a human judged an explanation acceptable.

### What approval does not do

It does not resolve the reconciliation exception, alter a settlement, or move
money. **The Investigation Service issues no write of any kind to the Financial
Core** — the review service holds no client to it at all, so the boundary is
structural rather than a rule to remember. Acting on an approved recommendation
is a separate, deliberate step outside this service.

### Rejection escalates rather than resolving

A rejected recommendation does not make the underlying discrepancy disappear.
The exception is still there and still needs a human; what was rejected is one
proposed explanation of it.

### Reviewer identity

V1 has **no authentication**. `reviewed_by` is caller-supplied, stored verbatim,
and returned with a note saying it is unverified. It is demo attribution, not
identity.

### One decision, once

At most one decision per investigation, enforced by a unique constraint rather
than an application check. Two reviewers submitting at once result in exactly
one stored decision; a decision that could be silently overwritten would not be
a decision.

### State Flow

```text
PENDING
  |
  v
RUNNING
  |
  +-------------------------+------------------+
  |  guardrail: confident   |  guardrail:      |  execution
  |  and grounded           |  anything weaker |  failure
  v                         v                  v
AWAITING_REVIEW         ESCALATED           FAILED
  |
  +------------+-------------+
  | approve    | reject      | escalate
  v            v             v
COMPLETED   ESCALATED    ESCALATED
```

**Only a human reaches COMPLETED.** No confidence value is a shortcut: the
guardrail has exactly two outcomes and neither completes anything.

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

Routing is decided by deterministic application code, never by the model. The
same result always routes the same way, which is what makes an escalation
explainable months later.

Implemented policy:

```text
classification is INSUFFICIENT_EVIDENCE or UNKNOWN
    -> ESCALATED   (checked first: the model has said it does not know,
                    and no confidence number changes that)

fewer verified evidence references than the minimum
    -> ESCALATED   (a conclusion citing nothing verifiable needs a human
                    however confident it sounds)

confidence < threshold
    -> ESCALATED

otherwise
    -> AWAITING_REVIEW
```

Both parameters are configurable:

```text
RECONAI_AGENT_REVIEW_CONFIDENCE_THRESHOLD   default 0.85
RECONAI_AGENT_REVIEW_MINIMUM_EVIDENCE       default 1
```

The reason is stored with the outcome, quoting the threshold that was applied,
so the decision can be re-derived from the record rather than reconstructed from
the code as it stands later.

The threshold is compared as an exact decimal, never a float, so a boundary case
cannot turn on binary rounding — and the value compared is the same one that is
stored, so the routing and the stored confidence can never disagree.

The earlier draft's middle band ("confidence + elevated review warning") is not
implemented. Every non-qualifying case converges on the same action — a human
looks at it — so a third tier would have been a label without a behaviour.

### Confidence is not truth

These thresholds are configurable operational policies, not intrinsic measures
of truth. The number being compared is the model's own self-report and is **not
a calibrated probability**: 0.9 does not mean nine such conclusions in ten are
correct. It is an ordering signal, and nothing in the system presents it as
anything more.

Their effectiveness should be evaluated against the project's evaluation
dataset.

---

### 12.5 Data Minimization

Only information necessary for investigation should be included in model context.

Unnecessary personally identifiable or sensitive financial information should be excluded or redacted before external model calls.

---

## 13. Audit Architecture

ReconAI records significant actions as audit events, so an investigation can be
reconstructed after the fact.

The trail of the AI lifecycle is owned by the Investigation Service — the
service that causes those events — and lives in the `investigation` schema. See
`docs/data-model.md` section 23 for why it is not the Financial Core's.

Implemented events:

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

An audit event contains:

```text
event ID (AUD-)
sequence number
investigation ID
event type
actor type   SYSTEM | AI | HUMAN
actor ID     set only for HUMAN events; unauthenticated
metadata     small, non-sensitive summary
occurred at
```

### Append-only

There is no update path and no delete path — not in the service, not in the API.
Append-only is enforced by the absence of a write path rather than by a check
that could be bypassed. A trail that can be revised is not evidence of anything.

### Written in the same transaction as what it describes

Each event is committed alongside the state change it records. An audit entry
that survived a rolled-back transition would be a record of something that never
happened.

### Ordered by an integer sequence

Not by timestamp, which ties when several events share a transaction, and not by
identifier text, which would sort `AUD-10001` before `AUD-9001`.

### Deliberately few events

`TOOL_CALLED` and `EVIDENCE_RETRIEVED` from the earlier draft are not audited. A
durable row per tool call would bury the events a reviewer actually needs, and
what the tools returned is already recorded — verified — as recommendation
evidence. Tool activity is logged, not audited. An audit trail nobody can read
is not an audit trail.

### What metadata never contains

No prompt, no provider request or response, no credential, and nothing
resembling model reasoning. Private chain-of-thought is never requested,
persisted or exposed anywhere in this system. This table is read by humans
reviewing decisions; it is not a debugging sink.

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

Two schemas in one PostgreSQL instance, owned by two services. Shared physical
storage is not shared application ownership, and the separate schema makes the
boundary something the database enforces rather than something a reviewer takes
on trust.

```text
====== Financial Core — schema: public ======

Transaction
     |
     | 1
     | *
Settlement


Transaction
     |
     | 1
     | *
ReconciliationException

FeeRule

========== no foreign keys cross ===========

== Investigation Service — schema: investigation ==

ReconciliationException.exception_id
     |        (a string reference, not a foreign key)
     | 0..1
Investigation
     |
     | 1
     | 0..1
Recommendation
     |                  \
     | 1                 \ 1
     | *                  \ 0..1
RecommendationEvidence   Review


AuditEvent → one investigation, by business identifier; append-only


Policy corpus: Markdown files on disk, not a table
```

### The boundary

Nothing in `investigation` holds a foreign key into `public`, and nothing in
`public` holds one into `investigation`. The Investigation Service stores
Financial Core business identifiers as plain string columns and resolves them
through the read-only API when it needs the records.

A foreign key would couple one service's writes to another service's schema and
make the ownership boundary decorative. The only foreign keys in the
`investigation` schema point at tables the same service owns.

### Cardinality

Each of the `0..1` relationships is a unique constraint, because each is a
question that must have exactly one answer: one investigation per exception, one
recommendation per investigation, one decision per investigation. Enforced by
the database rather than by application checks, since two concurrent requests
can both pass a check and only one can win a constraint.

Detailed schemas are defined in `docs/data-model.md`.

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

### Deterministic Lexical Policy Retrieval for V1

The policy corpus is small enough that neither a vector database nor pgvector is
warranted. Lexical search is also reproducible and explainable, which matters
more here than recall: policy text is cited as evidence, and evidence retrieved
by a process nobody can account for is weak evidence.

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
11. Only a human transition completes an investigation. No confidence value,
    guardrail outcome, or model output can reach `COMPLETED`.
12. The Investigation Service issues no write to the Financial Core. Approval
    records a judgement; it does not resolve an exception or move money.
13. Routing between review and escalation is deterministic, reproducible from
    the stored result, and decided by application code rather than the model.
14. Model confidence is a self-report and is never treated as a calibrated
    probability.
15. No prompt, provider payload, or model reasoning is persisted or exposed
    anywhere in the system.
16. No database transaction is held open across a model call.

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
        +---- get_settlements()
        |
        +---- get_fee_rules()
        |
        +---- search_policy_documents()
        |
        v
Grounding validation
(every citation checked against what was retrieved)
        |
        v
Evidence-Backed Recommendation (stored)
        |
        v
Deterministic guardrail
        |
        +---- AWAITING_REVIEW
        |
        +---- ESCALATED
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

The backend of this path is implemented. The Operations Console is the remaining
piece; the review and audit endpoints it will call exist and are tested.

This vertical slice is the primary implementation target before additional functionality is introduced.