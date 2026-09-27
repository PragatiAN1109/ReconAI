"""ReconAI Investigation Service.

An independent service that will investigate reconciliation exceptions detected
by the Spring Boot financial core. It is not a system of record: the financial
core remains authoritative for transactions and settlements, and this service
never writes to them.

Phase 4.1 is the scaffold only — configuration, logging, health and readiness.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from app.config import Settings
from app.health import router as health_router
from app.logging_config import configure_logging

logger = logging.getLogger(__name__)

DESCRIPTION = (
    "Investigates reconciliation exceptions raised by the ReconAI financial core. "
    "Advisory only: it holds no financial records and cannot modify them."
)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application.

    Settings are a parameter rather than a module-level singleton so that tests
    can construct an application with whatever configuration they need, without
    mutating global state or clearing caches.
    """
    settings = settings or Settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        logger.info(
            "Investigation service starting [service=%s environment=%s host=%s port=%d]",
            settings.service_name,
            settings.environment,
            settings.host,
            settings.port,
        )
        yield
        logger.info("Investigation service stopped [service=%s]", settings.service_name)

    app = FastAPI(
        title="ReconAI Investigation Service",
        description=DESCRIPTION,
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.include_router(health_router)
    return app


app = create_app()


def main() -> None:
    """Run the service with the configured host, port and log level."""
    settings = Settings()
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
