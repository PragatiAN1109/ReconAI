"""Persistence and idempotency, against a real PostgreSQL.

These tests use Testcontainers rather than a substitute engine, because what
they assert is PostgreSQL behaviour: a unique constraint deciding a race
between concurrent inserts, a sequence surviving conflicting transactions, and
ON CONFLICT semantics. A different engine passing these would prove nothing
about production.

They skip automatically when Docker is unavailable, so the rest of the suite
stays runnable on a machine with nothing installed.
"""

import asyncio
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.config import Settings
from app.database import Database
from app.events import ExceptionType, ReconciliationExceptionEvent
from app.investigation_service import InvestigationService
from app.models import SCHEMA, InvestigationStatus

pytestmark = pytest.mark.integration

DETECTED_AT = datetime(2026, 9, 27, 2, 4, 16, 954772, tzinfo=UTC)


def _docker_is_available() -> bool:
    try:
        import docker  # noqa: PLC0415

        docker.from_env().ping()
        return True
    except Exception:
        return False


@pytest.fixture(scope="module")
def postgres_url() -> Iterator[str]:
    """A throwaway PostgreSQL with this service's schema migrated into it."""
    if not _docker_is_available():
        pytest.skip("Docker is unavailable; skipping PostgreSQL integration tests")

    from alembic import command  # noqa: PLC0415
    from alembic.config import Config  # noqa: PLC0415
    from testcontainers.community.postgres import PostgresContainer  # noqa: PLC0415

    with PostgresContainer("postgres:16-alpine", driver="asyncpg") as container:
        url = container.get_connection_url()

        # The real migration runs here, so these tests exercise the schema the
        # application actually deploys rather than one built from ORM metadata.
        alembic_config = Config("alembic.ini")
        alembic_config.set_main_option("sqlalchemy.url", url)
        command.upgrade(alembic_config, "head")

        yield url


@pytest.fixture
async def database(postgres_url: str) -> AsyncIterator[Database]:
    db = Database(Settings(_env_file=None, database_url=postgres_url))
    await db.connect()
    yield db
    await db.disconnect()


@pytest.fixture
async def investigations(database: Database) -> InvestigationService:
    return InvestigationService(database)


@pytest.fixture(autouse=True)
async def clean_investigations(database: Database) -> AsyncIterator[None]:
    """Each test starts from an empty table in its own container."""
    yield
    async with database.session() as session:
        await session.execute(sa.text(f"DELETE FROM {SCHEMA}.investigations"))


def event(exception_id: str, **overrides: object) -> ReconciliationExceptionEvent:
    payload: dict[str, object] = {
        "exceptionId": exception_id,
        "transactionId": "TX-10007",
        "type": "AMOUNT_MISMATCH",
        "detectedAt": DETECTED_AT.isoformat(),
    }
    payload.update(overrides)
    return ReconciliationExceptionEvent.model_validate(payload)


async def count_rows(database: Database, exception_id: str | None = None) -> int:
    query = f"SELECT count(*) FROM {SCHEMA}.investigations"
    parameters: dict[str, str] = {}
    if exception_id is not None:
        query += " WHERE exception_id = :exception_id"
        parameters["exception_id"] = exception_id
    async with database.session() as session:
        return await session.scalar(sa.text(query), parameters)


# ---------------------------------------------------------------------------
# Creating an investigation
# ---------------------------------------------------------------------------


async def test_a_valid_event_creates_a_pending_investigation(
    investigations: InvestigationService,
) -> None:
    record = await investigations.create_or_get(event("EX-2001"))

    assert record.created is True
    assert record.investigation.status == InvestigationStatus.PENDING.value


async def test_the_investigation_receives_an_inv_business_id(
    investigations: InvestigationService,
) -> None:
    record = await investigations.create_or_get(event("EX-2002"))

    assert record.investigation.investigation_id.startswith("INV-")
    assert record.investigation.investigation_id.removeprefix("INV-").isdigit()


async def test_the_event_fields_are_persisted_faithfully(
    investigations: InvestigationService,
) -> None:
    record = await investigations.create_or_get(
        event("EX-2003", transactionId="TX-99999", type="CURRENCY_MISMATCH")
    )

    investigation = record.investigation
    assert investigation.exception_id == "EX-2003"
    assert investigation.transaction_id == "TX-99999"
    assert investigation.exception_type == "CURRENCY_MISMATCH"
    assert investigation.detected_at == DETECTED_AT


@pytest.mark.parametrize("exception_type", [member.value for member in ExceptionType])
async def test_every_deterministic_exception_type_is_accepted_by_the_database(
    investigations: InvestigationService, exception_type: str
) -> None:
    record = await investigations.create_or_get(
        event(f"EX-TYPE-{exception_type}", type=exception_type)
    )

    assert record.investigation.exception_type == exception_type


async def test_an_internal_uuid_is_assigned_separately_from_the_business_id(
    investigations: InvestigationService,
) -> None:
    record = await investigations.create_or_get(event("EX-2004"))

    assert record.investigation.id is not None
    assert str(record.investigation.id) != record.investigation.investigation_id


async def test_different_exceptions_create_different_investigations(
    investigations: InvestigationService, database: Database
) -> None:
    first = await investigations.create_or_get(event("EX-2005"))
    second = await investigations.create_or_get(event("EX-2006"))

    assert first.investigation.investigation_id != second.investigation.investigation_id
    assert await count_rows(database) == 2


# ---------------------------------------------------------------------------
# Idempotency by exception_id
# ---------------------------------------------------------------------------


async def test_a_duplicate_delivery_reuses_the_same_investigation(
    investigations: InvestigationService,
) -> None:
    first = await investigations.create_or_get(event("EX-3001"))
    second = await investigations.create_or_get(event("EX-3001"))

    assert first.created is True
    assert second.created is False
    assert second.investigation.investigation_id == first.investigation.investigation_id


async def test_a_duplicate_delivery_does_not_create_a_second_row(
    investigations: InvestigationService, database: Database
) -> None:
    for _ in range(5):
        await investigations.create_or_get(event("EX-3002"))

    assert await count_rows(database, "EX-3002") == 1


async def test_the_database_itself_refuses_a_second_investigation_for_one_exception(
    investigations: InvestigationService, database: Database
) -> None:
    """The guarantee must not depend on application code being correct."""
    await investigations.create_or_get(event("EX-3003"))

    with pytest.raises(IntegrityError) as failure:
        async with database.session() as session:
            await session.execute(
                sa.text(
                    f"INSERT INTO {SCHEMA}.investigations "
                    "(investigation_id, exception_id, transaction_id, exception_type, "
                    " status, detected_at) "
                    "VALUES ('INV-999999', 'EX-3003', 'TX-1', 'AMOUNT_MISMATCH', "
                    " 'PENDING', now())"
                )
            )

    assert "uq_investigations_exception_id" in str(failure.value)


async def test_concurrent_duplicate_deliveries_produce_exactly_one_investigation(
    investigations: InvestigationService, database: Database
) -> None:
    """The race the unique constraint exists for.

    Ten deliveries of one exception at once. A read-then-write would let
    several past the read; the constraint has to be what decides.
    """
    results = await asyncio.gather(
        *(investigations.create_or_get(event("EX-3004")) for _ in range(10))
    )

    assert await count_rows(database, "EX-3004") == 1
    assert len({record.investigation.investigation_id for record in results}) == 1
    assert sum(1 for record in results if record.created) <= 1


async def test_concurrent_deliveries_of_different_exceptions_all_succeed(
    investigations: InvestigationService, database: Database
) -> None:
    await asyncio.gather(
        *(investigations.create_or_get(event(f"EX-4{index:03d}")) for index in range(10))
    )

    assert await count_rows(database) == 10


async def test_a_redelivery_after_a_lost_offset_commit_reuses_the_investigation(
    investigations: InvestigationService, database: Database
) -> None:
    """The crash window: the row committed, the Kafka offset did not.

    Kafka redelivers, and idempotency is what makes that harmless.
    """
    original = await investigations.create_or_get(event("EX-3005"))

    redelivered = await investigations.create_or_get(event("EX-3005"))

    assert redelivered.created is False
    assert redelivered.investigation.investigation_id == original.investigation.investigation_id
    assert await count_rows(database, "EX-3005") == 1


# ---------------------------------------------------------------------------
# Business identifiers
# ---------------------------------------------------------------------------


async def test_business_ids_come_from_a_sequence_and_do_not_repeat(
    investigations: InvestigationService,
) -> None:
    identifiers = [
        (await investigations.create_or_get(event(f"EX-5{index:03d}"))).investigation.investigation_id
        for index in range(5)
    ]

    assert len(set(identifiers)) == 5


async def test_the_sequence_lives_in_the_database_not_in_process_memory(
    database: Database,
) -> None:
    """Restart safety: a fresh service instance must not reissue identifiers."""
    first = InvestigationService(database)
    second = InvestigationService(database)

    one = await first.create_or_get(event("EX-5100"))
    two = await second.create_or_get(event("EX-5101"))

    assert one.investigation.investigation_id != two.investigation.investigation_id


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


async def test_an_investigation_can_be_read_back_by_business_id(
    investigations: InvestigationService,
) -> None:
    created = await investigations.create_or_get(event("EX-6001"))

    found = await investigations.get_by_investigation_id(created.investigation.investigation_id)

    assert found is not None
    assert found.exception_id == "EX-6001"


async def test_an_unknown_business_id_reads_as_nothing(
    investigations: InvestigationService,
) -> None:
    assert await investigations.get_by_investigation_id("INV-000000") is None


async def test_listing_returns_every_investigation(
    investigations: InvestigationService,
) -> None:
    await investigations.create_or_get(event("EX-6002"))
    await investigations.create_or_get(event("EX-6003"))

    assert len(await investigations.list_all()) == 2


# ---------------------------------------------------------------------------
# Ownership boundary
# ---------------------------------------------------------------------------


async def test_this_service_creates_nothing_outside_its_own_schema(
    investigations: InvestigationService, database: Database
) -> None:
    """Only investigation-owned structures exist; no financial-core tables."""
    await investigations.create_or_get(event("EX-7001"))

    async with database.session() as session:
        tables = list(
            await session.scalars(
                sa.text(
                    "SELECT schemaname || '.' || tablename FROM pg_tables "
                    "WHERE schemaname NOT IN ('pg_catalog', 'information_schema')"
                )
            )
        )

    assert sorted(tables) == [
        f"{SCHEMA}.alembic_version",
        f"{SCHEMA}.investigations",
    ]
    assert not any("transactions" in table for table in tables)
    assert not any("settlements" in table for table in tables)
    assert not any("reconciliation_exceptions" in table for table in tables)


async def test_the_investigations_table_has_no_foreign_keys(database: Database) -> None:
    """References to financial-core records are identifiers, never constraints."""
    async with database.session() as session:
        foreign_keys = list(
            await session.scalars(
                sa.text(
                    "SELECT conname FROM pg_constraint "
                    f"WHERE conrelid = '{SCHEMA}.investigations'::regclass AND contype = 'f'"
                )
            )
        )

    assert foreign_keys == []
