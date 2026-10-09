# Deployment

How to run the Architecture Agent MCP server in an environment other than your
laptop. Read the security note before publishing any port.

## Choose a transport first

| Transport | Use when | Reached via |
| --- | --- | --- |
| `stdio` | An MCP client on the same machine launches the server | `docker run -i --rm image` |
| `streamable-http` | The server is a container or a service | `POST http://host:8000/mcp` |

stdio is the default. Inside a container it has no peer, so use HTTP there.

## Quick start

```bash
docker build -t architecture-agent:0.1.0 .
docker compose -f infrastructure/docker/compose.yaml up -d
curl -s http://127.0.0.1:8000/mcp   # any request should return an MCP response
```

## Security

**The server has no authentication.** It is read-only: it analyses, reviews,
designs, researches and generates. It executes no commands and modifies no
infrastructure. It does hold whatever credentials you gave it.

- Keep it on loopback, or behind a proxy that terminates TLS and authenticates.
- `ARCH_AGENT_SECURITY__ALLOWED_DOMAINS` restricts which hosts research may fetch.
  Set it in any shared environment.
- The container runs as UID 10001, read-only root filesystem, all capabilities
  dropped, `no-new-privileges`. The compose file enables all of these; a bare
  `docker run` does not, so prefer compose for a real deployment.

## Configuration

Everything is `ARCH_AGENT_`-prefixed environment variables. The image sets safe
defaults; override what you need.

| Variable | Image default | Notes |
| --- | --- | --- |
| `ARCH_AGENT_MCP__TRANSPORT` | `stdio` | `streamable-http` for a container service |
| `ARCH_AGENT_MCP__HOST` | `0.0.0.0` | Must be `0.0.0.0` in a container; loopback is unreachable from a published port |
| `ARCH_AGENT_MCP__PORT` | `8000` | |
| `ARCH_AGENT_LOG_LEVEL` | `INFO` | |
| `ARCH_AGENT_LOG_FORMAT` | `json` | Logs go to stderr, leaving stdout for the protocol |
| `ARCH_AGENT_LLM__API_KEY` | *(unset)* | Required for the 17 model-backed tools |
| `ARCH_AGENT_SEARCH__PROVIDER` | `duckduckgo` | |
| `ARCH_AGENT_SECURITY__ALLOWED_DOMAINS` | *(empty)* | Comma-separated hostnames |
| `ARCH_AGENT_KNOWLEDGE__DIRECTORY` | `/data/knowledge` | Mount a volume here |

## Connecting a client to a deployed container

The server ignores `Authorization` headers. Terminate TLS and authenticate at a
reverse proxy in front of it — see the reverse-proxy note in the root
[`README.md`](../../README.md#before-you-deploy-the-server-has-no-authentication).

```bash
# deployed over HTTP
claude mcp add architecture-agent --transport http architecture-agent https://arch-agent.internal/mcp
codex mcp add architecture-agent --url https://arch-agent.internal/mcp
# OpenCode: add a {"type": "remote", "url": "..."} entry to opencode.json
```

If the client runs on the same machine as the image, it can spawn the container
instead of connecting to a URL:

```bash
claude mcp add architecture-agent -- docker run -i --rm architecture-agent:0.1.0
codex mcp add architecture-agent -- docker run -i --rm architecture-agent:0.1.0
```

## Health

`HEALTHCHECK` runs `python -m scripts.health_check`, which verifies the process
imports, loads settings, can configure logging and holds the expected security
posture. A configuration with `allow_execution_tools` enabled fails the check, so a
compromised config takes the container out of rotation rather than serving.

```bash
docker inspect --format '{{.State.Health.Status}}' architecture-agent-mcp-1
docker exec architecture-agent-mcp-1 python -m scripts.health_check
```

## State

The only persistent state is the knowledge database at
`ARCH_AGENT_KNOWLEDGE__DIRECTORY`. It is disposable: delete the volume and re-run
`update_knowledge`. Mount it as a named volume or a host path.

## Kubernetes

The image is suitable as-is. A minimal `Deployment`:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: architecture-agent
spec:
  replicas: 1
  selector:
    matchLabels: { app: architecture-agent }
  template:
    metadata:
      labels: { app: architecture-agent }
    spec:
      containers:
        - name: mcp
          image: ghcr.io/architecture-agent/architecture_mcp:0.1.0
          ports: [{ containerPort: 8000 }]
          env:
            - { name: ARCH_AGENT_MCP__TRANSPORT, value: streamable-http }
            - { name: ARCH_AGENT_MCP__HOST, value: "0.0.0.0" }
            # From a Secret, never a literal.
            - name: ARCH_AGENT_LLM__API_KEY
              valueFrom: { secretKeyRef: { name: architecture-agent, key: llmApiKey } }
          envFrom:
            - configMapRef: { name: architecture-agent-config }
          readinessProbe:
            exec: { command: ["/opt/venv/bin/python", "-m", "scripts.health_check"] }
            initialDelaySeconds: 5
            periodSeconds: 30
          livenessProbe:
            httpGet: { path: /mcp, port: 8000 }
            initialDelaySeconds: 15
            periodSeconds: 30
          volumeMounts: [{ name: knowledge, mountPath: /data/knowledge }]
          securityContext:
            runAsNonRoot: true
            runAsUser: 10001
            readOnlyRootFilesystem: true
            allowPrivilegeEscalation: false
            capabilities: { drop: ["ALL"] }
      volumes:
        - name: knowledge
          persistentVolumeClaim: { claimName: architecture-agent-knowledge }
```

Put an `Ingress` or a `Service` with an authenticating proxy in front of it. Without
one, the endpoint is unauthenticated.

## CI/CD

`.github/workflows/ci.yml` builds and tests the image itself, not just the host
checkout, so a broken Dockerfile fails the pipeline. See that file for the stage
list; `infrastructure/deployment/verify-image.sh` runs the same gate locally.
