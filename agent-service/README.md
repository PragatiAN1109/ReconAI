# ReconAI Investigation Service

An independent Python service that will investigate reconciliation exceptions detected by
the Spring Boot financial core.

It is **not** a system of record. The financial core stays authoritative for transactions
and settlements; this service will never hold or modify them. It does not connect to the
financial core's database, and it will reach authoritative data only through narrow,
read-only interfaces when those are built.

> Deterministic systems detect. AI investigates. Humans authorize.

## Phase 4.5 scope

The service consumes reconciliation exceptions from Kafka and records a `PENDING`
investigation for each — exactly one per exception, however many times the event is
delivered. It also offers **controlled read-only evidence tools**: transactions,
settlements and fee rules from the Financial Core, plus excerpts from a local policy
corpus.

Nothing calls them automatically. No investigation runs, no cause is proposed, and no
model is involved. See [Not implemented yet](#not-implemented-yet).

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

The database port is **55432**, not 5432 — the Compose stack publishes PostgreSQL there
so it does not collide with a local server. Keep it aligned with `RECONAI_POSTGRES_PORT`.
The URL must use the `postgresql+asyncpg://` scheme; the financial core's JDBC URL is a
different thing entirely.

Values may also come from a `.env` file in this directory. There are **no secrets**: this
phase talks to nothing that requires credentials.

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

Statuses are `PENDING`, `RUNNING`, `COMPLETED`, `FAILED` and `ESCALATED`. Kafka ingestion
only ever creates `PENDING`. There is no agent yet, so nothing legitimately advances an
investigation beyond it, and pretending otherwise would be inventing a result.

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

Kafka consumption now works. **AI investigation does not.** A validated event is logged
and that is the end of it.

`get_transaction` and `get_settlements` exist, but **nothing calls them automatically**.
An investigation stays `PENDING`; there is no orchestrator.

Absent by design: LLM and agent framework integration of any kind, agent reasoning,
tool calling, `get_transaction_history`, investigation evidence persistence,
recommendation generation and persistence, **root-cause classification** (including
`PROCESSOR_FEE`, which is not a deterministic exception type and is not concluded
anywhere), confidence scoring, semantic search, embeddings, vector stores, pgvector,
approvals, audit workflow, authentication, a frontend, financial writes, and autonomous
actions of any kind.

Also absent, and worth naming because they are the natural next questions:

- **Dead-letter topics and retry infrastructure.** An unusable message is skipped and
  lost beyond its log line. A storage outage stops consumption rather than retrying.
- **Lifecycle transitions.** The statuses beyond `PENDING` exist in the schema but
  nothing moves an investigation into them, and no transition rules are enforced yet.
- **Atomic database and Kafka commits.** They are separate transactions; idempotency
  covers the gap rather than closing it.

No dependency for any of these is installed. They arrive in later sub-phases.
