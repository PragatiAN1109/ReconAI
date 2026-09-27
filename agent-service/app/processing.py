"""What consuming one record concluded, and what that means for the offset."""

from enum import Enum, auto


class ProcessingOutcome(Enum):
    """The three ways handling a record can end.

    Kafka offsets are positional: committing acknowledges everything up to and
    including the current record. So the only way to avoid acknowledging a
    record is to stop committing entirely — which is why a persistence failure
    has to halt the loop rather than move on.
    """

    #: Recorded durably. Safe to commit.
    PROCESSED = auto()

    #: The message could not be decoded or validated. It is not going to become
    #: valid on redelivery, so it is committed and skipped. Without a
    #: dead-letter topic the alternative is re-reading it forever and stalling
    #: every record behind it on the partition.
    INVALID = auto()

    #: The event was valid but could not be recorded — the database was
    #: unreachable, for instance. Nothing is committed and consumption stops.
    #: A valid financial exception must not be silently dropped because storage
    #: was briefly unavailable; leaving the offset uncommitted means it is
    #: redelivered once the service is healthy again.
    RETRY_LATER = auto()
