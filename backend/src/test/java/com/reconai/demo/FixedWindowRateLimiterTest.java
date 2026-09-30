package com.reconai.demo;

import org.junit.jupiter.api.Test;

import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneOffset;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * The window arithmetic, with time under the test's control.
 *
 * <p>A mutable clock rather than sleeping: the boundary between one window and the next
 * is the only interesting part of a fixed-window counter, and asserting it by waiting
 * would be both slow and flaky.
 */
class FixedWindowRateLimiterTest {

    /** A clock the test advances by hand. */
    private static final class MutableClock extends Clock {
        private Instant now = Instant.parse("2026-01-01T00:00:00Z");

        @Override
        public Instant instant() {
            return now;
        }

        @Override
        public ZoneOffset getZone() {
            return ZoneOffset.UTC;
        }

        @Override
        public Clock withZone(java.time.ZoneId zone) {
            return this;
        }

        void advance(Duration amount) {
            now = now.plus(amount);
        }
    }

    @Test
    void requestsWithinTheAllowanceAreAllowed() {
        MutableClock clock = new MutableClock();
        FixedWindowRateLimiter limiter =
                new FixedWindowRateLimiter(2, 10, Duration.ofMinutes(1), clock);

        assertThat(limiter.tryAcquire("a")).isEqualTo(FixedWindowRateLimiter.Decision.ALLOWED);
        assertThat(limiter.tryAcquire("a")).isEqualTo(FixedWindowRateLimiter.Decision.ALLOWED);
    }

    @Test
    void onePastTheAllowanceIsRefused() {
        FixedWindowRateLimiter limiter =
                new FixedWindowRateLimiter(2, 10, Duration.ofMinutes(1), new MutableClock());
        limiter.tryAcquire("a");
        limiter.tryAcquire("a");

        assertThat(limiter.tryAcquire("a"))
                .isEqualTo(FixedWindowRateLimiter.Decision.PER_CLIENT_LIMIT_REACHED);
    }

    @Test
    void oneKeyExhaustingItsAllowanceDoesNotAffectAnother() {
        FixedWindowRateLimiter limiter =
                new FixedWindowRateLimiter(1, 10, Duration.ofMinutes(1), new MutableClock());
        limiter.tryAcquire("a");

        assertThat(limiter.tryAcquire("a"))
                .isEqualTo(FixedWindowRateLimiter.Decision.PER_CLIENT_LIMIT_REACHED);
        assertThat(limiter.tryAcquire("b")).isEqualTo(FixedWindowRateLimiter.Decision.ALLOWED);
    }

    @Test
    void theGlobalLimitIsReportedDistinctlyAndStopsEveryKey() {
        FixedWindowRateLimiter limiter =
                new FixedWindowRateLimiter(10, 2, Duration.ofMinutes(1), new MutableClock());
        limiter.tryAcquire("a");
        limiter.tryAcquire("b");

        assertThat(limiter.tryAcquire("c"))
                .isEqualTo(FixedWindowRateLimiter.Decision.GLOBAL_LIMIT_REACHED);
    }

    @Test
    void theGlobalLimitIsCheckedBeforeThePerKeyOne() {
        // So a caller told "you have had enough" versus "the demo has had enough"
        // gets the accurate message rather than the first one that happens to match.
        FixedWindowRateLimiter limiter =
                new FixedWindowRateLimiter(1, 1, Duration.ofMinutes(1), new MutableClock());
        limiter.tryAcquire("a");

        assertThat(limiter.tryAcquire("a"))
                .isEqualTo(FixedWindowRateLimiter.Decision.GLOBAL_LIMIT_REACHED);
    }

    @Test
    void theAllowanceReturnsOnceTheWindowElapses() {
        MutableClock clock = new MutableClock();
        FixedWindowRateLimiter limiter =
                new FixedWindowRateLimiter(1, 10, Duration.ofMinutes(1), clock);
        limiter.tryAcquire("a");

        clock.advance(Duration.ofMinutes(1));

        assertThat(limiter.tryAcquire("a")).isEqualTo(FixedWindowRateLimiter.Decision.ALLOWED);
    }

    @Test
    void theAllowanceDoesNotReturnEarly() {
        MutableClock clock = new MutableClock();
        FixedWindowRateLimiter limiter =
                new FixedWindowRateLimiter(1, 10, Duration.ofMinutes(1), clock);
        limiter.tryAcquire("a");

        clock.advance(Duration.ofSeconds(59));

        assertThat(limiter.tryAcquire("a"))
                .isEqualTo(FixedWindowRateLimiter.Decision.PER_CLIENT_LIMIT_REACHED);
    }

    @Test
    void refusedRequestsDoNotConsumeTheNextWindowsAllowance() {
        MutableClock clock = new MutableClock();
        FixedWindowRateLimiter limiter =
                new FixedWindowRateLimiter(1, 10, Duration.ofMinutes(1), clock);
        limiter.tryAcquire("a");
        for (int attempt = 0; attempt < 20; attempt++) {
            limiter.tryAcquire("a");
        }

        clock.advance(Duration.ofMinutes(1));

        assertThat(limiter.tryAcquire("a")).isEqualTo(FixedWindowRateLimiter.Decision.ALLOWED);
    }

    @Test
    void retryAfterNeverRoundsDownBelowTheActualWait() {
        MutableClock clock = new MutableClock();
        FixedWindowRateLimiter limiter =
                new FixedWindowRateLimiter(1, 10, Duration.ofMinutes(1), clock);

        clock.advance(Duration.ofMillis(10_500));

        // 49.5s remain. Telling a caller 49 would have them retry too early.
        assertThat(limiter.secondsUntilWindowResets()).isEqualTo(50);
    }

    @Test
    void retryAfterIsNeverNegative() {
        MutableClock clock = new MutableClock();
        FixedWindowRateLimiter limiter =
                new FixedWindowRateLimiter(1, 10, Duration.ofMinutes(1), clock);

        clock.advance(Duration.ofMinutes(5));

        assertThat(limiter.secondsUntilWindowResets()).isZero();
    }
}
