"""Liveness and readiness endpoints."""

from typing import Literal

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    """Liveness: the process is running and serving requests."""

    status: Literal["UP"]
    service: str


class ReadinessResponse(BaseModel):
    """Readiness: the service can do its job.

    That job is turning reconciliation exceptions into durable investigations,
    which needs both Kafka and PostgreSQL. Readiness is READY only when both
    are usable, and returns 503 otherwise so an orchestrator stops routing to
    an instance that cannot do the work.

    The two are checked differently, on purpose. Kafka is reported from the
    consumer's own state rather than re-probed, which catches what matters — a
    broker unreachable at startup, and a loop that has died — without turning
    every poll into broker traffic. The database is probed with a ``SELECT 1``
    on a pooled connection, because a pool can be present while the server
    behind it is gone, and that is cheap enough to do per call.
    """

    status: Literal["READY", "NOT_READY"]
    service: str
    kafka_consumer: Literal["RUNNING", "NOT_RUNNING"]
    database: Literal["UP", "DOWN"]


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
def health(request: Request) -> HealthResponse:
    """Liveness only.

    Deliberately independent of Kafka and PostgreSQL. An outage in either does
    not mean this process should be killed and restarted; it means this
    instance is not ready. Conflating the two would turn a dependency blip into
    a restart loop.
    """
    return HealthResponse(status="UP", service=request.app.state.settings.service_name)


@router.get("/ready", response_model=ReadinessResponse, summary="Readiness probe")
async def ready(request: Request, response: Response) -> ReadinessResponse:
    consumer_running = request.app.state.consumer.is_running
    database_up = await request.app.state.database.check()

    if not (consumer_running and database_up):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return ReadinessResponse(
        status="READY" if consumer_running and database_up else "NOT_READY",
        service=request.app.state.settings.service_name,
        kafka_consumer="RUNNING" if consumer_running else "NOT_RUNNING",
        database="UP" if database_up else "DOWN",
    )
