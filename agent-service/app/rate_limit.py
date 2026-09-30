"""A fixed-window request counter for the paid investigation endpoint.

WHY THIS EXISTS
---------------
Running an investigation is the one operation in ReconAI that costs money: it
calls a language model provider. The deployed console is public and
unauthenticated, so without a ceiling the run endpoint is an open invitation to
spend someone else's Anthropic credits in a loop.

WHAT IT IS NOT
--------------
Not a security boundary and not a quota system. It counts in this process, so it
bounds nothing if the service is scaled out, and the per-client key comes from a
request header a caller can set. It exists to make runaway cost impossible by
accident and inconvenient on purpose, which is the right level of effort for a
synthetic-data portfolio demo.

The global limit is the one that matters. Per-client is a courtesy that stops one
visitor exhausting the window for everyone; the global count is what caps spend.
"""

import logging
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable

logger = logging.getLogger(__name__)


class Decision(Enum):
    """Whether a run may proceed, and if not, which limit stopped it."""

    ALLOWED = auto()
    #: This caller has used its allowance for the window.
    PER_CLIENT_LIMIT_REACHED = auto()
    #: The endpoint as a whole has used its allowance. This is the cost ceiling.
    GLOBAL_LIMIT_REACHED = auto()


@dataclass
class FixedWindowRateLimiter:
    """Counts requests per key and overall within a fixed window.

    A fixed window rather than a sliding one or a token bucket: the boundary
    case, where a caller spends two windows' worth of runs either side of one
    edge, does not matter for a limit whose job is to stop loops. Simplicity is
    worth more here than smoothness.

    Per-key counts are dropped when the window rolls, so there is no background
    task and nothing grows beyond the callers seen in a single window.
    """

    per_client_limit: int
    global_limit: int
    window_seconds: float
    #: Injectable so tests can advance time without sleeping.
    monotonic: Callable[[], float] = time.monotonic

    _window_start: float = field(default=0.0, init=False)
    _per_client: dict[str, int] = field(default_factory=dict, init=False)
    _global_count: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self._window_start = self.monotonic()

    def check(self, client_key: str | None) -> Decision:
        """Record one request and say whether it may run.

        ``client_key`` of ``None`` means **global only**: count this attempt
        against the shared ceiling and skip the per-client dimension entirely.
        That is the correct reading for an automatically triggered run, which
        has no requesting client — a Kafka event is not a visitor. Charging it
        to a synthetic key would either exhaust a per-client allowance that
        describes nobody, or hand automation its own separate budget, and a
        second budget is not a ceiling.

        Both callers share one limiter instance, so the global count is the real
        spend ceiling across manual and automatic runs together.

        Rolling the window and testing the counters happen together. Splitting
        them would let a roll race a test and either admit more than the limit
        or discard a count that had just been made.
        """
        self._roll_window_if_elapsed()

        if self._global_count >= self.global_limit:
            logger.warning(
                "Refusing investigation run: global window limit reached "
                "[limit=%d window_seconds=%s automatic=%s]",
                self.global_limit,
                self.window_seconds,
                client_key is None,
            )
            return Decision.GLOBAL_LIMIT_REACHED

        if client_key is not None:
            if self._per_client.get(client_key, 0) >= self.per_client_limit:
                logger.warning(
                    "Refusing investigation run: per-client window limit reached [limit=%d]",
                    self.per_client_limit,
                )
                return Decision.PER_CLIENT_LIMIT_REACHED
            self._per_client[client_key] = self._per_client.get(client_key, 0) + 1

        self._global_count += 1
        return Decision.ALLOWED

    def seconds_until_reset(self) -> int:
        """Whole seconds until the window rolls, for a ``Retry-After`` header."""
        remaining = self.window_seconds - (self.monotonic() - self._window_start)
        return 0 if remaining <= 0 else int(remaining) + 1

    def _roll_window_if_elapsed(self) -> None:
        now = self.monotonic()
        if now - self._window_start < self.window_seconds:
            return
        self._window_start = now
        self._per_client.clear()
        self._global_count = 0
