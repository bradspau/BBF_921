"""
Unit tests for src/handler/evaluator.py and src/handler/dispatcher.py.

All HTTP calls are intercepted by respx; no live Fuseki required.

Evaluator flow:
  - Intent expression query  → tmf921/sparql
  - Turtle parsed with RDFLib and evaluated in Python (no Fuseki eval dataset)
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import respx
import httpx

import rdflib
from rdflib.namespace import RDF, RDFS

from src.graph.store import FusekiClient
from src.handler.evaluator import (
    evaluate_intent,
    evaluate_turtle_conditions,
    _derive_ext_types,
)
from src.handler.dispatcher import dispatch_evaluation, schedule_evaluation

FUSEKI    = "http://localhost:3030"
DATASET   = "tmf921"
INTENT_ID = "intent-aaa"

_INTENT_GRAPH = f"http://tmforum.org/api/v5/intents/{INTENT_ID}"


def _sparql_bindings(*bindings: dict) -> dict:
    return {"results": {"bindings": list(bindings)}}


def _turtle_expr_row(expr_value: str = "@prefix : <http://example.org/> .") -> dict:
    return {
        "exprType": {
            "type": "uri",
            "value": "http://tmforum.org/api/v5/TurtleExpression",
        },
        "exprValue": {"type": "literal", "value": expr_value},
    }


def _json_ld_expr_row() -> dict:
    return {
        "exprType": {
            "type": "uri",
            "value": "http://tmforum.org/api/v5/JsonLdExpression",
        },
        "exprValue": {"type": "literal", "value": '{"@context": {}}'},
    }


# ── evaluate_intent — no expression / wrong type ──────────────────────────────


class TestEvaluateIntentNoExpression:
    @respx.mock
    async def test_no_expression_rows_returns_degraded(self):
        respx.post(f"{FUSEKI}/{DATASET}/sparql").mock(
            return_value=httpx.Response(200, json=_sparql_bindings())
        )
        async with FusekiClient(FUSEKI, DATASET) as client:
            result = await evaluate_intent(INTENT_ID, client)
        assert result["intentHandlingState"] == "Degraded"
        assert "no expression" in (result.get("reason") or "").lower()

    @respx.mock
    async def test_json_ld_expression_returns_degraded(self):
        respx.post(f"{FUSEKI}/{DATASET}/sparql").mock(
            return_value=httpx.Response(200, json=_sparql_bindings(_json_ld_expr_row()))
        )
        async with FusekiClient(FUSEKI, DATASET) as client:
            result = await evaluate_intent(INTENT_ID, client)
        assert result["intentHandlingState"] == "Degraded"
        assert "JsonLdExpression" in (result.get("reason") or "")

    @respx.mock
    async def test_turtle_expr_with_empty_value_returns_degraded(self):
        row = _turtle_expr_row("")
        del row["exprValue"]
        respx.post(f"{FUSEKI}/{DATASET}/sparql").mock(
            return_value=httpx.Response(200, json=_sparql_bindings(row))
        )
        async with FusekiClient(FUSEKI, DATASET) as client:
            result = await evaluate_intent(INTENT_ID, client)
        assert result["intentHandlingState"] == "Degraded"


# ── evaluate_intent — Turtle expression evaluated in Python ───────────────────

QUAN = "http://tio.models.tmforum.org/tio/v3.6.0/QuantityOntology/"

_AT_LEAST_TURTLE = """\
@prefix quan: <http://tio.models.tmforum.org/tio/v3.6.0/QuantityOntology/> .
@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .
<urn:t:cmp> a quan:quanatLeast ;
    rdf:first <urn:t:obs> ;
    rdf:rest  <urn:t:rst> .
<urn:t:rst> rdf:first <urn:t:bnd> .
<urn:t:obs> rdf:value "{obs}"^^xsd:decimal .
<urn:t:bnd> rdf:value "{bnd}"^^xsd:decimal .
"""

_SMALLER_TURTLE = """\
@prefix quan: <http://tio.models.tmforum.org/tio/v3.6.0/QuantityOntology/> .
@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .
<urn:t:cmp> a quan:quansmaller ;
    rdf:first <urn:t:obs> ;
    rdf:rest  <urn:t:rst> .
<urn:t:rst> rdf:first <urn:t:bnd> .
<urn:t:obs> rdf:value "{obs}"^^xsd:decimal .
<urn:t:bnd> rdf:value "{bnd}"^^xsd:decimal .
"""


_OBS_GRAPH_URI = f"http://tmforum.org/api/v5/intents/{INTENT_ID}/observations"


def _mock_no_observations():
    """Mock the observation graph GSP GET to return 404 (no observations yet)."""
    respx.get(f"{FUSEKI}/{DATASET}/data").mock(return_value=httpx.Response(404))


class TestEvaluateIntentTurtleExpression:
    @respx.mock
    async def test_conditions_fulfilled_when_all_pass(self):
        """quanatLeast with obs>=bound → Fulfilled."""
        turtle = _AT_LEAST_TURTLE.format(obs="120", bnd="100")
        respx.post(f"{FUSEKI}/{DATASET}/sparql").mock(
            return_value=httpx.Response(200, json=_sparql_bindings(_turtle_expr_row(turtle)))
        )
        _mock_no_observations()
        async with FusekiClient(FUSEKI, DATASET) as client:
            result = await evaluate_intent(INTENT_ID, client)
        assert result["intentHandlingState"] == "Fulfilled"
        assert result.get("reason") is None

    @respx.mock
    async def test_conditions_degraded_when_any_fails(self):
        """quanatLeast with obs<bound → Degraded."""
        turtle = _AT_LEAST_TURTLE.format(obs="80", bnd="100")
        respx.post(f"{FUSEKI}/{DATASET}/sparql").mock(
            return_value=httpx.Response(200, json=_sparql_bindings(_turtle_expr_row(turtle)))
        )
        _mock_no_observations()
        async with FusekiClient(FUSEKI, DATASET) as client:
            result = await evaluate_intent(INTENT_ID, client)
        assert result["intentHandlingState"] == "Degraded"
        assert "Conditions not met" in (result.get("reason") or "")

    @respx.mock
    async def test_no_conditions_in_turtle_returns_degraded(self):
        """Turtle with no quantity conditions → Degraded."""
        turtle = "@prefix : <http://example.org/> ."
        respx.post(f"{FUSEKI}/{DATASET}/sparql").mock(
            return_value=httpx.Response(200, json=_sparql_bindings(_turtle_expr_row(turtle)))
        )
        _mock_no_observations()
        async with FusekiClient(FUSEKI, DATASET) as client:
            result = await evaluate_intent(INTENT_ID, client)
        assert result["intentHandlingState"] == "Degraded"
        assert "No quantity conditions" in (result.get("reason") or "")

    @respx.mock
    async def test_invalid_turtle_returns_degraded(self):
        """Malformed Turtle → parse error → Degraded."""
        turtle = "this is not valid turtle !!!"
        respx.post(f"{FUSEKI}/{DATASET}/sparql").mock(
            return_value=httpx.Response(200, json=_sparql_bindings(_turtle_expr_row(turtle)))
        )
        _mock_no_observations()
        async with FusekiClient(FUSEKI, DATASET) as client:
            result = await evaluate_intent(INTENT_ID, client)
        assert result["intentHandlingState"] == "Degraded"
        assert "parse error" in (result.get("reason") or "").lower()

    @respx.mock
    async def test_quansmaller_in_range_returns_fulfilled(self):
        """quansmaller with obs<bound → Fulfilled."""
        turtle = _SMALLER_TURTLE.format(obs="10", bnd="25")
        respx.post(f"{FUSEKI}/{DATASET}/sparql").mock(
            return_value=httpx.Response(200, json=_sparql_bindings(_turtle_expr_row(turtle)))
        )
        _mock_no_observations()
        async with FusekiClient(FUSEKI, DATASET) as client:
            result = await evaluate_intent(INTENT_ID, client)
        assert result["intentHandlingState"] == "Fulfilled"

    @respx.mock
    async def test_quansmaller_at_boundary_returns_degraded(self):
        """quansmaller with obs==bound → Degraded (strict less-than)."""
        turtle = _SMALLER_TURTLE.format(obs="25", bnd="25")
        respx.post(f"{FUSEKI}/{DATASET}/sparql").mock(
            return_value=httpx.Response(200, json=_sparql_bindings(_turtle_expr_row(turtle)))
        )
        _mock_no_observations()
        async with FusekiClient(FUSEKI, DATASET) as client:
            result = await evaluate_intent(INTENT_ID, client)
        assert result["intentHandlingState"] == "Degraded"

    @respx.mock
    async def test_observations_merged_and_resolved(self):
        """Metric ref in Turtle + matching observation in graph → Fulfilled."""
        expr_turtle = """\
@prefix quan: <http://tio.models.tmforum.org/tio/v3.6.0/QuantityOntology/> .
@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .
<urn:test:cond> a quan:quanatLeast ;
    rdf:first <urn:test:dl_metric> ;
    rdf:rest  [ rdf:first <urn:test:bound> ] .
<urn:test:bound> rdf:value "100"^^xsd:decimal .
"""
        obs_turtle = """\
@prefix met: <http://tio.models.tmforum.org/tio/v3.6.0/MetricsAndObservations/> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
<urn:test:obs1> a met:Observation ;
    met:observedMetric <urn:test:dl_metric> ;
    rdf:value "120"^^xsd:decimal ;
    met:obtainedAt "2026-06-04T10:00:00Z"^^xsd:dateTime .
"""
        respx.post(f"{FUSEKI}/{DATASET}/sparql").mock(
            return_value=httpx.Response(200, json=_sparql_bindings(_turtle_expr_row(expr_turtle)))
        )
        respx.get(f"{FUSEKI}/{DATASET}/data").mock(return_value=httpx.Response(200, text=obs_turtle))
        async with FusekiClient(FUSEKI, DATASET) as client:
            result = await evaluate_intent(INTENT_ID, client)
        assert result["intentHandlingState"] == "Fulfilled"
        assert result["conditions"][0]["observed"] == 120.0


# ── evaluate_turtle_conditions — unit tests (no Fuseki) ───────────────────────


_RANGE_TURTLE = (
    "@prefix quan: <http://tio.models.tmforum.org/tio/v3.6.0/QuantityOntology/> .\n"
    "@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .\n"
    "@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .\n"
    "<urn:t:cmp> a quan:quaninRange ;\n"
    "    rdf:first <urn:t:val> ;\n"
    "    rdf:rest  <urn:t:r1> .\n"
    "<urn:t:r1> rdf:first <urn:t:lo> ; rdf:rest <urn:t:r2> .\n"
    "<urn:t:r2> rdf:first <urn:t:hi> .\n"
    '<urn:t:val> rdf:value "{val}"^^xsd:decimal .\n'
    '<urn:t:lo>  rdf:value "{lo}"^^xsd:decimal .\n'
    '<urn:t:hi>  rdf:value "{hi}"^^xsd:decimal .\n'
)


class TestEvaluateTurtleConditions:
    def _make_turtle(self, rdf_type: str, obs: str, bnd: str) -> str:
        return (
            "@prefix quan: <http://tio.models.tmforum.org/tio/v3.6.0/QuantityOntology/> .\n"
            "@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .\n"
            "@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .\n"
            f"<urn:t:cmp> a quan:{rdf_type} ;\n"
            "    rdf:first <urn:t:obs> ;\n"
            "    rdf:rest  <urn:t:rst> .\n"
            "<urn:t:rst> rdf:first <urn:t:bnd> .\n"
            f'<urn:t:obs> rdf:value "{obs}"^^xsd:decimal .\n'
            f'<urn:t:bnd> rdf:value "{bnd}"^^xsd:decimal .\n'
        )

    # ── aggregate state ───────────────────────────────────────────────────────

    def test_quanatLeast_pass(self):
        assert evaluate_turtle_conditions(self._make_turtle("quanatLeast", "100", "100"))["intentHandlingState"] == "Fulfilled"

    def test_quanatLeast_fail(self):
        assert evaluate_turtle_conditions(self._make_turtle("quanatLeast", "99", "100"))["intentHandlingState"] == "Degraded"

    def test_quanatMost_pass(self):
        assert evaluate_turtle_conditions(self._make_turtle("quanatMost", "5", "10"))["intentHandlingState"] == "Fulfilled"

    def test_quanatMost_fail(self):
        assert evaluate_turtle_conditions(self._make_turtle("quanatMost", "11", "10"))["intentHandlingState"] == "Degraded"

    def test_quangreater_pass(self):
        assert evaluate_turtle_conditions(self._make_turtle("quangreater", "101", "100"))["intentHandlingState"] == "Fulfilled"

    def test_quangreater_equal_is_fail(self):
        assert evaluate_turtle_conditions(self._make_turtle("quangreater", "100", "100"))["intentHandlingState"] == "Degraded"

    def test_quansmaller_pass(self):
        assert evaluate_turtle_conditions(self._make_turtle("quansmaller", "24", "25"))["intentHandlingState"] == "Fulfilled"

    def test_quansmaller_equal_is_fail(self):
        assert evaluate_turtle_conditions(self._make_turtle("quansmaller", "25", "25"))["intentHandlingState"] == "Degraded"

    def test_quanexactly_pass(self):
        assert evaluate_turtle_conditions(self._make_turtle("quanexactly", "42", "42"))["intentHandlingState"] == "Fulfilled"

    def test_quanexactly_fail(self):
        assert evaluate_turtle_conditions(self._make_turtle("quanexactly", "42", "43"))["intentHandlingState"] == "Degraded"

    def test_quaninRange_pass(self):
        assert evaluate_turtle_conditions(_RANGE_TURTLE.format(val="50", lo="10", hi="100"))["intentHandlingState"] == "Fulfilled"

    def test_quaninRange_fail_below(self):
        assert evaluate_turtle_conditions(_RANGE_TURTLE.format(val="5", lo="10", hi="100"))["intentHandlingState"] == "Degraded"

    def test_no_conditions_returns_degraded(self):
        result = evaluate_turtle_conditions("@prefix : <http://example.org/> .")
        assert result["intentHandlingState"] == "Degraded"
        assert "No quantity conditions" in result["reason"]
        assert result["conditions"] == []

    def test_combinator_wrapping_only_unknown_operators_is_degraded(self):
        """Regression BBF_921-35f: a log:allOf wrapping only unknown/opaque nodes
        must not silently return Fulfilled — it has no evaluable conditions."""
        turtle = (
            "@prefix log: <http://tio.models.tmforum.org/tio/v3.6.0/LogicalOperators/> .\n"
            "@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .\n"
            "<urn:root> log:allOf (<urn:unknown1> <urn:unknown2>) .\n"
            "<urn:unknown1> a <urn:SomeUnsupportedType> .\n"
            "<urn:unknown2> a <urn:SomeUnsupportedType> .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Degraded"
        assert result["conditions"], "Expected at least one opaqueChildren condition"
        assert result["conditions"][0]["type"] == "opaqueChildren"
        assert result["conditions"][0]["passed"] is False

    def test_invalid_turtle_returns_degraded(self):
        result = evaluate_turtle_conditions("this is !! not valid turtle")
        assert result["intentHandlingState"] == "Degraded"
        assert "parse error" in result["reason"].lower()
        assert result["conditions"] == []

    # ── error paths: missing structure / bad literals ─────────────────────────

    def test_two_arg_missing_operand_nodes_degraded(self):
        """quanatLeast with no rdf:rest → missing operand nodes error."""
        turtle = (
            "@prefix quan: <http://tio.models.tmforum.org/tio/v3.6.0/QuantityOntology/> .\n"
            "@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .\n"
            "@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .\n"
            "<urn:t:cmp> a quan:quanatLeast ;\n"
            "    rdf:first <urn:t:obs> .\n"  # no rdf:rest → bnd_node will be None
            '<urn:t:obs> rdf:value "120"^^xsd:decimal .\n'
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Degraded"
        c = result["conditions"][0]
        assert c["passed"] is False
        assert c["error"] == "missing operand nodes"
        assert "missing operand nodes" in result["reason"]

    def test_two_arg_missing_rdf_value_degraded(self):
        """quanatLeast nodes present but rdf:value absent on observed node."""
        turtle = (
            "@prefix quan: <http://tio.models.tmforum.org/tio/v3.6.0/QuantityOntology/> .\n"
            "@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .\n"
            "@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .\n"
            "<urn:t:cmp> a quan:quanatLeast ;\n"
            "    rdf:first <urn:t:obs> ;\n"
            "    rdf:rest  <urn:t:rst> .\n"
            "<urn:t:rst> rdf:first <urn:t:bnd> .\n"
            # obs has no rdf:value
            '<urn:t:bnd> rdf:value "100"^^xsd:decimal .\n'
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Degraded"
        c = result["conditions"][0]
        assert c["passed"] is False
        assert c["error"] == "missing rdf:value"

    def test_two_arg_non_numeric_value_degraded(self):
        """quanatLeast with a non-numeric literal → InvalidOperation error."""
        turtle = (
            "@prefix quan: <http://tio.models.tmforum.org/tio/v3.6.0/QuantityOntology/> .\n"
            "@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .\n"
            "@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .\n"
            "<urn:t:cmp> a quan:quanatLeast ;\n"
            "    rdf:first <urn:t:obs> ;\n"
            "    rdf:rest  <urn:t:rst> .\n"
            "<urn:t:rst> rdf:first <urn:t:bnd> .\n"
            '<urn:t:obs> rdf:value "not-a-number"^^xsd:string .\n'
            '<urn:t:bnd> rdf:value "100"^^xsd:decimal .\n'
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Degraded"
        c = result["conditions"][0]
        assert c["passed"] is False
        assert c["error"] == "non-numeric value"

    def test_quaninRange_missing_operand_nodes_degraded(self):
        """quaninRange with no rdf:rest chain → missing operand nodes error."""
        turtle = (
            "@prefix quan: <http://tio.models.tmforum.org/tio/v3.6.0/QuantityOntology/> .\n"
            "@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .\n"
            "@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .\n"
            "<urn:t:cmp> a quan:quaninRange ;\n"
            "    rdf:first <urn:t:val> .\n"  # no rdf:rest → lo_node/hi_node None
            '<urn:t:val> rdf:value "50"^^xsd:decimal .\n'
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Degraded"
        c = result["conditions"][0]
        assert c["passed"] is False
        assert c["error"] == "missing operand nodes"

    def test_quaninRange_missing_rdf_value_degraded(self):
        """quaninRange with correct structure but rdf:value absent on lower bound."""
        turtle = (
            "@prefix quan: <http://tio.models.tmforum.org/tio/v3.6.0/QuantityOntology/> .\n"
            "@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .\n"
            "@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .\n"
            "<urn:t:cmp> a quan:quaninRange ;\n"
            "    rdf:first <urn:t:val> ;\n"
            "    rdf:rest  <urn:t:r1> .\n"
            "<urn:t:r1> rdf:first <urn:t:lo> ; rdf:rest <urn:t:r2> .\n"
            "<urn:t:r2> rdf:first <urn:t:hi> .\n"
            '<urn:t:val> rdf:value "50"^^xsd:decimal .\n'
            # lo has no rdf:value
            '<urn:t:hi>  rdf:value "100"^^xsd:decimal .\n'
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Degraded"
        c = result["conditions"][0]
        assert c["passed"] is False
        assert c["error"] == "missing rdf:value"

    def test_quaninRange_non_numeric_value_degraded(self):
        """quaninRange with non-numeric literal on upper bound."""
        turtle = (
            "@prefix quan: <http://tio.models.tmforum.org/tio/v3.6.0/QuantityOntology/> .\n"
            "@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .\n"
            "@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .\n"
            "<urn:t:cmp> a quan:quaninRange ;\n"
            "    rdf:first <urn:t:val> ;\n"
            "    rdf:rest  <urn:t:r1> .\n"
            "<urn:t:r1> rdf:first <urn:t:lo> ; rdf:rest <urn:t:r2> .\n"
            "<urn:t:r2> rdf:first <urn:t:hi> .\n"
            '<urn:t:val> rdf:value "50"^^xsd:decimal .\n'
            '<urn:t:lo>  rdf:value "10"^^xsd:decimal .\n'
            '<urn:t:hi>  rdf:value "not-a-number"^^xsd:string .\n'
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Degraded"
        c = result["conditions"][0]
        assert c["passed"] is False
        assert c["error"] == "non-numeric value"

    # ── per-condition detail ──────────────────────────────────────────────────

    def test_conditions_list_present_on_fulfilled(self):
        result = evaluate_turtle_conditions(self._make_turtle("quanatLeast", "120", "100"))
        assert result["intentHandlingState"] == "Fulfilled"
        assert len(result["conditions"]) == 1
        c = result["conditions"][0]
        assert c["type"] == "quanatLeast"
        assert c["operator"] == ">="
        assert c["observed"] == 120.0
        assert c["bound"] == 100.0
        assert c["passed"] is True

    def test_conditions_list_present_on_degraded(self):
        result = evaluate_turtle_conditions(self._make_turtle("quansmaller", "30", "25"))
        assert result["intentHandlingState"] == "Degraded"
        assert len(result["conditions"]) == 1
        c = result["conditions"][0]
        assert c["type"] == "quansmaller"
        assert c["operator"] == "<"
        assert c["observed"] == 30.0
        assert c["bound"] == 25.0
        assert c["passed"] is False

    def test_quaninRange_condition_fields(self):
        result = evaluate_turtle_conditions(_RANGE_TURTLE.format(val="50", lo="10", hi="100"))
        c = result["conditions"][0]
        assert c["type"] == "quaninRange"
        assert c["operator"] == "<=<="
        assert c["observed"] == 50.0
        assert c["lower"] == 10.0
        assert c["upper"] == 100.0
        assert c["passed"] is True

    def test_multi_condition_all_pass(self):
        """Two conditions in one Turtle — both pass → Fulfilled, both in list."""
        turtle = (
            "@prefix quan: <http://tio.models.tmforum.org/tio/v3.6.0/QuantityOntology/> .\n"
            "@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .\n"
            "@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .\n"
            # downstream >= 100
            "<urn:t:dl> a quan:quanatLeast ; rdf:first <urn:t:dl_obs> ; rdf:rest <urn:t:dl_r> .\n"
            "<urn:t:dl_r> rdf:first <urn:t:dl_bnd> .\n"
            '<urn:t:dl_obs> rdf:value "120"^^xsd:decimal .\n'
            '<urn:t:dl_bnd> rdf:value "100"^^xsd:decimal .\n'
            # latency < 25
            "<urn:t:lat> a quan:quansmaller ; rdf:first <urn:t:lat_obs> ; rdf:rest <urn:t:lat_r> .\n"
            "<urn:t:lat_r> rdf:first <urn:t:lat_bnd> .\n"
            '<urn:t:lat_obs> rdf:value "10"^^xsd:decimal .\n'
            '<urn:t:lat_bnd> rdf:value "25"^^xsd:decimal .\n'
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Fulfilled"
        assert len(result["conditions"]) == 2
        assert all(c["passed"] for c in result["conditions"])

    def test_multi_condition_one_fails(self):
        """Two conditions — one fails → Degraded, failed condition visible in list."""
        turtle = (
            "@prefix quan: <http://tio.models.tmforum.org/tio/v3.6.0/QuantityOntology/> .\n"
            "@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .\n"
            "@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .\n"
            # downstream >= 100 — PASSES (120 >= 100)
            "<urn:t:dl> a quan:quanatLeast ; rdf:first <urn:t:dl_obs> ; rdf:rest <urn:t:dl_r> .\n"
            "<urn:t:dl_r> rdf:first <urn:t:dl_bnd> .\n"
            '<urn:t:dl_obs> rdf:value "120"^^xsd:decimal .\n'
            '<urn:t:dl_bnd> rdf:value "100"^^xsd:decimal .\n'
            # latency < 25 — FAILS (30 >= 25)
            "<urn:t:lat> a quan:quansmaller ; rdf:first <urn:t:lat_obs> ; rdf:rest <urn:t:lat_r> .\n"
            "<urn:t:lat_r> rdf:first <urn:t:lat_bnd> .\n"
            '<urn:t:lat_obs> rdf:value "30"^^xsd:decimal .\n'
            '<urn:t:lat_bnd> rdf:value "25"^^xsd:decimal .\n'
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Degraded"
        assert len(result["conditions"]) == 2
        passed = [c for c in result["conditions"] if c["passed"]]
        failed = [c for c in result["conditions"] if not c["passed"]]
        assert len(passed) == 1
        assert len(failed) == 1
        assert failed[0]["type"] == "quansmaller"
        assert failed[0]["observed"] == 30.0


# ── metric resolution (tmf_metrics_eval.rules Python port) ───────────────────

_MET_PREFIXES = """\
@prefix quan: <http://tio.models.tmforum.org/tio/v3.6.0/QuantityOntology/> .
@prefix met:  <http://tio.models.tmforum.org/tio/v3.6.0/MetricsAndObservations/> .
@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .
"""


class TestMetricResolution:
    """Verify that met:Observation records resolve to rdf:value on metric nodes."""

    def test_pattern_a_direct_metric_ref_fulfilled(self):
        """Pattern A: metric URI as rdf:first, observation in merged graph → Fulfilled."""
        turtle = (
            _MET_PREFIXES
            + "<urn:test:cond> a quan:quanatLeast ;\n"
            "    rdf:first <urn:test:metric> ;\n"
            "    rdf:rest  [ rdf:first <urn:test:bound> ] .\n"
            "<urn:test:bound> rdf:value \"100\"^^xsd:decimal .\n"
            "<urn:test:obs> a met:Observation ;\n"
            "    met:observedMetric <urn:test:metric> ;\n"
            "    rdf:value \"120\"^^xsd:decimal ;\n"
            "    met:obtainedAt \"2026-06-04T10:00:00Z\"^^xsd:dateTime .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Fulfilled"
        assert result["conditions"][0]["observed"] == 120.0

    def test_pattern_a_metric_below_bound_degraded(self):
        """Pattern A: observed < bound → Degraded."""
        turtle = (
            _MET_PREFIXES
            + "<urn:test:cond> a quan:quanatLeast ;\n"
            "    rdf:first <urn:test:metric> ;\n"
            "    rdf:rest  [ rdf:first <urn:test:bound> ] .\n"
            "<urn:test:bound> rdf:value \"100\"^^xsd:decimal .\n"
            "<urn:test:obs> a met:Observation ;\n"
            "    met:observedMetric <urn:test:metric> ;\n"
            "    rdf:value \"80\"^^xsd:decimal ;\n"
            "    met:obtainedAt \"2026-06-04T10:00:00Z\"^^xsd:dateTime .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Degraded"
        assert result["conditions"][0]["passed"] is False

    def test_pattern_a_latest_observation_wins(self):
        """Pattern A: when multiple observations exist, most recent is used."""
        turtle = (
            _MET_PREFIXES
            + "<urn:test:cond> a quan:quanatLeast ;\n"
            "    rdf:first <urn:test:metric> ;\n"
            "    rdf:rest  [ rdf:first <urn:test:bound> ] .\n"
            "<urn:test:bound> rdf:value \"100\"^^xsd:decimal .\n"
            "<urn:test:obs_old> a met:Observation ;\n"
            "    met:observedMetric <urn:test:metric> ;\n"
            "    rdf:value \"80\"^^xsd:decimal ;\n"
            "    met:obtainedAt \"2026-06-04T09:00:00Z\"^^xsd:dateTime .\n"
            "<urn:test:obs_new> a met:Observation ;\n"
            "    met:observedMetric <urn:test:metric> ;\n"
            "    rdf:value \"120\"^^xsd:decimal ;\n"
            "    met:obtainedAt \"2026-06-04T10:00:00Z\"^^xsd:dateTime .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Fulfilled"
        assert result["conditions"][0]["observed"] == 120.0

    def test_pattern_a_latest_observation_wins_mixed_z_and_offset(self):
        """Regression BBF_921-qlk: 'Z' and '+00:00' timestamps must compare as
        equal-offset datetimes, not as strings ('Z' > '+' lexicographically
        would invert the ordering and pick the stale observation)."""
        turtle = (
            _MET_PREFIXES
            + "<urn:test:cond> a quan:quanatLeast ;\n"
            "    rdf:first <urn:test:metric> ;\n"
            "    rdf:rest  [ rdf:first <urn:test:bound> ] .\n"
            "<urn:test:bound> rdf:value \"100\"^^xsd:decimal .\n"
            # Older observation written with '+00:00' (RDFLib round-trip form)
            "<urn:test:obs_old> a met:Observation ;\n"
            "    met:observedMetric <urn:test:metric> ;\n"
            "    rdf:value \"80\"^^xsd:decimal ;\n"
            "    met:obtainedAt \"2026-06-04T09:00:00+00:00\"^^xsd:dateTime .\n"
            # Newer observation written with 'Z' (observation_store form)
            "<urn:test:obs_new> a met:Observation ;\n"
            "    met:observedMetric <urn:test:metric> ;\n"
            "    rdf:value \"120\"^^xsd:decimal ;\n"
            "    met:obtainedAt \"2026-06-04T10:00:00Z\"^^xsd:dateTime .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        # Must pick obs_new (120 >= 100 → Fulfilled), not obs_old (80 < 100 → Degraded)
        assert result["intentHandlingState"] == "Fulfilled"
        assert result["conditions"][0]["observed"] == 120.0

    def test_pattern_a_quansmaller_with_metric_ref(self):
        """Pattern A works for quansmaller (latency < bound)."""
        turtle = (
            _MET_PREFIXES
            + "<urn:test:cond> a quan:quansmaller ;\n"
            "    rdf:first <urn:test:lat_metric> ;\n"
            "    rdf:rest  [ rdf:first <urn:test:bound> ] .\n"
            "<urn:test:bound> rdf:value \"25\"^^xsd:decimal .\n"
            "<urn:test:obs> a met:Observation ;\n"
            "    met:observedMetric <urn:test:lat_metric> ;\n"
            "    rdf:value \"10\"^^xsd:decimal ;\n"
            "    met:obtainedAt \"2026-06-04T10:00:00Z\"^^xsd:dateTime .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Fulfilled"

    def test_pattern_a_no_observation_gives_missing_rdf_value(self):
        """Pattern A: metric node with no observation → missing rdf:value error."""
        turtle = (
            _MET_PREFIXES
            + "<urn:test:cond> a quan:quanatLeast ;\n"
            "    rdf:first <urn:test:metric> ;\n"
            "    rdf:rest  [ rdf:first <urn:test:bound> ] .\n"
            "<urn:test:bound> rdf:value \"100\"^^xsd:decimal .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Degraded"
        assert result["conditions"][0]["error"] == "missing rdf:value"

    def test_pattern_b_metlastvalue_resolved(self):
        """Pattern B: met:metlastValue function node resolved via observation."""
        turtle = (
            _MET_PREFIXES
            + "<urn:test:cond> a quan:quanatLeast ;\n"
            "    rdf:first <urn:test:fn> ;\n"
            "    rdf:rest  [ rdf:first <urn:test:bound> ] .\n"
            "<urn:test:bound> rdf:value \"100\"^^xsd:decimal .\n"
            "<urn:test:fn> a met:metlastValue ;\n"
            "    rdfs:member <urn:test:metric> .\n"
            "<urn:test:obs> a met:Observation ;\n"
            "    met:observedMetric <urn:test:metric> ;\n"
            "    rdf:value \"110\"^^xsd:decimal ;\n"
            "    met:obtainedAt \"2026-06-04T10:00:00Z\"^^xsd:dateTime .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Fulfilled"

    def test_pattern_c_metobservedvalue_resolved(self):
        """Pattern C: met:metobservedValue function node resolved from direct observation."""
        turtle = (
            _MET_PREFIXES
            + "<urn:test:cond> a quan:quanatLeast ;\n"
            "    rdf:first <urn:test:fn> ;\n"
            "    rdf:rest  [ rdf:first <urn:test:bound> ] .\n"
            "<urn:test:bound> rdf:value \"100\"^^xsd:decimal .\n"
            "<urn:test:fn> a met:metobservedValue ;\n"
            "    rdf:first <urn:test:obs> .\n"
            "<urn:test:obs> a met:Observation ;\n"
            "    rdf:value \"115\"^^xsd:decimal .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Fulfilled"

    def test_pattern_a_quaninrange_with_metric_ref(self):
        """Pattern A applies to quaninRange value node too."""
        turtle = (
            _MET_PREFIXES
            + "<urn:test:cond> a quan:quaninRange ;\n"
            "    rdf:first <urn:test:metric> ;\n"
            "    rdf:rest  <urn:test:r1> .\n"
            "<urn:test:r1> rdf:first <urn:test:lo> ; rdf:rest <urn:test:r2> .\n"
            "<urn:test:r2> rdf:first <urn:test:hi> .\n"
            "<urn:test:lo> rdf:value \"10\"^^xsd:decimal .\n"
            "<urn:test:hi> rdf:value \"100\"^^xsd:decimal .\n"
            "<urn:test:obs> a met:Observation ;\n"
            "    met:observedMetric <urn:test:metric> ;\n"
            "    rdf:value \"50\"^^xsd:decimal ;\n"
            "    met:obtainedAt \"2026-06-04T10:00:00Z\"^^xsd:dateTime .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Fulfilled"
        assert result["conditions"][0]["observed"] == 50.0


# ── Validity evaluation (tmf_validity_eval.rules Python port) ────────────────

_IV_PFX = """\
@prefix iv:   <http://tio.models.tmforum.org/tio/v3.6.0/IntentValidityOntology/> .
@prefix quan: <http://tio.models.tmforum.org/tio/v3.6.0/QuantityOntology/> .
@prefix log:  <http://tio.models.tmforum.org/tio/v3.6.0/LogicalOperators/> .
@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .
"""

# Quantity condition shared across window tests
_WINDOW_COND = (
    "<urn:t:cond> a quan:quanatLeast ;\n"
    "  rdf:first <urn:t:val> ;\n"
    "  rdf:rest  [ rdf:first <urn:t:bnd> ] ;\n"
    "  iv:ivvalidIf <urn:t:window> .\n"
    "<urn:t:val> rdf:value \"120\"^^xsd:decimal .\n"
    "<urn:t:bnd> rdf:value \"100\"^^xsd:decimal .\n"
)


class TestValidityGate:
    """iv:ivvalidIf — condition fails immediately when the window is closed."""

    def test_open_window_condition_evaluated_normally(self):
        """Valid window: 120 >= 100 → Fulfilled."""
        turtle = _IV_PFX + _WINDOW_COND + "<urn:t:window> iv:ivisValid true .\n"
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Fulfilled"

    def test_closed_window_degrades_regardless_of_condition(self):
        """Expired window: condition would pass (120 >= 100) but gate fires → Degraded."""
        turtle = _IV_PFX + _WINDOW_COND + "<urn:t:window> iv:ivisValid false .\n"
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Degraded"
        c = result["conditions"][0]
        assert c["type"] == "validityGate"
        assert c["passed"] is False

    def test_absent_ivisvalid_degrades(self):
        """No iv:ivisValid on context node → treat as closed window → Degraded."""
        turtle = _IV_PFX + _WINDOW_COND  # no iv:ivisValid asserted on window
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Degraded"
        assert result["conditions"][0]["type"] == "validityGate"

    def test_validity_gate_inside_allof(self):
        """Gate applied to node inside a log:allOf combinator tree."""
        turtle = (
            _IV_PFX
            + "<urn:t:root> log:allOf ( <urn:t:cond> ) .\n"
            + _WINDOW_COND
            + "<urn:t:window> iv:ivisValid false .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Degraded"
        assert any(c["type"] == "validityGate" for c in result["conditions"])


class TestSameValidityAs:
    """iv:ivsameValidityAs — iv:ivisValid propagated through chains."""

    def test_chain_propagates_true(self):
        """X sameValidityAs Y; Y.ivisValid = true → X gating passes → Fulfilled."""
        turtle = (
            _IV_PFX
            + _WINDOW_COND
            + "<urn:t:window> iv:ivsameValidityAs <urn:t:other> .\n"
            "<urn:t:other> iv:ivisValid true .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Fulfilled"

    def test_chain_propagates_false(self):
        """X sameValidityAs Y; Y.ivisValid = false → X gating fails → Degraded."""
        turtle = (
            _IV_PFX
            + _WINDOW_COND
            + "<urn:t:window> iv:ivsameValidityAs <urn:t:other> .\n"
            "<urn:t:other> iv:ivisValid false .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Degraded"
        assert result["conditions"][0]["type"] == "validityGate"

    def test_two_hop_chain(self):
        """X → Y → Z; Z.ivisValid = true propagates to X."""
        turtle = (
            _IV_PFX
            + _WINDOW_COND
            + "<urn:t:window> iv:ivsameValidityAs <urn:t:y> .\n"
            "<urn:t:y> iv:ivsameValidityAs <urn:t:z> .\n"
            "<urn:t:z> iv:ivisValid true .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Fulfilled"


class TestValidityOf:
    """iv:ivvalidityOf — passes iff ALL members have iv:ivisValid true."""

    def test_all_members_valid_pass(self):
        turtle = (
            _IV_PFX
            + "<urn:t:fn> a iv:ivvalidityOf ;\n"
            "  rdfs:member <urn:t:r1> ;\n"
            "  rdfs:member <urn:t:r2> .\n"
            "<urn:t:r1> iv:ivisValid true .\n"
            "<urn:t:r2> iv:ivisValid true .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Fulfilled"
        c = result["conditions"][0]
        assert c["type"] == "validityOf"
        assert c["member_count"] == 2
        assert c["passed"] is True

    def test_one_member_invalid_fails(self):
        turtle = (
            _IV_PFX
            + "<urn:t:fn> a iv:ivvalidityOf ;\n"
            "  rdfs:member <urn:t:r1> ;\n"
            "  rdfs:member <urn:t:r2> .\n"
            "<urn:t:r1> iv:ivisValid true .\n"
            "<urn:t:r2> iv:ivisValid false .\n"  # one invalid
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Degraded"
        assert result["conditions"][0]["passed"] is False

    def test_member_absent_ivisvalid_fails(self):
        """Member with no iv:ivisValid counts as invalid."""
        turtle = (
            _IV_PFX
            + "<urn:t:fn> a iv:ivvalidityOf ;\n"
            "  rdfs:member <urn:t:r1> .\n"
            # r1 has no iv:ivisValid
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Degraded"

    def test_empty_members_vacuous_pass(self):
        turtle = _IV_PFX + "<urn:t:fn> a iv:ivvalidityOf .\n"
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Fulfilled"
        assert result["conditions"][0]["member_count"] == 0

    def test_validity_of_inside_allof(self):
        turtle = (
            _IV_PFX
            + "<urn:t:root> log:allOf ( <urn:t:fn> ) .\n"
            "<urn:t:fn> a iv:ivvalidityOf ;\n"
            "  rdfs:member <urn:t:r1> .\n"
            "<urn:t:r1> iv:ivisValid true .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Fulfilled"

    def test_validity_of_via_samevalidityas(self):
        """Member's validity resolved through ivsameValidityAs chain."""
        turtle = (
            _IV_PFX
            + "<urn:t:fn> a iv:ivvalidityOf ;\n"
            "  rdfs:member <urn:t:r1> .\n"
            "<urn:t:r1> iv:ivsameValidityAs <urn:t:source> .\n"
            "<urn:t:source> iv:ivisValid true .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Fulfilled"

    def test_validity_of_fail_label_in_reason(self):
        turtle = (
            _IV_PFX
            + "<urn:t:fn> a iv:ivvalidityOf ;\n"
            "  rdfs:member <urn:t:r1> .\n"
            "<urn:t:r1> iv:ivisValid false .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert "validityOf: FAIL" in result["reason"]


# ── ICM expectations (tio_core + tmf_icm_eval Python port) ──────────────────

_ICM_PFX = """\
@prefix icm: <http://tio.models.tmforum.org/tio/v3.6.0/IntentCommonModel/> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
"""


class TestDeliveryExpectation:
    """icm:DeliveryExpectation — target must contain a member of deliveryType."""

    def test_delivery_pass_member_has_type(self):
        turtle = (
            _ICM_PFX
            + "<urn:t:exp> a icm:DeliveryExpectation ;\n"
            "  icm:target <urn:t:target> ;\n"
            "  icm:deliveryType <urn:t:MyClass> .\n"
            "<urn:t:target> rdfs:member <urn:t:res> .\n"
            "<urn:t:res> rdf:type <urn:t:MyClass> .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Fulfilled"
        c = result["conditions"][0]
        assert c["type"] == "DeliveryExpectation"
        assert c["passed"] is True
        assert c["member_count"] == 1

    def test_delivery_fail_wrong_type(self):
        turtle = (
            _ICM_PFX
            + "<urn:t:exp> a icm:DeliveryExpectation ;\n"
            "  icm:target <urn:t:target> ;\n"
            "  icm:deliveryType <urn:t:MyClass> .\n"
            "<urn:t:target> rdfs:member <urn:t:res> .\n"
            "<urn:t:res> rdf:type <urn:t:OtherClass> .\n"  # wrong type
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Degraded"
        c = result["conditions"][0]
        assert c["type"] == "DeliveryExpectation"
        assert c["passed"] is False

    def test_delivery_pass_one_of_many_members_matches(self):
        """Only one member needs to have the required type."""
        turtle = (
            _ICM_PFX
            + "<urn:t:exp> a icm:DeliveryExpectation ;\n"
            "  icm:target <urn:t:target> ;\n"
            "  icm:deliveryType <urn:t:MyClass> .\n"
            "<urn:t:target> rdfs:member <urn:t:r1> ; rdfs:member <urn:t:r2> .\n"
            "<urn:t:r1> rdf:type <urn:t:OtherClass> .\n"
            "<urn:t:r2> rdf:type <urn:t:MyClass> .\n"  # this one matches
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Fulfilled"
        assert result["conditions"][0]["member_count"] == 2

    def test_delivery_fail_empty_target(self):
        turtle = (
            _ICM_PFX
            + "<urn:t:exp> a icm:DeliveryExpectation ;\n"
            "  icm:target <urn:t:target> ;\n"
            "  icm:deliveryType <urn:t:MyClass> .\n"
            # target has no rdfs:member triples
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Degraded"
        c = result["conditions"][0]
        assert c["passed"] is False
        assert c["error"] == "empty target container"

    def test_delivery_fail_missing_target(self):
        turtle = (
            _ICM_PFX
            + "<urn:t:exp> a icm:DeliveryExpectation ;\n"
            "  icm:deliveryType <urn:t:MyClass> .\n"  # no icm:target
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Degraded"
        assert "missing icm:target" in result["conditions"][0]["error"]

    def test_delivery_fail_missing_delivery_type(self):
        turtle = (
            _ICM_PFX
            + "<urn:t:exp> a icm:DeliveryExpectation ;\n"
            "  icm:target <urn:t:target> .\n"  # no icm:deliveryType
            "<urn:t:target> rdfs:member <urn:t:res> .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Degraded"
        assert "missing icm:deliveryType" in result["conditions"][0]["error"]

    def test_delivery_pre_computed_result_true(self):
        """icm:result true already asserted — fast-path pass."""
        turtle = (
            _ICM_PFX
            + "<urn:t:exp> a icm:DeliveryExpectation ;\n"
            "  icm:result true .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Fulfilled"
        assert result["conditions"][0]["passed"] is True

    def test_delivery_fail_reason_includes_type(self):
        """A delivery that fails due to wrong type should surface type name."""
        turtle = (
            _ICM_PFX
            + "<urn:t:exp> a icm:DeliveryExpectation ;\n"
            "  icm:target <urn:t:target> ;\n"
            "  icm:deliveryType <urn:t:MyClass> .\n"
            "<urn:t:target> rdfs:member <urn:t:res> .\n"
            "<urn:t:res> rdf:type <urn:t:OtherClass> .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert "DeliveryExpectation: FAIL" in result["reason"]

    def test_delivery_inside_allof_combinator(self):
        turtle = (
            _ICM_PFX
            + "@prefix log: <http://tio.models.tmforum.org/tio/v3.6.0/LogicalOperators/> .\n"
            "<urn:t:root> log:allOf ( <urn:t:exp> ) .\n"
            "<urn:t:exp> a icm:DeliveryExpectation ;\n"
            "  icm:target <urn:t:target> ;\n"
            "  icm:deliveryType <urn:t:MyClass> .\n"
            "<urn:t:target> rdfs:member <urn:t:res> .\n"
            "<urn:t:res> rdf:type <urn:t:MyClass> .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Fulfilled"


class TestPropertyExpectation:
    """icm:PropertyExpectation — passes if rdf:value is boolean true."""

    def test_property_pass_value_true(self):
        turtle = (
            _ICM_PFX
            + "<urn:t:exp> a icm:PropertyExpectation ;\n"
            "  rdf:value true .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Fulfilled"
        c = result["conditions"][0]
        assert c["type"] == "PropertyExpectation"
        assert c["passed"] is True

    def test_property_fail_value_false(self):
        turtle = (
            _ICM_PFX
            + "<urn:t:exp> a icm:PropertyExpectation ;\n"
            "  rdf:value false .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Degraded"
        c = result["conditions"][0]
        assert c["type"] == "PropertyExpectation"
        assert c["passed"] is False

    def test_property_fail_no_value(self):
        turtle = (
            _ICM_PFX
            + "<urn:t:exp> a icm:PropertyExpectation .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Degraded"
        assert result["conditions"][0]["passed"] is False
        assert result["conditions"][0]["value"] is None

    def test_property_fail_non_boolean_string(self):
        """rdf:value "true"^^xsd:string is not a boolean — fails."""
        turtle = (
            _ICM_PFX
            + "<urn:t:exp> a icm:PropertyExpectation ;\n"
            '  rdf:value "true"^^xsd:string .\n'
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Degraded"
        assert result["conditions"][0]["passed"] is False

    def test_property_inside_allof_pass(self):
        turtle = (
            _ICM_PFX
            + "@prefix log: <http://tio.models.tmforum.org/tio/v3.6.0/LogicalOperators/> .\n"
            "<urn:t:root> log:allOf ( <urn:t:exp> ) .\n"
            "<urn:t:exp> a icm:PropertyExpectation ;\n"
            "  rdf:value true .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Fulfilled"

    def test_property_inside_allof_fail(self):
        turtle = (
            _ICM_PFX
            + "@prefix log: <http://tio.models.tmforum.org/tio/v3.6.0/LogicalOperators/> .\n"
            "<urn:t:root> log:allOf ( <urn:t:exp> ) .\n"
            "<urn:t:exp> a icm:PropertyExpectation ;\n"
            "  rdf:value false .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Degraded"

    def test_multiple_expectations_all_pass(self):
        """Two expectations combined — both must pass."""
        turtle = (
            _ICM_PFX
            + "<urn:t:e1> a icm:PropertyExpectation ; rdf:value true .\n"
            "<urn:t:e2> a icm:DeliveryExpectation ;\n"
            "  icm:target <urn:t:target> ;\n"
            "  icm:deliveryType <urn:t:MyClass> .\n"
            "<urn:t:target> rdfs:member <urn:t:res> .\n"
            "<urn:t:res> rdf:type <urn:t:MyClass> .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Fulfilled"
        assert len(result["conditions"]) == 2
        assert all(c["passed"] for c in result["conditions"])

    def test_mixed_expectations_one_fails(self):
        """Property passes but delivery fails → overall Degraded."""
        turtle = (
            _ICM_PFX
            + "<urn:t:e1> a icm:PropertyExpectation ; rdf:value true .\n"
            "<urn:t:e2> a icm:DeliveryExpectation ;\n"
            "  icm:target <urn:t:target> ;\n"
            "  icm:deliveryType <urn:t:MyClass> .\n"
            # target empty → delivery fails
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Degraded"
        failed = [c for c in result["conditions"] if not c["passed"]]
        assert len(failed) == 1
        assert failed[0]["type"] == "DeliveryExpectation"


# ── set operators (tmf_set_ops_eval.rules Python port) ───────────────────────

_SET_PFX = """\
@prefix set:  <http://tio.models.tmforum.org/tio/v3.6.0/SetOperators/> .
@prefix quan: <http://tio.models.tmforum.org/tio/v3.6.0/QuantityOntology/> .
@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .
"""


class TestSetIsMember:
    """set:setisMember — resource in any listed container → True."""

    def _turtle(self, resource_in_c1: bool) -> str:
        member_line = "<urn:t:c1> rdfs:member <urn:t:res> ." if resource_in_c1 else ""
        return (
            _SET_PFX
            + "<urn:t:check> a set:setisMember ;\n"
            "  rdf:first <urn:t:res> ;\n"
            "  rdf:rest  <urn:t:bag> .\n"
            "<urn:t:bag> rdfs:member <urn:t:c1> .\n"
            + member_line + "\n"
        )

    def test_is_member_pass(self):
        result = evaluate_turtle_conditions(self._turtle(True))
        assert result["intentHandlingState"] == "Fulfilled"
        c = result["conditions"][0]
        assert c["type"] == "setIsMember"
        assert c["passed"] is True

    def test_is_member_fail(self):
        result = evaluate_turtle_conditions(self._turtle(False))
        assert result["intentHandlingState"] == "Degraded"
        c = result["conditions"][0]
        assert c["type"] == "setIsMember"
        assert c["passed"] is False

    def test_is_member_in_second_container(self):
        """Resource absent from C1 but present in C2 → passes."""
        turtle = (
            _SET_PFX
            + "<urn:t:check> a set:setisMember ;\n"
            "  rdf:first <urn:t:res> ;\n"
            "  rdf:rest  <urn:t:bag> .\n"
            "<urn:t:bag> rdfs:member <urn:t:c1> ;\n"
            "            rdfs:member <urn:t:c2> .\n"
            # not in c1
            "<urn:t:c2> rdfs:member <urn:t:res> .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Fulfilled"

    def test_is_member_rdf_list_encoding(self):
        """RDF-list encoding for the container list (fallback path)."""
        turtle = (
            _SET_PFX
            + "<urn:t:check> a set:setisMember ;\n"
            "  rdf:first <urn:t:res> ;\n"
            "  rdf:rest  ( <urn:t:c1> ) .\n"
            "<urn:t:c1> rdfs:member <urn:t:res> .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Fulfilled"

    def test_is_member_missing_rest_degraded(self):
        turtle = (
            _SET_PFX
            + "<urn:t:check> a set:setisMember ;\n"
            "  rdf:first <urn:t:res> .\n"  # no rdf:rest
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Degraded"
        assert "missing" in result["conditions"][0]["error"]

    def test_is_member_fail_label_in_reason(self):
        result = evaluate_turtle_conditions(self._turtle(False))
        assert "setIsMember: FAIL" in result["reason"]


class TestSetIntersectsWith:
    """set:setintersectsWith — common member in C1 and C2 → True."""

    def _turtle(self, shared: bool) -> str:
        c2_members = (
            "<urn:t:c2> rdfs:member <urn:t:item2> ; rdfs:member <urn:t:item3> .\n"
            if shared
            else "<urn:t:c2> rdfs:member <urn:t:item3> .\n"
        )
        return (
            _SET_PFX
            + "<urn:t:check> a set:setintersectsWith ;\n"
            "  rdf:first <urn:t:c1> ;\n"
            "  rdf:rest  [ rdf:first <urn:t:c2> ] .\n"
            "<urn:t:c1> rdfs:member <urn:t:item1> ; rdfs:member <urn:t:item2> .\n"
            + c2_members
        )

    def test_intersects_pass(self):
        result = evaluate_turtle_conditions(self._turtle(True))
        assert result["intentHandlingState"] == "Fulfilled"
        c = result["conditions"][0]
        assert c["type"] == "setIntersectsWith"
        assert c["passed"] is True

    def test_intersects_fail_disjoint(self):
        result = evaluate_turtle_conditions(self._turtle(False))
        assert result["intentHandlingState"] == "Degraded"
        assert result["conditions"][0]["passed"] is False

    def test_intersects_empty_containers_fail(self):
        turtle = (
            _SET_PFX
            + "<urn:t:check> a set:setintersectsWith ;\n"
            "  rdf:first <urn:t:c1> ;\n"
            "  rdf:rest  [ rdf:first <urn:t:c2> ] .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Degraded"
        assert result["conditions"][0]["passed"] is False

    def test_intersects_missing_c2_error(self):
        turtle = (
            _SET_PFX
            + "<urn:t:check> a set:setintersectsWith ;\n"
            "  rdf:first <urn:t:c1> .\n"  # no rdf:rest
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Degraded"
        assert "missing" in result["conditions"][0]["error"]


class TestSetIncludedIn:
    """set:setincludedIn — C1 ⊆ C2 → True."""

    def test_included_in_pass(self):
        turtle = (
            _SET_PFX
            + "<urn:t:check> a set:setincludedIn ;\n"
            "  rdf:first <urn:t:c1> ;\n"
            "  rdf:rest  [ rdf:first <urn:t:c2> ] .\n"
            "<urn:t:c1> rdfs:member <urn:t:a> ; rdfs:member <urn:t:b> .\n"
            "<urn:t:c2> rdfs:member <urn:t:a> ; rdfs:member <urn:t:b> ; rdfs:member <urn:t:c> .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Fulfilled"
        c = result["conditions"][0]
        assert c["type"] == "setIncludedIn"
        assert c["passed"] is True

    def test_included_in_fail_extra_member(self):
        turtle = (
            _SET_PFX
            + "<urn:t:check> a set:setincludedIn ;\n"
            "  rdf:first <urn:t:c1> ;\n"
            "  rdf:rest  [ rdf:first <urn:t:c2> ] .\n"
            "<urn:t:c1> rdfs:member <urn:t:a> ; rdfs:member <urn:t:x> .\n"  # x not in c2
            "<urn:t:c2> rdfs:member <urn:t:a> .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Degraded"
        assert result["conditions"][0]["passed"] is False

    def test_included_in_empty_c1_vacuous_pass(self):
        turtle = (
            _SET_PFX
            + "<urn:t:check> a set:setincludedIn ;\n"
            "  rdf:first <urn:t:c1> ;\n"
            "  rdf:rest  [ rdf:first <urn:t:c2> ] .\n"
            # c1 has no members — vacuously included in anything
            "<urn:t:c2> rdfs:member <urn:t:a> .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Fulfilled"

    def test_included_in_missing_second_arg_error(self):
        turtle = (
            _SET_PFX
            + "<urn:t:check> a set:setincludedIn ;\n"
            "  rdf:first <urn:t:c1> .\n"  # only one arg
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Degraded"
        assert "expected" in result["conditions"][0]["error"]


class TestSetForAll:
    """set:setforAll — every member of container satisfies condition."""

    _FORALL_PFX = _SET_PFX + (
        "<urn:t:forAll> a set:setforAll ;\n"
        "  rdf:first <urn:t:x> ;\n"           # member variable
        "  rdf:rest  <urn:t:r1> .\n"
        "<urn:t:r1> rdf:first <urn:t:c1> ;\n"  # container
        "           rdf:rest  <urn:t:r2> .\n"
        "<urn:t:r2> rdf:first <urn:t:cond> .\n" # condition
        "<urn:t:cond> a quan:quanatLeast ;\n"
        "  rdf:first <urn:t:x> ;\n"            # references member variable
        "  rdf:rest  [ rdf:first <urn:t:bnd> ] .\n"
        "<urn:t:bnd> rdf:value \"40\"^^xsd:decimal .\n"
    )

    def test_forall_all_pass(self):
        turtle = (
            self._FORALL_PFX
            + "<urn:t:c1> rdfs:member <urn:t:v50> ; rdfs:member <urn:t:v80> .\n"
            "<urn:t:v50> rdf:value \"50\"^^xsd:decimal .\n"
            "<urn:t:v80> rdf:value \"80\"^^xsd:decimal .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Fulfilled"

    def test_forall_one_member_fails(self):
        turtle = (
            self._FORALL_PFX
            + "<urn:t:c1> rdfs:member <urn:t:v50> ; rdfs:member <urn:t:v30> .\n"
            "<urn:t:v50> rdf:value \"50\"^^xsd:decimal .\n"
            "<urn:t:v30> rdf:value \"30\"^^xsd:decimal .\n"  # 30 < 40 → fail
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Degraded"

    def test_forall_empty_container_vacuous_pass(self):
        turtle = (
            self._FORALL_PFX
            # c1 has no members
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Fulfilled"
        assert result["conditions"][0]["member_count"] == 0

    def test_forall_missing_structure_error(self):
        turtle = (
            _SET_PFX
            + "<urn:t:forAll> a set:setforAll .\n"  # no rdf:first or rest
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Degraded"
        assert "missing" in result["conditions"][0]["error"]

    def test_forall_inside_allof_combinator(self):
        """setForAll nested inside a log:allOf combinator tree."""
        turtle = (
            _SET_PFX
            + "@prefix log: <http://tio.models.tmforum.org/tio/v3.6.0/LogicalOperators/> .\n"
            "<urn:t:root> log:allOf ( <urn:t:forAll> ) .\n"
            "<urn:t:forAll> a set:setforAll ;\n"
            "  rdf:first <urn:t:x> ;\n"
            "  rdf:rest  <urn:t:r1> .\n"
            "<urn:t:r1> rdf:first <urn:t:c1> ; rdf:rest <urn:t:r2> .\n"
            "<urn:t:r2> rdf:first <urn:t:cond> .\n"
            "<urn:t:cond> a quan:quanatLeast ;\n"
            "  rdf:first <urn:t:x> ;\n"
            "  rdf:rest  [ rdf:first <urn:t:bnd> ] .\n"
            "<urn:t:bnd> rdf:value \"10\"^^xsd:decimal .\n"
            "<urn:t:c1> rdfs:member <urn:t:v20> .\n"
            "<urn:t:v20> rdf:value \"20\"^^xsd:decimal .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Fulfilled"


class TestSetOpsFlatScan:
    """Set ops collected by flat-scan when no combinators are present."""

    def test_flat_scan_collects_is_member(self):
        turtle = (
            _SET_PFX
            + "<urn:t:check> a set:setisMember ;\n"
            "  rdf:first <urn:t:res> ;\n"
            "  rdf:rest  <urn:t:bag> .\n"
            "<urn:t:bag> rdfs:member <urn:t:c1> .\n"
            "<urn:t:c1> rdfs:member <urn:t:res> .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert any(c["type"] == "setIsMember" for c in result["conditions"])

    def test_flat_scan_collects_intersects_with(self):
        turtle = (
            _SET_PFX
            + "<urn:t:check> a set:setintersectsWith ;\n"
            "  rdf:first <urn:t:c1> ;\n"
            "  rdf:rest  [ rdf:first <urn:t:c2> ] .\n"
            "<urn:t:c1> rdfs:member <urn:t:x> .\n"
            "<urn:t:c2> rdfs:member <urn:t:x> .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert any(c["type"] == "setIntersectsWith" for c in result["conditions"])

    def test_flat_scan_collects_included_in(self):
        turtle = (
            _SET_PFX
            + "<urn:t:check> a set:setincludedIn ;\n"
            "  rdf:first <urn:t:c1> ;\n"
            "  rdf:rest  [ rdf:first <urn:t:c2> ] .\n"
            "<urn:t:c1> rdfs:member <urn:t:a> .\n"
            "<urn:t:c2> rdfs:member <urn:t:a> .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert any(c["type"] == "setIncludedIn" for c in result["conditions"])

    def test_flat_scan_collects_forall(self):
        turtle = (
            _SET_PFX
            + "<urn:t:f> a set:setforAll ;\n"
            "  rdf:first <urn:t:x> ;\n"
            "  rdf:rest  <urn:t:r1> .\n"
            "<urn:t:r1> rdf:first <urn:t:c1> ; rdf:rest <urn:t:r2> .\n"
            "<urn:t:r2> rdf:first <urn:t:cond> .\n"
            "<urn:t:cond> a quan:quanatLeast ;\n"
            "  rdf:first <urn:t:x> ;\n"
            "  rdf:rest  [ rdf:first <urn:t:bnd> ] .\n"
            "<urn:t:bnd> rdf:value \"5\"^^xsd:decimal .\n"
            "<urn:t:c1> rdfs:member <urn:t:v10> .\n"
            "<urn:t:v10> rdf:value \"10\"^^xsd:decimal .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Fulfilled"
        assert any(c.get("passed") for c in result["conditions"])


# ── Guarantee report evaluation (tmf_guarantee_eval.rules Python port) ───────

_IG_PFX = """\
@prefix ig:   <http://tio.models.tmforum.org/tio/v3.6.0/IntentGuaranteeOntology/> .
@prefix icm:  <http://tio.models.tmforum.org/tio/v3.6.0/IntentCommonModel/> .
@prefix imo:  <http://tio.models.tmforum.org/tio/v3.6.0/IntentManagementOntology/> .
@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
"""

_IG_REPORT = (
    "<urn:t:report> a ig:igGuaranteeReport ;\n"
    "    icm:icmabout <urn:t:intent> .\n"
)
_IG_ACCEPTED = (
    "<urn:t:event> a ig:igGuaranteeAccepted ;\n"
    "    imo:imoeventIssuedFor <urn:t:intent> .\n"
)
_IG_REJECTED = (
    "<urn:t:event> a ig:igGuaranteeRejected ;\n"
    "    imo:imoeventIssuedFor <urn:t:intent> .\n"
)


class TestGuaranteeReport:
    """ig:GuaranteeReport — state derivation from GuaranteeAccepted/Rejected events."""

    def test_accepted_event_yields_compliant(self):
        """GuaranteeAccepted event for the same intent → report state Compliant → Fulfilled."""
        turtle = _IG_PFX + _IG_REPORT + _IG_ACCEPTED
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Fulfilled"
        c = result["conditions"][0]
        assert c["type"] == "GuaranteeReport"
        assert c["state"] == "igGuaranteeStateCompliant"
        assert c["passed"] is True

    def test_rejected_event_yields_degraded(self):
        """GuaranteeRejected event for the same intent → report state Degraded → Degraded."""
        turtle = _IG_PFX + _IG_REPORT + _IG_REJECTED
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Degraded"
        c = result["conditions"][0]
        assert c["type"] == "GuaranteeReport"
        assert c["state"] == "igGuaranteeStateDegraded"
        assert c["passed"] is False

    def test_no_matching_event_degrades(self):
        """GuaranteeReport with no matching event → no ig:state derived → Degraded."""
        turtle = _IG_PFX + _IG_REPORT  # no event triples
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Degraded"
        c = result["conditions"][0]
        assert c["type"] == "GuaranteeReport"
        assert "error" in c
        assert c["passed"] is False

    def test_event_for_different_intent_ignored(self):
        """GuaranteeAccepted issued for a different intent URI → no state derived → Degraded."""
        turtle = (
            _IG_PFX
            + _IG_REPORT
            + "<urn:t:event> a ig:igGuaranteeAccepted ;\n"
            "    imo:imoeventIssuedFor <urn:t:other-intent> .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Degraded"
        assert result["conditions"][0]["type"] == "GuaranteeReport"

    def test_state_already_set_compliant_is_respected(self):
        """ig:igstate asserted directly in the expression → _derive skips, evaluator reads it."""
        turtle = (
            _IG_PFX
            + "<urn:t:report> a ig:igGuaranteeReport ;\n"
            "    icm:icmabout <urn:t:intent> ;\n"
            "    ig:igstate ig:igGuaranteeStateCompliant .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Fulfilled"
        assert result["conditions"][0]["state"] == "igGuaranteeStateCompliant"

    def test_state_already_set_degraded_is_respected(self):
        """ig:igstate asserted as Degraded directly → Degraded even with no event."""
        turtle = (
            _IG_PFX
            + "<urn:t:report> a ig:igGuaranteeReport ;\n"
            "    icm:icmabout <urn:t:intent> ;\n"
            "    ig:igstate ig:igGuaranteeStateDegraded .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Degraded"
        assert result["conditions"][0]["state"] == "igGuaranteeStateDegraded"

    def test_accepted_takes_precedence_over_rejected(self):
        """When both events present, Compliant takes precedence over Degraded."""
        turtle = _IG_PFX + _IG_REPORT + _IG_ACCEPTED + (
            "<urn:t:event2> a ig:igGuaranteeRejected ;\n"
            "    imo:imoeventIssuedFor <urn:t:intent> .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Fulfilled"
        assert result["conditions"][0]["state"] == "igGuaranteeStateCompliant"

    def test_guarantee_report_inside_allof(self):
        """ig:igGuaranteeReport node as child of log:allOf combinator."""
        turtle = (
            _IG_PFX
            + "<urn:t:root> <http://tio.models.tmforum.org/tio/v3.6.0/LogicalOperators/allOf>"
            " ( <urn:t:report> ) .\n"
            + _IG_REPORT
            + _IG_ACCEPTED
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Fulfilled"
        assert result["conditions"][0]["type"] == "GuaranteeReport"

    def test_two_reports_both_compliant(self):
        """Two guarantee reports, both with accepted events → Fulfilled."""
        turtle = (
            _IG_PFX
            + "<urn:t:r1> a ig:igGuaranteeReport ; icm:icmabout <urn:t:i1> .\n"
            "<urn:t:r2> a ig:igGuaranteeReport ; icm:icmabout <urn:t:i2> .\n"
            "<urn:t:e1> a ig:igGuaranteeAccepted ; imo:imoeventIssuedFor <urn:t:i1> .\n"
            "<urn:t:e2> a ig:igGuaranteeAccepted ; imo:imoeventIssuedFor <urn:t:i2> .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Fulfilled"
        assert all(c["passed"] for c in result["conditions"])

    def test_two_reports_one_degraded(self):
        """Two reports: one compliant, one rejected → overall Degraded."""
        turtle = (
            _IG_PFX
            + "<urn:t:r1> a ig:igGuaranteeReport ; icm:icmabout <urn:t:i1> .\n"
            "<urn:t:r2> a ig:igGuaranteeReport ; icm:icmabout <urn:t:i2> .\n"
            "<urn:t:e1> a ig:igGuaranteeAccepted ; imo:imoeventIssuedFor <urn:t:i1> .\n"
            "<urn:t:e2> a ig:igGuaranteeRejected ; imo:imoeventIssuedFor <urn:t:i2> .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Degraded"
        passed = [c["passed"] for c in result["conditions"]]
        assert True in passed
        assert False in passed


# ── IntentSpecification evaluation (tmf_insp_eval.rules Python port) ─────────

_INSP_PFX = """\
@prefix insp: <http://tio.models.tmforum.org/tio/v3.6.0/IntentSpecification/> .
@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .
"""


class TestInspValueSelected:
    """insp:inspvalueSelected — chosen value in allowed container."""

    def test_chosen_value_in_allowed_container_passes(self):
        """OT's allowedValues member is in the allowed container → Fulfilled."""
        turtle = (
            _INSP_PFX
            + "<urn:t:fn> a insp:inspvalueSelected ;\n"
            "    rdf:first <urn:t:ot> ;\n"
            "    rdf:rest  <urn:t:rest> .\n"
            "<urn:t:ot> insp:inspallowedValues <urn:t:vals> .\n"
            "<urn:t:vals> rdfs:member <urn:t:v1> .\n"
            "<urn:t:rest> rdfs:member <urn:t:allowed> .\n"
            "<urn:t:allowed> rdfs:member <urn:t:v1> .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Fulfilled"
        c = result["conditions"][0]
        assert c["type"] == "valueSelected"
        assert c["passed"] is True

    def test_chosen_value_not_in_allowed_container_degrades(self):
        """OT's allowedValues has no overlap with allowed container → Degraded."""
        turtle = (
            _INSP_PFX
            + "<urn:t:fn> a insp:inspvalueSelected ;\n"
            "    rdf:first <urn:t:ot> ;\n"
            "    rdf:rest  <urn:t:rest> .\n"
            "<urn:t:ot> insp:inspallowedValues <urn:t:vals> .\n"
            "<urn:t:vals> rdfs:member <urn:t:v1> .\n"
            "<urn:t:rest> rdfs:member <urn:t:allowed> .\n"
            "<urn:t:allowed> rdfs:member <urn:t:v2> .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Degraded"
        assert result["conditions"][0]["passed"] is False

    def test_missing_ot_first_arg_degrades(self):
        """No rdf:first → error condition → Degraded."""
        turtle = _INSP_PFX + "<urn:t:fn> a insp:inspvalueSelected .\n"
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Degraded"
        assert "error" in result["conditions"][0]

    def test_empty_allowed_values_degrades(self):
        """OT has inspallowedValues container but no members → Degraded."""
        turtle = (
            _INSP_PFX
            + "<urn:t:fn> a insp:inspvalueSelected ;\n"
            "    rdf:first <urn:t:ot> ;\n"
            "    rdf:rest  <urn:t:rest> .\n"
            "<urn:t:ot> insp:inspallowedValues <urn:t:vals> .\n"
            "<urn:t:rest> rdfs:member <urn:t:allowed> .\n"
            "<urn:t:allowed> rdfs:member <urn:t:v1> .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Degraded"
        assert "empty" in result["conditions"][0].get("error", "")

    def test_multiple_allowed_containers_any_match_passes(self):
        """Two allowed containers; value matches only the second → Fulfilled."""
        turtle = (
            _INSP_PFX
            + "<urn:t:fn> a insp:inspvalueSelected ;\n"
            "    rdf:first <urn:t:ot> ;\n"
            "    rdf:rest  <urn:t:rest> .\n"
            "<urn:t:ot> insp:inspallowedValues <urn:t:vals> .\n"
            "<urn:t:vals> rdfs:member <urn:t:v1> .\n"
            "<urn:t:rest> rdfs:member <urn:t:ac1> , <urn:t:ac2> .\n"
            "<urn:t:ac1> rdfs:member <urn:t:other> .\n"
            "<urn:t:ac2> rdfs:member <urn:t:v1> .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Fulfilled"


class TestInspChosenAny:
    """insp:inspchosenAny — at least one ContentTemplate has content."""

    def test_one_member_with_content_passes(self):
        """Single member ContentTemplate with insp:inspcontent → Fulfilled."""
        turtle = (
            _INSP_PFX
            + "<urn:t:fn> a insp:inspchosenAny ;\n"
            "    rdfs:member <urn:t:ct> .\n"
            "<urn:t:ct> a insp:inspContentTemplate ;\n"
            "    insp:inspcontent <urn:t:stuff> .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Fulfilled"
        assert result["conditions"][0]["passed"] is True

    def test_no_members_degrades(self):
        """Function node with no rdfs:member → error → Degraded."""
        turtle = _INSP_PFX + "<urn:t:fn> a insp:inspchosenAny .\n"
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Degraded"
        assert "error" in result["conditions"][0]

    def test_member_without_content_degrades(self):
        """Member is typed ContentTemplate but has no insp:inspcontent → Degraded."""
        turtle = (
            _INSP_PFX
            + "<urn:t:fn> a insp:inspchosenAny ;\n"
            "    rdfs:member <urn:t:ct> .\n"
            "<urn:t:ct> a insp:inspContentTemplate .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Degraded"

    def test_member_not_content_template_type_degrades(self):
        """Member has insp:inspcontent but wrong type → Degraded."""
        turtle = (
            _INSP_PFX
            + "<urn:t:fn> a insp:inspchosenAny ;\n"
            "    rdfs:member <urn:t:ct> .\n"
            "<urn:t:ct> insp:inspcontent <urn:t:stuff> .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Degraded"

    def test_second_member_with_content_passes(self):
        """Two members; only the second has content → Fulfilled (any semantics)."""
        turtle = (
            _INSP_PFX
            + "<urn:t:fn> a insp:inspchosenAny ;\n"
            "    rdfs:member <urn:t:ct1> , <urn:t:ct2> .\n"
            "<urn:t:ct1> a insp:inspContentTemplate .\n"
            "<urn:t:ct2> a insp:inspContentTemplate ;\n"
            "    insp:inspcontent <urn:t:stuff> .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Fulfilled"


class TestInspUsedVocabularyFor:
    """insp:inspusedVocabularyFor — intent element uses vocabulary term as predicate."""

    def test_term_used_as_predicate_passes(self):
        """Intent element uses a vocabulary term → Fulfilled."""
        turtle = (
            _INSP_PFX
            + "<urn:t:fn> a insp:inspusedVocabularyFor ;\n"
            "    rdf:first <urn:t:elem> ;\n"
            "    rdf:rest  <urn:t:vl> .\n"
            "<urn:t:vl> rdfs:member <urn:t:vc> .\n"
            "<urn:t:vc> rdfs:member <urn:t:term> .\n"
            "<urn:t:elem> <urn:t:term> <urn:t:value> .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Fulfilled"
        assert result["conditions"][0]["passed"] is True

    def test_no_vocabulary_term_used_degrades(self):
        """Intent element does not use any vocabulary term → Degraded."""
        turtle = (
            _INSP_PFX
            + "<urn:t:fn> a insp:inspusedVocabularyFor ;\n"
            "    rdf:first <urn:t:elem> ;\n"
            "    rdf:rest  <urn:t:vl> .\n"
            "<urn:t:vl> rdfs:member <urn:t:vc> .\n"
            "<urn:t:vc> rdfs:member <urn:t:term> .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Degraded"
        assert result["conditions"][0]["passed"] is False

    def test_empty_vocabulary_list_degrades(self):
        """VocabList has no members → error condition → Degraded."""
        turtle = (
            _INSP_PFX
            + "<urn:t:fn> a insp:inspusedVocabularyFor ;\n"
            "    rdf:first <urn:t:elem> ;\n"
            "    rdf:rest  <urn:t:vl> .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Degraded"
        assert "error" in result["conditions"][0]

    def test_missing_intent_elem_degrades(self):
        """No rdf:first → error → Degraded."""
        turtle = _INSP_PFX + "<urn:t:fn> a insp:inspusedVocabularyFor .\n"
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Degraded"
        assert "error" in result["conditions"][0]

    def test_term_in_second_container_passes(self):
        """Term is in the second vocab container → any-match semantics → Fulfilled."""
        turtle = (
            _INSP_PFX
            + "<urn:t:fn> a insp:inspusedVocabularyFor ;\n"
            "    rdf:first <urn:t:elem> ;\n"
            "    rdf:rest  <urn:t:vl> .\n"
            "<urn:t:vl> rdfs:member <urn:t:vc1> , <urn:t:vc2> .\n"
            "<urn:t:vc1> rdfs:member <urn:t:other-term> .\n"
            "<urn:t:vc2> rdfs:member <urn:t:term> .\n"
            "<urn:t:elem> <urn:t:term> <urn:t:value> .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Fulfilled"

    def test_inside_allof_combinator(self):
        """inspusedVocabularyFor inside log:allOf → evaluated via tree traversal."""
        turtle = (
            _INSP_PFX
            + "<urn:t:root> <http://tio.models.tmforum.org/tio/v3.6.0/LogicalOperators/allOf>"
            " ( <urn:t:fn> ) .\n"
            "<urn:t:fn> a insp:inspusedVocabularyFor ;\n"
            "    rdf:first <urn:t:elem> ;\n"
            "    rdf:rest  <urn:t:vl> .\n"
            "<urn:t:vl> rdfs:member <urn:t:vc> .\n"
            "<urn:t:vc> rdfs:member <urn:t:term> .\n"
            "<urn:t:elem> <urn:t:term> <urn:t:value> .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Fulfilled"


# ── Math function evaluation (tmf_mathfn_eval.rules Python port) ─────────────

_MF_PFX = """\
@prefix mf:   <http://tio.models.tmforum.org/tio/v3.6.0/MathFunctions/> .
@prefix quan: <http://tio.models.tmforum.org/tio/v3.6.0/QuantityOntology/> .
@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .
"""

# Shared scaffold: mf:mflogistic node used as rdf:first of a quanatLeast cond
_MF_COND_WRAP = (
    "<urn:t:cond> a quan:quanatLeast ;\n"
    "    rdf:first <urn:t:fn> ;\n"
    "    rdf:rest  [ rdf:first <urn:t:bnd> ] .\n"
    "<urn:t:bnd> rdf:value \"{bnd}\"^^xsd:decimal .\n"
)


class TestMfLogistic:
    """mf:mflogistic — L / (1 + exp(-k*(x-x0))) + c"""

    def test_midpoint_gives_half_max(self):
        """At x=x0 with defaults, logistic(0) = 0.5; bound 0.4 → Fulfilled."""
        turtle = (
            _MF_PFX
            + _MF_COND_WRAP.format(bnd="0.4")
            + "<urn:t:fn> a mf:mflogistic ;\n"
            "    mf:mfinput <urn:t:inp> .\n"
            "<urn:t:inp> rdf:value \"0\"^^xsd:decimal .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Fulfilled"
        c = result["conditions"][0]
        assert c["passed"] is True
        assert abs(c["observed"] - 0.5) < 1e-6

    def test_large_positive_x_approaches_max(self):
        """x=10, L=1, k=1 → logistic ≈ 0.9999; bound 0.99 → Fulfilled."""
        turtle = (
            _MF_PFX
            + _MF_COND_WRAP.format(bnd="0.99")
            + "<urn:t:fn> a mf:mflogistic ;\n"
            "    mf:mfinput <urn:t:inp> ;\n"
            "    mf:mfk <urn:t:k> ;\n"
            "    mf:mfl <urn:t:l> .\n"
            "<urn:t:inp> rdf:value \"10\"^^xsd:decimal .\n"
            "<urn:t:k>   rdf:value \"1\"^^xsd:decimal .\n"
            "<urn:t:l>   rdf:value \"1\"^^xsd:decimal .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Fulfilled"

    def test_vertical_stretch_and_offset(self):
        """L=2, c=1 at x=x0 → result = 2/2 + 1 = 2; bound 1.9 → Fulfilled."""
        turtle = (
            _MF_PFX
            + _MF_COND_WRAP.format(bnd="1.9")
            + "<urn:t:fn> a mf:mflogistic ;\n"
            "    mf:mfinput <urn:t:inp> ;\n"
            "    mf:mfl <urn:t:l> ;\n"
            "    mf:mfc <urn:t:c> .\n"
            "<urn:t:inp> rdf:value \"0\"^^xsd:decimal .\n"
            "<urn:t:l>   rdf:value \"2\"^^xsd:decimal .\n"
            "<urn:t:c>   rdf:value \"1\"^^xsd:decimal .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Fulfilled"
        c = result["conditions"][0]
        assert abs(c["observed"] - 2.0) < 1e-6

    def test_large_negative_x_approaches_zero(self):
        """x=-10 → logistic ≈ 0.00005; bound 0.001 → Degraded (0.00005 < 0.001)."""
        turtle = (
            _MF_PFX
            + _MF_COND_WRAP.format(bnd="0.001")
            + "<urn:t:fn> a mf:mflogistic ;\n"
            "    mf:mfinput <urn:t:inp> .\n"
            "<urn:t:inp> rdf:value \"-10\"^^xsd:decimal .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Degraded"

    def test_horizontal_shift_x0(self):
        """x0=5: logistic at x=5 = 0.5; x=5 with x0=5 → midpoint → 0.5 ≥ 0.4 → Fulfilled."""
        turtle = (
            _MF_PFX
            + _MF_COND_WRAP.format(bnd="0.4")
            + "<urn:t:fn> a mf:mflogistic ;\n"
            "    mf:mfinput <urn:t:inp> ;\n"
            "    mf:mfx0 <urn:t:x0> .\n"
            "<urn:t:inp> rdf:value \"5\"^^xsd:decimal .\n"
            "<urn:t:x0>  rdf:value \"5\"^^xsd:decimal .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Fulfilled"

    def test_missing_input_skips_computation(self):
        """No mf:mfinput → rdf:value not set → quantity eval fails (missing value)."""
        turtle = (
            _MF_PFX
            + _MF_COND_WRAP.format(bnd="0.4")
            + "<urn:t:fn> a mf:mflogistic .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Degraded"


class TestMfPoly:
    """mf:mfpoly — l * sum(coeff_i * x^i) + c"""

    def test_constant_polynomial(self):
        """Single coefficient [5], l=1, c=0 → f(x) = 5; bound 4 → Fulfilled."""
        turtle = (
            _MF_PFX
            + _MF_COND_WRAP.format(bnd="4")
            + "<urn:t:fn> a mf:mfpoly ;\n"
            "    mf:mfinput        <urn:t:inp> ;\n"
            "    mf:mfcoefficients ( <urn:t:c0> ) .\n"
            "<urn:t:inp> rdf:value \"3\"^^xsd:decimal .\n"
            "<urn:t:c0>  rdf:value \"5\"^^xsd:decimal .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Fulfilled"
        assert abs(result["conditions"][0]["observed"] - 5.0) < 1e-6

    def test_linear_polynomial(self):
        """Coefficients [1, 2] → f(x) = 1 + 2*3 = 7; bound 6 → Fulfilled."""
        turtle = (
            _MF_PFX
            + _MF_COND_WRAP.format(bnd="6")
            + "<urn:t:fn> a mf:mfpoly ;\n"
            "    mf:mfinput        <urn:t:inp> ;\n"
            "    mf:mfcoefficients ( <urn:t:c0> <urn:t:c1> ) .\n"
            "<urn:t:inp> rdf:value \"3\"^^xsd:decimal .\n"
            "<urn:t:c0>  rdf:value \"1\"^^xsd:decimal .\n"
            "<urn:t:c1>  rdf:value \"2\"^^xsd:decimal .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Fulfilled"
        assert abs(result["conditions"][0]["observed"] - 7.0) < 1e-6

    def test_quadratic_polynomial(self):
        """[0, 0, 1] → f(x) = x² ; x=3 → 9; bound 8 → Fulfilled."""
        turtle = (
            _MF_PFX
            + _MF_COND_WRAP.format(bnd="8")
            + "<urn:t:fn> a mf:mfpoly ;\n"
            "    mf:mfinput        <urn:t:inp> ;\n"
            "    mf:mfcoefficients ( <urn:t:c0> <urn:t:c1> <urn:t:c2> ) .\n"
            "<urn:t:inp> rdf:value \"3\"^^xsd:decimal .\n"
            "<urn:t:c0>  rdf:value \"0\"^^xsd:decimal .\n"
            "<urn:t:c1>  rdf:value \"0\"^^xsd:decimal .\n"
            "<urn:t:c2>  rdf:value \"1\"^^xsd:decimal .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Fulfilled"
        assert abs(result["conditions"][0]["observed"] - 9.0) < 1e-6

    def test_stretch_and_offset(self):
        """[1] (constant 1), l=3, c=2 → f = 3*1 + 2 = 5; bound 4 → Fulfilled."""
        turtle = (
            _MF_PFX
            + _MF_COND_WRAP.format(bnd="4")
            + "<urn:t:fn> a mf:mfpoly ;\n"
            "    mf:mfinput        <urn:t:inp> ;\n"
            "    mf:mfcoefficients ( <urn:t:c0> ) ;\n"
            "    mf:mfl <urn:t:l> ;\n"
            "    mf:mfc <urn:t:c> .\n"
            "<urn:t:inp> rdf:value \"0\"^^xsd:decimal .\n"
            "<urn:t:c0>  rdf:value \"1\"^^xsd:decimal .\n"
            "<urn:t:l>   rdf:value \"3\"^^xsd:decimal .\n"
            "<urn:t:c>   rdf:value \"2\"^^xsd:decimal .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Fulfilled"
        assert abs(result["conditions"][0]["observed"] - 5.0) < 1e-6

    def test_poly_below_bound_degrades(self):
        """f(x)=2 with bound 3 → Degraded."""
        turtle = (
            _MF_PFX
            + _MF_COND_WRAP.format(bnd="3")
            + "<urn:t:fn> a mf:mfpoly ;\n"
            "    mf:mfinput        <urn:t:inp> ;\n"
            "    mf:mfcoefficients ( <urn:t:c0> ) .\n"
            "<urn:t:inp> rdf:value \"0\"^^xsd:decimal .\n"
            "<urn:t:c0>  rdf:value \"2\"^^xsd:decimal .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Degraded"

    def test_missing_coefficients_skips(self):
        """No mf:mfcoefficients → rdf:value not set → Degraded."""
        turtle = (
            _MF_PFX
            + _MF_COND_WRAP.format(bnd="4")
            + "<urn:t:fn> a mf:mfpoly ;\n"
            "    mf:mfinput <urn:t:inp> .\n"
            "<urn:t:inp> rdf:value \"3\"^^xsd:decimal .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Degraded"


class TestMfMapping:
    """mf:mfmapping — piecewise lookup: map input value to result."""

    def test_matching_entry_returns_result(self):
        """Input 2 maps to result 10; bound 9 → Fulfilled."""
        turtle = (
            _MF_PFX
            + _MF_COND_WRAP.format(bnd="9")
            + "<urn:t:fn> a mf:mfmapping ;\n"
            "    mf:mfinput <urn:t:inp> ;\n"
            "    mf:mfmap   ( ( <urn:t:r1> <urn:t:v1> ) ( <urn:t:r2> <urn:t:v2> ) ) .\n"
            "<urn:t:inp> rdf:value \"2\"^^xsd:decimal .\n"
            "<urn:t:r1>  rdf:value \"100\"^^xsd:decimal .\n"
            "<urn:t:v1>  rdf:value \"1\"^^xsd:decimal .\n"
            "<urn:t:r2>  rdf:value \"10\"^^xsd:decimal .\n"
            "<urn:t:v2>  rdf:value \"2\"^^xsd:decimal .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Fulfilled"
        assert abs(result["conditions"][0]["observed"] - 10.0) < 1e-6

    def test_no_matching_entry_skips(self):
        """Input 5 not in any mapping entry → rdf:value not set → Degraded."""
        turtle = (
            _MF_PFX
            + _MF_COND_WRAP.format(bnd="9")
            + "<urn:t:fn> a mf:mfmapping ;\n"
            "    mf:mfinput <urn:t:inp> ;\n"
            "    mf:mfmap   ( ( <urn:t:r1> <urn:t:v1> ) ) .\n"
            "<urn:t:inp> rdf:value \"5\"^^xsd:decimal .\n"
            "<urn:t:r1>  rdf:value \"10\"^^xsd:decimal .\n"
            "<urn:t:v1>  rdf:value \"1\"^^xsd:decimal .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Degraded"

    def test_multiple_sources_per_entry(self):
        """Entry [result=7, src=1, src=2, src=3]: input 3 matches → Fulfilled."""
        turtle = (
            _MF_PFX
            + _MF_COND_WRAP.format(bnd="6")
            + "<urn:t:fn> a mf:mfmapping ;\n"
            "    mf:mfinput <urn:t:inp> ;\n"
            "    mf:mfmap   ( ( <urn:t:res> <urn:t:s1> <urn:t:s2> <urn:t:s3> ) ) .\n"
            "<urn:t:inp> rdf:value \"3\"^^xsd:decimal .\n"
            "<urn:t:res> rdf:value \"7\"^^xsd:decimal .\n"
            "<urn:t:s1>  rdf:value \"1\"^^xsd:decimal .\n"
            "<urn:t:s2>  rdf:value \"2\"^^xsd:decimal .\n"
            "<urn:t:s3>  rdf:value \"3\"^^xsd:decimal .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Fulfilled"
        assert abs(result["conditions"][0]["observed"] - 7.0) < 1e-6

    def test_first_matching_entry_wins(self):
        """Two entries both match input 1 (impossible in practice but logic uses first)."""
        turtle = (
            _MF_PFX
            + _MF_COND_WRAP.format(bnd="4")
            + "<urn:t:fn> a mf:mfmapping ;\n"
            "    mf:mfinput <urn:t:inp> ;\n"
            "    mf:mfmap   ( ( <urn:t:r1> <urn:t:v1> ) ( <urn:t:r2> <urn:t:v1> ) ) .\n"
            "<urn:t:inp> rdf:value \"1\"^^xsd:decimal .\n"
            "<urn:t:r1>  rdf:value \"5\"^^xsd:decimal .\n"
            "<urn:t:r2>  rdf:value \"99\"^^xsd:decimal .\n"
            "<urn:t:v1>  rdf:value \"1\"^^xsd:decimal .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Fulfilled"
        assert abs(result["conditions"][0]["observed"] - 5.0) < 1e-6

    def test_missing_map_skips(self):
        """No mf:mfmap → rdf:value not set → Degraded."""
        turtle = (
            _MF_PFX
            + _MF_COND_WRAP.format(bnd="9")
            + "<urn:t:fn> a mf:mfmapping ;\n"
            "    mf:mfinput <urn:t:inp> .\n"
            "<urn:t:inp> rdf:value \"2\"^^xsd:decimal .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        assert result["intentHandlingState"] == "Degraded"


# ── Extension type-propagation (tmf_ext_eval.rules Python port) ──────────────

_UT  = rdflib.Namespace("http://tio.models.tmforum.org/tio/v3.6.0/Utility/")
_PRE = rdflib.Namespace("http://tio.models.tmforum.org/tio/v3.6.0/PreferenceOfHandlingOutcomes/")
_PBI = rdflib.Namespace("http://tio.models.tmforum.org/tio/v3.6.0/ProposalBestIntent/")
_ICM_NS = rdflib.Namespace("http://tio.models.tmforum.org/tio/v3.6.0/IntentCommonModel/")

_EXT_PFX = """\
@prefix ut:   <http://tio.models.tmforum.org/tio/v3.6.0/Utility/> .
@prefix pre:  <http://tio.models.tmforum.org/tio/v3.6.0/PreferenceOfHandlingOutcomes/> .
@prefix pbi:  <http://tio.models.tmforum.org/tio/v3.6.0/ProposalBestIntent/> .
@prefix icm:  <http://tio.models.tmforum.org/tio/v3.6.0/IntentCommonModel/> .
@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
"""


def _parse(turtle: str) -> rdflib.Graph:
    g = rdflib.Graph()
    g.parse(data=turtle, format="turtle")
    return g


class TestDeriveExtTypesUtility:
    """Utility type propagation rules."""

    def test_ututility_property_infers_utility_information(self):
        """(?X ut:ututility ?U) → (?U rdf:type ut:utUtilityInformation)"""
        g = _parse(_EXT_PFX + "<urn:x> ut:ututility <urn:u> .\n")
        _derive_ext_types(g)
        assert (rdflib.URIRef("urn:u"), RDF.type, _UT.utUtilityInformation) in g

    def test_utility_information_infers_icm_information(self):
        """(?U rdf:type ut:utUtilityInformation) → (?U rdf:type icm:icmInformation)"""
        g = _parse(_EXT_PFX + "<urn:u> a ut:utUtilityInformation .\n")
        _derive_ext_types(g)
        assert (rdflib.URIRef("urn:u"), RDF.type, _ICM_NS.icmInformation) in g

    def test_utility_via_property_chains_to_icm_information(self):
        """Property chain: ututility → utUtilityInformation → icmInformation."""
        g = _parse(_EXT_PFX + "<urn:x> ut:ututility <urn:u> .\n")
        _derive_ext_types(g)
        assert (rdflib.URIRef("urn:u"), RDF.type, _ICM_NS.icmInformation) in g

    def test_utility_profile_property_infers_profile_type(self):
        """(?X ut:ututilityProfile ?P) → (?P rdf:type ut:utUtilityProfile)"""
        g = _parse(_EXT_PFX + "<urn:x> ut:ututilityProfile <urn:p> .\n")
        _derive_ext_types(g)
        assert (rdflib.URIRef("urn:p"), RDF.type, _UT.utUtilityProfile) in g


class TestDeriveExtTypesPreference:
    """Preference type propagation rules."""

    def test_prepreference_property_infers_preference_type(self):
        """(?X pre:prepreference ?P) → (?P rdf:type pre:prePreference)"""
        g = _parse(_EXT_PFX + "<urn:x> pre:prepreference <urn:p> .\n")
        _derive_ext_types(g)
        assert (rdflib.URIRef("urn:p"), RDF.type, _PRE.prePreference) in g

    def test_prejudgementrequest_property_infers_judgement_type(self):
        """(?X pre:prejudgementRequest ?J) → (?J rdf:type pre:preJudgementRequest)"""
        g = _parse(_EXT_PFX + "<urn:x> pre:prejudgementRequest <urn:j> .\n")
        _derive_ext_types(g)
        assert (rdflib.URIRef("urn:j"), RDF.type, _PRE.preJudgementRequest) in g

    def test_judgement_request_infers_container(self):
        """(?J rdf:type pre:preJudgementRequest) → (?J rdf:type rdfs:Container)"""
        g = _parse(_EXT_PFX + "<urn:j> a pre:preJudgementRequest .\n")
        _derive_ext_types(g)
        assert (rdflib.URIRef("urn:j"), RDF.type, RDFS.Container) in g

    def test_judgement_request_via_property_chains_to_container(self):
        """Property chain: prejudgementRequest → preJudgementRequest → rdfs:Container."""
        g = _parse(_EXT_PFX + "<urn:x> pre:prejudgementRequest <urn:j> .\n")
        _derive_ext_types(g)
        assert (rdflib.URIRef("urn:j"), RDF.type, RDFS.Container) in g


class TestDeriveExtTypesProposal:
    """Proposal/BestIntent type propagation rules."""

    def test_pbiproposal_property_infers_best_proposal_report(self):
        """(?X pbi:pbiproposal ?R) → (?R rdf:type pbi:pbiBestProposalReport)"""
        g = _parse(_EXT_PFX + "<urn:x> pbi:pbiproposal <urn:r> .\n")
        _derive_ext_types(g)
        assert (rdflib.URIRef("urn:r"), RDF.type, _PBI.pbiBestProposalReport) in g

    def test_best_proposal_report_infers_expectation_report(self):
        """(?R rdf:type pbi:pbiBestProposalReport) → (?R rdf:type icm:icmExpectationReport)"""
        g = _parse(_EXT_PFX + "<urn:r> a pbi:pbiBestProposalReport .\n")
        _derive_ext_types(g)
        assert (rdflib.URIRef("urn:r"), RDF.type, _ICM_NS.icmExpectationReport) in g

    def test_pbiproposal_chains_to_expectation_report(self):
        """Property chain: pbiproposal → pbiBestProposalReport → icmExpectationReport."""
        g = _parse(_EXT_PFX + "<urn:x> pbi:pbiproposal <urn:r> .\n")
        _derive_ext_types(g)
        assert (rdflib.URIRef("urn:r"), RDF.type, _ICM_NS.icmExpectationReport) in g

    def test_pbiproposed_property_infers_proposal_type(self):
        """(?R pbi:pbiproposed ?P) → (?P rdf:type pbi:pbiProposal)"""
        g = _parse(_EXT_PFX + "<urn:r> pbi:pbiproposed <urn:p> .\n")
        _derive_ext_types(g)
        assert (rdflib.URIRef("urn:p"), RDF.type, _PBI.pbiProposal) in g

    def test_best_proposal_expectation_infers_reporting_expectation(self):
        """(?R rdf:type pbi:pbiBestProposalExpectation) → (?R rdf:type icm:icmReportingExpectation)"""
        g = _parse(_EXT_PFX + "<urn:r> a pbi:pbiBestProposalExpectation .\n")
        _derive_ext_types(g)
        assert (rdflib.URIRef("urn:r"), RDF.type, _ICM_NS.icmReportingExpectation) in g


class TestExtTypesViaEvaluateTurtleConditions:
    """Ext-ontology nodes don't break evaluate_turtle_conditions and pass silently."""

    def test_utility_node_in_expression_passes_silently(self):
        """Utility node in an otherwise empty expression passes (opaque)."""
        turtle = (
            _EXT_PFX
            + "@prefix quan: <http://tio.models.tmforum.org/tio/v3.6.0/QuantityOntology/> .\n"
            "@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .\n"
            "@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .\n"
            "<urn:cond> a quan:quanatLeast ;\n"
            "    rdf:first <urn:v> ; rdf:rest [ rdf:first <urn:b> ] .\n"
            "<urn:v> rdf:value \"5\"^^xsd:decimal .\n"
            "<urn:b> rdf:value \"3\"^^xsd:decimal .\n"
            "<urn:intent> ut:ututility <urn:util> .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        # Quantity condition (5 >= 3) should still evaluate correctly
        assert result["intentHandlingState"] == "Fulfilled"

    def test_preference_node_in_expression_does_not_break_evaluation(self):
        """Preference nodes alongside quantity conditions don't interfere."""
        turtle = (
            _EXT_PFX
            + "@prefix quan: <http://tio.models.tmforum.org/tio/v3.6.0/QuantityOntology/> .\n"
            "@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .\n"
            "@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .\n"
            "<urn:cond> a quan:quanatLeast ;\n"
            "    rdf:first <urn:v> ; rdf:rest [ rdf:first <urn:b> ] .\n"
            "<urn:v> rdf:value \"2\"^^xsd:decimal .\n"
            "<urn:b> rdf:value \"4\"^^xsd:decimal .\n"
            "<urn:intent> pre:prepreference <urn:pref> .\n"
        )
        result = evaluate_turtle_conditions(turtle)
        # 2 < 4 → Degraded; preference node doesn't interfere
        assert result["intentHandlingState"] == "Degraded"


# ── dispatcher ────────────────────────────────────────────────────────────────


class TestDispatchEvaluation:
    async def test_dispatch_creates_report_on_success(self):
        mock_client = MagicMock(spec=FusekiClient)
        mock_report_repo = MagicMock()
        mock_report_repo.create = AsyncMock(return_value={"id": "r1"})
        mock_hub_repo = MagicMock()

        with patch(
            "src.handler.dispatcher.evaluate_intent",
            AsyncMock(return_value={"intentHandlingState": "Active", "reason": None}),
        ):
            await dispatch_evaluation(INTENT_ID, mock_client, mock_report_repo, mock_hub_repo)

        mock_report_repo.create.assert_called_once()
        call_args = mock_report_repo.create.call_args
        assert call_args[0][0] == INTENT_ID
        report_data = call_args[0][1]
        assert report_data["intentHandlingState"] == "Active"
        assert report_data["@type"] == "IntentReport"

    async def test_dispatch_swallows_evaluate_error(self):
        mock_client = MagicMock(spec=FusekiClient)
        mock_report_repo = MagicMock()
        mock_report_repo.create = AsyncMock()
        mock_hub_repo = MagicMock()

        with patch(
            "src.handler.dispatcher.evaluate_intent",
            AsyncMock(side_effect=RuntimeError("boom")),
        ):
            await dispatch_evaluation(INTENT_ID, mock_client, mock_report_repo, mock_hub_repo)

        mock_report_repo.create.assert_not_called()

    async def test_dispatch_swallows_create_error(self):
        mock_client = MagicMock(spec=FusekiClient)
        mock_report_repo = MagicMock()
        mock_report_repo.create = AsyncMock(side_effect=RuntimeError("db error"))
        mock_hub_repo = MagicMock()

        with patch(
            "src.handler.dispatcher.evaluate_intent",
            AsyncMock(return_value={"intentHandlingState": "Degraded", "reason": None}),
        ):
            await dispatch_evaluation(INTENT_ID, mock_client, mock_report_repo, mock_hub_repo)

    async def test_dispatch_sets_degraded_when_reason_present(self):
        mock_client = MagicMock(spec=FusekiClient)
        mock_report_repo = MagicMock()
        mock_report_repo.create = AsyncMock(return_value={"id": "r1"})
        mock_hub_repo = MagicMock()

        with patch(
            "src.handler.dispatcher.evaluate_intent",
            AsyncMock(return_value={"intentHandlingState": "Degraded", "reason": "No Turtle expression"}),
        ):
            await dispatch_evaluation(INTENT_ID, mock_client, mock_report_repo, mock_hub_repo)

        report_data = mock_report_repo.create.call_args[0][1]
        assert report_data["intentHandlingState"] == "Degraded"
        assert report_data["intentHandlingReason"] == "No Turtle expression"


class TestScheduleEvaluation:
    async def test_schedule_returns_task(self):
        mock_client = MagicMock(spec=FusekiClient)
        mock_report_repo = MagicMock()
        mock_hub_repo = MagicMock()

        with patch("src.handler.dispatcher.evaluate_intent", AsyncMock(return_value={})):
            task = schedule_evaluation(INTENT_ID, mock_client, mock_report_repo, mock_hub_repo)
            assert isinstance(task, asyncio.Task)
            await task

    async def test_schedule_task_name_includes_intent_id(self):
        mock_client = MagicMock(spec=FusekiClient)
        mock_report_repo = MagicMock()
        mock_hub_repo = MagicMock()

        with patch("src.handler.dispatcher.evaluate_intent", AsyncMock(return_value={})):
            task = schedule_evaluation(INTENT_ID, mock_client, mock_report_repo, mock_hub_repo)
            assert INTENT_ID in task.get_name()
            await task
