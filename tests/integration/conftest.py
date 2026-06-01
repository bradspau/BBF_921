"""
Integration test fixtures.

All tests use FastAPI TestClient against the real app stack.
Fuseki HTTP calls are intercepted by respx — no live Fuseki required.
"""
from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from src.main import app

FUSEKI = "http://localhost:3030"
DATASET = "tmf921"

BASE = "/tmf-api/intentManagement/v5"


# ── SPARQL response builders ──────────────────────────────────────────────────

def sparql_bindings(*rows: dict) -> dict:
    return {"results": {"bindings": list(rows)}}


def ask_response(value: bool) -> dict:
    return {"boolean": value}


def intent_row(
    intent_id: str,
    name: str = "Test Intent",
    lifecycle_status: str = "ACKNOWLEDGED",
    expr_type: str = "JsonLdExpression",
    expr_value: str = '{"@context":{}}',
) -> dict:
    return {
        "id":              {"type": "literal", "value": intent_id},
        "href":            {"type": "literal", "value": f"http://tmforum.org/tmf-api/intentManagement/v5/intent/{intent_id}"},
        "name":            {"type": "literal", "value": name},
        "type":            {"type": "uri",     "value": "http://tmforum.org/api/v5/Intent"},
        "baseType":        {"type": "literal", "value": "Intent"},
        "lifecycleStatus": {"type": "literal", "value": lifecycle_status},
        "created":         {"type": "literal", "value": "2024-01-01T00:00:00+00:00"},
        "modified":        {"type": "literal", "value": "2024-01-01T00:00:00+00:00"},
        "exprType":        {"type": "uri",     "value": f"http://tmforum.org/api/v5/{expr_type}"},
        "exprIri":         {"type": "literal", "value": "http://tio.example.org/model/v1"},
        "exprValue":       {"type": "literal", "value": expr_value},
    }


def spec_row(spec_id: str, name: str = "Test Spec") -> dict:
    return {
        "id":       {"type": "literal", "value": spec_id},
        "href":     {"type": "literal", "value": f"http://tmforum.org/tmf-api/intentManagement/v5/intentSpecification/{spec_id}"},
        "name":     {"type": "literal", "value": name},
        "type":     {"type": "uri",     "value": "http://tmforum.org/api/v5/IntentSpecification"},
        "created":  {"type": "literal", "value": "2024-01-01T00:00:00+00:00"},
        "modified": {"type": "literal", "value": "2024-01-01T00:00:00+00:00"},
    }


def hub_row(hub_id: str, callback: str = "http://listener.example.com/events") -> dict:
    return {
        "id":       {"type": "literal", "value": hub_id},
        "href":     {"type": "literal", "value": f"http://tmforum.org/tmf-api/intentManagement/v5/hub/{hub_id}"},
        "callback": {"type": "literal", "value": callback},
    }


def count_row(n: int) -> dict:
    return {"count": {"type": "literal", "value": str(n)}}


def sequential(*responses: httpx.Response):
    """Return a respx side_effect that yields responses in order, repeating the last."""
    items = list(responses)

    def _side_effect(request, **_):
        if len(items) > 1:
            return items.pop(0)
        return items[0]

    return _side_effect


# ── App fixture ───────────────────────────────────────────────────────────────

@pytest.fixture
def tc():
    """TestClient that exercises the full app stack (services + repositories)."""
    with TestClient(app, raise_server_exceptions=True) as client:
        yield client
