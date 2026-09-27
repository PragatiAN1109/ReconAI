"""ReconAI Investigation Service.

An independent service that will investigate reconciliation exceptions detected
by the Spring Boot financial core. It is not a system of record: the financial
core remains authoritative for transactions and settlements, and this service
never writes to them.

Phase 4.5 consumes reconciliation exceptions from Kafka, records a PENDING
investigation for each, and offers controlled read-only ways to retrieve
evidence: transactions, settlements and fee rules from the financial core, and
excerpts from a local policy corpus. Nothing uses them automatically. No
investigation runs, no cause is proposed, and no model is called.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from app.config import Settings
from app.database import Database
from app.financial_core_client import FinancialCoreClient
from app.health import router as health_router
from app.investigation_service import InvestigationService
from app.investigations_api import router as investigations_router
from app.kafka_consumer import ReconciliationExceptionConsumer
from app.logging_config import configure_logging
from app.policy_search import PolicySearch

logger = logging.getLogger(__name__)

DESCRIPTION = (
    "Investigates reconciliation exceptions raised by the ReconAI financial core. "
    "Advisory only: it holds no financial records and cannot modify them."
)


def create_app(
    settings: Settings | None = None,
    consumer: ReconciliationExceptionConsumer | None = None,
    database: Database | None = None,
    financial_core: FinancialCoreClient | None = None,
) -> FastAPI:
    """Build the application.

    Settings, the consumer and the database are parameters rather than
    module-level singletons so that tests can supply their own, without global
    state, cache clearing, a broker or a database.
    """
    settings = settings or Settings()
    configure_logging(settings.log_level)
    database = database or Database(settings)
    financial_core = financial_core or FinancialCoreClient(settings)
    # Read once at startup: the corpus is small, changes rarely, and loading it
    # per query would make results depend on filesystem timing.
    policies = PolicySearch(settings.policy_corpus_path)
    investigations = InvestigationService(database)
    consumer = consumer or ReconciliationExceptionConsumer(settings, investigations)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        logger.info(
            "Investigation service starting [service=%s environment=%s host=%s port=%d]",
            settings.service_name,
            settings.environment,
            settings.host,
            settings.port,
        )
        # The database comes up before the consumer: recording an investigation
        # is the only thing consuming an event is for, so there is no value in
        # pulling events we cannot store.
        try:
            await database.connect()
        except Exception:
            logger.exception(
                "Database failed to connect; the service will report itself as not ready [url=%s]",
                settings.redacted_database_url,
            )

        # Opening the pool does not contact the financial core, so there is
        # nothing here that can fail on its account.
        await financial_core.open()

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
            # Reverse order: stop consuming before closing the pool the
            # consumer writes through.
            await consumer.stop()
            await financial_core.close()
            await database.disconnect()
            logger.info("Investigation service stopped [service=%s]", settings.service_name)

    app = FastAPI(
        title="ReconAI Investigation Service",
        description=DESCRIPTION,
        version="0.5.0",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.consumer = consumer
    app.state.database = database
    app.state.investigations = investigations
    app.state.financial_core = financial_core
    app.state.policies = policies
    app.include_router(health_router)
    app.include_router(investigations_router)
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
