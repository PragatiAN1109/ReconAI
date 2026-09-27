"""Liveness and readiness endpoints."""

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    """Liveness: the process is running and serving requests."""

    status: Literal["UP"]
    service: str


class ReadinessResponse(BaseModel):
    """Readiness: the service is able to accept work.

    The service has no external dependencies in this phase — no Kafka, no
    database, no model provider — so readiness is equivalent to having started
    successfully. Reporting on dependencies that do not exist would be a
    fabricated check, so this stays honest and gains real checks when there is
    something real to check.
    """

    status: Literal["READY"]
    service: str


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
def health(request: Request) -> HealthResponse:
    return HealthResponse(status="UP", service=request.app.state.settings.service_name)


@router.get("/ready", response_model=ReadinessResponse, summary="Readiness probe")
def ready(request: Request) -> ReadinessResponse:
    return ReadinessResponse(status="READY", service=request.app.state.settings.service_name)
