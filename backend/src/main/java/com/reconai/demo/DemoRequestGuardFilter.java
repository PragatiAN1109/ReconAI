package com.reconai.demo;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.reconai.common.config.CorrelationIdFilter;
import com.reconai.common.error.ApiError;
import com.reconai.common.error.ErrorCode;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;

/**
 * Bounds the public demo endpoint: body size, then request rate.
 *
 * <h2>Why a filter rather than validation</h2>
 *
 * Bean validation runs after Spring has read and parsed the body, which is too late to
 * decline an oversized one. {@code Content-Length} is available before the body is
 * touched, so an over-limit request is refused without reading it.
 *
 * <p>Rate limiting lives here for the same reason — there is no value in constructing a
 * database transaction for a request that is about to be refused — and because it keeps
 * every "is this request allowed at all" concern in one place, leaving the controller and
 * service to deal only with the domain.
 *
 * <h2>Scope</h2>
 *
 * Applies to {@code POST} on exactly one path. Every other route, including every read
 * endpoint and the non-public generic write endpoints, is untouched: the filter returns
 * immediately. Nothing else in the financial core is throttled.
 *
 * <h2>Client identity</h2>
 *
 * The key is the leftmost {@code X-Forwarded-For} entry when present, since in the
 * deployed stack every request arrives via CloudFront and then an ALB, making
 * {@code getRemoteAddr()} the load balancer. A caller can set that header, so this
 * identifies a caller only as well as a demo needs to — see
 * {@link FixedWindowRateLimiter} on why that is acceptable and what it is not.
 */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE + 10) // after CorrelationIdFilter, so errors carry its id
public class DemoRequestGuardFilter extends OncePerRequestFilter {

    private static final Logger log = LoggerFactory.getLogger(DemoRequestGuardFilter.class);

    private static final String FORWARDED_FOR = "X-Forwarded-For";
    private static final String UNKNOWN_CLIENT = "unknown";

    private final DemoProperties properties;
    private final ObjectMapper objectMapper;
    private final Clock clock;
    private final FixedWindowRateLimiter rateLimiter;

    public DemoRequestGuardFilter(DemoProperties properties, ObjectMapper objectMapper, Clock clock) {
        this.properties = properties;
        this.objectMapper = objectMapper;
        this.clock = clock;
        this.rateLimiter = new FixedWindowRateLimiter(
                properties.perClientLimit(),
                properties.globalLimit(),
                Duration.ofSeconds(properties.windowSeconds()),
                clock);
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        return !(HttpMethod.POST.matches(request.getMethod())
                && DemoReconciliationController.PATH.equals(request.getRequestURI()));
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request,
                                    HttpServletResponse response,
                                    FilterChain chain) throws ServletException, IOException {

        long declaredLength = request.getContentLengthLong();
        if (declaredLength > properties.maxRequestBytes()) {
            log.warn("Refusing oversized demo request [bytes={} limit={}]",
                    declaredLength, properties.maxRequestBytes());
            write(response, request, ErrorCode.PAYLOAD_TOO_LARGE,
                    "The request body exceeds the " + properties.maxRequestBytes()
                            + " byte limit for this endpoint.");
            return;
        }

        FixedWindowRateLimiter.Decision decision = rateLimiter.tryAcquire(clientKey(request));
        if (decision != FixedWindowRateLimiter.Decision.ALLOWED) {
            long retryAfter = rateLimiter.secondsUntilWindowResets();
            log.warn("Throttling demo request [decision={} retryAfterSeconds={}]",
                    decision, retryAfter);
            response.setHeader(HttpHeaders.RETRY_AFTER, Long.toString(retryAfter));
            write(response, request, ErrorCode.RATE_LIMITED, message(decision, retryAfter));
            return;
        }

        chain.doFilter(request, response);
    }

    private String message(FixedWindowRateLimiter.Decision decision, long retryAfter) {
        String shared = " This is a shared public demo; please retry in " + retryAfter + "s.";
        return decision == FixedWindowRateLimiter.Decision.GLOBAL_LIMIT_REACHED
                ? "The demo reconciliation endpoint has reached its overall request limit."
                        + shared
                : "Too many demo reconciliation runs from this client." + shared;
    }

    /**
     * Writes the same error envelope the rest of the API uses.
     *
     * <p>Rendered here rather than by throwing, because a filter's exception does not
     * reach {@code @ExceptionHandler} methods. The shape still has to match
     * {@link ApiError} exactly, or a client parsing one error cannot parse this one.
     */
    private void write(HttpServletResponse response, HttpServletRequest request,
                       ErrorCode errorCode, String message) throws IOException {

        ApiError body = new ApiError(
                Instant.now(clock),
                errorCode.httpStatus().value(),
                errorCode,
                message,
                request.getRequestURI(),
                CorrelationIdFilter.currentCorrelationId(request));

        response.setStatus(errorCode.httpStatus().value());
        response.setContentType(MediaType.APPLICATION_JSON_VALUE);
        objectMapper.writeValue(response.getOutputStream(), body);
    }

    private String clientKey(HttpServletRequest request) {
        String forwardedFor = request.getHeader(FORWARDED_FOR);
        if (forwardedFor != null && !forwardedFor.isBlank()) {
            String first = forwardedFor.split(",", 2)[0].trim();
            if (!first.isEmpty()) {
                return first;
            }
        }
        String remote = request.getRemoteAddr();
        return remote == null || remote.isBlank() ? UNKNOWN_CLIENT : remote;
    }
}
