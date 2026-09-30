"""The fixed-window limiter that caps paid investigation runs.

Time is injected rather than slept, so these assert the window's behaviour
exactly instead of approximately.
"""

from app.rate_limit import Decision, FixedWindowRateLimiter


class FakeClock:
    """A monotonic clock the test advances by hand."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def limiter(
    *, per_client: int = 2, global_limit: int = 5, window: float = 60.0
) -> tuple[FixedWindowRateLimiter, FakeClock]:
    clock = FakeClock()
    return (
        FixedWindowRateLimiter(
            per_client_limit=per_client,
            global_limit=global_limit,
            window_seconds=window,
            monotonic=clock,
        ),
        clock,
    )


class TestPerClientLimit:
    def test_requests_within_the_allowance_are_allowed(self) -> None:
        subject, _ = limiter(per_client=2)

        assert subject.check("1.2.3.4") is Decision.ALLOWED
        assert subject.check("1.2.3.4") is Decision.ALLOWED

    def test_one_past_the_allowance_is_refused(self) -> None:
        subject, _ = limiter(per_client=2)
        subject.check("1.2.3.4")
        subject.check("1.2.3.4")

        assert subject.check("1.2.3.4") is Decision.PER_CLIENT_LIMIT_REACHED

    def test_one_client_exhausting_its_allowance_does_not_block_another(self) -> None:
        subject, _ = limiter(per_client=1, global_limit=10)
        subject.check("1.2.3.4")

        assert subject.check("1.2.3.4") is Decision.PER_CLIENT_LIMIT_REACHED
        assert subject.check("5.6.7.8") is Decision.ALLOWED

    def test_a_refused_request_does_not_consume_allowance(self) -> None:
        # Otherwise a client hammering the endpoint would never recover within a
        # window even after the window rolled.
        subject, clock = limiter(per_client=1, window=60)
        subject.check("1.2.3.4")
        for _ in range(10):
            subject.check("1.2.3.4")

        clock.advance(61)
        assert subject.check("1.2.3.4") is Decision.ALLOWED


class TestGlobalLimit:
    def test_the_global_limit_stops_everyone_regardless_of_client(self) -> None:
        # This is the actual spend ceiling: distinct callers must not be able to
        # add up to more than the endpoint's total allowance.
        subject, _ = limiter(per_client=10, global_limit=3)

        assert subject.check("a") is Decision.ALLOWED
        assert subject.check("b") is Decision.ALLOWED
        assert subject.check("c") is Decision.ALLOWED
        assert subject.check("d") is Decision.GLOBAL_LIMIT_REACHED

    def test_the_global_limit_is_reported_distinctly_from_the_per_client_one(self) -> None:
        # The two produce different messages, so a visitor is not told to wait
        # when the real problem is that the demo is out of budget.
        subject, _ = limiter(per_client=1, global_limit=1)
        subject.check("a")

        assert subject.check("a") is Decision.GLOBAL_LIMIT_REACHED


class TestWindowRolling:
    def test_the_allowance_returns_once_the_window_elapses(self) -> None:
        subject, clock = limiter(per_client=1, window=60)
        subject.check("1.2.3.4")
        assert subject.check("1.2.3.4") is Decision.PER_CLIENT_LIMIT_REACHED

        clock.advance(60)
        assert subject.check("1.2.3.4") is Decision.ALLOWED

    def test_the_allowance_does_not_return_early(self) -> None:
        subject, clock = limiter(per_client=1, window=60)
        subject.check("1.2.3.4")

        clock.advance(59.9)
        assert subject.check("1.2.3.4") is Decision.PER_CLIENT_LIMIT_REACHED

    def test_the_global_count_resets_with_the_window_too(self) -> None:
        subject, clock = limiter(per_client=10, global_limit=1, window=30)
        subject.check("a")
        assert subject.check("b") is Decision.GLOBAL_LIMIT_REACHED

        clock.advance(31)
        assert subject.check("b") is Decision.ALLOWED


class TestRetryAfter:
    def test_it_reports_whole_seconds_remaining_in_the_window(self) -> None:
        subject, clock = limiter(window=60)
        clock.advance(10)

        # 50s remain; a Retry-After must not round down to less than the wait.
        assert subject.seconds_until_reset() == 51

    def test_it_never_reports_a_negative_wait(self) -> None:
        subject, clock = limiter(window=60)
        clock.advance(120)

        assert subject.seconds_until_reset() == 0
