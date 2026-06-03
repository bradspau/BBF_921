"""
Unit tests for src/handler/evaluator.py and src/handler/dispatcher.py.

All HTTP calls are intercepted by respx; no live Fuseki required.

Evaluator flow:
  - Intent expression query  → tmf921/sparql
  - CLEAR DEFAULT (pre)      → tmf921-eval/update
  - GSP POST Turtle          → tmf921-eval/data  (no graph= param)
  - State query              → tmf921-eval/sparql
  - CLEAR DEFAULT (post)     → tmf921-eval/update
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import respx
import httpx

from src.graph.store import FusekiClient
from src.handler.evaluator import evaluate_intent
from src.handler.dispatcher import dispatch_evaluation, schedule_evaluation

FUSEKI   = "http://localhost:3030"
DATASET  = "tmf921"
EVAL_DS  = "tmf921-eval"
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


def _state_row(state: str = "Active") -> dict:
    return {
        "state": {
            "type": "uri",
            "value": f"http://tio.models.tmforum.org/tio/v3.6.0/IntentManagementOntology/{state}",
        }
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


# ── evaluate_intent — Turtle expression with eval dataset ─────────────────────


class TestEvaluateIntentTurtleExpression:
    @respx.mock
    async def test_state_found_returns_correct_state(self):
        turtle = "@prefix imo: <http://tio.models.tmforum.org/tio/v3.6.0/IntentManagementOntology/> ."
        respx.post(f"{FUSEKI}/{DATASET}/sparql").mock(
            return_value=httpx.Response(200, json=_sparql_bindings(_turtle_expr_row(turtle)))
        )
        respx.post(f"{FUSEKI}/{EVAL_DS}/update").mock(return_value=httpx.Response(200))
        respx.post(f"{FUSEKI}/{EVAL_DS}/data").mock(return_value=httpx.Response(200))
        respx.post(f"{FUSEKI}/{EVAL_DS}/sparql").mock(
            return_value=httpx.Response(200, json=_sparql_bindings(_state_row("Active")))
        )

        async with FusekiClient(FUSEKI, DATASET) as client:
            result = await evaluate_intent(INTENT_ID, client)

        assert result["intentHandlingState"] == "Active"
        assert result.get("reason") is None

    @respx.mock
    async def test_no_state_inferred_returns_degraded(self):
        turtle = "@prefix : <http://example.org/> ."
        respx.post(f"{FUSEKI}/{DATASET}/sparql").mock(
            return_value=httpx.Response(200, json=_sparql_bindings(_turtle_expr_row(turtle)))
        )
        respx.post(f"{FUSEKI}/{EVAL_DS}/update").mock(return_value=httpx.Response(200))
        respx.post(f"{FUSEKI}/{EVAL_DS}/data").mock(return_value=httpx.Response(200))
        respx.post(f"{FUSEKI}/{EVAL_DS}/sparql").mock(
            return_value=httpx.Response(200, json=_sparql_bindings())
        )

        async with FusekiClient(FUSEKI, DATASET) as client:
            result = await evaluate_intent(INTENT_ID, client)

        assert result["intentHandlingState"] == "Degraded"
        assert "No intentHandlingState" in (result.get("reason") or "")

    @respx.mock
    async def test_gsp_post_failure_returns_degraded(self):
        turtle = "@prefix : <http://example.org/> ."
        respx.post(f"{FUSEKI}/{DATASET}/sparql").mock(
            return_value=httpx.Response(200, json=_sparql_bindings(_turtle_expr_row(turtle)))
        )
        respx.post(f"{FUSEKI}/{EVAL_DS}/update").mock(return_value=httpx.Response(200))
        respx.post(f"{FUSEKI}/{EVAL_DS}/data").mock(
            return_value=httpx.Response(500, text="Internal Server Error")
        )

        async with FusekiClient(FUSEKI, DATASET) as client:
            result = await evaluate_intent(INTENT_ID, client)

        assert result["intentHandlingState"] == "Degraded"
        assert "failed" in (result.get("reason") or "").lower()

    @respx.mock
    async def test_eval_dataset_always_cleared(self):
        """CLEAR DEFAULT must be called before and after the state query."""
        turtle = "@prefix : <http://example.org/> ."
        respx.post(f"{FUSEKI}/{DATASET}/sparql").mock(
            return_value=httpx.Response(200, json=_sparql_bindings(_turtle_expr_row(turtle)))
        )
        respx.post(f"{FUSEKI}/{EVAL_DS}/data").mock(return_value=httpx.Response(200))
        respx.post(f"{FUSEKI}/{EVAL_DS}/sparql").mock(
            return_value=httpx.Response(200, json=_sparql_bindings(_state_row("Active")))
        )
        update_route = respx.post(f"{FUSEKI}/{EVAL_DS}/update").mock(
            return_value=httpx.Response(200)
        )

        async with FusekiClient(FUSEKI, DATASET) as client:
            await evaluate_intent(INTENT_ID, client)

        assert update_route.call_count == 2
        for call in update_route.calls:
            body = call.request.content.decode()
            assert "CLEAR" in body and "DEFAULT" in body

    @respx.mock
    async def test_clear_failure_does_not_raise(self):
        """A CLEAR DEFAULT failure must be swallowed, not raised."""
        turtle = "@prefix : <http://example.org/> ."
        respx.post(f"{FUSEKI}/{DATASET}/sparql").mock(
            return_value=httpx.Response(200, json=_sparql_bindings(_turtle_expr_row(turtle)))
        )
        respx.post(f"{FUSEKI}/{EVAL_DS}/data").mock(return_value=httpx.Response(200))
        respx.post(f"{FUSEKI}/{EVAL_DS}/sparql").mock(
            return_value=httpx.Response(200, json=_sparql_bindings(_state_row("Active")))
        )
        respx.post(f"{FUSEKI}/{EVAL_DS}/update").mock(
            return_value=httpx.Response(500, text="update failed")
        )

        async with FusekiClient(FUSEKI, DATASET) as client:
            result = await evaluate_intent(INTENT_ID, client)

        assert result["intentHandlingState"] == "Active"

    @respx.mock
    async def test_state_uri_fragment_parsed_correctly(self):
        """State URIs using a # fragment are trimmed to the local name."""
        turtle = "@prefix : <http://example.org/> ."
        respx.post(f"{FUSEKI}/{DATASET}/sparql").mock(
            return_value=httpx.Response(200, json=_sparql_bindings(_turtle_expr_row(turtle)))
        )
        respx.post(f"{FUSEKI}/{EVAL_DS}/update").mock(return_value=httpx.Response(200))
        respx.post(f"{FUSEKI}/{EVAL_DS}/data").mock(return_value=httpx.Response(200))
        respx.post(f"{FUSEKI}/{EVAL_DS}/sparql").mock(
            return_value=httpx.Response(200, json=_sparql_bindings({
                "state": {
                    "type": "uri",
                    "value": "http://tio.models.tmforum.org/tio/v3.6.0/IntentManagmentOntology#Fulfilled",
                }
            }))
        )

        async with FusekiClient(FUSEKI, DATASET) as client:
            result = await evaluate_intent(INTENT_ID, client)

        assert result["intentHandlingState"] == "Fulfilled"


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
