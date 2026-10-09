#!/usr/bin/env bash
# Verify a built image the way CI does, so a broken image is caught before push.
#
#   ./infrastructure/deployment/verify-image.sh architecture-agent:0.1.0
set -euo pipefail

IMAGE="${1:-architecture-agent:0.1.0}"
FAILURES=0

pass() { printf '  \033[32mPASS\033[0m %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; FAILURES=$((FAILURES + 1)); }

echo "Verifying ${IMAGE}"

echo "[1/8] image is a container, not the test target"
if docker run --rm --entrypoint python "$IMAGE" -c \
   "import importlib.util,sys; sys.exit(1 if importlib.util.find_spec('mypy') else 0)" 2>/dev/null; then
  pass "no dev dependencies in the production image"
else
  fail "dev dependencies present: the default target is not 'runtime'"
fi

echo "[2/8] runs as a non-root user"
USER_ID="$(docker run --rm --entrypoint id "$IMAGE" -u 2>/dev/null)"
if [ "${USER_ID}" = "10001" ]; then pass "uid ${USER_ID}"; else fail "uid is '${USER_ID}'"; fi

echo "[3/8] has a healthcheck"
if [ "$(docker inspect --format '{{json .Config.Healthcheck.Test}}' "$IMAGE")" != "null" ]; then
  pass "healthcheck declared"
else
  fail "no healthcheck"
fi

echo "[4/8] readiness check passes"
if docker run --rm "$IMAGE" python -m scripts.health_check >/dev/null 2>&1; then
  pass "health_check"
else
  fail "health_check"
fi

echo "[5/8] serves MCP over stdio"
if command -v uv >/dev/null 2>&1; then
  if timeout 120 uv run --quiet python - "$IMAGE" <<'PY' >/dev/null 2>&1
import asyncio, sys
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
async def main():
    t = StdioTransport(command="docker", args=["run","-i","--rm",sys.argv[1]])
    async with Client(t) as c:
        assert len(await c.list_tools()) == 19
asyncio.run(main())
PY
  then pass "19 tools over docker stdio"; else fail "stdio transport"; fi
else
  printf '  SKIP stdio transport check (uv not available)\n'
fi

echo "[6/8] serves MCP over http and reports healthy"
NAME="aa-verify-$$"
docker run -d --name "$NAME" -p 8933:8000 \
  -e ARCH_AGENT_MCP__TRANSPORT=streamable-http -e ARCH_AGENT_LOG_LEVEL=WARNING \
  "$IMAGE" >/dev/null 2>&1 || fail "container start"
sleep 8
HEALTH="$(docker inspect --format '{{.State.Health.Status}}' "$NAME" 2>/dev/null || echo none)"
if [ "$HEALTH" = "healthy" ]; then pass "healthy"; else fail "health status: ${HEALTH}"; fi
if timeout 60 uv run --quiet python -c "
import asyncio
from fastmcp import Client
async def m():
    async with Client('http://127.0.0.1:8933/mcp') as c:
        assert len(await c.list_tools()) == 19
asyncio.run(m())
" >/dev/null 2>&1; then pass "19 tools over http"; else fail "http transport"; fi
docker rm -f "$NAME" >/dev/null 2>&1 || true

echo "[7/8] knowledge volume is writable by the runtime user"
if docker run --rm --entrypoint sh "$IMAGE" -c 'touch /data/knowledge/.probe && rm /data/knowledge/.probe'; then
  pass "/data/knowledge writable"
else
  fail "/data/knowledge not writable"
fi

echo "[8/8] no secrets in the image"
if docker run --rm --entrypoint sh "$IMAGE" -c \
   'test ! -e /app/.env && ! grep -rqs "sk-ant-api" /app/src /app/scripts /app/pyproject.toml'; then
  pass "no .env, no credential literals"
else
  fail "possible secret material in the image"
fi

if [ "$FAILURES" -eq 0 ]; then
  printf '\n\033[32mImage verified\033[0m\n'
else
  printf '\n\033[31m%d check(s) failed\033[0m\n' "$FAILURES"
  exit 1
fi
