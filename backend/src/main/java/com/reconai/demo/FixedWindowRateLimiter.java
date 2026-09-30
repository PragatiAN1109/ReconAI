package com.reconai.demo;

import java.time.Clock;
import java.time.Duration;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicLong;

/**
 * A fixed-window counter, per key and overall.
 *
 * <h2>What this is for, and what it is not</h2>
 *
 * It exists so a publicly reachable demo endpoint cannot be driven in a loop. It is a
 * cost and nuisance control for a synthetic-data portfolio demo, <strong>not</strong> a
 * security boundary: it counts per process, so it bounds nothing if the service is scaled
 * out, and the key it is given comes from a request header a caller can set. Both
 * limitations are acceptable here and neither should be relied on for anything else.
 *
 * <h2>Why a fixed window</h2>
 *
 * A sliding window or token bucket would smooth the boundary, where a caller can spend
 * two windows' worth of requests across one window edge. For a demo whose purpose is to
 * stop runaway loops, that is irrelevant, and a fixed window is simple enough to be
 * obviously correct.
 *
 * <p>Per-key entries are discarded when the window rolls rather than on a timer, so there
 * is no background thread and no unbounded growth beyond the callers seen in one window.
 */
public class FixedWindowRateLimiter {

    /** The outcome of asking to proceed. */
    public enum Decision {
        ALLOWED,
        /** This caller has used its allowance. */
        PER_CLIENT_LIMIT_REACHED,
        /** The endpoint as a whole has used its allowance. */
        GLOBAL_LIMIT_REACHED
    }

    private final int perKeyLimit;
    private final int globalLimit;
    private final long windowMillis;
    private final Clock clock;

    private final Map<String, AtomicLong> perKeyCounts = new ConcurrentHashMap<>();
    private final AtomicLong globalCount = new AtomicLong();

    /** Start of the window the counts above belong to. Guarded by {@code this}. */
    private long windowStartMillis;

    public FixedWindowRateLimiter(int perKeyLimit, int globalLimit, Duration window, Clock clock) {
        this.perKeyLimit = perKeyLimit;
        this.globalLimit = globalLimit;
        this.windowMillis = window.toMillis();
        this.clock = clock;
        this.windowStartMillis = clock.millis();
    }

    /**
     * Records one request against {@code key} and says whether it may proceed.
     *
     * <p>Synchronised in full rather than relying on the atomics alone: rolling the
     * window and testing the counters have to be one step, or a roll racing a test can
     * admit more than the limit or discard a count that was just made.
     */
    public synchronized Decision tryAcquire(String key) {
        rollWindowIfElapsed();

        if (globalCount.get() >= globalLimit) {
            return Decision.GLOBAL_LIMIT_REACHED;
        }

        AtomicLong forKey = perKeyCounts.computeIfAbsent(key, ignored -> new AtomicLong());
        if (forKey.get() >= perKeyLimit) {
            return Decision.PER_CLIENT_LIMIT_REACHED;
        }

        forKey.incrementAndGet();
        globalCount.incrementAndGet();
        return Decision.ALLOWED;
    }

    /** Seconds until the current window ends, for a {@code Retry-After} header. */
    public synchronized long secondsUntilWindowResets() {
        long elapsed = clock.millis() - windowStartMillis;
        long remaining = windowMillis - elapsed;
        return remaining <= 0 ? 0 : (remaining + 999) / 1000;
    }

    private void rollWindowIfElapsed() {
        long now = clock.millis();
        if (now - windowStartMillis < windowMillis) {
            return;
        }
        windowStartMillis = now;
        globalCount.set(0);
        perKeyCounts.clear();
    }
}
