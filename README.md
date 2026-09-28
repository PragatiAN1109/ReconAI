# ReconAI

> Deterministic systems detect. AI investigates. Humans authorize.

A payment reconciliation platform: a Spring Boot financial core detects
discrepancies deterministically, a Python service investigates them with a
language model under controlled tools and evidence grounding, a deterministic
guardrail decides what a human sees, and a React console is where a person
decides.

## Running the whole stack

### Prerequisites

Docker with Compose v2. Nothing else — no local Java, Python, Node, PostgreSQL
or Kafka is required.

### Optional: enable the AI provider

The stack runs fully without it. With no provider configured every service
starts normally and only the investigation *run* endpoint reports 503.

```bash
cp .env.example .env
```

Then uncomment `RECONAI_AGENT_LLM_PROVIDER` and `RECONAI_AGENT_LLM_API_KEY` in
`.env`. That file is gitignored, is never copied into an image, and never
appears in `docker compose config`.

⚠️ Running a live investigation calls a paid API.

### Start

```bash
docker compose up --build
```

### Service URLs

| | URL |
|---|---|
| **Operations Console** | <http://localhost:3000> |
| Financial Core API | <http://localhost:8099/api/v1/exceptions> |
| Investigation Service API | <http://localhost:8000/api/v1/investigations> |
| Investigation Service health | <http://localhost:8000/health> · <http://localhost:8000/ready> |
| PostgreSQL | `localhost:55432` (user/db `reconai`) |
| Kafka | `localhost:9092` |

PostgreSQL is on **55432**, not 5432, because a developer machine commonly
already runs PostgreSQL on the default port; the services would otherwise
silently connect to the wrong server.

Inside the Compose network services address each other by name —
`postgres:5432`, `kafka:29092`, `financial-core:8080`,
`investigation-service:8000`. The host ports above are for debugging only.

### Logs

```bash
docker compose logs -f
```

```bash
docker compose logs -f investigation-service
```

### Stopping WITHOUT deleting data

```bash
docker compose down
```

This stops and removes the containers and keeps the named volume, so recorded
investigations, recommendations and audit history survive. `docker compose stop`
also works and leaves the containers in place.

> ### ⚠️ `docker compose down -v` DELETES YOUR DATA
>
> The `-v` flag removes the `reconai-postgres-data` volume, and with it every
> transaction, settlement, exception, investigation, recommendation and audit
> event the stack has recorded. There is no undo. Use plain `docker compose
> down` unless you genuinely intend to start from an empty database.

