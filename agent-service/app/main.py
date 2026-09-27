"""ReconAI Investigation Service.

An independent service that will investigate reconciliation exceptions detected
by the Spring Boot financial core. It is not a system of record: the financial
core remains authoritative for transactions and settlements, and this service
never writes to them.

Phase 4.2 consumes reconciliation exceptions from Kafka and validates them.
It stops there: nothing is investigated, fetched, persisted or sent to a model.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from app.config import Settings
from app.health import router as health_router
from app.kafka_consumer import ReconciliationExceptionConsumer
from app.logging_config import configure_logging

logger = logging.getLogger(__name__)

DESCRIPTION = (
    "Investigates reconciliation exceptions raised by the ReconAI financial core. "
    "Advisory only: it holds no financial records and cannot modify them."
)


def create_app(
    settings: Settings | None = None,
    consumer: ReconciliationExceptionConsumer | None = None,
) -> FastAPI:
    """Build the application.

    Settings and the consumer are parameters rather than module-level
    singletons so that tests can supply their own, without global state, cache
    clearing or a broker.
    """
    settings = settings or Settings()
    configure_logging(settings.log_level)
    consumer = consumer or ReconciliationExceptionConsumer(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        logger.info(
            "Investigation service starting [service=%s environment=%s host=%s port=%d]",
            settings.service_name,
            settings.environment,
            settings.host,
            settings.port,
        )
        try:
            await consumer.start()
        except Exception:
            # An unreachable broker must not stop the process from starting.
            # Liveness stays up, readiness reports NOT_READY, and an
            # orchestrator can route away from this instance rather than watch
            # it crash-loop through an outage it cannot fix.
            logger.exception(
                "Kafka consumer failed to start; the service will report itself as not ready "
                "[bootstrap_servers=%s topic=%s]",
                settings.kafka_bootstrap_servers,
                settings.kafka_exceptions_topic,
            )

        try:
            yield
        finally:
            await consumer.stop()
            logger.info("Investigation service stopped [service=%s]", settings.service_name)

    app = FastAPI(
        title="ReconAI Investigation Service",
        description=DESCRIPTION,
        version="0.2.0",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.consumer = consumer
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
