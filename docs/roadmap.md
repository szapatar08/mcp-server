# Roadmap

Eleven milestones, in order. Each ends green: `ruff format`, `ruff check`,
`mypy` and `pytest` all pass, and documentation matches the code.

**Status: Milestones 1–10 implemented in one pass at the user's direction.** Milestone 11 has not started.

This milestone record was written under `CLAUDE.md` §9, one milestone at a time. Milestones 3–10 were then implemented together, with verification deferred. §What is not verified is honest about what that cost.

---

## Status

| # | Milestone | Status | Delivers |
| --- | --- | --- | --- |
| 1 | Foundation | **Complete** | Repo, typed config, structured logging, domain vocabulary, tooling, CI |
| 2 | MCP | **Complete** | FastMCP server, first two tools, resources, prompts, contract snapshot |
| 3 | LangChain agent | Implemented | `LLMClient` port, LangChain adapter, `FakeLLMClient`, untrusted-content guard |
| 4 | Architecture engine | Implemented | `design_architecture`, `analyze_architecture`, `generate_adr` |
| 5 | Infrastructure engine | Implemented | Design and review tools for infrastructure, cloud, deployment, and the three review dimensions |
| 6 | Web research | Implemented | DuckDuckGo adapter, egress guard, degraded results |
| 7 | Knowledge layer | Implemented | Chunking, hashed-ngram embeddings, SQLite vector store, hash dedup |
| 8 | Cost engine | Implemented | `estimate_cloud_cost` pricing only from a supplied price book; `analyze_budget` |
| 9 | Diagram engine | Implemented | Both renderers, validation, injection defence |
| 10 | QA and evaluation | Partial | Unit, integration, MCP contract, security and e2e tiers populated; evaluation and performance tiers empty |
| 11 | Deployment | Complete | Multi-stage Dockerfile (469 MB), compose, health check, hardened compose, Kubernetes manifest, image verification script, Docker-based CI |

---

## Milestone 1 — Foundation (complete)

### Delivered

| Area | What exists |
| --- | --- |
| Repository | `pyproject.toml`, `uv.lock`, `.python-version`, `py.typed`, `.gitignore`, `.env.example`, `.pre-commit-config.yaml` |
| Configuration | `config/settings.py`: `Settings` with `llm`, `search`, `cache`, `knowledge`, `mcp`, `security` sections; `ARCH_AGENT_` prefix, `__` nesting, flat logging fields; `SecretStr` credentials; egress allowlist |
| Observability | `infrastructure/observability/`: `context.py` (request correlation), `logging.py` (JSON and text formatters, idempotent configuration, operation timing), `redaction.py` (structural and textual scrubbing) |
| Domain | `research.py` (source tiers, confidence, freshness, provenance), `architecture.py` (components, interactions, dependencies, assumptions, trade-offs, ADRs), `infrastructure.py` (compute, network, storage, database, observability, availability), `cloud.py` (providers, services, comparisons), `cost.py` (estimates, line items, pricing references, budgets), `deployment.py` (strategies, exposure path, pipelines), `security.py` (findings, dimensions, scores), `diagram.py` (kinds, languages, validation issues) |
| Tests | 218 tests, 98% branch coverage, no network and no credentials |
| Tooling | ruff (format, lint incl. bandit and pydocstyle), mypy strict, pytest with markers and coverage, pre-commit |
| CI | Format, lint, type check, test matrix (3.12/3.13), `pip-audit --strict`, `.env` leak gate, wheel build and install check |
| Docs | `README.md`, `CLAUDE.md`, `requirements.md`, `architecture.md`, `testing.md`, `security.md`, ADRs 001–006 |
| Ops | `scripts/health_check.py` |

### Explicitly not delivered

No MCP server, no LLM call, no search call, no vector store, no renderer, no
Docker image. The domain is a vocabulary with invariants, not an engine.

### Exit criteria met

- `uv run ruff format --check .` passes.
- `uv run ruff check .` passes.
- `uv run mypy .` passes under `strict = true`.
- `uv run pytest` passes: 218 tests.
- `uv run pip-audit --strict` reports no known vulnerabilities.
- Every new setting appears in `.env.example`.
- Six ADRs record the decisions taken.

---

## Milestone 2 — MCP (complete)

### Delivered

| Area | What exists |
| --- | --- |
| Server | `mcp/server.py`: FastMCP 4 composition root, stdio and HTTP transports, `create_server()`, `main()` entry point |
| Tools | `review_architecture`, `generate_diagram` via `mcp/tools.py`, with `ToolServices` injected rather than resolved |
| Schemas | `tools/schemas.py`: typed Pydantic models with `extra="forbid"`; output models produce precise output schemas |
| Errors | `application/errors.py`: `ErrorReport`, field-level validation descriptions, and the split between actionable (`ToolError`) and opaque failures |
| Review engine | `application/review_service.py`: 12-finding catalogue, penalty-weighted scoring, 4 scored dimensions, evidence gaps |
| Diagram engine | `application/diagram_service.py` plus `infrastructure/rendering/`: Mermaid and PlantUML, `DiagramRenderer` port, syntax validation, injection defence |
| Resources | `architecture://capabilities`, `//review/rubric`, `//diagram/kinds`, generated from live server state so they cannot drift |
| Prompts | `design_architecture`, `review_architecture`, `estimate_infrastructure_cost`, each carrying the agent's non-negotiable rules |
| Tests | 34 MCP contract tests, 8 security-boundary tests, plus service and renderer units |

### Decisions recorded

- **ADR-007** — MCP runtime, transport, validation strictness and error masking.
  It carries a visible correction: `strict_input_validation=True` was specified,
  measured to make every tool uncallable, and reversed to `False`.

### Risks retired

- ~~FastMCP may not preserve `extra="forbid"`.~~ It does; asserted by a test on
  both the request object and the nested domain model.
- ~~Strict validation is safer.~~ It rejects arrays and enum strings, which is
  what a conforming client sends. Reversed with the measurement recorded.

### Known limits, deliberately

- **Activity and state diagrams are unsupported.** They cannot be derived from an
  architecture without inventing steps or states. The capability resource says so.
- **Review scoring is deterministic only.** It does not yet interpret prose; that
  is Milestone 4.
- **No cost, cloud or deployment design tools.** The domain models exist; the
  engines do not.

### Exit criteria met

- 370 tests pass; no network, no credentials.
- `ruff format`, `ruff check` and `mypy --strict` pass.
- The MCP contract snapshot is committed and compared in CI.
- An internal error message is proven not to reach the client.
- Diagram syntax injection is proven contained.

## Milestone 3 — LangChain agent (next)

- Add `langchain` and a provider adapter package under `infrastructure/llm/`.
- Declare the `LLMClient` port in `application/`, implement it per provider plus a
  deterministic `fake` for tests.
- System prompts encoding the rules in `CLAUDE.md` §2.
- The untrusted-content guard: retrieved text is quoted data, never instructions;
  instruction-shaped content is recorded as evidence.
- Structured output with one retry on schema failure, then a clean failure.
- `agent/` gains the planner, policies and prompt templates.

Open questions to settle with an ADR:

- **ADR-008** — LangChain version and the provider abstraction surface.
- Whether prompts live in `agent/prompts.py` as Python constants or as files. Files
  make review of long prompts easier; constants keep packaging simple.

## Milestone 4 — Architecture engine

- `analyze_architecture`, `design_architecture`, `review_architecture`,
  `generate_adr`.
- Trade-off analysis and assumption extraction as explicit steps.
- Review scoring that refuses to invent precision: no evidence, no high score.

## Milestone 5 — Infrastructure engine

- `design_infrastructure`, `review_infrastructure`, `design_cloud_architecture`,
  `compare_cloud_services`, `design_deployment`, `review_deployment`,
  `security_review`, `reliability_review`, `scalability_review`.

## Milestone 6 — Web research

- `research_topic`, `search_architecture_knowledge`.
- Add the search dependency; decide hand-rolled HTTP versus a client library.
  ADR-008.
- Timeouts, rate-limit handling and `degraded` results; caching per
  `CacheSettings`.

## Milestone 7 — Knowledge layer

- `update_knowledge`; ingestion, chunking, embeddings, retrieval, freshness,
  content-hash deduplication.
- Choose the vector store. ADR-009.

## Milestone 8 — Cost engine

- `estimate_cloud_cost`, `analyze_budget`.
- Pricing retrieval with provenance; never a bare number.

## Milestone 9 — Diagram engine

Partly delivered in Milestone 2: `generate_diagram`, both renderers, structural
validation and the injection defence. Remaining:

- Activity and state diagrams, which need a workflow input the domain does not
  have yet.
- Entity-relationship diagrams from a real data model rather than from components.
- Optional remote rendering for clients that cannot render Mermaid locally.

## Milestone 10 — QA and evaluation

Partly delivered: `tests/mcp` and `tests/security` are populated. Remaining:

- `tests/integration`: adapters against stubs and recorded fixtures.
- `tests/e2e`: full scenarios, including the specification's three.
- `tests/evaluation`: the dataset with required concepts and forbidden behaviours.
- `tests/performance`: concurrency, latency, cache effectiveness, documented
  baseline.

## Milestone 11 — Deployment

- `Dockerfile` and `.dockerignore`: minimal base image, non-root user, health
  check, no secrets in layers.
- Terraform examples for dev, staging and production.
- Deployment and troubleshooting documentation.

---

## What is verified

Every claim below is backed by a passing test in this repository.

| Claim | Evidence |
| --- | --- |
| All 19 specified tools are registered and typed | `tests/mcp/test_contract.py`, `tests/e2e/test_scenarios.py` |
| Unknown fields rejected, wrong types rejected, output schemas precise | `tests/mcp/test_contract.py` |
| A domain model never returns an incoherent proposal | `tests/integration/test_research_pipeline.py` |
| Prompt injection is detected, neutralised and reported | `tests/security/test_untrusted_content.py` |
| No credential reaches an error message, log line or MCP response | `tests/security/test_mcp_boundary.py` |
| The egress allowlist blocks a non-allowed host before any fetch | `tests/integration/test_research_pipeline.py` |
| A cost estimate contains only prices with a retrieved source | `tests/integration/test_research_pipeline.py`, `tests/unit/application/test_knowledge_and_cost.py` |
| An unpriceable design is refused rather than estimated | Same as above |
| Ingestion is idempotent on content hash | `tests/unit/application/test_knowledge_and_cost.py` |
| Chunking terminates and covers the document | Same as above |
| A dimension with no supporting evidence is never scored | `tests/unit/application/test_review_service.py`, `tests/e2e/test_scenarios.py` |
| Diagram syntax injection cannot escape a quoted span | `tests/unit/infrastructure/test_rendering_safety.py` |
| The server runs over a real stdio subprocess | Verified manually and by `verify-image.sh` |
| The image serves 19 tools over stdio and HTTP | `verify-image.sh` steps 5 and 6, all passing |
| The image is non-root, health-checked, secret-free | `verify-image.sh` steps 1-4 and 8 |
| The suite passes inside the image as UID 10001 | `docker run architecture-agent:test`: 434 passed |
| The image has no known CRITICAL/HIGH vulnerabilities | **Not verified locally** — Trivy's DB is unreachable here |

## What is not verified

Stated plainly, because Milestones 3–10 were written before being tested.

| Gap | Why it matters | Fix |
| --- | --- | --- |
| **No real provider has been called.** Every model-backed path runs against `FakeLLMClient`. | `LangChainLLMClient` is unexercised. Its prompt format, the envelope contract and the retry path may not survive a real model. | One live smoke test per provider against a short prompt, checking the envelope parses. |
| **DuckDuckGo has not been called.** The fake corpus stands in for it. | `duckduckgo-search` changed its API between major versions; `text()` and the result keys may differ. | One live search, then pin the version. |
| **`update_knowledge` assigns `COMMUNITY`/`LOW` to every ingested document.** | Knowledge retrieved from a caller-provided URL is not classified, so retrieved chunks inherit the weakest trust level even for official documentation. | Classify by URL through `TierClassifier`, as the research path does. |
| **`tests/evaluation/` is empty.** No dataset, no scorers. | Nothing measures whether answers are architecturally sound. This is the specification's §40 requirement and the largest remaining gap. | Build the dataset from §40's ten topics. |
| **`tests/performance/` is empty.** No latency, concurrency or cache baseline. | NFR-001 is unmeasured. The SQLite store's 5,000-row scan ceiling is asserted nowhere. | Baseline single-tool latency and the retrieval scan at 1k/10k/100k chunks. |
| **No LLM or search cache.** NFR-001 is only partially met. | Repeated questions re-query DuckDuckGo and re-bill the provider. | Cache at the adapter boundary using `CacheSettings`. |
| **No image size optimisation beyond `--no-dev`.** 469 MB, of which 203 MB is dependencies. | `anthropic`, `zstandard`, `cryptography`, `primp` and `lxml` come from the LangChain, MCP auth and search stacks. Not a defect, but it is large. | Measure whether the provider SDK and auth stack are both needed; prune only with evidence. |
| **`tools/engine_tools.py` is 49% covered.** | The thin adapter layer is mostly unexercised; a signature mismatch would surface as a runtime error, not a test failure. | One test per tool through the in-memory client. |
| **Model output quality is unmeasured.** | A model can return a valid, well-formed, architecturally wrong proposal, and every invariant would pass. | Evaluation dataset (above) is the only real answer. |

## Recommended next steps

1. **Live-provider smoke tests.** Cheap, and they would have caught the two integration
   gaps above.
2. **Evaluation dataset.** Ten cases from specification §40, scored on required
   concepts and forbidden behaviours.
3. **Classify ingested knowledge by URL.** A ten-line fix with a real correctness
   effect on every retrieval.
4. **Tool-level coverage through the client.** Raises confidence in the 49%.
5. **Cache the LLM and search adapters.** Closes NFR-001.
6. ~~**Milestone 11: container, health checks, deployment.**~~ Done: `Dockerfile`,
   `.dockerignore`, `infrastructure/docker/compose.yaml`,
   `infrastructure/deployment/README.md`, `verify-image.sh`, and a six-stage
   Docker-based `ci.yml`.
7. **Run Trivy in CI.** The image scan step is configured but could not be verified
   locally: this machine cannot reach `mirror.gcr.io` to download Trivy's
   vulnerability database. The step itself is standard and will run on GitHub
   Actions; treat the first CI run as its real test.

## The lesson from the strict-validation bug

Milestone 2's `strict_input_validation=True` looked right, read right, and made every
tool uncallable. Only a real end-to-end call found it. The same class of error is
plausible in the unexercised paths listed above, and the mitigation is identical:
run the thing for real, once, before believing it.

Deferring verification while writing nine milestones at once traded a known process
rule for speed. The delivered system is larger and more capable, and four of the gaps
above are integration risks that a slower cadence would probably have caught
immediately.
