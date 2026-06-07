"""
Unit tests for the TMF921 repository layer — Phase 2.

All SPARQL HTTP calls are intercepted by respx; no live Fuseki required.
Each test class covers a single repository and exercises: create, get, list,
update (where applicable), delete, and the "not found" branches.
"""
from __future__ import annotations

import json
import pytest
import respx
import httpx

from src.graph.store import FusekiClient
from src.graph.repositories.base_repository import BaseRepository
from src.graph.repositories.intent_repository import IntentRepository
from src.graph.repositories.intent_report_repository import IntentReportRepository
from src.graph.repositories.intent_spec_repository import IntentSpecRepository
from src.graph.repositories.hub_repository import HubRepository

FUSEKI = "http://localhost:3030"
DATASET = "tmf921"
SPARQL_URL = f"{FUSEKI}/{DATASET}/sparql"
UPDATE_URL = f"{FUSEKI}/{DATASET}/update"


# ── Shared fixtures ───────────────────────────────────────────────────────────

INTENT_ID = "abc-123"
REPORT_ID = "rpt-456"
SPEC_ID = "spec-789"
HUB_ID = "hub-001"

INTENT_DATA = {
    "id": INTENT_ID,
    "href": f"http://host/tmf-api/intentManagement/v5/intent/{INTENT_ID}",
    "@type": "Intent",
    "@baseType": "Intent",
    "@schemaLocation": None,
    "name": "My Intent",
    "description": "Test intent",
    "lifecycleStatus": "ACKNOWLEDGED",
    "creationDate": "2024-01-01T00:00:00+00:00",
    "lastUpdate": "2024-01-01T00:00:00+00:00",
    "statusChangeDate": None,
    "priority": None,
    "context": None,
    "version": None,
    "isBundle": False,
    "expression": {
        "@type": "JsonLdExpression",
        "iri": "http://tio.models.tmforum.org/tio/v3.4.0/IntentCommonModel",
        "expressionValue": {"@context": {"icm": "http://example.org/"}},
    },
}

TURTLE_INTENT_DATA = {
    **INTENT_DATA,
    "id": "turtle-1",
    "expression": {
        "@type": "TurtleExpression",
        "iri": "http://tio.models.tmforum.org/tio/v1.0.0/IntentCommonModel",
        "expressionValue": "@prefix ex: <urn:ex:> . ex:a a ex:B .",
    },
}

PROBE_INTENT_DATA = {
    **INTENT_DATA,
    "id": "probe-1",
    "@type": "ProbeIntent",
}

REPORT_DATA = {
    "id": REPORT_ID,
    "href": f"http://host/tmf-api/intentManagement/v5/intent/{INTENT_ID}/intentReport/{REPORT_ID}",
    "@type": "IntentReport",
    "name": "My Report",
    "creationDate": "2024-01-01T00:00:00+00:00",
    "expression": {
        "@type": "JsonLdExpression",
        "iri": "http://tio.models.tmforum.org/tio/v3.4.0/IntentCommonModel",
        "expressionValue": {"status": "compliant"},
    },
}

SPEC_DATA = {
    "id": SPEC_ID,
    "href": f"http://host/tmf-api/intentManagement/v5/intentSpecification/{SPEC_ID}",
    "@type": "IntentSpecification",
    "@baseType": "EntitySpecification",
    "@schemaLocation": None,
    "name": "My Spec",
    "description": "A spec",
    "lifecycleStatus": "ACTIVE",
    "lastUpdate": "2024-01-01T00:00:00+00:00",
    "version": "1.0",
    "isBundle": False,
}

HUB_DATA = {
    "id": HUB_ID,
    "href": f"http://host/tmf-api/intentManagement/v5/hub/{HUB_ID}",
    "callback": "http://listener.example.com/events",
    "query": "eventType=intentCreateEvent",
}


# ── Binding factories ─────────────────────────────────────────────────────────

def _intent_binding(
    intent_id: str = INTENT_ID,
    type_local: str = "Intent",
    expr_type: str = "JsonLdExpression",
    expr_value: str | None = None,
) -> dict:
    if expr_value is None:
        expr_value = json.dumps({"@context": {"icm": "http://example.org/"}})
    return {
        "id":              {"type": "literal", "value": intent_id},
        "href":            {"type": "literal", "value": f"http://host/intent/{intent_id}"},
        "name":            {"type": "literal", "value": "My Intent"},
        "type":            {"type": "uri",     "value": f"http://tmforum.org/api/v5/{type_local}"},
        "lifecycleStatus": {"type": "literal", "value": "ACKNOWLEDGED"},
        "created":         {"type": "literal", "value": "2024-01-01T00:00:00+00:00",
                            "datatype": "http://www.w3.org/2001/XMLSchema#dateTime"},
        "modified":        {"type": "literal", "value": "2024-01-01T00:00:00+00:00",
                            "datatype": "http://www.w3.org/2001/XMLSchema#dateTime"},
        "exprType":        {"type": "uri",     "value": f"http://tmforum.org/api/v5/{expr_type}"},
        "exprIri":         {"type": "literal", "value": "http://tio.models.tmforum.org/tio/v3.4.0/IntentCommonModel"},
        "exprValue":       {"type": "literal", "value": expr_value,
                            "datatype": "http://www.w3.org/2001/XMLSchema#string"},
    }


def _bindings(rows: list[dict]) -> dict:
    return {"results": {"bindings": rows}}


def _count_binding(n: int) -> dict:
    return {"results": {"bindings": [
        {"count": {"type": "literal", "value": str(n),
                   "datatype": "http://www.w3.org/2001/XMLSchema#integer"}}
    ]}}


def _sparql_ok() -> httpx.Response:
    return httpx.Response(200)


def _ask(value: bool) -> dict:
    return {"boolean": value}


# ── BaseRepository ────────────────────────────────────────────────────────────

class TestBaseRepository:
    def _make_repo(self, client: FusekiClient) -> BaseRepository:
        return BaseRepository(client)

    def test_esc_quotes_and_backslash(self):
        assert BaseRepository._esc('say "hi"') == r'say \"hi\"'
        assert BaseRepository._esc("back\\slash") == r"back\\slash"

    def test_esc_newline_and_tab(self):
        assert BaseRepository._esc("a\nb") == r"a\nb"
        assert BaseRepository._esc("a\tb") == r"a\tb"

    def test_local_name_from_uri(self):
        assert BaseRepository._local("http://tmforum.org/api/v5/Intent") == "Intent"
        assert BaseRepository._local("http://ex.org/foo#Bar") == "Bar"

    def test_v_returns_value(self):
        b = {"foo": {"value": "bar"}}
        assert BaseRepository._v(b, "foo") == "bar"

    def test_v_returns_none_when_missing(self):
        assert BaseRepository._v({}, "missing") is None

    def test_vbool_true(self):
        b = {"x": {"value": "true"}}
        assert BaseRepository._vbool(b, "x") is True

    def test_vbool_false(self):
        b = {"x": {"value": "false"}}
        assert BaseRepository._vbool(b, "x") is False

    def test_vbool_none_when_missing(self):
        assert BaseRepository._vbool({}, "x") is None

    def test_ser_expr_value_dict(self):
        d = {"@context": {"a": "b"}}
        result = BaseRepository._ser_expr_value(d)
        assert json.loads(result) == d

    def test_ser_expr_value_string(self):
        assert BaseRepository._ser_expr_value("turtle content") == "turtle content"

    def test_deser_jsonld(self):
        raw = '{"@context": {"a": "b"}}'
        result = BaseRepository._deser_expr_value("JsonLdExpression", raw)
        assert result == {"@context": {"a": "b"}}

    def test_deser_turtle(self):
        raw = "@prefix ex: <urn:ex:> . ex:a a ex:B ."
        result = BaseRepository._deser_expr_value("TurtleExpression", raw)
        assert result == raw

    def test_deser_none(self):
        assert BaseRepository._deser_expr_value("JsonLdExpression", None) is None

    def test_deser_invalid_json_returns_raw(self):
        raw = "not-json"
        result = BaseRepository._deser_expr_value("JsonLdExpression", raw)
        assert result == raw

    def test_opt_triple_none_returns_empty(self):
        assert BaseRepository._opt_triple("<s>", "tmf:name", None) == ""

    def test_opt_triple_value_returns_triple(self):
        result = BaseRepository._opt_triple("<s>", "tmf:name", "hello")
        assert "tmf:name" in result
        assert '"hello"' in result

    def test_opt_typed_triple_none_returns_empty(self):
        assert BaseRepository._opt_typed_triple("<s>", "dcterms:created", None, "xsd:dateTime") == ""

    def test_opt_typed_triple_value_returns_typed_triple(self):
        result = BaseRepository._opt_typed_triple("<s>", "dcterms:created", "2024-01-01T00:00:00", "xsd:dateTime")
        assert "^^xsd:dateTime" in result
        assert "2024-01-01T00:00:00" in result


# ── IntentRepository ──────────────────────────────────────────────────────────

class TestIntentRepository:

    @respx.mock
    async def test_create_executes_insert(self):
        route = respx.post(UPDATE_URL).mock(return_value=_sparql_ok())
        async with FusekiClient(FUSEKI, DATASET) as client:
            repo = IntentRepository(client)
            result = await repo.create(INTENT_DATA)
        assert route.called
        assert result["id"] == INTENT_ID

    @respx.mock
    async def test_create_turtle_expression(self):
        route = respx.post(UPDATE_URL).mock(return_value=_sparql_ok())
        async with FusekiClient(FUSEKI, DATASET) as client:
            repo = IntentRepository(client)
            result = await repo.create(TURTLE_INTENT_DATA)
        assert route.called
        assert result["expression"]["@type"] == "TurtleExpression"

    @respx.mock
    async def test_create_probe_intent(self):
        route = respx.post(UPDATE_URL).mock(return_value=_sparql_ok())
        async with FusekiClient(FUSEKI, DATASET) as client:
            repo = IntentRepository(client)
            result = await repo.create(PROBE_INTENT_DATA)
        assert route.called
        assert result["@type"] == "ProbeIntent"

    @respx.mock
    async def test_get_by_id_found(self):
        row = _intent_binding()
        respx.post(SPARQL_URL).mock(return_value=httpx.Response(200, json=_bindings([row])))
        async with FusekiClient(FUSEKI, DATASET) as client:
            repo = IntentRepository(client)
            result = await repo.get_by_id(INTENT_ID)
        assert result is not None
        assert result["id"] == INTENT_ID
        assert result["@type"] == "Intent"
        assert result["lifecycleStatus"] == "ACKNOWLEDGED"

    @respx.mock
    async def test_get_by_id_deserialises_jsonld_expression(self):
        expr_val = json.dumps({"@context": {"icm": "http://example.org/"}})
        row = _intent_binding(expr_type="JsonLdExpression", expr_value=expr_val)
        respx.post(SPARQL_URL).mock(return_value=httpx.Response(200, json=_bindings([row])))
        async with FusekiClient(FUSEKI, DATASET) as client:
            repo = IntentRepository(client)
            result = await repo.get_by_id(INTENT_ID)
        assert isinstance(result["expression"]["expressionValue"], dict)

    @respx.mock
    async def test_get_by_id_keeps_turtle_as_string(self):
        turtle = "@prefix ex: <urn:ex:> . ex:a a ex:B ."
        row = _intent_binding(expr_type="TurtleExpression", expr_value=turtle)
        respx.post(SPARQL_URL).mock(return_value=httpx.Response(200, json=_bindings([row])))
        async with FusekiClient(FUSEKI, DATASET) as client:
            repo = IntentRepository(client)
            result = await repo.get_by_id(INTENT_ID)
        assert isinstance(result["expression"]["expressionValue"], str)

    @respx.mock
    async def test_get_by_id_not_found_returns_none(self):
        respx.post(SPARQL_URL).mock(return_value=httpx.Response(200, json=_bindings([])))
        async with FusekiClient(FUSEKI, DATASET) as client:
            repo = IntentRepository(client)
            result = await repo.get_by_id("nonexistent")
        assert result is None

    @respx.mock
    async def test_get_by_id_probe_intent(self):
        row = _intent_binding(type_local="ProbeIntent")
        respx.post(SPARQL_URL).mock(return_value=httpx.Response(200, json=_bindings([row])))
        async with FusekiClient(FUSEKI, DATASET) as client:
            repo = IntentRepository(client)
            result = await repo.get_by_id(INTENT_ID)
        assert result["@type"] == "ProbeIntent"

    @respx.mock
    async def test_list_returns_items_and_count(self):
        row = _intent_binding()

        def router(request):
            body = request.content.decode()
            if "COUNT" in body:
                return httpx.Response(200, json=_count_binding(3))
            return httpx.Response(200, json=_bindings([row]))

        respx.post(SPARQL_URL).mock(side_effect=router)
        async with FusekiClient(FUSEKI, DATASET) as client:
            repo = IntentRepository(client)
            items, total = await repo.list(limit=20, offset=0)
        assert total == 3
        assert len(items) == 1
        assert items[0]["id"] == INTENT_ID

    @respx.mock
    async def test_list_empty_returns_zero(self):
        respx.post(SPARQL_URL).mock(return_value=httpx.Response(200, json=_count_binding(0)))
        async with FusekiClient(FUSEKI, DATASET) as client:
            repo = IntentRepository(client)
            items, total = await repo.list()
        assert total == 0
        assert items == []

    @respx.mock
    async def test_list_with_lifecycle_filter(self):
        row = _intent_binding()

        def router(request):
            body = request.content.decode()
            if "COUNT" in body:
                return httpx.Response(200, json=_count_binding(1))
            return httpx.Response(200, json=_bindings([row]))

        respx.post(SPARQL_URL).mock(side_effect=router)
        async with FusekiClient(FUSEKI, DATASET) as client:
            repo = IntentRepository(client)
            items, total = await repo.list(filters={"lifecycleStatus": "ACKNOWLEDGED"})
        assert total == 1

    @respx.mock
    async def test_list_with_type_filter(self):
        row = _intent_binding(type_local="ProbeIntent")

        def router(request):
            body = request.content.decode()
            if "COUNT" in body:
                return httpx.Response(200, json=_count_binding(1))
            return httpx.Response(200, json=_bindings([row]))

        respx.post(SPARQL_URL).mock(side_effect=router)
        async with FusekiClient(FUSEKI, DATASET) as client:
            repo = IntentRepository(client)
            items, total = await repo.list(filters={"@type": "ProbeIntent"})
        assert total == 1

    @respx.mock
    async def test_update_returns_updated_dict(self):
        row = _intent_binding()
        respx.post(UPDATE_URL).mock(return_value=_sparql_ok())
        respx.post(SPARQL_URL).mock(return_value=httpx.Response(200, json=_bindings([row])))
        async with FusekiClient(FUSEKI, DATASET) as client:
            repo = IntentRepository(client)
            result = await repo.update(
                INTENT_ID,
                {"name": "Updated", "lifecycleStatus": "ACTIVE"},
                "2024-06-01T00:00:00+00:00",
            )
        assert result is not None
        assert result["id"] == INTENT_ID

    @respx.mock
    async def test_update_returns_none_when_not_found(self):
        respx.post(UPDATE_URL).mock(return_value=_sparql_ok())
        respx.post(SPARQL_URL).mock(return_value=httpx.Response(200, json=_bindings([])))
        async with FusekiClient(FUSEKI, DATASET) as client:
            repo = IntentRepository(client)
            result = await repo.update(
                "nonexistent", {"name": "X"}, "2024-06-01T00:00:00+00:00"
            )
        assert result is None

    @respx.mock
    async def test_update_typed_field_generates_xsd_datatype(self):
        """statusChangeDate has typed=True — ensures the ^^xsd:dateTime branch is reached."""
        row = _intent_binding()
        respx.post(UPDATE_URL).mock(return_value=_sparql_ok())
        respx.post(SPARQL_URL).mock(return_value=httpx.Response(200, json=_bindings([row])))
        async with FusekiClient(FUSEKI, DATASET) as client:
            repo = IntentRepository(client)
            result = await repo.update(
                INTENT_ID,
                {"statusChangeDate": "2024-06-01T00:00:00+00:00"},
                "2024-06-01T00:00:00+00:00",
            )
        assert result is not None

    @respx.mock
    async def test_update_expression_patch(self):
        row = _intent_binding()
        respx.post(UPDATE_URL).mock(return_value=_sparql_ok())
        respx.post(SPARQL_URL).mock(return_value=httpx.Response(200, json=_bindings([row])))
        async with FusekiClient(FUSEKI, DATASET) as client:
            repo = IntentRepository(client)
            result = await repo.update(
                INTENT_ID,
                {"expression": {
                    "@type": "TurtleExpression",
                    "iri": "http://new-iri",
                    "expressionValue": "new turtle",
                }},
                "2024-06-01T00:00:00+00:00",
            )
        assert result is not None

    @respx.mock
    async def test_update_expression_patch_writes_has_expression_link(self):
        """Regression: PATCH adding/replacing an expression must always emit
        the tmf:hasExpression link so get_by_id() and the handler can find it."""
        from urllib.parse import unquote_plus

        captured: list[str] = []

        def capture_update(request):
            # Body is form-encoded: "update=<url-encoded-sparql>"
            raw = request.content.decode()
            sparql = unquote_plus(raw.removeprefix("update="))
            captured.append(sparql)
            return _sparql_ok()

        row = _intent_binding()
        respx.post(UPDATE_URL).mock(side_effect=capture_update)
        respx.post(SPARQL_URL).mock(return_value=httpx.Response(200, json=_bindings([row])))
        async with FusekiClient(FUSEKI, DATASET) as client:
            repo = IntentRepository(client)
            await repo.update(
                INTENT_ID,
                {"expression": {
                    "@type": "TurtleExpression",
                    "iri": "http://new-iri",
                    "expressionValue": "@prefix ex: <urn:ex:> . ex:a a ex:B .",
                }},
                "2024-06-01T00:00:00+00:00",
            )
        assert captured, "UPDATE was never called"
        sparql = captured[0]
        # The INSERT block must include the hasExpression link so the intent
        # node points to the expression node after the PATCH — both when an
        # expression already existed AND when it did not.
        insert_block = sparql[sparql.index("INSERT"):sparql.index("WHERE")]
        assert "tmf:hasExpression" in insert_block

    @respx.mock
    async def test_delete_existing_intent(self):
        respx.post(SPARQL_URL).mock(return_value=httpx.Response(200, json=_ask(True)))
        respx.post(UPDATE_URL).mock(return_value=_sparql_ok())
        async with FusekiClient(FUSEKI, DATASET) as client:
            repo = IntentRepository(client)
            deleted = await repo.delete(INTENT_ID)
        assert deleted is True

    @respx.mock
    async def test_delete_nonexistent_intent(self):
        respx.post(SPARQL_URL).mock(return_value=httpx.Response(200, json=_ask(False)))
        async with FusekiClient(FUSEKI, DATASET) as client:
            repo = IntentRepository(client)
            deleted = await repo.delete("nonexistent")
        assert deleted is False

    @respx.mock
    async def test_write_state_change(self):
        route = respx.post(UPDATE_URL).mock(return_value=_sparql_ok())
        async with FusekiClient(FUSEKI, DATASET) as client:
            repo = IntentRepository(client)
            await repo.write_state_change(
                INTENT_ID, "sc-uuid-1",
                "ACKNOWLEDGED", "ACTIVE",
                "2024-01-01T00:00:00+00:00",
            )
        assert route.called

    @respx.mock
    async def test_create_intent_with_status_change_date(self):
        route = respx.post(UPDATE_URL).mock(return_value=_sparql_ok())
        data = {**INTENT_DATA, "statusChangeDate": "2024-01-01T00:00:00+00:00"}
        async with FusekiClient(FUSEKI, DATASET) as client:
            repo = IntentRepository(client)
            result = await repo.create(data)
        assert route.called
        assert result["statusChangeDate"] == "2024-01-01T00:00:00+00:00"


# ── IntentReportRepository ────────────────────────────────────────────────────

def _report_binding(report_id: str = REPORT_ID, intent_id: str = INTENT_ID) -> dict:
    return {
        "id":          {"type": "literal", "value": report_id},
        "href":        {"type": "literal", "value": f"http://host/intent/{intent_id}/report/{report_id}"},
        "name":        {"type": "literal", "value": "My Report"},
        "type":        {"type": "uri",     "value": "http://tmforum.org/api/v5/IntentReport"},
        "created":     {"type": "literal", "value": "2024-01-01T00:00:00+00:00"},
        "parentIntent":{"type": "uri",     "value": f"http://tmforum.org/api/v5/intents/{intent_id}"},
        "exprType":    {"type": "uri",     "value": "http://tmforum.org/api/v5/JsonLdExpression"},
        "exprIri":     {"type": "literal", "value": "http://tio.models.tmforum.org/tio/v3.4.0/IntentCommonModel"},
        "exprValue":   {"type": "literal", "value": '{"status":"compliant"}',
                        "datatype": "http://www.w3.org/2001/XMLSchema#string"},
    }


class TestIntentReportRepository:

    @respx.mock
    async def test_create_executes_insert(self):
        route = respx.post(UPDATE_URL).mock(return_value=_sparql_ok())
        async with FusekiClient(FUSEKI, DATASET) as client:
            repo = IntentReportRepository(client)
            result = await repo.create(INTENT_ID, REPORT_DATA)
        assert route.called
        assert result["id"] == REPORT_ID

    @respx.mock
    async def test_create_with_base_type_and_handling_state(self):
        route = respx.post(UPDATE_URL).mock(return_value=_sparql_ok())
        data = {
            **REPORT_DATA,
            "@baseType": "IntentReport",
            "intentHandlingState": "FULFILLED",
            "intentHandlingReason": "all conditions met",
        }
        async with FusekiClient(FUSEKI, DATASET) as client:
            repo = IntentReportRepository(client)
            result = await repo.create(INTENT_ID, data)
        assert route.called
        assert result["@baseType"] == "IntentReport"
        assert result["intentHandlingState"] == "FULFILLED"

    @respx.mock
    async def test_get_by_id_found(self):
        row = _report_binding()
        respx.post(SPARQL_URL).mock(return_value=httpx.Response(200, json=_bindings([row])))
        async with FusekiClient(FUSEKI, DATASET) as client:
            repo = IntentReportRepository(client)
            result = await repo.get_by_id(INTENT_ID, REPORT_ID)
        assert result is not None
        assert result["id"] == REPORT_ID
        assert result["@type"] == "IntentReport"

    @respx.mock
    async def test_get_by_id_not_found(self):
        respx.post(SPARQL_URL).mock(return_value=httpx.Response(200, json=_bindings([])))
        async with FusekiClient(FUSEKI, DATASET) as client:
            repo = IntentReportRepository(client)
            result = await repo.get_by_id(INTENT_ID, "nonexistent")
        assert result is None

    @respx.mock
    async def test_get_by_id_expr_deserialised(self):
        row = _report_binding()
        respx.post(SPARQL_URL).mock(return_value=httpx.Response(200, json=_bindings([row])))
        async with FusekiClient(FUSEKI, DATASET) as client:
            repo = IntentReportRepository(client)
            result = await repo.get_by_id(INTENT_ID, REPORT_ID)
        assert isinstance(result["expression"]["expressionValue"], dict)

    @respx.mock
    async def test_list_returns_items_and_count(self):
        row = _report_binding()

        def router(request):
            body = request.content.decode()
            if "COUNT" in body:
                return httpx.Response(200, json=_count_binding(2))
            return httpx.Response(200, json=_bindings([row]))

        respx.post(SPARQL_URL).mock(side_effect=router)
        async with FusekiClient(FUSEKI, DATASET) as client:
            repo = IntentReportRepository(client)
            items, total = await repo.list(INTENT_ID)
        assert total == 2
        assert len(items) == 1

    @respx.mock
    async def test_list_empty(self):
        respx.post(SPARQL_URL).mock(return_value=httpx.Response(200, json=_count_binding(0)))
        async with FusekiClient(FUSEKI, DATASET) as client:
            repo = IntentReportRepository(client)
            items, total = await repo.list(INTENT_ID)
        assert total == 0
        assert items == []

    @respx.mock
    async def test_delete_existing_report(self):
        row = _report_binding()
        respx.post(SPARQL_URL).mock(return_value=httpx.Response(200, json=_bindings([row])))
        respx.post(UPDATE_URL).mock(return_value=_sparql_ok())
        async with FusekiClient(FUSEKI, DATASET) as client:
            repo = IntentReportRepository(client)
            deleted = await repo.delete(INTENT_ID, REPORT_ID)
        assert deleted is True

    @respx.mock
    async def test_delete_not_found(self):
        respx.post(SPARQL_URL).mock(return_value=httpx.Response(200, json=_bindings([])))
        async with FusekiClient(FUSEKI, DATASET) as client:
            repo = IntentReportRepository(client)
            deleted = await repo.delete(INTENT_ID, "nonexistent")
        assert deleted is False


# ── IntentSpecRepository ──────────────────────────────────────────────────────

def _spec_binding(spec_id: str = SPEC_ID) -> dict:
    return {
        "id":              {"type": "literal", "value": spec_id},
        "href":            {"type": "literal", "value": f"http://host/intentSpecification/{spec_id}"},
        "name":            {"type": "literal", "value": "My Spec"},
        "type":            {"type": "uri",     "value": "http://tmforum.org/api/v5/IntentSpecification"},
        "lifecycleStatus": {"type": "literal", "value": "ACTIVE"},
        "modified":        {"type": "literal", "value": "2024-01-01T00:00:00+00:00",
                            "datatype": "http://www.w3.org/2001/XMLSchema#dateTime"},
        "version":         {"type": "literal", "value": "1.0"},
    }


class TestIntentSpecRepository:

    @respx.mock
    async def test_create_executes_insert(self):
        route = respx.post(UPDATE_URL).mock(return_value=_sparql_ok())
        async with FusekiClient(FUSEKI, DATASET) as client:
            repo = IntentSpecRepository(client)
            result = await repo.create(SPEC_DATA)
        assert route.called
        assert result["id"] == SPEC_ID

    @respx.mock
    async def test_get_by_id_found(self):
        row = _spec_binding()
        respx.post(SPARQL_URL).mock(return_value=httpx.Response(200, json=_bindings([row])))
        async with FusekiClient(FUSEKI, DATASET) as client:
            repo = IntentSpecRepository(client)
            result = await repo.get_by_id(SPEC_ID)
        assert result is not None
        assert result["id"] == SPEC_ID
        assert result["@type"] == "IntentSpecification"
        assert result["lifecycleStatus"] == "ACTIVE"

    @respx.mock
    async def test_get_by_id_not_found(self):
        respx.post(SPARQL_URL).mock(return_value=httpx.Response(200, json=_bindings([])))
        async with FusekiClient(FUSEKI, DATASET) as client:
            repo = IntentSpecRepository(client)
            result = await repo.get_by_id("nonexistent")
        assert result is None

    @respx.mock
    async def test_list_returns_items_and_count(self):
        row = _spec_binding()

        def router(request):
            body = request.content.decode()
            if "COUNT" in body:
                return httpx.Response(200, json=_count_binding(1))
            return httpx.Response(200, json=_bindings([row]))

        respx.post(SPARQL_URL).mock(side_effect=router)
        async with FusekiClient(FUSEKI, DATASET) as client:
            repo = IntentSpecRepository(client)
            items, total = await repo.list()
        assert total == 1
        assert items[0]["id"] == SPEC_ID

    @respx.mock
    async def test_list_empty(self):
        respx.post(SPARQL_URL).mock(return_value=httpx.Response(200, json=_count_binding(0)))
        async with FusekiClient(FUSEKI, DATASET) as client:
            repo = IntentSpecRepository(client)
            items, total = await repo.list()
        assert total == 0

    @respx.mock
    async def test_list_with_filter(self):
        row = _spec_binding()

        def router(request):
            body = request.content.decode()
            if "COUNT" in body:
                return httpx.Response(200, json=_count_binding(1))
            return httpx.Response(200, json=_bindings([row]))

        respx.post(SPARQL_URL).mock(side_effect=router)
        async with FusekiClient(FUSEKI, DATASET) as client:
            repo = IntentSpecRepository(client)
            items, total = await repo.list(filters={"lifecycleStatus": "ACTIVE"})
        assert total == 1

    @respx.mock
    async def test_update_returns_updated_dict(self):
        row = _spec_binding()
        respx.post(UPDATE_URL).mock(return_value=_sparql_ok())
        respx.post(SPARQL_URL).mock(return_value=httpx.Response(200, json=_bindings([row])))
        async with FusekiClient(FUSEKI, DATASET) as client:
            repo = IntentSpecRepository(client)
            result = await repo.update(
                SPEC_ID, {"name": "Updated Spec"}, "2024-06-01T00:00:00+00:00"
            )
        assert result is not None

    @respx.mock
    async def test_update_returns_none_when_not_found(self):
        respx.post(UPDATE_URL).mock(return_value=_sparql_ok())
        respx.post(SPARQL_URL).mock(return_value=httpx.Response(200, json=_bindings([])))
        async with FusekiClient(FUSEKI, DATASET) as client:
            repo = IntentSpecRepository(client)
            result = await repo.update(
                "nonexistent", {"name": "X"}, "2024-06-01T00:00:00+00:00"
            )
        assert result is None

    @respx.mock
    async def test_delete_existing_spec(self):
        respx.post(SPARQL_URL).mock(return_value=httpx.Response(200, json=_ask(True)))
        respx.post(UPDATE_URL).mock(return_value=_sparql_ok())
        async with FusekiClient(FUSEKI, DATASET) as client:
            repo = IntentSpecRepository(client)
            deleted = await repo.delete(SPEC_ID)
        assert deleted is True

    @respx.mock
    async def test_delete_nonexistent_spec(self):
        respx.post(SPARQL_URL).mock(return_value=httpx.Response(200, json=_ask(False)))
        async with FusekiClient(FUSEKI, DATASET) as client:
            repo = IntentSpecRepository(client)
            deleted = await repo.delete("nonexistent")
        assert deleted is False


# ── HubRepository ─────────────────────────────────────────────────────────────

def _hub_binding(hub_id: str = HUB_ID) -> dict:
    return {
        "id":       {"type": "literal", "value": hub_id},
        "href":     {"type": "literal", "value": f"http://host/hub/{hub_id}"},
        "callback": {"type": "literal", "value": "http://listener.example.com/events"},
        "query":    {"type": "literal", "value": "eventType=intentCreateEvent"},
    }


class TestHubRepository:

    @respx.mock
    async def test_create_with_query(self):
        route = respx.post(UPDATE_URL).mock(return_value=_sparql_ok())
        async with FusekiClient(FUSEKI, DATASET) as client:
            repo = HubRepository(client)
            result = await repo.create(HUB_DATA)
        assert route.called
        assert result["id"] == HUB_ID

    @respx.mock
    async def test_create_without_query(self):
        route = respx.post(UPDATE_URL).mock(return_value=_sparql_ok())
        data = {**HUB_DATA, "query": None}
        async with FusekiClient(FUSEKI, DATASET) as client:
            repo = HubRepository(client)
            result = await repo.create(data)
        assert route.called

    @respx.mock
    async def test_get_by_id_found(self):
        row = _hub_binding()
        respx.post(SPARQL_URL).mock(return_value=httpx.Response(200, json=_bindings([row])))
        async with FusekiClient(FUSEKI, DATASET) as client:
            repo = HubRepository(client)
            result = await repo.get_by_id(HUB_ID)
        assert result is not None
        assert result["callback"] == "http://listener.example.com/events"

    @respx.mock
    async def test_get_by_id_not_found(self):
        respx.post(SPARQL_URL).mock(return_value=httpx.Response(200, json=_bindings([])))
        async with FusekiClient(FUSEKI, DATASET) as client:
            repo = HubRepository(client)
            result = await repo.get_by_id("nonexistent")
        assert result is None

    @respx.mock
    async def test_list_all_returns_hubs(self):
        rows = [_hub_binding("h1"), _hub_binding("h2")]
        respx.post(SPARQL_URL).mock(return_value=httpx.Response(200, json=_bindings(rows)))
        async with FusekiClient(FUSEKI, DATASET) as client:
            repo = HubRepository(client)
            hubs = await repo.list_all()
        assert len(hubs) == 2

    @respx.mock
    async def test_list_all_empty(self):
        respx.post(SPARQL_URL).mock(return_value=httpx.Response(200, json=_bindings([])))
        async with FusekiClient(FUSEKI, DATASET) as client:
            repo = HubRepository(client)
            hubs = await repo.list_all()
        assert hubs == []

    @respx.mock
    async def test_delete_existing_hub(self):
        respx.post(SPARQL_URL).mock(return_value=httpx.Response(200, json=_ask(True)))
        respx.post(UPDATE_URL).mock(return_value=_sparql_ok())
        async with FusekiClient(FUSEKI, DATASET) as client:
            repo = HubRepository(client)
            deleted = await repo.delete(HUB_ID)
        assert deleted is True

    @respx.mock
    async def test_delete_nonexistent_hub(self):
        respx.post(SPARQL_URL).mock(return_value=httpx.Response(200, json=_ask(False)))
        async with FusekiClient(FUSEKI, DATASET) as client:
            repo = HubRepository(client)
            deleted = await repo.delete("nonexistent")
        assert deleted is False
