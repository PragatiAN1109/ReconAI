# ReconAI Investigation Service

An independent Python service that will investigate reconciliation exceptions detected by
the Spring Boot financial core.

It is **not** a system of record. The financial core stays authoritative for transactions
and settlements; this service will never hold or modify them. It does not connect to the
financial core's database, and it will reach authoritative data only through narrow,
read-only interfaces when those are built.

> Deterministic systems detect. AI investigates. Humans authorize.

## Phase 4.1 scope

This is the scaffold. It proves the service installs, configures, starts, reports health,
logs, and tests — nothing more. See [Not implemented yet](#not-implemented-yet).

## Prerequisites

- **Python 3.12**

Everything runs offline. Kafka, PostgreSQL, the Spring backend, Docker and a model
provider are all unnecessary for this phase, including for the tests.

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

Values may also come from a `.env` file in this directory. There are **no secrets**: this
phase talks to nothing that requires credentials.

Example:

```bash
RECONAI_AGENT_PORT=9100 RECONAI_AGENT_LOG_LEVEL=DEBUG python -m app.main
```

Note that `8000` is this service's own port. It is unrelated to the financial core's
`8080`, and the two can run side by side.

## Endpoints

### `GET /health` — liveness

```json
{"status": "UP", "service": "reconai-investigation-service"}
```

The process is running and serving requests.

### `GET /ready` — readiness

```json
{"status": "READY", "service": "reconai-investigation-service"}
```

The service has no external dependencies yet, so readiness means it started
successfully. It deliberately does **not** report on Kafka, PostgreSQL, the financial
core or a model provider: checking things this service does not yet talk to would be a
fabricated signal. Real checks arrive with real dependencies.

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

The service is not part of the root `docker-compose.yml` yet; it has nothing to connect
to, so it runs standalone for now.

## Not implemented yet

**Kafka consumption and AI investigation are not implemented in Phase 4.1.** The service
does not read from `reconciliation.exceptions`, and it makes no model calls.

Also absent, by design: reconciliation event models, any Kafka client, LLM or agent
framework integration, tool calling, financial-core tools, fee rules, transaction
history, policy retrieval, RAG, embeddings, pgvector, investigation and recommendation
persistence, approval and audit workflows, and authentication.

No dependency for any of these is installed. They arrive in later sub-phases, along with
the readiness checks and configuration each one needs.
