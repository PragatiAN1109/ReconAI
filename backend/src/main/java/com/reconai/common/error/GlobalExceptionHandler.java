package com.reconai.common.error;

import com.fasterxml.jackson.databind.exc.InvalidFormatException;
import com.reconai.common.config.CorrelationIdFilter;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.ConstraintViolation;
import jakarta.validation.ConstraintViolationException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatusCode;
import org.springframework.http.ResponseEntity;
import org.springframework.lang.Nullable;
import org.springframework.validation.FieldError;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.context.request.ServletWebRequest;
import org.springframework.web.context.request.WebRequest;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;
import org.springframework.web.servlet.mvc.method.annotation.ResponseEntityExceptionHandler;

import java.time.Clock;
import java.time.Instant;
import java.util.Arrays;
import java.util.stream.Collectors;
import java.util.stream.Stream;

/**
 * Turns every failure into the single error shape defined by docs/api-contract.md
 * section 5.
 *
 * <p>The guiding rule is that nothing internal escapes: no stack traces, no exception
 * class names, no SQL, no constraint names, no Jackson parse detail. Unexpected
 * failures are logged in full on the server with their correlation ID and reported to
 * the caller as a single generic sentence.
 *
 * <p>It extends {@link ResponseEntityExceptionHandler} so that Spring's own MVC
 * exceptions — unreadable bodies, unsupported methods and media types — are rendered in
 * the contract shape too, rather than falling through to a default error body.
 */
@RestControllerAdvice
public class GlobalExceptionHandler extends ResponseEntityExceptionHandler {

    private static final Logger log = LoggerFactory.getLogger(GlobalExceptionHandler.class);

    private static final String GENERIC_INTERNAL_MESSAGE =
            "An unexpected internal error occurred.";
    private static final String GENERIC_CONFLICT_MESSAGE =
            "The request conflicts with the current state of the data.";
    private static final String GENERIC_MALFORMED_BODY_MESSAGE =
            "Request body is missing or malformed.";

    private final Clock clock;

    public GlobalExceptionHandler(Clock clock) {
        this.clock = clock;
    }

    // -----------------------------------------------------------------
    // Application exceptions
    // -----------------------------------------------------------------

    @ExceptionHandler(ApiException.class)
    public ResponseEntity<ApiError> handleApiException(ApiException ex, HttpServletRequest request) {
        return respond(ex.errorCode(), ex.getMessage(), request);
    }

    /** Constraint violations raised outside request-body binding, e.g. on query parameters. */
    @ExceptionHandler(ConstraintViolationException.class)
    public ResponseEntity<ApiError> handleConstraintViolation(ConstraintViolationException ex,
                                                              HttpServletRequest request) {
        String message = ex.getConstraintViolations().stream()
                .map(ConstraintViolation::getMessage)
                .distinct()
                .sorted()
                .collect(Collectors.joining("; "));
        return respond(ErrorCode.VALIDATION_ERROR, message.isBlank() ? "Request is invalid." : message, request);
    }

    /**
     * A path variable or query parameter could not be converted, most often an unknown
     * enum value in a filter. The permitted values are named so a caller can correct
     * the request; the rejected value is not echoed back.
     */
    @ExceptionHandler(MethodArgumentTypeMismatchException.class)
    public ResponseEntity<ApiError> handleTypeMismatch(MethodArgumentTypeMismatchException ex,
                                                       HttpServletRequest request) {
        Class<?> requiredType = ex.getRequiredType();
        String message = requiredType != null && requiredType.isEnum()
                ? ex.getName() + " must be one of [" + Arrays.stream(requiredType.getEnumConstants())
                        .map(Object::toString).collect(Collectors.joining(", ")) + "]"
                : ex.getName() + " is not a valid value";
        return respond(ErrorCode.VALIDATION_ERROR, message, request);
    }

    /**
     * A database constraint rejected the write. The constraint name and SQL are
     * deliberately not surfaced; they are only logged.
     */
    @ExceptionHandler(DataIntegrityViolationException.class)
    public ResponseEntity<ApiError> handleDataIntegrityViolation(DataIntegrityViolationException ex,
                                                                 HttpServletRequest request) {
        log.warn("Database constraint violation on {} [correlationId={}]",
                request.getRequestURI(), correlationId(request), ex);
        return respond(ErrorCode.CONFLICT, GENERIC_CONFLICT_MESSAGE, request);
    }

    /** Last resort. The caller learns nothing beyond the correlation ID. */
    @ExceptionHandler(Exception.class)
    public ResponseEntity<ApiError> handleUnexpected(Exception ex, HttpServletRequest request) {
        log.error("Unhandled exception on {} [correlationId={}]",
                request.getRequestURI(), correlationId(request), ex);
        return respond(ErrorCode.INTERNAL_ERROR, GENERIC_INTERNAL_MESSAGE, request);
    }

    // -----------------------------------------------------------------
    // Spring MVC exceptions
    // -----------------------------------------------------------------

    @Override
    protected ResponseEntity<Object> handleMethodArgumentNotValid(MethodArgumentNotValidException ex,
                                                                  HttpHeaders headers,
                                                                  HttpStatusCode status,
                                                                  WebRequest request) {
        String message = Stream.concat(
                        ex.getBindingResult().getFieldErrors().stream().map(this::describe),
                        ex.getBindingResult().getGlobalErrors().stream()
                                .map(error -> error.getDefaultMessage() == null
                                        ? "Request is invalid." : error.getDefaultMessage()))
                .distinct()
                .sorted()
                .collect(Collectors.joining("; "));

        return asResponseEntity(status, ErrorCode.VALIDATION_ERROR,
                message.isBlank() ? "Request is invalid." : message, request);
    }

    /**
     * The body could not be parsed. An unknown enum value is the common case and is
     * worth naming precisely, because otherwise a caller cannot tell which field was
     * wrong. Everything else gets a generic message so that no parser detail leaks.
     */
    @Override
    protected ResponseEntity<Object> handleHttpMessageNotReadable(
            org.springframework.http.converter.HttpMessageNotReadableException ex,
            HttpHeaders headers, HttpStatusCode status, WebRequest request) {

        return asResponseEntity(status, ErrorCode.VALIDATION_ERROR, describeUnreadableBody(ex), request);
    }

    /**
     * Renders every remaining Spring MVC exception in the contract shape. The body
     * Spring prepared, which may be a ProblemDetail, is discarded.
     */
    @Override
    protected ResponseEntity<Object> handleExceptionInternal(Exception ex, @Nullable Object body,
                                                             HttpHeaders headers,
                                                             HttpStatusCode statusCode,
                                                             WebRequest request) {
        ErrorCode errorCode = errorCodeFor(statusCode);
        if (errorCode == ErrorCode.INTERNAL_ERROR) {
            log.error("Unhandled MVC exception [correlationId={}]", correlationId(request), ex);
        }
        return asResponseEntity(statusCode, errorCode, messageFor(errorCode), request);
    }

    // -----------------------------------------------------------------
    // Construction
    // -----------------------------------------------------------------

    private String describe(FieldError error) {
        return error.getDefaultMessage() == null
                ? error.getField() + " is invalid"
                : error.getDefaultMessage();
    }

    private String describeUnreadableBody(Throwable ex) {
        Throwable cause = ex.getCause();
        if (cause instanceof InvalidFormatException invalidFormat) {
            Class<?> targetType = invalidFormat.getTargetType();
            if (targetType != null && targetType.isEnum()) {
                String field = invalidFormat.getPath().isEmpty()
                        ? "A field"
                        : invalidFormat.getPath().get(invalidFormat.getPath().size() - 1).getFieldName();
                String permitted = Arrays.stream(targetType.getEnumConstants())
                        .map(Object::toString)
                        .collect(Collectors.joining(", "));
                return field + " must be one of [" + permitted + "]";
            }
        }
        return GENERIC_MALFORMED_BODY_MESSAGE;
    }

    /**
     * Maps an HTTP status onto a contract category.
     *
     * <p>This chooses the category only. The response keeps the status Spring decided
     * on, so a 405 stays a 405 even though the closed category list has nothing better
     * than VALIDATION_ERROR to call it — accurate in that the request as sent cannot be
     * accepted.
     */
    private ErrorCode errorCodeFor(HttpStatusCode statusCode) {
        return switch (statusCode.value()) {
            case 404 -> ErrorCode.NOT_FOUND;
            case 409 -> ErrorCode.CONFLICT;
            case 503 -> ErrorCode.DEPENDENCY_UNAVAILABLE;
            default -> statusCode.is5xxServerError() ? ErrorCode.INTERNAL_ERROR : ErrorCode.VALIDATION_ERROR;
        };
    }

    private String messageFor(ErrorCode errorCode) {
        return switch (errorCode) {
            case NOT_FOUND -> "The requested resource was not found.";
            case CONFLICT -> GENERIC_CONFLICT_MESSAGE;
            case INTERNAL_ERROR -> GENERIC_INTERNAL_MESSAGE;
            case DEPENDENCY_UNAVAILABLE -> "A required dependency is unavailable.";
            default -> "The request could not be processed as sent.";
        };
    }

    /** Renders an application exception at the status its category defines. */
    private ResponseEntity<ApiError> respond(ErrorCode errorCode, String message,
                                             HttpServletRequest request) {
        return ResponseEntity.status(errorCode.httpStatus())
                .body(build(errorCode.httpStatus(), errorCode, message,
                        request.getRequestURI(), correlationId(request)));
    }

    /**
     * Renders a Spring MVC exception at the status Spring itself decided on.
     *
     * <p>The status is a parameter rather than being derived from the category. The
     * contract's categories are closed and do not cover every HTTP status, so deriving
     * the status from the category collapsed distinct outcomes onto one code: a request
     * to a resource that exists but does not support the method is 405, and reporting it
     * as 400 told the caller their request was malformed when it was not.
     *
     * <p>The category still describes the failure; the status still describes what HTTP
     * says happened. The two are related but not interchangeable.
     */
    private ResponseEntity<Object> asResponseEntity(HttpStatusCode statusCode, ErrorCode errorCode,
                                                    String message, WebRequest request) {
        return ResponseEntity.status(statusCode)
                .body(build(statusCode, errorCode, message, path(request), correlationId(request)));
    }

    private ApiError build(HttpStatusCode statusCode, ErrorCode errorCode, String message,
                           String path, String correlationId) {
        return new ApiError(Instant.now(clock), statusCode.value(), errorCode,
                message, path, correlationId);
    }

    private String path(WebRequest request) {
        return request instanceof ServletWebRequest servletRequest
                ? servletRequest.getRequest().getRequestURI()
                : "";
    }

    private String correlationId(WebRequest request) {
        return request instanceof ServletWebRequest servletRequest
                ? correlationId(servletRequest.getRequest())
                : "UNKNOWN";
    }

    private String correlationId(HttpServletRequest request) {
        return CorrelationIdFilter.currentCorrelationId(request);
    }
}
