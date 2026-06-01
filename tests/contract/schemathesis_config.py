"""
Schemathesis contract tests for the TMF921 Intent Management API.

Verifies that the running API's responses conform to the OAS spec at
docs/spec/TMF921_Intent_Management_v5.0.0.oas.yaml.

Strategy:
  - Load schema from the canonical OAS file (not FastAPI's auto-generated spec).
  - Route requests through the FastAPI ASGI app via schemathesis ASGI transport.
  - Mock all Fuseki HTTP calls with respx so tests run without a live database.
  - Scope to core API tags; /listener endpoints (inbound webhook receivers) excluded.
  - Check: no server errors (5xx).  Status codes in other ranges (200, 400, 404)
    are all valid API responses given the mocked Fuseki state.
"""
from __future__ import annotations

import warnings

import httpx
import respx
import schemathesis
from schemathesis.checks import not_a_server_error

from src.main import app

OAS_PATH = "docs/spec/TMF921_Intent_Management_v5.0.0.oas.yaml"
_BASE = "/tmf-api/intentManagement/v5"

FUSEKI = "http://localhost:3030"
DATASET = "tmf921"

# ── Load schema ────────────────────────────────────────────────────────────────
# tag= is deprecated in schemathesis 4.0 but is the correct filter in 3.39.
# Suppress the warning so CI output stays clean.

with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)
    schema = schemathesis.from_path(
        OAS_PATH,
        app=app,
        base_url=f"http://testserver{_BASE}",
        validate_schema=False,
        # Scoped tags cover all endpoints we own; /listener is excluded.
        tag=["intent", "intentReport", "intentSpecification", "events subscription"],
    )


# ── Blanket Fuseki mock ───────────────────────────────────────────────────────

_EMPTY_SELECT = {"results": {"bindings": []}}
_EMPTY_COUNT  = {"results": {"bindings": [{"count": {"type": "literal", "value": "0"}}]}}
_ASK_FALSE    = {"boolean": False}


def _default_sparql(request: httpx.Request) -> httpx.Response:
    """
    Return sensible defaults for every Fuseki SPARQL call.
      - ASK  → false  (resource not found → operations return 404)
      - COUNT → 0     (list operations return empty array)
      - SELECT → empty (individual GET returns 404)
    """
    body = request.content.decode(errors="replace")
    if "ASK" in body:
        return httpx.Response(200, json=_ASK_FALSE)
    if "COUNT" in body:
        return httpx.Response(200, json=_EMPTY_COUNT)
    return httpx.Response(200, json=_EMPTY_SELECT)


# ── Contract test ──────────────────────────────────────────────────────────────

@schema.parametrize()
@respx.mock
def test_api_conforms_to_oas(case):
    """
    Schemathesis generates requests from the OAS spec and sends them through
    the ASGI app.  We assert that no response is a server error (5xx).

    With the blanket Fuseki mock returning empty/false, most operations will
    return 400/404 — all documented in the OAS and all acceptable here.
    """
    respx.post(f"{FUSEKI}/{DATASET}/sparql").mock(side_effect=_default_sparql)
    respx.post(f"{FUSEKI}/{DATASET}/update").mock(return_value=httpx.Response(200))
    respx.post(f"{FUSEKI}/{DATASET}/data").mock(return_value=httpx.Response(200))
    respx.get(f"{FUSEKI}/$/ping").mock(return_value=httpx.Response(200))

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        response = case.call_asgi(app=app)

    case.validate_response(response, checks=(not_a_server_error,))
