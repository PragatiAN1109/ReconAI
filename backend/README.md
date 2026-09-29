# ReconAI Financial Core

Deterministic payment reconciliation. Compares authoritative transaction and settlement
records, records the discrepancies it finds, and publishes them for later investigation.

Contains no AI. See [Not implemented yet](#not-implemented-yet).

## Prerequisites

- **Java 21** — required. Spring Boot 3.5 does not support JDK 25, and Homebrew Maven
  often defaults to it. Check with `mvn -version`, and if it reports anything other than
  21, set `JAVA_HOME` explicitly (examples below).
- Maven 3.9+
- Docker (PostgreSQL, Kafka, and the Testcontainers-based tests)

## Start the infrastructure

```bash
RECONAI_POSTGRES_PORT=55432 docker compose up -d
docker compose ps
```

Both services should report `healthy`.

### Why 55432?

`RECONAI_POSTGRES_PORT` only chooses the **host port Docker publishes**. Without it the
container binds `5432`, which on a machine with PostgreSQL already installed collides
with the local server. Docker still starts and reports healthy, but the backend silently
connects to the local PostgreSQL and fails with:

```
FATAL: role "reconai" does not exist
```

If you have no local PostgreSQL, omit the variable and use the `5432` default throughout.

### `RECONAI_POSTGRES_PORT` and `RECONAI_DB_URL` are separate

This is the one thing worth remembering:

| Variable | Read by | Effect |
|---|---|---|
| `RECONAI_POSTGRES_PORT` | Docker Compose only | host port the container is published on |
| `RECONAI_DB_URL` | Spring Boot only | JDBC URL the backend connects to |

Spring knows nothing about `RECONAI_POSTGRES_PORT`. **Changing the Compose port means
also setting `RECONAI_DB_URL`**, or the backend keeps using its default of
`jdbc:postgresql://localhost:5432/reconai`.

Verify the container is the database you expect:

```bash
docker exec reconai-postgres psql -U reconai -d reconai -c "SELECT current_user, current_database();"
```

Both values should be `reconai`.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `RECONAI_DB_URL` | `jdbc:postgresql://localhost:5432/reconai` | datasource URL |
| `RECONAI_DB_USERNAME` | `reconai` | datasource user |
| `RECONAI_DB_PASSWORD` | `reconai` | datasource password |
| `RECONAI_PORT` | `8080` | HTTP port |
| `RECONAI_KAFKA_BOOTSTRAP_SERVERS` | `localhost:9092` | Kafka broker |
| `RECONAI_KAFKA_EXCEPTIONS_TOPIC` | `reconciliation.exceptions` | exception event topic |

The defaults match `docker compose up -d` with no overrides.

## Migrations

Flyway owns the schema and runs automatically at startup; there is no separate migration
command. Hibernate is set to `ddl-auto: validate` and never creates or alters anything.

Development seed data lives in `db/seed` and is applied only under the `dev` profile
(`--spring.profiles.active=dev`).

## Start the backend

```bash
cd backend
JAVA_HOME=$(/usr/libexec/java_home -v 21) \
RECONAI_DB_URL=jdbc:postgresql://localhost:55432/reconai \
  mvn spring-boot:run
```

Startup is successful when the log reaches `Started ReconAiApplication` and Flyway
reports `Successfully applied 3 migrations`.

### Running on a different port

Port `8080` is a common default and may already be taken by another application, which
typically shows up as an unexpected `401` or an unfamiliar response from an endpoint that
should exist. Check with `lsof -nP -iTCP:8080 -sTCP:LISTEN`, then either free the port or
move ReconAI:

```bash
JAVA_HOME=$(/usr/libexec/java_home -v 21) \
RECONAI_DB_URL=jdbc:postgresql://localhost:55432/reconai \
RECONAI_PORT=8099 \
  mvn spring-boot:run
```

Confirm the right application answers:

```bash
curl -s http://localhost:8099/api/v1/exceptions
# {"items":[],"total":0}
```

## Run the tests

```bash
cd backend
JAVA_HOME=$(/usr/libexec/java_home -v 21) mvn test
```

Tests need Docker but not the Compose stack: they start their own PostgreSQL through
Testcontainers, and one isolated class starts its own Kafka broker. They never touch the
Compose database.

## Implemented APIs

Base path `/api/v1`. Every request accepts an optional `X-Correlation-ID` header, which is
generated when absent and returned on the response.

### Transactions

| Method | Path |
|---|---|
| `POST` | `/api/v1/transactions` |
| `GET` | `/api/v1/transactions/{transactionId}` |

### Settlements

| Method | Path |
|---|---|
| `POST` | `/api/v1/settlements` |
| `GET` | `/api/v1/transactions/{transactionId}/settlements` |

A transaction may have zero, one or many settlements; duplicates are storable on purpose.

### Reconciliation

| Method | Path |
|---|---|
| `POST` | `/api/v1/reconciliation/transactions/{transactionId}` |
| `POST` | `/api/v1/reconciliation/run` |

Deterministic, and idempotent: re-running over unchanged records reuses the existing
exception rather than creating another.

### Reconciliation exceptions

| Method | Path |
|---|---|
| `GET` | `/api/v1/exceptions` — optional `status`, `exceptionType`, `transactionId` filters |
| `GET` | `/api/v1/exceptions/{exceptionId}` |

Read-only. Exceptions are created by the reconciliation engine, never by a caller.

## Messaging

Newly detected exceptions are published to `reconciliation.exceptions` after the database
transaction commits, so a rolled-back detection is never announced. The payload carries
identity only — `exceptionId`, `transactionId`, `type`, `detectedAt`.

The financial core publishes and never consumes. Reconciliation does not wait for Kafka
acknowledgement, and a broker outage does not affect whether a discrepancy is detected and
persisted. See `docs/architecture.md` §5.1–5.5, including the V1 delivery limitation.

## Not implemented yet

There is **no AI or agent functionality in this service**, by design. Reconciliation is
fully deterministic and works with no investigation service present.

Implemented elsewhere, deliberately not here: the Python investigation agent, FastAPI,
LLM calls, investigations, recommendations, approvals and the audit API all live in
`agent-service/`; the React Operations Console lives in `frontend/`. This service
serves fee rules at `/api/v1/fee-rules` as evidence for those investigations, but it
never reads them during reconciliation.

Not implemented anywhere: RAG, embeddings, pgvector (policy retrieval is deterministic
lexical matching), the dashboard API, and authentication.

If a request to this service returns an authentication error, it is not ReconAI answering:
this service has no authentication.
