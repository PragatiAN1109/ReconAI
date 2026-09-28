# Investigation Service — Python 3.12 / FastAPI.
#
# NOTE: the build context is the REPOSITORY ROOT, not agent-service/.
#
# That is not arbitrary. PolicySearch reads a corpus that lives at policies/ in
# the repository root, and a service whose evidence source sits outside its own
# build context cannot be containerised from that context. Building from the
# root lets the image carry the corpus it actually needs.
#
#   docker build -f agent-service/Dockerfile .
#
# Root-context builds are filtered by the root .dockerignore.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv

# Dependencies first, so this layer is reused whenever only source changed. A
# placeholder package is enough for the build backend to resolve and install the
# dependency set from pyproject.toml; the real package replaces it below.
COPY agent-service/pyproject.toml agent-service/README.md ./
RUN mkdir -p app \
    && printf '' > app/__init__.py \
    && pip install ".[llm]" \
    && rm -rf app

# Source changes on nearly every build, so it comes after the dependency layer.
COPY agent-service/app ./app
RUN pip install --no-deps .

# Schema migrations. Copied because the container applies them at startup; an
# image that cannot migrate its own schema is an image that needs a second,
# undocumented deployment step.
COPY agent-service/alembic.ini ./alembic.ini
COPY agent-service/migrations ./migrations

# The policy corpus that PolicySearch reads.
#
# The default path is computed relative to app/config.py, and the directory
# depth differs between a checkout and this image: agent-service/app/config.py
# has three parents up to the repository root, /srv/app/config.py has two. The
# default would therefore resolve to /policies and silently load zero documents
# — an investigation with no policy evidence rather than a crash.
#
# Pinned explicitly through the application's own setting instead of relying on
# a path that happens to work. No application code is changed.
COPY policies ./policies
ENV RECONAI_AGENT_POLICY_CORPUS_PATH=/srv/policies

# Applies migrations, then starts the service. Kept as a file rather than an
# inline CMD chain so a failed migration aborts startup loudly instead of being
# swallowed by a shell operator.
COPY agent-service/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Least privilege: nothing at runtime needs to write to the image.
RUN useradd --create-home --uid 1001 --shell /usr/sbin/nologin agent \
    && chown -R agent:agent /srv
USER agent

# Matches the default RECONAI_AGENT_PORT. Override both together if you change it.
EXPOSE 8000

ENTRYPOINT ["docker-entrypoint.sh"]
# Started through the package entry point rather than a hardcoded uvicorn command
# so that RECONAI_AGENT_HOST, RECONAI_AGENT_PORT and RECONAI_AGENT_LOG_LEVEL are
# honoured inside the container exactly as they are outside it.
CMD ["python", "-m", "app.main"]
