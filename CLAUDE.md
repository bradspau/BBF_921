# TMF921 Intent Management API v5.0.0

**Stack:** Python 3.12 + FastAPI + Apache Jena Fuseki/TDB2 as the authoritative RDF graph store (SPARQL 1.1 / Graph Store Protocol). TIO quantity reasoning runs in Python via RDFLib (`src/handler/evaluator.py`) — Fuseki is storage only.
**Models:** Pre-generated → `src/api/schemas/generated.py` — import only, never rewrite
**OAS:** `docs/spec/TMF921_Intent_Management_v5.0.0_oas.yaml`
**Repo:** [FILL: GitHub URL + branch]

## Commands
- Generate models: `make models`
- Run tests: `.venv/bin/pytest tests/ -v --cov=src --cov-fail-under=80`
- Run single test file: `.venv/bin/pytest tests/unit/test_evaluator.py -v`
- Start dev server: `.venv/bin/uvicorn src.main:app --reload`
- Lint: `ruff check src/` / `ruff check src/ --fix` (auto-fix)
- Seed data: `.venv/bin/python seed_data/seed_intents.py`
- Docker (requires sudo on this machine): `sudo docker compose up --build`

## Handler Layer (`src/handler/`)
- `evaluator.py` — `evaluate_turtle_conditions(turtle)` → `{intentHandlingState, reason, conditions[]}`
- `state_writer.py` — writes per-condition facts to `intents/{uuid}/handlerState` graph (OODA working memory)
- `dispatcher.py` — background task: evaluate → write handlerState → create IntentReport → notify

## Rules
- Read the listed docs/ file before writing code for each phase
- Real RDFLib ConjunctiveGraph from line one — no dicts, no stubs
- `expressionValue` → `Literal(value, datatype=XSD.string)` — never parsed into triples
- `id`, `href`, `creationDate`, `lastUpdate` set server-side — strip from POST, reject on PATCH
- Responses serialised with `model.model_dump(by_alias=True)` — preserves `@type`, `@baseType`
- Tests must pass at ≥80% coverage before any phase advances — show pytest output

## Reference Files
| File | Read for |
|---|---|
| `docs/01-resources.md` | Mandatory attributes, sub-resource fields, polymorphism |
| `docs/02-operations.md` | All endpoints, status codes, query params, pagination |
| `docs/03-expressions.md` | JsonLd/Turtle structure, RDFLib storage rule, TIO namespaces |
| `docs/04-state-machine.md` | lifecycleStatus FSM, valid transitions, RDF StateChange nodes |
| `docs/05-notifications.md` | Hub, 10 eventTypes, async fan-out |
| `docs/06-negotiation.md` | ProbeIntent, JudgePreference, BestPropose flows |
| `docs/07-graph-schema.md` | RDF node/predicate design, named graphs, SPARQL traversal |
| `docs/08-patch-rules.md` | Patchable / non-patchable tables, RFC 7386 merge patch |
| `docs/09-deliverables.md` | Phase checklist with exact file paths |

## Skills
> Note: tmf921-intent and tmf-api-guidelines use non-default filenames — load by full path.

| Skill | Path | Auto-loads when working on |
|---|---|---|
| TMF921 Intent | `.claude/skills/tmf921-intent/tmf921-intent-SKILL.md` | Intent, IntentReport, IntentSpec, ProbeIntent, expressions, lifecycle, events, conformance |
| TMF API Guidelines | `.claude/skills/tmf-api-guidelines/tmf-api-guidelines-SKILL.md` | routers, validators, error handling, TMF630, URI structure, patching, notifications |
| Advanced Python Asyncio | `.claude/skills/advanced-python-asyncio/SKILL.md` | async/await, TaskGroup, structured concurrency, semaphores, timeouts, fan-out |
| Backend Architecture | `.claude/skills/backend-architecture-patterns/SKILL.md` | service layering, repository pattern, domain boundaries, intent handler structure |
| API Service Tests | `.claude/skills/api-service-test-generator/SKILL.md` | multi-step end-to-end intent lifecycle flows, Schemathesis, contract tests |
| Python Testing | `.claude/skills/python-testing-patterns/SKILL.md` | pytest fixtures, async tests, ConjunctiveGraph test setup, ≥80% coverage |
| REST API Design | `.claude/skills/rest-api-design-guide/SKILL.md` | REST naming, URI patterns, semantic HTTP, versioning |
| RESTful Standards | `.claude/skills/restful-api-design-standards/SKILL.md` | error responses, REST conventions, endpoint documentation |
| OpenAPI Spec Architect | `.claude/skills/openapi-spec-architect-1/SKILL.md` | designing/writing OAS 3.0 spec, schema composition |
| OpenAPI App Builder | `.claude/skills/openapi-application-builder/SKILL.md` | scaffolding backend project from OAS spec |
| Swagger Downloader | `.claude/skills/swagger-openapi-downloader/SKILL.md` | fetching, validating, parsing remote OAS/Swagger files |
| Swagger to Code | `.claude/skills/swagger-to-code-generator/SKILL.md` | generating server stubs and client code from OAS spec |
| Code Reviewer | `.claude/skills/code-reviewer/SKILL.md` | security, performance, best-practice review on any generated or modified code |

## Infrastructure
- Custom `fuseki.Dockerfile` builds ARM64-native Fuseki from `eclipse-temurin:17-jre-jammy` + Fuseki 5.6.0 (archive.apache.org) — `stain/jena-fuseki` is amd64-only
- Fuseki 6.x `--config` assembler is BROKEN for `tdb2:DatasetTDB2` — use Fuseki 5.6.0
- `fuseki-config.ttl` defines two datasets: `tmf921` (TDB2, API store) + `tmf921-eval` (in-memory InfModel + TIO rules)
- No git remote configured — `git push` will fail until `git remote add origin <url>`

## Fuseki Operational Notes
- Default graph is always empty — query with `GRAPH ?g { ?s ?p ?o }` in the console
- Named graph URIs: `http://tmforum.org/api/v5/intents/{uuid}`, `/intentSpecifications/{uuid}`, `/reports/{uuid}`
- Handler state graph (OODA working memory): `http://tmforum.org/api/v5/intents/{uuid}/handlerState` — written after every evaluation cycle by `src/handler/state_writer.py`
- `ensure_dataset()` treats 401/403 as OK — dataset pre-exists via assembler config
- `tmf921-eval` dataset defined in fuseki-config.ttl but NOT used for evaluation — Fuseki 5.x SPARQL bypasses `ja:InfModel` at the DatasetGraph level; all TIO quantity evaluation runs in Python via `evaluate_turtle_conditions()` in `src/handler/evaluator.py`

## Evaluation Architecture
- `evaluate_turtle_conditions(turtle)` returns `{intentHandlingState, reason, conditions[]}` — `conditions` list has per-condition `{type, operator, observed, bound, passed}` detail for the OODA loop
- `gsp_put(graph_uri, turtle)` preferred over SPARQL DROP+INSERT for named graph replacement — atomic, one HTTP call
- TIO namespace authority: `http://tio.models.tmforum.org/tio/v3.6.0/{Module}/` — canonical prefixes in `ontology/jena-rules/tio_all.rules`; all seed/ontology files must use `http://` (not `https://`) and include `/tio/v3.6.0/`
- OODA Decide step queries failed conditions: `SELECT ?type ?observed ?bound WHERE { GRAPH <…/handlerState> { <intent> imo:hasConditionResult ?c . ?c imo:conditionPassed "false"^^xsd:boolean ; a ?type ; imo:observedValue ?observed ; imo:boundValue ?bound } }`

## Python/RDFLib Testing Gotchas
- `Decimal("NaN")` does NOT raise `InvalidOperation` — use `"not-a-number"` string in tests targeting the non-numeric error path
- RDFLib normalises `xsd:dateTime` `Z` suffix to `+00:00` on round-trip — use `assert "2026-06-04T09:00:00" in str(val)` not `== "...Z"`

## Security Patterns
- All resource ID path params validated as UUID: `Annotated[str, Path(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")]`
- `lifecycleStatus` FSM uses ALL-CAPS only: `ACKNOWLEDGED`, `ACTIVE`, `FULFILLED`, `DEGRADED`, `SUSPENDED`, `TERMINATED`

## Base URLs
```
Base: {scheme}://{host}:{port}/tmf-api/intentManagement/v5
Intent:       {base}/intent/{id}
IntentReport: {base}/intent/{intentId}/intentReport/{id}
IntentSpec:   {base}/intentSpecification/{id}
```

## Phase Order (do not skip ahead — wait for confirmation each phase)
1. Graph layer → `graph/namespaces.py`, `graph/nodes.py`, `graph/store.py`, `graph/schema_init.py`
2. Repository layer → 4 repositories under `graph/repositories/`
3. Service + state machine → `services/intent_service.py`, `state_machine.py`, `notification_service.py`
4. API layer → all routers, `fields_filter.py`, `error_handlers.py`, `main.py`
5. Integration + contract tests → FastAPI TestClient + Schemathesis vs OAS file
6. Infrastructure → Dockerfile, docker-compose, seed data, README, Postman collection


<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:7510c1e2 -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/SYNC_CONCEPTS.md for details and anti-patterns.

## Session Completion

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
<!-- END BEADS INTEGRATION -->
