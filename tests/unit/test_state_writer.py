"""
Unit tests for src/handler/state_writer.py.

Covers Turtle generation (verified by round-tripping through RDFLib) and
the write_handler_state() async function (gsp_put calls intercepted by respx).
"""
from __future__ import annotations

import pytest
import rdflib
import respx
import httpx
from rdflib.namespace import RDF
from unittest.mock import AsyncMock, patch

from src.graph.store import FusekiClient
from src.graph.nodes import handler_state_graph_uri, handler_state_condition_uri, intent_node
from src.handler.state_writer import build_handler_state_turtle, write_handler_state

FUSEKI   = "http://localhost:3030"
DATASET  = "tmf921"
INTENT_ID = "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"

_IMO  = rdflib.Namespace("http://tio.models.tmforum.org/tio/v3.6.0/IntentManagementOntology/")
_QUAN = rdflib.Namespace("http://tio.models.tmforum.org/tio/v3.6.0/QuantityOntology/")
_XSD  = rdflib.namespace.XSD

_TS = "2026-06-04T09:00:00Z"

_FULFILLED_RESULT = {
    "intentHandlingState": "Fulfilled",
    "reason": None,
    "conditions": [
        {"type": "quanatLeast", "operator": ">=", "observed": 120.0, "bound": 100.0, "passed": True},
        {"type": "quansmaller", "operator": "<",  "observed": 10.0,  "bound": 25.0,  "passed": True},
    ],
}

_DEGRADED_RESULT = {
    "intentHandlingState": "Degraded",
    "reason": "Conditions not met: 80.0 >= 100.0: FAIL",
    "conditions": [
        {"type": "quanatLeast", "operator": ">=", "observed": 80.0,  "bound": 100.0, "passed": False},
        {"type": "quansmaller", "operator": "<",  "observed": 10.0,  "bound": 25.0,  "passed": True},
    ],
}

_RANGE_RESULT = {
    "intentHandlingState": "Fulfilled",
    "reason": None,
    "conditions": [
        {"type": "quaninRange", "operator": "<=<=", "observed": 50.0, "lower": 10.0, "upper": 100.0, "passed": True},
    ],
}

_ERROR_RESULT = {
    "intentHandlingState": "Degraded",
    "reason": "No quantity conditions found in expression",
    "conditions": [],
}

_STRUCT_ERROR_RESULT = {
    "intentHandlingState": "Degraded",
    "reason": "Conditions not met: quanatLeast: missing rdf:value",
    "conditions": [
        {"type": "quanatLeast", "operator": ">=", "error": "missing rdf:value", "passed": False},
    ],
}


def _parse(turtle: str) -> rdflib.Graph:
    g = rdflib.Graph()
    g.parse(data=turtle, format="turtle")
    return g


# ── build_handler_state_turtle ────────────────────────────────────────────────


class TestBuildHandlerStateTurtle:

    def test_turtle_is_valid(self):
        turtle = build_handler_state_turtle(INTENT_ID, _FULFILLED_RESULT, _TS)
        g = _parse(turtle)
        assert len(g) > 0

    def test_intent_handling_state_fulfilled(self):
        g = _parse(build_handler_state_turtle(INTENT_ID, _FULFILLED_RESULT, _TS))
        intent = intent_node(INTENT_ID)
        states = list(g.objects(intent, _IMO.intentHandlingState))
        assert len(states) == 1
        assert str(states[0]).endswith("Fulfilled")

    def test_intent_handling_state_degraded(self):
        g = _parse(build_handler_state_turtle(INTENT_ID, _DEGRADED_RESULT, _TS))
        intent = intent_node(INTENT_ID)
        states = list(g.objects(intent, _IMO.intentHandlingState))
        assert str(states[0]).endswith("Degraded")

    def test_last_evaluated_timestamp_written(self):
        g = _parse(build_handler_state_turtle(INTENT_ID, _FULFILLED_RESULT, _TS))
        intent = intent_node(INTENT_ID)
        timestamps = list(g.objects(intent, _IMO.lastEvaluated))
        assert len(timestamps) == 1
        # RDFLib normalises Z → +00:00; check the date portion is correct
        assert "2026-06-04T09:00:00" in str(timestamps[0])

    def test_condition_links_written(self):
        g = _parse(build_handler_state_turtle(INTENT_ID, _FULFILLED_RESULT, _TS))
        intent = intent_node(INTENT_ID)
        linked = list(g.objects(intent, _IMO.hasConditionResult))
        assert len(linked) == 2

    def test_no_condition_links_when_empty(self):
        g = _parse(build_handler_state_turtle(INTENT_ID, _ERROR_RESULT, _TS))
        intent = intent_node(INTENT_ID)
        linked = list(g.objects(intent, _IMO.hasConditionResult))
        assert linked == []

    def test_two_arg_condition_type(self):
        g = _parse(build_handler_state_turtle(INTENT_ID, _FULFILLED_RESULT, _TS))
        cond0 = handler_state_condition_uri(INTENT_ID, 0)
        types = list(g.objects(cond0, RDF.type))
        assert _QUAN.quanatLeast in types

    def test_two_arg_condition_observed_and_bound(self):
        g = _parse(build_handler_state_turtle(INTENT_ID, _FULFILLED_RESULT, _TS))
        cond0 = handler_state_condition_uri(INTENT_ID, 0)
        obs = g.value(cond0, _IMO.observedValue)
        bnd = g.value(cond0, _IMO.boundValue)
        assert float(str(obs)) == 120.0
        assert float(str(bnd)) == 100.0

    def test_two_arg_condition_passed_true(self):
        g = _parse(build_handler_state_turtle(INTENT_ID, _FULFILLED_RESULT, _TS))
        cond0 = handler_state_condition_uri(INTENT_ID, 0)
        passed = g.value(cond0, _IMO.conditionPassed)
        assert str(passed) == "true"

    def test_two_arg_condition_passed_false(self):
        g = _parse(build_handler_state_turtle(INTENT_ID, _DEGRADED_RESULT, _TS))
        cond0 = handler_state_condition_uri(INTENT_ID, 0)
        passed = g.value(cond0, _IMO.conditionPassed)
        assert str(passed) == "false"

    def test_quaninRange_condition_lower_upper(self):
        g = _parse(build_handler_state_turtle(INTENT_ID, _RANGE_RESULT, _TS))
        cond0 = handler_state_condition_uri(INTENT_ID, 0)
        types = list(g.objects(cond0, RDF.type))
        assert _QUAN.quaninRange in types
        assert float(str(g.value(cond0, _IMO.observedValue))) == 50.0
        assert float(str(g.value(cond0, _IMO.lowerBound)))    == 10.0
        assert float(str(g.value(cond0, _IMO.upperBound)))    == 100.0

    def test_non_quantity_condition_no_observed_key_does_not_raise(self):
        """Regression: logMatch / DeliveryExpectation conditions have no 'observed' or
        'bound' key. _condition_block must not raise KeyError for these types."""
        result = {
            "intentHandlingState": "Fulfilled",
            "reason": None,
            "conditions": [
                {"type": "logMatch", "subject": "urn:s", "predicate": "urn:p",
                 "object": "urn:o", "passed": True},
                {"type": "DeliveryExpectation", "deliveryType": "urn:T",
                 "member_count": 1, "passed": True},
                {"type": "setForAll", "member_count": 3, "passed": True},
            ],
        }
        # Must not raise
        turtle = build_handler_state_turtle(INTENT_ID, result, _TS)
        g = _parse(turtle)
        # All three conditions are written; none have observedValue
        for i in range(3):
            cond = handler_state_condition_uri(INTENT_ID, i)
            assert g.value(cond, _IMO.conditionPassed) is not None
            assert g.value(cond, _IMO.observedValue) is None

    def test_structural_error_condition_has_error_predicate(self):
        g = _parse(build_handler_state_turtle(INTENT_ID, _STRUCT_ERROR_RESULT, _TS))
        cond0 = handler_state_condition_uri(INTENT_ID, 0)
        error = g.value(cond0, _IMO.conditionError)
        assert str(error) == "missing rdf:value"
        assert g.value(cond0, _IMO.observedValue) is None

    def test_condition_evaluated_at_timestamp(self):
        g = _parse(build_handler_state_turtle(INTENT_ID, _FULFILLED_RESULT, _TS))
        cond0 = handler_state_condition_uri(INTENT_ID, 0)
        ts = g.value(cond0, _IMO.evaluatedAt)
        assert "2026-06-04T09:00:00" in str(ts)

    def test_multiple_conditions_all_written(self):
        g = _parse(build_handler_state_turtle(INTENT_ID, _FULFILLED_RESULT, _TS))
        cond1 = handler_state_condition_uri(INTENT_ID, 1)
        types = list(g.objects(cond1, RDF.type))
        assert _QUAN.quansmaller in types


# ── write_handler_state ───────────────────────────────────────────────────────


class TestWriteHandlerState:

    @respx.mock
    async def test_gsp_put_called_with_correct_graph_uri(self):
        graph_uri = str(handler_state_graph_uri(INTENT_ID))
        route = respx.put(f"{FUSEKI}/{DATASET}/data").mock(
            return_value=httpx.Response(200)
        )
        async with FusekiClient(FUSEKI, DATASET) as client:
            await write_handler_state(INTENT_ID, _FULFILLED_RESULT, client)

        assert route.called
        assert route.calls[0].request.url.params["graph"] == graph_uri

    @respx.mock
    async def test_gsp_put_sends_turtle_content_type(self):
        respx.put(f"{FUSEKI}/{DATASET}/data").mock(return_value=httpx.Response(200))
        async with FusekiClient(FUSEKI, DATASET) as client:
            await write_handler_state(INTENT_ID, _FULFILLED_RESULT, client)

        req = respx.calls.last.request
        assert req.headers["content-type"] == "text/turtle"

    @respx.mock
    async def test_gsp_put_body_is_valid_turtle(self):
        respx.put(f"{FUSEKI}/{DATASET}/data").mock(return_value=httpx.Response(200))
        async with FusekiClient(FUSEKI, DATASET) as client:
            await write_handler_state(INTENT_ID, _FULFILLED_RESULT, client)

        body = respx.calls.last.request.content.decode()
        g = rdflib.Graph()
        g.parse(data=body, format="turtle")
        assert len(g) > 0

    @respx.mock
    async def test_write_failure_does_not_raise(self):
        respx.put(f"{FUSEKI}/{DATASET}/data").mock(
            return_value=httpx.Response(500, text="server error")
        )
        async with FusekiClient(FUSEKI, DATASET) as client:
            await write_handler_state(INTENT_ID, _FULFILLED_RESULT, client)
        # no exception raised

    @respx.mock
    async def test_write_with_empty_conditions(self):
        route = respx.put(f"{FUSEKI}/{DATASET}/data").mock(
            return_value=httpx.Response(200)
        )
        async with FusekiClient(FUSEKI, DATASET) as client:
            await write_handler_state(INTENT_ID, _ERROR_RESULT, client)

        assert route.called
        body = route.calls[0].request.content.decode()
        g = rdflib.Graph()
        g.parse(data=body, format="turtle")
        intent = intent_node(INTENT_ID)
        linked = list(g.objects(intent, _IMO.hasConditionResult))
        assert linked == []


# ── dispatcher integration: write_handler_state is called ─────────────────────


class TestDispatcherCallsStateWriter:
    async def test_write_handler_state_called_before_report(self):
        """dispatch_evaluation must call write_handler_state with the evaluation result."""
        from src.handler.dispatcher import dispatch_evaluation
        from unittest.mock import MagicMock

        mock_client = MagicMock(spec=FusekiClient)
        mock_report_repo = MagicMock()
        mock_report_repo.create = AsyncMock(return_value={"id": "r1"})
        mock_hub_repo = MagicMock()

        eval_result = {"intentHandlingState": "Fulfilled", "reason": None, "conditions": []}

        write_calls = []

        async def _fake_write(iid, result, client):
            write_calls.append((iid, result))

        with (
            patch("src.handler.dispatcher.evaluate_intent",
                  AsyncMock(return_value=eval_result)),
            patch("src.handler.dispatcher.write_handler_state",
                  side_effect=_fake_write),
        ):
            await dispatch_evaluation(INTENT_ID, mock_client, mock_report_repo, mock_hub_repo)

        assert len(write_calls) == 1
        assert write_calls[0][0] == INTENT_ID
        assert write_calls[0][1] == eval_result
