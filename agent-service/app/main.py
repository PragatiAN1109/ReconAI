"""ReconAI Investigation Service.

An independent service that will investigate reconciliation exceptions detected
by the Spring Boot financial core. It is not a system of record: the financial
core remains authoritative for transactions and settlements, and this service
never writes to them.

Phase 4.7 completes the backend lifecycle. A PENDING investigation can be run
through a bounded loop in which a language model requests evidence through four
controlled tools and proposes an explanation. The application checks that
explanation against the evidence actually retrieved, stores it, and a
deterministic guardrail routes it to a human or escalates it. A human then
approves, rejects or escalates, and every step is recorded in an append-only
audit trail.

Every recommendation is advisory. Nothing here approves anything on its own,
and nothing here writes to the financial core: this service reads from it and
owns only its own schema.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from app.config import Settings
from app.database import Database
from app.financial_core_client import FinancialCoreClient
from app.audit_service import AuditService
from app.health import router as health_router
from app.investigation_service import InvestigationService
from app.investigation_workflow import InvestigationWorkflow
from app.investigations_api import router as investigations_router
from app.kafka_consumer import ReconciliationExceptionConsumer
from app.review_service import ReviewService
from app.logging_config import configure_logging
from app.investigation_agent import InvestigationAgent
from app.investigation_model import InvestigationModel
from app.policy_search import PolicySearch
from app.rate_limit import FixedWindowRateLimiter

logger = logging.getLogger(__name__)

DESCRIPTION = (
    "Investigates reconciliation exceptions raised by the ReconAI financial core. "
    "Advisory only: it holds no financial records and cannot modify them."
)


def _build_model(settings: Settings) -> InvestigationModel | None:
    """Construct the configured provider, or none.

    Imported lazily so the provider SDK stays an optional dependency.
    """
    if not settings.investigation_model_configured:
        logger.info(
            "No investigation model configured; the investigation endpoint will report "
            "itself unavailable [provider=%s]",
            settings.llm_provider,
        )
        return None

    from app.anthropic_model import AnthropicInvestigationModel  # noqa: PLC0415

    assert settings.llm_api_key is not None
    logger.info(
        "Investigation model configured [provider=%s model=%s]",
        settings.llm_provider,
        settings.llm_model,
    )
    return AnthropicInvestigationModel(
        api_key=settings.llm_api_key.get_secret_value(),
        model=settings.llm_model,
        max_tokens=settings.llm_output_limit,
    )


def create_app(
    settings: Settings | None = None,
    consumer: ReconciliationExceptionConsumer | None = None,
    database: Database | None = None,
    financial_core: FinancialCoreClient | None = None,
    investigation_model: InvestigationModel | None = None,
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
    # Review and audit need no model and no financial-core client: a human
    # decision and its record are entirely this service's own business.
    reviews = ReviewService(database)
    audit = AuditService(database)

    # Optional. With no model configured the service still consumes Kafka and
    # records investigations; only the investigation endpoint is unavailable,
    # which it reports as 503 rather than failing at startup.
    investigation_model = investigation_model or _build_model(settings)
    investigation_agent = (
        InvestigationAgent(
            investigation_model,
            financial_core,
            policies,
            max_tool_rounds=settings.investigation_max_tool_rounds,
        )
        if investigation_model is not None
        else None
    )
    # The workflow is what persists an outcome. Without a model there is
    # nothing to run, so it is absent rather than a stub, and the run endpoint
    # reports 503 instead of failing later.
    investigation_workflow = (
        InvestigationWorkflow(
            database,
            investigation_agent,
            confidence_threshold=settings.review_confidence_threshold,
            minimum_evidence=settings.review_minimum_evidence,
            model_provider=settings.llm_provider,
            model_name=settings.llm_model,
            prompt_version=settings.prompt_version,
        )
        if investigation_agent is not None
        else None
    )
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
        version="0.7.0",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.consumer = consumer
    app.state.database = database
    app.state.investigations = investigations
    app.state.financial_core = financial_core
    app.state.policies = policies
    app.state.investigation_agent = investigation_agent
    app.state.investigation_workflow = investigation_workflow
    app.state.reviews = reviews
    app.state.audit = audit
    # One limiter for the lifetime of the app, shared by every run request. The
    # run endpoint is the only paid operation here and this service is public.
    app.state.run_limiter = FixedWindowRateLimiter(
        per_client_limit=settings.run_per_client_limit,
        global_limit=settings.run_global_limit,
        window_seconds=settings.run_window_seconds,
    )
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
