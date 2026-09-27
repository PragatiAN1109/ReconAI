# ReconAI Investigation Service

An independent Python service that will investigate reconciliation exceptions detected by
the Spring Boot financial core.

It is **not** a system of record. The financial core stays authoritative for transactions
and settlements; this service will never hold or modify them. It does not connect to the
financial core's database, and it will reach authoritative data only through narrow,
read-only interfaces when those are built.

> Deterministic systems detect. AI investigates. Humans authorize.

## Phase 4.2 scope

The service consumes reconciliation exceptions from Kafka, validates them against the
contract the financial core publishes, and logs them. **It stops there** — nothing is
investigated, fetched, persisted, or sent to a model. See
[Not implemented yet](#not-implemented-yet).

## Prerequisites

- **Python 3.12**
- A Kafka broker, to actually consume events. The repository's Compose stack provides
  one; see [Local startup order](#local-startup-order).

The **tests** need none of this. They fake the Kafka boundary and run offline, with no
broker, database, backend, Docker, network or model provider.

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

Auto-commit is off; each record is committed after it has been handled. Delivery is
therefore **at-least-once** — a crash between handling and committing replays the
record. Today that only repeats a log line, but **investigation handling in a later
phase must be idempotent by `exceptionId`.** This is not exactly-once and is not claimed
to be.

### Messages the service cannot use

Malformed JSON, a missing field, an unknown exception type, an unparseable timestamp or
a timezone-naive timestamp are all logged with the topic, partition and offset — then
the record is **committed anyway, which skips it**.

That is a deliberate trade-off. With no dead-letter topic (out of scope here), *not*
committing would make the consumer re-read the same poison record forever and stall
every record behind it on that partition. The cost is that such a record is dropped and
the log line is its only remaining trace.

## Endpoints

### `GET /health` — liveness

```json
{"status": "UP", "service": "reconai-investigation-service"}
```

The process is running and serving requests.

### `GET /ready` — readiness

```json
{"status": "READY", "service": "reconai-investigation-service", "kafka_consumer": "RUNNING"}
```

Kafka is now a real dependency, so readiness follows the consumer. It returns `200` only
when the consumer connected at startup and its loop is still alive, and `503` otherwise:

```json
{"status": "NOT_READY", "service": "reconai-investigation-service", "kafka_consumer": "NOT_RUNNING"}
```

It reflects the consumer's own state rather than re-probing the broker on every call.
That catches the failures that matter — a broker unreachable at startup, and a consumer
loop that has died — without turning each readiness poll into broker traffic.

An unreachable broker does **not** stop the process from starting. Liveness stays up and
readiness reports `NOT_READY`, so an orchestrator routes away from the instance instead
of watching it crash-loop through an outage it cannot fix.

## Local startup order

Order matters. `auto.offset.reset` is `latest`, so an event produced before the consumer
is listening is not delivered to a new consumer group.

```bash
# 1. Infrastructure. Use RECONAI_POSTGRES_PORT if 5432 is already taken locally.
RECONAI_POSTGRES_PORT=55432 docker compose up -d
```
```bash
# 2. This service, BEFORE producing anything
cd agent-service && python -m app.main
```
```bash
# 3. The financial core, in another shell
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

Reconciling the same unchanged records again reuses the exception and publishes nothing,
so no further log line appears. That is correct: one investigation per discrepancy, not
per API call.

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

Absent by design: LLM and agent framework integration of any kind, agent reasoning, tool
calling, the financial-core tools (`get_transaction`, `get_settlements`, `get_fee_rules`,
`get_transaction_history`, `search_policy_documents`), an HTTP client to the financial
core, any database access, investigation and recommendation persistence, fee rules, RAG,
embeddings, pgvector, approvals, audit workflow, authentication, a frontend, and
autonomous actions of any kind.

Also absent, and worth naming because they are the natural next questions about the
consumer: dead-letter topics, retry infrastructure, and idempotency by `exceptionId`.
An unusable message is skipped, and a replayed message would be processed twice.

No dependency for any of these is installed. They arrive in later sub-phases.
