package com.reconai.common.config;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.slf4j.MDC;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.util.UUID;
import java.util.regex.Pattern;

/**
 * Gives every request a correlation identifier, as required by docs/api-contract.md
 * section 25.
 *
 * <p>An {@code X-Correlation-ID} supplied by the caller is reused so a single workflow
 * can be traced across systems; otherwise one is generated. The value is echoed in the
 * response header, placed in the logging context, and stored as a request attribute so
 * that error responses can include it.
 *
 * <p>This is deliberately not distributed tracing. It is the minimum needed to tie an
 * error response to a log line.
 */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE)
public class CorrelationIdFilter extends OncePerRequestFilter {

    public static final String HEADER_NAME = "X-Correlation-ID";
    public static final String REQUEST_ATTRIBUTE = "reconai.correlationId";
    public static final String MDC_KEY = "correlationId";

    /**
     * A caller-supplied value is written to a response header and to application logs,
     * so it is accepted only if it is a short, plain token. Anything else is replaced
     * rather than rejected: a malformed header should not fail an otherwise valid
     * financial request.
     */
    private static final Pattern ACCEPTABLE = Pattern.compile("^[A-Za-z0-9._-]{1,64}$");

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response,
                                    FilterChain filterChain) throws ServletException, IOException {
        String correlationId = resolve(request.getHeader(HEADER_NAME));

        request.setAttribute(REQUEST_ATTRIBUTE, correlationId);
        response.setHeader(HEADER_NAME, correlationId);
        MDC.put(MDC_KEY, correlationId);
        try {
            filterChain.doFilter(request, response);
        } finally {
            MDC.remove(MDC_KEY);
        }
    }

    private String resolve(String supplied) {
        if (supplied != null && ACCEPTABLE.matcher(supplied).matches()) {
            return supplied;
        }
        return generate();
    }

    private String generate() {
        return "CORR-" + UUID.randomUUID().toString().replace("-", "").substring(0, 8).toUpperCase();
    }

    /** Returns the correlation ID attached to the current request, never {@code null}. */
    public static String currentCorrelationId(HttpServletRequest request) {
        Object value = request.getAttribute(REQUEST_ATTRIBUTE);
        return value instanceof String correlationId ? correlationId : "UNKNOWN";
    }
}
