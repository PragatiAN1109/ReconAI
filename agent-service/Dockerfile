FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv/app

# Dependencies first, so this layer is reused whenever only source has changed.
# A placeholder package is enough for the build backend to resolve and install
# the dependency set from pyproject.toml; the real package replaces it below.
COPY pyproject.toml README.md ./
RUN mkdir -p app \
    && printf '' > app/__init__.py \
    && pip install . \
    && rm -rf app

# Source changes on nearly every build, so it comes after the dependency layer.
COPY app ./app
RUN pip install --no-deps .

# Least privilege: nothing at runtime needs to write to the image.
RUN useradd --create-home --uid 1001 --shell /usr/sbin/nologin agent \
    && chown -R agent:agent /srv/app
USER agent

# Matches the default RECONAI_AGENT_PORT. Override both together if you change it.
EXPOSE 8000

# Started through the package entry point rather than a hardcoded uvicorn command
# so that RECONAI_AGENT_HOST, RECONAI_AGENT_PORT and RECONAI_AGENT_LOG_LEVEL are
# honoured inside the container exactly as they are outside it.
CMD ["python", "-m", "app.main"]
