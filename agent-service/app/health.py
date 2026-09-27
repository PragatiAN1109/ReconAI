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

    That job is consuming reconciliation exceptions, so readiness follows the
    Kafka consumer. It reports READY only when the consumer connected at startup
    and its loop is still alive, and returns 503 otherwise so an orchestrator
    stops routing to an instance that is processing nothing.

    What this does *not* do is re-probe the broker on every call. It reflects
    the consumer's own state, which catches the failures that matter — a broker
    unreachable at startup, and a consumer loop that has died — without turning
    every readiness poll into broker traffic.
    """

    status: Literal["READY", "NOT_READY"]
    service: str
    kafka_consumer: Literal["RUNNING", "NOT_RUNNING"]


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
def health(request: Request) -> HealthResponse:
    """Liveness only.

    Deliberately independent of Kafka. A broker outage does not mean this
    process should be killed and restarted; it means this instance is not ready.
    Conflating the two would turn a broker blip into a restart loop.
    """
    return HealthResponse(status="UP", service=request.app.state.settings.service_name)


@router.get("/ready", response_model=ReadinessResponse, summary="Readiness probe")
def ready(request: Request, response: Response) -> ReadinessResponse:
    consumer_running = request.app.state.consumer.is_running
    if not consumer_running:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return ReadinessResponse(
        status="READY" if consumer_running else "NOT_READY",
        service=request.app.state.settings.service_name,
        kafka_consumer="RUNNING" if consumer_running else "NOT_RUNNING",
    )
