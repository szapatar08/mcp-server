# Requirements

Traceability between the specification and the implementation. A requirement is
**Delivered** when it exists, is tested, and is documented. A requirement that is
**Not started** is a commitment, not an omission.

Status legend: `Delivered` · `Partial` · `Not started`

Last reviewed 2026-10-08, after Milestones 1 to 10.

---

## 1. Functional requirements

| ID | Requirement | Status | Notes |
| --- | --- | --- | --- |
| FR-001 | Analyse an existing architecture | Delivered | `analyze_architecture` with a validated `ArchitectureAnalysis` |
| FR-002 | Design a new architecture from requirements and constraints | Delivered | `design_architecture`, validated before return |
| FR-003 | Internal infrastructure: compute, network, storage, database, observability | Delivered | Models plus `design_infrastructure` and `review_infrastructure` |
| FR-004 | Identify external dependencies and their risks | Partial | `ExternalDependency`, `ExternalDependencyRisk` delivered |
| FR-005 | Provider-aware cloud architecture (AWS, Azure, GCP) | Delivered | `design_cloud_architecture`; provider-validated service names |
| FR-006 | Compare cloud services against nine criteria | Delivered | `compare_cloud_services` over ten criteria |
| FR-007 | Estimate infrastructure cost with provenance | Delivered | `estimate_cloud_cost` prices only from a supplied price book |
| FR-008 | Model budget scenarios | Delivered | `analyze_budget` with named multipliers and rationale |
| FR-009 | Design deployment strategies | Delivered | `design_deployment`, `review_deployment` |
| FR-010 | Design CI/CD pipelines | Delivered | `PipelinePolicy` enforced by `DeploymentPlan` |
| FR-011 | Security review | Delivered | `security_review` with OWASP/NIST framework grounding |
| FR-012 | Reliability review (HA, DR, RTO, RPO) | Delivered | `reliability_review` against a supplied target, RTO and RPO |
| FR-013 | Scalability review | Delivered | `scalability_review`; topology-based, limitation reported |
| FR-014 | Observability analysis and gap detection | Delivered | Scored dimension plus OBS-001 listing every missing signal |
| FR-015 | UML and C4 diagram generation | Partial | Mermaid and PlantUML with validation and injection defence. Activity and state diagrams unsupported: they cannot be derived without inventing steps or states |
| FR-016 | Architecture Decision Records | Delivered | `generate_adr` with sequential IDs and validation |
| FR-017 | Web research for dynamic facts | Delivered | `research_topic` over DuckDuckGo, degraded on failure |
| FR-018 | Distinguish stable from dynamic knowledge with freshness metadata | Delivered | Categories on every source and chunk; `FreshnessPolicy` drives staleness |
| FR-019 | Source validation and trust classification | Delivered | `TierClassifier`, `assess_confidence` with tests |
| FR-020 | Structured architecture review with justified scores | Delivered | Deterministic scorer plus model observations kept separate |

## 2. Non-functional requirements

| ID | Requirement | Status | Evidence |
| --- | --- | --- | --- |
| NFR-001 | Performance: no redundant network, embedding or LLM calls | Partial | Knowledge dedup avoids re-embedding; no LLM or search cache yet |
| NFR-002 | Reliability: external failure must not crash the server | Partial | `ResearchResult.degraded` models the contract |
| NFR-003 | Security: no secrets in logs; validate all external input | Delivered | `SecretStr`, `redact`, `extra="forbid"`, tests |
| NFR-004 | Prompt-injection defence | Delivered | `UntrustedContentGuard`: nonce fencing, neutralisation, reporting |
| NFR-005 | Observability: structured logs with correlation fields | Delivered | `JsonFormatter`, `request_scope`, `log_operation` |
| NFR-006 | Maintainability: PEP 8, type hints, small modules, docstrings | Delivered | ruff (incl. pydocstyle), mypy strict |
| NFR-007 | Extensibility: new provider, store, diagram format, tool | Delivered | `LLMClient`, `SearchClient`, `PageFetcher`, `Embedder`, `VectorStore`, `DiagramRenderer` ports |
| NFR-008 | Predictable errors; no stack traces or secrets across MCP | Delivered | `application/errors.py`, `mask_error_details`, masked-internal-error test |
| NFR-009 | Production-oriented: Docker image, health checks, CI/CD | Partial | CI and health check delivered; Docker image is Milestone 11 |

## 3. Constraints

| ID | Constraint | Rationale |
| --- | --- | --- |
| C-001 | Python 3.12 (3.13 supported) | Target of the LangChain and FastMCP ecosystem |
| C-002 | MCP interface is a versioned public API contract | Clients depend on tool names and schemas |
| C-003 | The agent never executes commands or mutates infrastructure | Blast radius of an autonomous architecture tool |
| C-004 | Retrieved web content is data, never instructions | Indirect prompt injection is the primary threat |
| C-005 | Cost figures are always estimates with provenance | Cloud pricing is dynamic, regional and not a contract |
| C-006 | Domain layer has no I/O and no third-party imports | Keeps reasoning testable without a network |
| C-008 | The MCP surface is versioned by a committed schema snapshot | A client that cannot tell whether a schema changed has no contract (ADR-007) |
| C-009 | A dimension is never scored without the inputs that justify it | FR-020 forbids invented precision |
| C-007 | Milestones ship in order; no speculative features | Every untested capability is a liability |

## 4. Explicitly out of scope for the first release

- Executing infrastructure changes (`terraform apply`, `kubectl apply`, SSH).
- Reading a customer's private repositories or cloud accounts.
- Acting as a source of truth for live billing data.
- Replacing a certified cloud cost calculator; the agent explains and
  cross-checks, it does not invoice.

## 5. Requirement conflicts found during implementation

| Conflict | Resolution |
| --- | --- |
| §4 lists no `observability` package, but §27 requires structured logging | Observability is cross-cutting technical infrastructure, so it lives in `infrastructure/observability/`. Recorded in ADR-006 |
| §33 requires an explicit assumption list; several required response sections do not mention assumptions | Assumptions are a required field on `CostEstimate` and an explicit list on `Architecture`, so a recommendation cannot omit them |
| FR-007 asks for a "confidence" field while FR-019 forbids treating unverified sources as authoritative | `ConfidenceLevel` is derived by `assess_confidence` from tier and corroboration only; high confidence is impossible without provenance |
| §38 asks for an MCP contract test; nothing defines what "stable contract" means | Tool names, descriptions and both JSON schemas are snapshotted to a committed file; a change fails CI until the snapshot and an ADR change together (ADR-007) |
| FR-020 asks for scores on every dimension; §24 forbids invented precision | A dimension whose inputs were not supplied is omitted and reported as an evidence gap. The tool response carries `complete: false` |
