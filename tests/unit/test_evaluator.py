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

from src.graph.store import FusekiClient
from src.handler.evaluator import evaluate_intent, evaluate_turtle_conditions
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


class TestEvaluateIntentTurtleExpression:
    @respx.mock
    async def test_conditions_fulfilled_when_all_pass(self):
        """quanatLeast with obs>=bound → Fulfilled."""
        turtle = _AT_LEAST_TURTLE.format(obs="120", bnd="100")
        respx.post(f"{FUSEKI}/{DATASET}/sparql").mock(
            return_value=httpx.Response(200, json=_sparql_bindings(_turtle_expr_row(turtle)))
        )
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
        async with FusekiClient(FUSEKI, DATASET) as client:
            result = await evaluate_intent(INTENT_ID, client)
        assert result["intentHandlingState"] == "Degraded"


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
