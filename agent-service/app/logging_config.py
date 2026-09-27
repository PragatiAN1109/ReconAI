"""Application logging setup."""

import logging
import sys

LOG_FORMAT = "%(asctime)s %(levelname)-5s [%(name)s] %(message)s"
DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"


def configure_logging(log_level: str) -> None:
    """Send consistently formatted logs to stdout.

    Standard library logging is enough here. A container runtime collects stdout,
    and there is no log aggregator yet that would benefit from JSON output; that
    is a change worth making when something is actually parsing these lines.

    ``force`` replaces any handlers already installed, so calling this more than
    once — as the tests do when they build several application instances — does
    not duplicate every log line.
    """
    logging.basicConfig(
        level=log_level,
        format=LOG_FORMAT,
        datefmt=DATE_FORMAT,
        stream=sys.stdout,
        force=True,
    )
