"""The append-only audit trail for investigations.

Every recorded event answers one question for someone reading months later:
what happened, who caused it, and when. There is no update and no delete — not
in this module, not in the service, not in the API. A trail that can be revised
is not evidence of anything.

Writes take a session rather than opening one, so an event is committed by the
same transaction as the state change it describes. An audit entry that survives
a rolled-back transition would be a record of something that never happened.
"""

import logging
from typing import Any

from sqlalchemy import Sequence, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SCHEMA, ActorType, AuditEvent, AuditEventType

logger = logging.getLogger(__name__)

AUDIT_ID_SEQUENCE = Sequence("audit_event_business_id_seq", schema=SCHEMA)
AUDIT_ID_PREFIX = "AUD-"


async def record(
    session: AsyncSession,
    *,
    investigation_id: str,
    event_type: AuditEventType,
    actor_type: ActorType,
    actor_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditEvent:
    """Append one event to the trail, inside the caller's transaction.

    ``metadata`` must stay a small, non-sensitive summary — a classification, a
    confidence, a failure category. Never a prompt, a provider payload, a
    credential, or anything resembling model reasoning: this table is read by
    humans reviewing decisions, and is not a debugging sink.

    ``actor_id`` is caller-supplied for human events. It records a claim, not a
    verified identity: this service has no authentication.
    """
    sequence_no = await session.scalar(AUDIT_ID_SEQUENCE.next_value().select())
    event_id = f"{AUDIT_ID_PREFIX}{sequence_no}"
    event = AuditEvent(
        event_id=event_id,
        sequence_no=sequence_no,
        investigation_id=investigation_id,
        event_type=event_type.value,
        actor_type=actor_type.value,
        actor_id=actor_id,
        metadata_json=metadata,
    )
    session.add(event)
    # Forces the insert now, so a constraint violation surfaces here rather
    # than at commit, where it would be harder to attribute.
    await session.flush()

    logger.info(
        "Audit event recorded [event_id=%s investigation_id=%s event_type=%s actor_type=%s]",
        event_id,
        investigation_id,
        event_type.value,
        actor_type.value,
    )
    return event


class AuditService:
    """Reads the trail. Deliberately has no write method.

    Appending is a function that takes a caller's session, so there is no
    object here offering a tempting ``delete`` or ``update`` next to a ``read``.
    """

    def __init__(self, database: Any) -> None:
        self._database = database

    async def list_for_investigation(self, investigation_id: str) -> list[AuditEvent]:
        """The timeline for one investigation, oldest first.

        Chronological rather than newest-first: this is read as a story of what
        happened, and stories are read forwards.

        Ordered by ``sequence_no``, which is a total order. Timestamps are not:
        two events written by the same transaction can be microseconds apart or
        identical, and their relative order still matters to a reader.
        """
        async with self._database.session() as session:
            result = await session.scalars(
                select(AuditEvent)
                .where(AuditEvent.investigation_id == investigation_id)
                .order_by(AuditEvent.sequence_no.asc())
            )
            return list(result)
