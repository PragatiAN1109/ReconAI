# ReconAI Investigation Service

An independent Python service that will investigate reconciliation exceptions detected by
the Spring Boot financial core.

It is **not** a system of record. The financial core stays authoritative for transactions
and settlements; this service will never hold or modify them. It does not connect to the
financial core's database, and it will reach authoritative data only through narrow,
read-only interfaces when those are built.

> Deterministic systems detect. AI investigates. Humans authorize.

## Phase 4.7 scope

The complete backend lifecycle, end to end:

```
Kafka event ──▶ PENDING ──▶ RUNNING ──▶ AWAITING_REVIEW ──▶ COMPLETED
                              │              │  (human approves)
                              │              └──▶ ESCALATED (human rejects/escalates)
                              ├──▶ ESCALATED (guardrail)
                              └──▶ FAILED
```

The service consumes reconciliation exceptions from Kafka and records a `PENDING`
investigation for each. A recorded investigation can then be **run**: a language model
requests evidence through four controlled tools, reasons over what comes back, and
proposes an explanation — which the application checks against the evidence actually
retrieved before accepting it.

The result is **durably stored**, a **deterministic guardrail** routes it to a human or
escalates it, a **human decides**, and every step lands in an **append-only audit trail**.

**Only a human reaches `COMPLETED`.** No confidence value is a shortcut, and approving a
recommendation writes nothing to the financial core. See
[Human review](#human-review) and [Not implemented yet](#not-implemented-yet).

## Prerequisites

- **Python 3.12**
- A Kafka broker and a PostgreSQL database. The repository's Compose stack provides
  both; see [Local startup order](#local-startup-order).

Most **tests** need none of this: they fake both boundaries and run offline. A smaller
set is marked `integration` and starts a real PostgreSQL through Testcontainers, because
unique constraints under concurrency and sequence behaviour cannot be proven against a
substitute. Those skip automatically when Docker is unavailable.

## Setup

```bash
cd agent-service
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

`pyproject.toml` is the source of truth for dependencies. Runtime dependencies are in
`[project.dependencies]`; test-only ones are in the `dev` extra.

## Start the service

```bash
python -m app.main
```

Or with uvicorn directly, for auto-reload during development:

```bash
uvicorn app.main:app --reload --port 8000
```

`python -m app.main` reads host, port and log level from configuration; the `uvicorn`
form takes them from its own flags.

Startup is successful when the log shows:

```
INFO  [app.main] Investigation service starting [service=reconai-investigation-service environment=local host=0.0.0.0 port=8000]
```

Interactive API docs are at `http://localhost:8000/docs`.

## Run the tests

```bash
pytest
```

## Configuration

All variables use the `RECONAI_AGENT_` prefix. Every one has a default that works
locally, so the service starts with nothing set.

| Variable | Default | Purpose |
|---|---|---|
| `RECONAI_AGENT_SERVICE_NAME` | `reconai-investigation-service` | name reported by the health endpoints |
| `RECONAI_AGENT_ENVIRONMENT` | `local` | one of `local`, `dev`, `test`, `prod` |
| `RECONAI_AGENT_HOST` | `0.0.0.0` | bind address |
| `RECONAI_AGENT_PORT` | `8000` | HTTP port |
| `RECONAI_AGENT_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL` |
| `RECONAI_AGENT_KAFKA_BOOTSTRAP_SERVERS` | `localhost:9092` | broker address |
| `RECONAI_AGENT_KAFKA_EXCEPTIONS_TOPIC` | `reconciliation.exceptions` | topic to consume |
| `RECONAI_AGENT_KAFKA_CONSUMER_GROUP` | `reconai-investigation-service` | consumer group |
| `RECONAI_AGENT_DATABASE_URL` | `postgresql+asyncpg://reconai:reconai@localhost:55432/reconai` | database |
| `RECONAI_AGENT_FINANCIAL_CORE_BASE_URL` | `http://localhost:8099` | Financial Core, for evidence |
| `RECONAI_AGENT_FINANCIAL_CORE_TIMEOUT_SECONDS` | `5.0` | evidence request timeout |
| `RECONAI_AGENT_POLICY_CORPUS_PATH` | `<repo>/policies` | policy corpus directory |
| `RECONAI_AGENT_LLM_PROVIDER` | `none` | `none` or `anthropic` |
| `RECONAI_AGENT_LLM_MODEL` | `claude-sonnet-5` | model identifier |
| `RECONAI_AGENT_LLM_API_KEY` | *(unset)* | provider key; `SecretStr`, never logged |
| `RECONAI_AGENT_INVESTIGATION_MAX_TOOL_ROUNDS` | `8` | bound on the tool loop |
| `RECONAI_AGENT_PROMPT_VERSION` | `v1` | recorded on every recommendation |
| `RECONAI_AGENT_REVIEW_CONFIDENCE_THRESHOLD` | `0.85` | guardrail: minimum confidence for human review |
| `RECONAI_AGENT_REVIEW_MINIMUM_EVIDENCE` | `1` | guardrail: minimum verified evidence references |

The provider defaults to `none`. The service runs, consumes Kafka and records
investigations with no model configured at all; only the investigation endpoint is
unavailable, and it reports that as `503` rather than failing at startup.

**The API key has no default and is a `SecretStr`**, so it cannot be printed by an
accidental `repr` of settings. It is never logged. Supply it through the environment —
never in code, never in a committed file.

The database port is **55432**, not 5432 — the Compose stack publishes PostgreSQL there
so it does not collide with a local server. Keep it aligned with `RECONAI_POSTGRES_PORT`.
The URL must use the `postgresql+asyncpg://` scheme; the financial core's JDBC URL is a
different thing entirely.

Values may also come from a `.env` file in this directory. `RECONAI_AGENT_LLM_API_KEY`
**is** a secret when a provider is configured — keep it out of anything committed. It is
a `SecretStr`, so it cannot be printed by an accidental `repr` of settings, and it is
never logged.

Example:

```bash
RECONAI_AGENT_PORT=9100 RECONAI_AGENT_LOG_LEVEL=DEBUG python -m app.main
```

Note that `8000` is this service's own port. It is unrelated to the financial core's
`8080`, and the two can run side by side.

## Kafka

The financial core detects reconciliation discrepancies deterministically and publishes
each newly created exception to Kafka **after its database transaction commits**. This
service consumes them.

```
Spring Boot ──AFTER_COMMIT──▶ reconciliation.exceptions ──▶ Python consumer
                                                                  │
                                                     JSON decode ─┤
                                              Pydantic validation ─┤
                                                  structured log ──┘
```

Kafka is what keeps investigation — variable in latency, with failure modes of its own —
from affecting whether the financial core establishes that two records disagree.

| | |
|---|---|
| Topic | `reconciliation.exceptions` |
| Consumer group | `reconai-investigation-service` |
| Message key | `transactionId` |
| Format | plain JSON, no Java type headers |

### Event contract

```json
{
  "exceptionId": "EX-1005",
  "transactionId": "TX-10006",
  "type": "AMOUNT_MISMATCH",
  "detectedAt": "2026-09-27T00:13:42.485226Z"
}
```

`type` is one of `MISSING_SETTLEMENT`, `DUPLICATE_SETTLEMENT`, `CURRENCY_MISMATCH` or
`AMOUNT_MISMATCH`. `detectedAt` must carry a timezone; a naive timestamp is rejected
rather than assumed to be UTC.

The event carries identity only — no amounts, settlements, merchant or internal UUID.
Authoritative detail will be fetched through controlled interfaces, so the event never
becomes a second, drifting copy of financial data.

Those four fields are the whole contract, and an event carrying anything else is
**rejected**. Both sides of the contract are owned in this repository, so drift between
them should surface immediately rather than be quietly tolerated. Extending the contract
— with `eventId`, `eventVersion` or `correlationId`, for instance — is a deliberate
change made across the Spring producer, the Python model, the tests and this
documentation together.

A message with an unexpected field is therefore treated like any other invalid event:
logged, skipped, and the consumer keeps running.

### Offsets and delivery

`auto.offset.reset` is **`latest`**. A brand-new consumer group starts from events
produced from that point on, rather than replaying the topic's entire development
history. **The consumer must therefore be running before an event is produced**, or it
will not see it.

The consumer group is fixed, never generated: a stable group keeps committed offsets
across restarts and lets several instances share partitions instead of each receiving
everything.

Auto-commit is off. A record is committed only once it has been durably recorded, or
once it has been judged permanently unusable. There are three outcomes, and they differ
in exactly one way — whether the offset moves:

| Outcome | Offset | Why |
|---|---|---|
| Recorded | committed | the work is done |
| Unusable message | committed, skipped | redelivery will not make it valid |
| Valid, but could not be recorded | **not committed** | it must not be lost |

The third case is the important one. A database outage must not silently discard a real
financial exception, so the offset stays put and **consumption stops**. Kafka offsets are
positional — committing acknowledges everything up to the current record — so continuing
past a failed record would acknowledge it via the next successful commit. Readiness then
reports `NOT_READY`, and on restart the record is redelivered.

Delivery is **at-least-once**. The database commit and the Kafka commit are two separate
transactions and are **not** atomic; a crash between them replays the record. The unique
`exception_id` is what makes that replay harmless rather than a duplicate investigation.
This is not exactly-once and is not claimed to be.

### Messages the service cannot use

Malformed JSON, a missing field, an unknown exception type, an unparseable timestamp or
a timezone-naive timestamp are all logged with the topic, partition and offset — then
the record is **committed anyway, which skips it**.

That is a deliberate trade-off. With no dead-letter topic (out of scope here), *not*
committing would make the consumer re-read the same poison record forever and stall
every record behind it on that partition. The cost is that such a record is dropped and
the log line is its only remaining trace.

## Investigations

A valid event becomes a durable investigation:

```
validated event ──▶ create or reuse ──▶ investigation.investigations
                                         INV-1001, status=PENDING
```

Statuses are `PENDING`, `RUNNING`, `AWAITING_REVIEW`, `COMPLETED`, `FAILED` and
`ESCALATED`. Kafka ingestion only ever creates `PENDING`; running the investigation and
reviewing it move it onward. Every transition is made by application code — the model
proposes an explanation and never decides what state the workflow is in.

### One investigation per exception

`exception_id` carries a **unique constraint**. Not an application check — duplicate
deliveries can arrive concurrently, and a read followed by a write would let two of them
through. The insert itself resolves the race: the constraint decides, and the loser reads
the winner's row.

```
EX-1008 first delivery   ──▶ created   INV-1001
EX-1008 delivered again  ──▶ reused    INV-1001
EX-1008 delivered again  ──▶ reused    INV-1001
                              one row, always
```

Business identifiers come from a PostgreSQL sequence, the same approach the financial
core uses for `TX-`, `SET-` and `EX-`. A sequence is safe across concurrent workers and
process restarts, which an in-memory counter is not. Allocation is non-transactional, so
a losing insert leaves a gap in the numbering; uniqueness matters and contiguity does not.

### Database ownership

This service owns the **`investigation` schema** and nothing else. The financial core's
`transactions`, `settlements` and `reconciliation_exceptions` live in `public` in the
same database and are never read, written or referenced by foreign key. `exception_id`
and `transaction_id` are stored as plain identifiers — references to be resolved through
the financial core's API later, not join keys.

A separate schema makes that boundary something the database shows rather than something
a reviewer has to take on trust.

### Migrations

This service owns its own migration history, entirely separate from the financial core's
Flyway migrations.

```bash
alembic upgrade head       # apply
alembic downgrade -1       # roll back one
alembic current            # what is applied
```

| Revision | What it creates |
|---|---|
| `0001` | the `investigation` schema and `investigations` |
| `0002` | `recommendations`, `recommendation_evidence`, `reviews`, `audit_events`; widens the status constraint to admit `AWAITING_REVIEW` |

Migration files live in `migrations/versions/` (not `alembic/`).

The URL comes from `RECONAI_AGENT_DATABASE_URL`, so migrations and the running service
cannot drift onto different databases. Tables are never created from ORM metadata at
startup: a service that creates its own schema leaves no reviewable history of how it got
that way.

## Evidence tools

Investigating a discrepancy needs the authoritative records behind it. This service does
not hold them and must not read the financial core's tables, so it asks over HTTP:

```
Investigation Service ──GET──▶ Financial Core API ──▶ typed evidence
```

| Tool | Financial Core endpoint |
|---|---|
| `get_transaction(transaction_id)` | `GET /api/v1/transactions/{transactionId}` |
| `get_settlements(transaction_id)` | `GET /api/v1/transactions/{transactionId}/settlements` |
| `get_fee_rules(...)` | `GET /api/v1/fee-rules` |

```python
async with FinancialCoreClient(settings) as core:
    transaction = await core.get_transaction("TX-10009")
    settlements = await core.get_settlements("TX-10009")
    fee_rules = await core.get_fee_rules(
        merchant_id="MERCHANT-PHASE43-DEMO",
        processor="NORTHSTAR_PAYMENTS",
        currency="USD",
        active=True,
    )
```

`get_fee_rules` takes optional `merchant_id`, `processor`, `currency` and `active`
filters; omitted ones are not sent. A merchant filter also returns rules that name no
merchant, since those apply to every merchant on the processor. No matching rules is an
empty list, not an error.

**A fee rule is context, not a conclusion.** A rule whose amount equals a settlement
difference is evidence that such a fee exists — not a finding that this transaction was
charged it. Nothing in this service draws that inference.

Both return typed models, never raw responses or dictionaries. Money is `Decimal` and
never `float` — a binary float cannot hold 1247.50 exactly, and evidence rounded on the
way in is not evidence. Timestamps stay timezone-aware.

`get_settlements` is **plural and returns a list**, possibly empty. A transaction may have
none, one, or several, and which of those it is decides between `MISSING_SETTLEMENT`,
`DUPLICATE_SETTLEMENT` and everything else.

### Read-only, by construction

`FinancialCoreClient` exposes exactly four public methods: `get_transaction`,
`get_settlements`, `open` and `close`. There is **no** `request(method, path)`, no
`fetch_url`, and no write operation of any kind.

This is the point of the design, not an omission. These methods are the allowlist a
future investigation agent receives, so anything added here becomes a capability that
agent has. A generic method would hand it the whole API, including the endpoints that
create and reconcile financial records. Tests assert the public surface stays exactly
these four and that only `GET` requests are ever issued.

The service also holds **no database access to financial records**. `transactions`,
`settlements` and `reconciliation_exceptions` are reachable only through the endpoints
above.

### When evidence cannot be retrieved

Four distinct errors, because an investigation has to tell them apart:

| Error | Meaning |
|---|---|
| `FinancialCoreNotFound` | the financial core says the record does not exist |
| `FinancialCoreUnavailable` | unreachable, or answered with a server error |
| `FinancialCoreTimeout` | did not answer within the configured timeout |
| `FinancialCoreContractError` | the response did not match the expected contract |

All inherit `FinancialCoreError`. A missing transaction **raises** rather than returning
`None`, and unknown-transaction **never** degrades into an empty settlement list. An
absence that was never verified is not a finding, and softening these would let an
investigation conclude something from an outage.

Responses are validated strictly: an unexpected field is a `FinancialCoreContractError`,
because both sides of this contract are owned in this repository and drift should surface.

## Policy corpus

Five concise Markdown documents in `policies/`, covering fees, settlement processing,
reconciliation operations, currency conversion and exception handling.

**They are synthetic.** Invented for demonstration, describing no real processor, bank,
network or regulator. See `policies/README.md`.

```python
policies = PolicySearch(settings.policy_corpus_path)
results = policies.search("NORTHSTAR_PAYMENTS cross-network settlement fee")
```

Each result carries the provenance needed to cite it:

```
[POL-FEE-001] Merchant Fee Schedule — §Cross-Network Settlement Fees  (score 1.5115)
  "A cross-network settlement processing fee applies when a purchase is settled ..."
```

### Deterministic lexical search, not semantic search

No embeddings, no vector store, no model, no external service. Term matching with a
fixed scoring rule: how many of the query's distinct terms a section contains, weighted
by frequency damped for length, with extra weight for terms in the heading. Ties break
by document ID then position, so the same corpus and query always produce the same
results in the same order — never dependent on filesystem enumeration.

Matching is case-insensitive and punctuation-tolerant, and identifiers like
`NORTHSTAR_PAYMENTS` stay whole rather than fragmenting. No match returns an empty list.

This is a tool contract, not a retrieval engine. The implementation can be replaced
without changing what callers see.

### The filesystem boundary

`PolicySearch` exposes exactly one public method: `search(query)`. There is **no**
`read_file`, no directory listing, and no path or filename argument. The corpus
directory is fixed at construction, only `*.md` files inside it are read, and a
document without a `document_id` is skipped because nothing could cite it. A query is
search terms and never a path — `../../etc/passwd` is just four terms that match
nothing. Tests assert all of this.

### Verifying against a running Financial Core

```bash
python - <<'EOF'
import asyncio
from app.config import Settings
from app.financial_core_client import FinancialCoreClient

async def main():
    async with FinancialCoreClient(Settings()) as core:
        print(await core.get_transaction("TX-10009"))
        print(await core.get_settlements("TX-10009"))

asyncio.run(main())
EOF
```

Inside a container, `localhost:8099` is the container itself. Point
`RECONAI_AGENT_FINANCIAL_CORE_BASE_URL` at `host.docker.internal:8099` or a Compose
service name instead; the default assumes host development.

## Investigation

```
INV-1001 (PENDING)
      ↓
  bounded loop, at most 8 rounds
      ↓  model requests a tool
  application validates name + arguments, executes it, records what came back
      ↓  evidence returned to the model
      ↓  ... repeat ...
      ↓  model submits a result
  schema validation → evidence grounding → validated InvestigationResult
```

Run one (development entry point):

```bash
curl -s -X POST http://localhost:8000/api/v1/investigations/INV-1001/run
```

### The model's entire universe

```
get_transaction(transaction_id)
get_settlements(transaction_id)
get_fee_rules(merchant_id, processor, currency, active)
search_policy_documents(query)
```

That is the whole allowlist. There is **no** generic HTTP request, no URL parameter, no
SQL, no filesystem read, no shell and no code execution — not disabled, simply absent.
The model may *request* a tool; the application decides whether it is allowed, validates
the arguments against a strict schema, executes it, and records the result. A request
for anything unrecognised is refused and reported back to the model, which is
information it can act on rather than a reason to crash.

Argument schemas forbid unexpected fields, so a plausible-looking request cannot smuggle
in an extra parameter.

### The result

```json
{
  "classification": "PROCESSOR_FEE",
  "rootCause": "An active 50.00 USD processing fee is consistent with the difference.",
  "confidence": 0.86,
  "evidence": [
    {"sourceType": "TRANSACTION", "reference": "TX-10009"},
    {"sourceType": "SETTLEMENT", "reference": "SET-8008"},
    {"sourceType": "FEE_RULE", "reference": "FR-14"},
    {"sourceType": "POLICY_DOCUMENT", "reference": "POL-FEE-001",
     "section": "Cross-Network Settlement Fees"}
  ],
  "recommendedAction": "Review and classify the discrepancy as a processor fee adjustment.",
  "requiresHumanApproval": true
}
```

Classifications: `PROCESSOR_FEE`, `PROCESSOR_DELAY`, `DUPLICATE_PROCESSING`,
`CURRENCY_CONVERSION`, `PROCESSOR_ERROR`, `UNKNOWN`, `INSUFFICIENT_EVIDENCE`.

**This is a different enum from the deterministic exception type, deliberately.**
Reconciliation says `AMOUNT_MISMATCH` — two records disagree. An investigation may say
`PROCESSOR_FEE` — here is why. `PROCESSOR_FEE` is not and will never be a reconciliation
exception type.

`requiresHumanApproval` is pinned true by a validator and independently by a database
`CHECK` constraint: a result cannot describe itself as needing no review.

`confidence` is the model's stated confidence, range-checked and nothing more. It is
**not calibrated**, and nothing is ever approved on the strength of it — it is compared
against a configured threshold to decide whether a human sees the recommendation or the
investigation is escalated, and that is its entire role. See
[Guardrails](#guardrails).

### Evidence grounding

A model asked for its sources will produce plausible ones whether or not it saw them.
`FR-999` looks exactly like `FR-14`. So the application keeps an **evidence ledger** —
an in-memory record, scoped to one run, of every identifier the tools actually returned —
and checks each citation against it:

```
ledger: transactions=[TX-10009] settlements=[SET-8008] fee_rules=[FR-14, FR-15] policies=[POL-FEE-001]
result cites FR-999  →  UngroundedResultError, the whole result is rejected
```

One bad citation invalidates the result; nothing is silently dropped. A policy citation
naming a section must match a section actually returned, because citing the right
document and the wrong section is still a claim about text nobody read.

Grounding is decided by the application, never asserted by the model.

### Insufficient evidence

`INSUFFICIENT_EVIDENCE` is a **successful outcome**, not a failure. The instructions
explicitly prefer it to a plausible guess. An investigation that finds no matching fee
rule reports that rather than reaching for the nearest explanation.

### Bounded loop

At most `RECONAI_AGENT_INVESTIGATION_MAX_TOOL_ROUNDS` rounds (default 8). An agent that
can call tools indefinitely is an agent that can spend indefinitely. On exhausting the
budget the investigation fails loudly — **no result is fabricated to fill the gap**.

### Running with a fake model

Every test uses a scripted `FakeModel` (`tests/fake_model.py`); none calls a provider.
The workflow worth testing — allowlist, loop bound, ledger, grounding — is all on our
side of the model boundary:

```python
model = FakeModel([
    tool_turn("get_transaction", {"transaction_id": "TX-10009"}),
    final_turn(evidence=[{"sourceType": "TRANSACTION", "reference": "TX-10009"}]),
])
result, ledger = await InvestigationAgent(model, core, policies).investigate(context)
```

### Configuring a real provider

```bash
pip install -e ".[llm]"
RECONAI_AGENT_LLM_PROVIDER=anthropic RECONAI_AGENT_LLM_API_KEY=... python -m app.main
```

⚠️ **The Anthropic provider has not been verified against a live API.** No credentials
were available when it was written, so the translation in `app/anthropic_model.py` is
unexercised end to end. Treat the first real run as a verification step.

## Persisting the result

A completed run stores four things, in one transaction:

```
recommendations           the conclusion  (REC-3001)
recommendation_evidence   the verified references behind it
investigations.status     AWAITING_REVIEW or ESCALATED
audit_events              what happened, and who caused it
```

They commit together because they are one fact. A recommendation with no status change
is invisible; a status change with no recommendation is unexplainable.

### One conclusion per investigation

`UNIQUE(investigation_id)` on `recommendations`. A second would make "the AI's
conclusion" an ambiguous phrase.

### What is deliberately not stored

The prompt, the provider's request or response, and anything resembling model reasoning.
**Private chain-of-thought is never requested, persisted or exposed.** What is kept is
the structured result a reviewer needs, plus enough metadata — provider, model, prompt
version — to know what produced it.

### Confidence

Stored as `NUMERIC(5,4)`, never a float, so it reads back as what was written and a
threshold comparison cannot turn on binary rounding.

It is the model's **self-report**. It is *not* a calibrated probability: 0.9 does not
mean nine such conclusions in ten are correct. It is used only as an ordering signal
against a configured threshold, and every API response carrying it says so.

### Database-enforced invariants

```sql
CHECK requires_human_approval = true
CHECK confidence BETWEEN 0 AND 1
CHECK classification IN (the seven root-cause values)
```

The first is the important one: a recommendation that did not require human approval
would be an autonomous decision, and the database refuses to store one even if every
layer of application code were wrong.

## Transaction boundaries

A model call is slow, external, and cannot join a database transaction. Wrapping the
workflow in one would mean holding a row lock open across a network call to a third
party. So the run is three short transactions with the call between them:

```
TX1   claim PENDING → RUNNING, audit INVESTIGATION_STARTED       commit
        │
        │   no transaction open
        ▼
      agent: bounded tool loop, model call, grounding validation
        │
        ▼
TX2   recommendation + evidence + status + audit                 commit
```

On failure, a third transaction records `FAILED` and the reason.

TX2 is **one transaction**. The recommendation, every evidence row, the guardrail's status
transition and both success audit events succeed or fail together. If any of them fails,
the whole transaction rolls back — no partial recommendation, no orphan evidence, no
misleading `AI_RESULT_GENERATED` or `AWAITING_REVIEW` audit — and a separate transaction
then records `RUNNING → FAILED`. This is proven in `tests/test_transaction_atomicity.py`
by injecting real PostgreSQL failures mid-transaction, not by mocking.

**The database and the model are not atomic together, and nothing here pretends they
are.** A *crash* between TX1 and TX2 leaves the investigation `RUNNING` — no failure
handler gets to run — which is a visible, honest state meaning "started, outcome unknown".
Recovering from it is a deliberate operational decision; there is no background scheduler
and no distributed lock to quietly undo it.

### Concurrency

Claiming is a conditional update:

```sql
UPDATE investigations SET status = 'RUNNING'
 WHERE investigation_id = :id AND status = 'PENDING'
RETURNING *
```

Two simultaneous requests both pass any read-based check; only one can win this, because
the database serialises the row. The loser gets zero rows and a `409` — **before** any
model call is made, so a rejected run costs nothing.

Tested with eight concurrent runs against a real PostgreSQL: exactly one recommendation,
exactly one agent invocation. A concurrency guarantee demonstrated against a mock is a
guarantee about the mock.

## Guardrails

Deterministic routing between human review and escalation. No model call, no network, no
randomness — the same result always routes the same way, which is what makes an
escalation explainable months later.

```
classification is INSUFFICIENT_EVIDENCE or UNKNOWN
    → ESCALATED    checked first: the model has said it does not know,
                   and no confidence number changes that

fewer verified evidence references than the minimum
    → ESCALATED    a conclusion citing nothing verifiable needs a human
                   however confident it sounds

confidence < threshold
    → ESCALATED

otherwise
    → AWAITING_REVIEW
```

Both parameters are configurable (`RECONAI_AGENT_REVIEW_CONFIDENCE_THRESHOLD`,
`RECONAI_AGENT_REVIEW_MINIMUM_EVIDENCE`). The reason — quoting the threshold applied — is
stored with the outcome, so the decision can be re-derived from the record rather than
reconstructed from whatever the code says later.

**The guardrail has exactly two outcomes.** Neither is `COMPLETED`. Nothing in this
module can complete an investigation.

**Escalation is not failure.** It is the system declining to present a weak explanation
as a finding, and it is the expected outcome whenever the evidence does not carry the
conclusion. The reasoning is still stored, so a human picking it up starts from what was
found rather than from nothing.

## Human review

This is where authority lives.

```bash
curl -s -X POST http://localhost:8000/api/v1/investigations/INV-1001/approve \
  -H 'content-type: application/json' \
  -d '{"reviewed_by": "ops.analyst", "comment": "Fee rule matches the difference."}'
```

| Decision | Investigation becomes | Meaning |
|---|---|---|
| `approve` | `COMPLETED` | a human accepted the explanation |
| `reject` | `ESCALATED` | the explanation was not accepted |
| `escalate` | `ESCALATED` | the reviewer passed it on rather than deciding |

Rejection **escalates rather than resolving**. A rejected recommendation does not make
the discrepancy disappear — the exception is still there and still needs a human. What
was rejected is one proposed explanation of it.

Rejection and escalation are kept distinct because they say different things to whoever
picks it up next: one judges the explanation wrong, the other judges it above this
reviewer's authority.

### Approval does not act

**No review endpoint calls the financial core.** Not `POST`, not `PUT`, not `PATCH`, not
`DELETE`. `ReviewService` holds no client to it at all — the boundary is structural, not
a rule to remember, and a test asserts the object's only attribute is its database.

Approving records that a human judged an explanation acceptable. It does not resolve the
exception, alter a settlement, or move money. Acting on an approved recommendation is a
separate, deliberate step outside this service.

### Reviewer identity is not authenticated

**There is no authentication in this service.** `reviewed_by` is whatever the caller
sends, stored verbatim and returned alongside a `reviewer_note` saying it is unverified.
It is demo attribution, not identity. Nothing should be built on it that assumes
otherwise.

It is required and must be non-empty: unauthenticated is not the same as anonymous.

### One decision, once

`UNIQUE(investigation_id)` on `reviews`, plus the same conditional-update pattern used
for claiming a run. Two reviewers pressing approve simultaneously result in exactly one
stored decision — the first. A decision that could be silently overwritten would not be
a decision.

The decision is also **one transaction**: the review row, the status transition and the
audit event commit together. A failure in any of them leaves the investigation
`AWAITING_REVIEW` with no review recorded, so the reviewer can simply try again — never
`COMPLETED` with nobody accountable for it.

Only an investigation that is `AWAITING_REVIEW` can be decided. An `ESCALATED` one cannot
be approved: the guardrail already routed it away from recommendation review toward a
human investigating it directly, which is a different activity with a different outcome.

## Audit trail

Append-only, one row per significant event, scoped to one investigation.

```
AUD-9001  INVESTIGATION_STARTED          SYSTEM
AUD-9002  AI_RESULT_GENERATED            AI
AUD-9003  INVESTIGATION_AWAITING_REVIEW  SYSTEM   reason, threshold applied
AUD-9004  REVIEW_APPROVED                HUMAN    ops.analyst
```

`actor_type` is `SYSTEM`, `AI` or `HUMAN` — the question an audit trail exists to answer
is who did what. `actor_id` is set only for human events and carries the same
unauthenticated caller-supplied name as `reviewed_by`.

### Append-only by absence

There is no update path and no delete path — not in the service, not in the API. `POST`,
`PUT` and `DELETE` on the audit endpoint all return `405`. `AuditService` exposes exactly
one public method, `list_for_investigation`; appending is a module-level function that
takes a caller's session, so there is no object offering a tempting `delete` next to a
`read`. A trail that can be revised is not evidence of anything.

### Committed with what it describes

Each event is written in the same transaction as the state change it records. An audit
entry surviving a rolled-back transition would be a record of something that never
happened — there is a test that forces exactly that rollback.

### Ordered by an integer sequence

Not by timestamp, which ties when several events share a transaction, and not by
identifier text, which would sort `AUD-10001` before `AUD-9001`. `occurred_at` uses
`clock_timestamp()` rather than `now()`, so it records when the event happened rather
than when its transaction began.

### Deliberately few event types

Eight. `TOOL_CALLED` and `EVIDENCE_RETRIEVED` are **not** audited: a durable row per tool
call would bury the events a reviewer actually needs, and what the tools returned is
already recorded — verified — as recommendation evidence. Tool activity is logged, not
audited. An audit trail nobody can read is not an audit trail.

### What metadata never contains

A classification, a confidence, an evidence count, a guardrail reason, a failure
category — enough to re-derive why something was routed where it was.

Never a prompt, a provider payload, a credential, or model reasoning. This table is read
by humans reviewing decisions; it is not a debugging sink.

## Endpoints

### `GET /health` — liveness

```json
{"status": "UP", "service": "reconai-investigation-service"}
```

The process is running and serving requests.

### `GET /api/v1/investigations` — list

```json
{"items": [{"investigation_id": "INV-1001", "exception_id": "EX-1008",
            "transaction_id": "TX-10009", "exception_type": "AMOUNT_MISMATCH",
            "status": "PENDING", "detected_at": "...", "created_at": "...",
            "updated_at": "..."}], "total": 1}
```

### `GET /api/v1/investigations/{investigationId}` — one investigation

`404` when it does not exist. The internal UUID is not exposed.

There is deliberately **no create endpoint**. Investigations exist because the financial
core detected a discrepancy and said so on Kafka; letting a caller assert one into
existence would make this service a second, unverified source of truth.

### `POST /api/v1/investigations/{investigationId}/run` — investigate

Runs the bounded loop, validates the result, stores it, and routes it.

```json
{
  "investigation_id": "INV-1001",
  "status": "AWAITING_REVIEW",
  "guardrail_reason": "Reported confidence 0.8600 meets the review threshold 0.85 with 3 verified evidence reference(s); awaiting human approval.",
  "recommendation": {"recommendation_id": "REC-3001", "classification": "PROCESSOR_FEE", "...": "..."},
  "evidence_retrieved": {"transactions": ["TX-10009"], "settlements": ["SET-8008"],
                         "feeRules": ["FR-14"], "policyDocuments": ["POL-FEE-001"]}
}
```

`status` is `AWAITING_REVIEW` or `ESCALATED`, **never `COMPLETED`**.

| Code | Meaning |
|---:|---|
| 200 | ran; result stored and routed |
| 404 | no such investigation |
| 409 | not `PENDING` — already running, concluded, or reviewed |
| 422 | malformed result, or one citing evidence never retrieved |
| 503 | no model configured, or the provider could not be reached |

A `422` stores no recommendation at all. The investigation moves to `FAILED` and the
reason goes in the audit trail; no placeholder conclusion is invented.

### `GET /api/v1/investigations/{investigationId}/recommendation` — the stored conclusion

Returns the recommendation with its verified evidence. `404` while an investigation is
still `PENDING` or `RUNNING`: there is genuinely no conclusion yet, and an empty one
would invite a client to render something nobody concluded.

### `POST .../approve`, `.../reject`, `.../escalate` — human decisions

```json
{"reviewed_by": "ops.analyst", "comment": "Fee rule matches the difference."}
```

| Code | Meaning |
|---:|---|
| 200 | decision recorded |
| 404 | no such investigation |
| 409 | not `AWAITING_REVIEW`, or already reviewed |
| 422 | `reviewed_by` missing or empty |

`409` rather than `400` for a state conflict: the request is well-formed, and it is the
state of the investigation that makes it impossible.

### `GET /api/v1/investigations/{investigationId}/audit` — the trail

Oldest first. Read-only — `POST`, `PUT` and `DELETE` return `405`.

### `GET /ready` — readiness

```json
{"status": "READY", "service": "reconai-investigation-service",
 "kafka_consumer": "RUNNING", "database": "UP"}
```

The job needs both Kafka and PostgreSQL, so readiness is `200` only when both are usable
and `503` otherwise:

```json
{"status": "NOT_READY", "service": "reconai-investigation-service",
 "kafka_consumer": "NOT_RUNNING", "database": "DOWN"}
```

The two are checked differently on purpose. Kafka is reported from the consumer's own
state rather than re-probed, which catches what matters — a broker unreachable at
startup, and a loop that has died — without turning every poll into broker traffic. The
database gets a `SELECT 1` on a pooled connection, because a pool can be present while
the server behind it is gone, and that is cheap enough to do per call.

Neither dependency being down stops the process from starting. Liveness stays up and
readiness reports `NOT_READY`, so an orchestrator routes away from the instance instead
of watching it crash-loop through an outage it cannot fix.

The **Financial Core is deliberately not part of readiness.** Kafka and PostgreSQL are
needed continuously to record investigations; the Financial Core is needed only when
evidence is actually retrieved. A brief outage there should make that retrieval fail
explicitly — with one of the errors above — rather than take an otherwise healthy
instance out of rotation and invite restarts that cannot fix an upstream problem.

## Local startup order

Order matters. `auto.offset.reset` is `latest`, so an event produced before the consumer
is listening is not delivered to a new consumer group.

```bash
# 1. Infrastructure. Use RECONAI_POSTGRES_PORT if 5432 is already taken locally.
RECONAI_POSTGRES_PORT=55432 docker compose up -d
```
```bash
# 2. Apply this service's migrations (first run, or after pulling new ones)
cd agent-service && alembic upgrade head
```
```bash
# 3. This service, BEFORE producing anything
python -m app.main
```
```bash
# 4. The financial core, in another shell
cd backend && JAVA_HOME=$(/usr/libexec/java_home -v 21) \
  RECONAI_DB_URL=jdbc:postgresql://localhost:55432/reconai RECONAI_PORT=8099 \
  mvn spring-boot:run
```

### Verifying Spring → Kafka → Python

Create a transaction and a settlement that disagree, then reconcile:

```bash
curl -s -X POST http://localhost:8099/api/v1/transactions -H 'Content-Type: application/json' \
  -d '{"merchantId":"MERCHANT-DEMO","amount":1247.50,"expectedSettlementAmount":1247.50,"currency":"USD","transactionType":"PURCHASE","status":"POSTED","transactionTimestamp":"2026-09-27T09:00:00Z"}'
```
```bash
curl -s -X POST http://localhost:8099/api/v1/settlements -H 'Content-Type: application/json' \
  -d '{"transactionId":"TX-10007","processor":"NORTHSTAR_PAYMENTS","settledAmount":1217.50,"currency":"USD","status":"COMPLETED","settlementTimestamp":"2026-09-27T09:30:00Z"}'
```
```bash
curl -s -X POST http://localhost:8099/api/v1/reconciliation/transactions/TX-10007
```

The investigation service should log:

```
INFO  [app.message_handler] Received reconciliation exception [exception_id=EX-1006 transaction_id=TX-10007 exception_type=AMOUNT_MISMATCH detected_at=2026-09-27T02:04:16.954772+00:00 topic=reconciliation.exceptions partition=0 offset=5]
```

followed immediately by:

```
INFO  [app.investigation_service] Created investigation [investigation_id=INV-1001 exception_id=EX-1006 transaction_id=TX-10007 exception_type=AMOUNT_MISMATCH status=PENDING]
```

Read it back:

```bash
curl -s http://localhost:8000/api/v1/investigations
```

Reconciling the same unchanged records again reuses the exception and publishes nothing,
so no further log line appears. And if the same event is delivered again anyway, the
consumer logs `Reusing existing investigation` and the table still holds one row.

Inspect the group's position at any time:

```bash
docker exec reconai-kafka /opt/kafka/bin/kafka-consumer-groups.sh \
  --bootstrap-server localhost:9092 --describe --group reconai-investigation-service
```

## Docker

```bash
docker build -t reconai-investigation-service .
docker run --rm -p 8000:8000 reconai-investigation-service
curl -s http://localhost:8000/health
```

The image runs as a non-root user and honours the same `RECONAI_AGENT_` variables:

```bash
docker run --rm -p 9100:9100 -e RECONAI_AGENT_PORT=9100 reconai-investigation-service
```

**Inside a container, `localhost:9092` means the container itself**, not the broker on
your machine. A containerised run needs the broker's reachable address:

```bash
docker run --rm -p 8000:8000 \
  -e RECONAI_AGENT_KAFKA_BOOTSTRAP_SERVERS=host.docker.internal:9092 \
  reconai-investigation-service
```

The default stays `localhost:9092` because host development is the common case; it is
overridden rather than changed.

The service is not part of the root `docker-compose.yml` yet. Joining the Compose
network would let it use `kafka:9092`, which is worth doing when the service is deployed
alongside the stack rather than run from a shell.

## Not implemented yet

The backend lifecycle is complete: an exception detected by the financial core becomes an
investigation, is investigated, is stored, is routed by policy, is decided by a human, and
is auditable throughout.

The React Operations Console that calls these endpoints is implemented in
`frontend/` and deployed. What remains absent from *this service* is listed
below.

Absent by design:

- **Authentication and authorization.** `reviewed_by` is caller-supplied and unverified.
  There is no login, no session, no role, and no check that the person approving is
  entitled to. This is a demo boundary and is documented as one everywhere it appears.
- **Any write to the financial core.** Approval records a judgement; it does not resolve
  an exception, adjust a settlement, or move money. No code path here can.
- **Automatic approval or autonomous action of any kind.** The guardrail routes; it never
  decides. Only a human transition reaches `COMPLETED`.
- **Retrying a failed or stuck investigation.** A `FAILED` one stays failed and a crashed
  one stays `RUNNING`. There is no scheduler, no background worker, no retry queue and no
  distributed lock. Recovery is a deliberate operational act, which is the honest
  position until there is an operator to define what recovery should mean.
- **Execution-attempt history.** One investigation has at most one recommendation. If the
  history of attempts is needed later it should be a separate concept — an
  `InvestigationRun` — not extra columns on the investigation.
- **Dead-letter topics and retry infrastructure.** An unusable Kafka message is skipped
  and lost beyond its log line. A storage outage stops consumption rather than retrying.
- **Atomic database and Kafka commits.** They are separate transactions; idempotency
  covers the gap rather than closing it. Delivery is at-least-once and is not claimed to
  be more.
- `get_transaction_history`, semantic search, embeddings, vector stores, pgvector, and
  multiple agents.

`PROCESSOR_FEE` remains a root-cause classification an investigation may propose. It is
not, and must not become, a deterministic reconciliation exception type — enforced in the
Java enum, the Python enum, and a PostgreSQL `CHECK` constraint on each side.

### A note on the provider

The Anthropic adapter has been exercised against the live API in the deployed
environment, where it produced the escalated `PROCESSOR_FEE` recommendation shown in the
root `README.md`. Its latency, rate-limit and retry behaviour under sustained load has
not been characterised.

Every test runs against a scripted `FakeModel`. None calls a provider, and none requires
an API key.
