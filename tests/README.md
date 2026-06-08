# Test Suite

793 tests across three suites. No running Fuseki instance required — all Fuseki HTTP calls are mocked with `respx`.

## Prerequisites

```bash
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

Or if using `uv`:

```bash
uv sync
```

---

## Running Tests

### All tests with coverage report

```bash
pytest tests/ -v --cov=src --cov-fail-under=80
```

Coverage must stay at or above **80%** — the CI gate will fail below this threshold.

### Unit tests only

```bash
pytest tests/unit/ -v
```

Fast, no I/O. Covers the core logic layers:

| File | What it tests |
|---|---|
| `test_evaluator.py` | TIO expression evaluation — all quantity operators, logical operators, math functions, set constructors, validity chains |
| `test_dispatcher.py` | Flow 1 (ProbeIntent auto-transition) and Flow 3 (Best/Propose bound substitution) |
| `test_limits.py` | `HANDLER_LIMITS_JSON` loading and `apply_best_effort_bounds()` |
| `test_intent_service.py` | Intent CRUD, state machine enforcement |
| `test_state_machine.py` | FSM valid/invalid transition table |
| `test_repositories.py` | SPARQL query construction and Fuseki response parsing |
| `test_routers.py` | API layer — request validation, response serialisation, error codes |
| `test_state_writer.py` | OODA working memory Turtle serialisation |
| `test_observation_store.py` | Observation append and pruning logic |
| `test_store.py` | FusekiClient retry, backoff, timeout handling |
| `test_logical_operators.py` | `log:allOf`, `log:anyOf`, `log:match` evaluation |
| `test_error_handlers.py` | FastAPI exception → RFC 7807 problem JSON mapping |
| `test_generated_models.py` | Pydantic schema round-trips |

### Integration tests only

```bash
pytest tests/integration/ -v
```

End-to-end flows through the full stack (FastAPI → service → repository → mocked Fuseki):

| File | What it tests |
|---|---|
| `test_intent_lifecycle.py` | Full intent lifecycle: create → evaluate → state transitions → delete |
| `test_negotiation.py` | All three TMF921A §4.2 negotiation flows (ProbeIntent, Judge/Preference, Best/Propose) |
| `test_notifications.py` | Event fan-out to hub subscribers — all 10 event types, TMF envelope validation |
| `test_observations.py` | Observation POST → OODA trigger → evaluation cycle |
| `test_hsi_eval_conditions.py` | BBF HSI intent expression — bandwidth, latency, jitter, packet-loss conditions |

### Contract tests only (Schemathesis)

```bash
pytest tests/contract/ -v
```

Validates that every API response conforms to the OAS spec at `docs/spec/TMF921_Intent_Management_v5.0.0_oas.yaml`. Schemathesis generates requests automatically from the schema and checks for 5xx errors.

---

## Running a single test file

```bash
pytest tests/unit/test_evaluator.py -v
pytest tests/integration/test_negotiation.py -v
```

## Running a single test by name

```bash
pytest tests/unit/test_evaluator.py -v -k "test_quanat_least_passes"
```

---

## Coverage report

```bash
pytest tests/ --cov=src --cov-report=html
open htmlcov/index.html
```

---

## Environment variables affecting tests

Tests mock all Fuseki calls — no environment variables are required. If you want to override evaluation behaviour, these are respected:

| Variable | Effect on tests |
|---|---|
| `EVAL_TIMEOUT_SECONDS` | Controls the evaluation wall-time limit (default `30`) |
| `EVAL_MAX_TURTLE_BYTES` | Controls max expression size guard (default `524288`) |
| `HANDLER_LIMITS_JSON` | Fallback bounds for Flow 3 Best/Propose tests — set to e.g. `'{"quanatLeast": 100.0}'` |
