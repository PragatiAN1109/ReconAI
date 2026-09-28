# Financial Core — Java 21 / Spring Boot.
#
# Multi-stage: the build stage carries Maven and the full JDK, the runtime stage
# carries neither. The published image holds a JRE and one jar.

# ---------------------------------------------------------------- build ----
FROM maven:3.9-eclipse-temurin-21 AS build

WORKDIR /build

# Dependencies resolve from the POM alone, so this layer is reused on every
# build where only source changed — which is nearly all of them. There is no
# Maven wrapper in the repository, hence the Maven image rather than ./mvnw.
COPY pom.xml ./
RUN mvn -B -q dependency:go-offline

COPY src ./src
# Tests run in CI and in the existing suites, not in the image build: a build
# that needs Docker, Testcontainers and a network to produce a jar is a build
# that fails for reasons unrelated to the jar.
RUN mvn -B -q clean package -DskipTests

# -------------------------------------------------------------- runtime ----
FROM eclipse-temurin:21-jre

# curl is here for one reason: the container healthcheck. The application
# exposes no Actuator endpoint, and adding Actuator purely to be health-checked
# would be a production dependency bought for an infrastructure concern.
RUN apt-get update \
    && apt-get install --no-install-recommends -y curl \
    && rm -rf /var/lib/apt/lists/*

# Least privilege: nothing at runtime writes to the image.
RUN useradd --create-home --uid 1001 --shell /usr/sbin/nologin reconai
WORKDIR /srv

COPY --from=build --chown=reconai:reconai /build/target/*.jar /srv/financial-core.jar

USER reconai

# The application's own default. RECONAI_PORT overrides it, but the published
# port and this value must then move together.
EXPOSE 8080

# Container memory is the real limit, so let the JVM size its heap from the
# cgroup rather than from the host's physical RAM.
ENV JAVA_OPTS="-XX:MaxRAMPercentage=75"

ENTRYPOINT ["sh", "-c", "exec java $JAVA_OPTS -jar /srv/financial-core.jar"]
