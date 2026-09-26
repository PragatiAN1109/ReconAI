package com.reconai.support;

import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.test.context.ActiveProfiles;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.utility.DockerImageName;

/**
 * Base class for integration tests that require a real database.
 *
 * <p>Tests run against PostgreSQL rather than an in-memory substitute. The schema
 * depends on PostgreSQL-specific behaviour — most importantly the partial unique index
 * that enforces reconciliation idempotency — which H2 would not reproduce, so a passing
 * H2 test would be misleading.
 *
 * <p>The container is a singleton: it is started once in a static initialiser and
 * deliberately never stopped. Testcontainers' own JUnit lifecycle would stop a static
 * container when its first test class finishes and start a fresh one, on a new random
 * port, for the next class. Spring caches the application context across classes that
 * share this configuration, so the cached DataSource would still point at the old port
 * and every later test would fail to connect. Leaving the container running for the
 * whole JVM keeps one container and one context; the Ryuk sidecar removes it on exit.
 */
@SpringBootTest
@ActiveProfiles("test")
public abstract class PostgresIntegrationTest {

    @ServiceConnection
    static final PostgreSQLContainer<?> POSTGRES =
            new PostgreSQLContainer<>(DockerImageName.parse("postgres:16-alpine"));

    static {
        POSTGRES.start();
    }
}
