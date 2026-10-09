# syntax=docker/dockerfile:1.7
#
# Multi-stage build for the Architecture Agent MCP server.
#
#   docker build -t architecture-agent:0.1.0 .                      # production
#   docker build --target test -t architecture-agent:test .         # with dev deps
#
# Stage order matters: the last stage is the default target, and the default must
# be the production image. Building without --target must never yield a test image.
#
# Dependency determinism comes from uv.lock, not from pinning here: `uv sync
# --frozen` fails the build rather than resolving something new. The project source
# is installed non-editable so the image is self-contained.

ARG PYTHON_VERSION=3.12
ARG UV_VERSION=0.12.23

# --------------------------------------------------------------------------- base
FROM python:${PYTHON_VERSION}-slim-bookworm AS base

ARG UV_VERSION

# `tini` gives the server a real PID 1 so SIGTERM reaches Python and the stdio
# stream closes cleanly instead of being severed mid-message. Nothing else is
# added: no compiler, no shell utilities beyond the shell itself.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends tini; \
    rm -rf /var/lib/apt/lists/*; \
    groupadd --gid 10001 app; \
    useradd --uid 10001 --gid 10001 --create-home --home-dir /home/app --shell /usr/sbin/nologin app; \
    rm -rf /root/.cache

# PATH belongs in base: `test` extends `deps`, not `runtime`, so declaring it only
# in runtime would leave the test stage invoking the system interpreter.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    ARCH_AGENT_LOG_FORMAT=json \
    ARCH_AGENT_SERVICE_NAME=architecture-agent \
    ARCH_AGENT_KNOWLEDGE__DIRECTORY=/data/knowledge

WORKDIR /app

# --------------------------------------------------------------------------- deps
#
# uv is installed from PyPI, not taken from the uv distribution image: that image
# is distroless (no /bin/sh) and its version-pinned tags carry no interpreter.
# Pinning from PyPI fixes the uv version this repository's uv.lock was made with,
# instead of inheriting whatever a floating image tag ships today.
FROM base AS deps

RUN pip install --no-cache-dir "uv==${UV_VERSION}" && uv --version

# Step 1: third-party dependencies only. Keyed on the lockfile, so editing source
# does not invalidate the layer.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# Step 2: the project itself, baked in non-editable.
COPY src ./src
# Parenthesised so a failed `uv sync` fails the build: written as `A && B || true`
# the uninstall's exit status would mask a broken dependency install.
RUN --mount=type=cache,target=/root/.cache/uv     uv sync --frozen --no-dev --no-editable  && (uv pip uninstall --python /opt/venv/bin/python pip setuptools wheel >/dev/null 2>&1 || true)

# --------------------------------------------------------------------------- test
# Declared before runtime so that `runtime` stays the default target.
FROM deps AS test

ARG UV_VERSION
USER root
RUN pip install --no-cache-dir "uv==${UV_VERSION}"
# `test` extends `deps`, so it does not inherit `scripts/` from runtime. The
# health-check tests import it, and the image healthcheck invokes it.
COPY scripts ./scripts
COPY tests ./tests
RUN set -eux; \
    uv sync --frozen --group dev --no-editable; \
    chown -R app:app /app
USER app:app
CMD ["/opt/venv/bin/python", "-m", "pytest", "-q", "--cov=architecture_agent", "--cov-report=term-missing"]

# --------------------------------------------------------------------------- runtime
FROM base AS runtime

LABEL org.opencontainers.image.title="Architecture Engineering Agent" \
      org.opencontainers.image.description="MCP server for software architecture, infrastructure, cloud, cost and review." \
      org.opencontainers.image.source="https://github.com/architecture-agent/architecture_mcp" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="0.1.0"

# HTTP-in-a-container must bind all interfaces inside the container's own network
# namespace: 127.0.0.1 there is unreachable from the published port. This is not
# the same as exposing the server, which only happens if the operator publishes the
# port with -p. Note the server has no authentication, so publishing 8000 hands an
# unauthenticated read-only endpoint to whoever can reach it.
ENV ARCH_AGENT_MCP__TRANSPORT=stdio \
    ARCH_AGENT_MCP__HOST=0.0.0.0

COPY --from=deps /opt/venv /opt/venv
COPY src ./src
COPY scripts ./scripts
# Kept so `--target test` and `pip-audit` inside the image resolve against the same
# lockfile the image was built with.
COPY pyproject.toml uv.lock README.md ./

# Fail the build here rather than at first request if the package will not import.
RUN set -eux; \
    mkdir -p /data/knowledge; \
    chown -R app:app /data /app; \
    python -c "import architecture_agent, sys; print('architecture_agent', architecture_agent.__version__, 'on Python', sys.version.split()[0])"; \
    ! python -c "import mypy" 2>/dev/null

# Run as a non-root user. Nothing the server does requires privileges.
USER app:app

# Readiness, not liveness. Verifies the process loads settings and holds the
# expected security posture: `allow_execution_tools` must be false, so a
# compromised configuration fails the container health check instead of serving.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["/opt/venv/bin/python", "-m", "scripts.health_check"]

STOPSIGNAL SIGTERM
EXPOSE 8000

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/opt/venv/bin/python", "-m", "architecture_agent.mcp.server"]