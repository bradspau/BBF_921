"""Unit tests for dispatcher Flow 1 (ProbeIntent) and Flow 3 (Best/Propose)."""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from src.handler.dispatcher import _try_best_propose, _try_probe_transition

_TURTLE = """\
@prefix quan: <http://tio.models.tmforum.org/tio/v3.6.0/QuantityOntology/> .
@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .

_:cond a quan:quanatLeast ;
    rdf:first _:obs ;
    rdf:rest  ( _:bnd ) .
_:obs rdf:value "15.0"^^xsd:decimal .
_:bnd rdf:value "10.0"^^xsd:decimal .
"""


@pytest.fixture
def intent_repo():
    repo = AsyncMock()
    repo.update.return_value = {"id": "test-id", "lifecycleStatus": "ACTIVE"}
    return repo


@pytest.fixture
def hub_repo():
    return AsyncMock()


# ── Flow 1: ProbeIntent auto-transition ──────────────────────────────────────

class TestTryProbeTransition:
    @pytest.mark.asyncio
    async def test_fulfilled_transitions_to_active(self, intent_repo, hub_repo):
        intent_repo.get_by_id.return_value = {
            "id": "test-id", "@type": "ProbeIntent", "lifecycleStatus": "ACKNOWLEDGED"
        }
        result = {"intentHandlingState": "Fulfilled"}

        with patch("src.handler.dispatcher.NotificationService"):
            await _try_probe_transition("test-id", result, intent_repo, hub_repo)

        intent_repo.update.assert_called_once()
        assert intent_repo.update.call_args[0][1]["lifecycleStatus"] == "ACTIVE"

    @pytest.mark.asyncio
    async def test_degraded_transitions_to_terminated(self, intent_repo, hub_repo):
        intent_repo.get_by_id.return_value = {
            "id": "test-id", "@type": "ProbeIntent", "lifecycleStatus": "ACKNOWLEDGED"
        }
        intent_repo.update.return_value = {"id": "test-id", "lifecycleStatus": "TERMINATED"}
        result = {"intentHandlingState": "Degraded"}

        with patch("src.handler.dispatcher.NotificationService"):
            await _try_probe_transition("test-id", result, intent_repo, hub_repo)

        intent_repo.update.assert_called_once()
        assert intent_repo.update.call_args[0][1]["lifecycleStatus"] == "TERMINATED"

    @pytest.mark.asyncio
    async def test_normal_intent_skipped(self, intent_repo, hub_repo):
        intent_repo.get_by_id.return_value = {
            "id": "test-id", "@type": "Intent", "lifecycleStatus": "ACKNOWLEDGED"
        }
        result = {"intentHandlingState": "Fulfilled"}

        await _try_probe_transition("test-id", result, intent_repo, hub_repo)

        intent_repo.update.assert_not_called()

    @pytest.mark.asyncio
    async def test_already_active_probe_skipped(self, intent_repo, hub_repo):
        intent_repo.get_by_id.return_value = {
            "id": "test-id", "@type": "ProbeIntent", "lifecycleStatus": "ACTIVE"
        }
        result = {"intentHandlingState": "Fulfilled"}

        await _try_probe_transition("test-id", result, intent_repo, hub_repo)

        intent_repo.update.assert_not_called()

    @pytest.mark.asyncio
    async def test_none_intent_skipped(self, intent_repo, hub_repo):
        intent_repo.get_by_id.return_value = None
        result = {"intentHandlingState": "Fulfilled"}

        await _try_probe_transition("test-id", result, intent_repo, hub_repo)

        intent_repo.update.assert_not_called()

    @pytest.mark.asyncio
    async def test_writes_state_change_audit(self, intent_repo, hub_repo):
        intent_repo.get_by_id.return_value = {
            "id": "test-id", "@type": "ProbeIntent", "lifecycleStatus": "ACKNOWLEDGED"
        }
        result = {"intentHandlingState": "Fulfilled"}

        with patch("src.handler.dispatcher.NotificationService"):
            await _try_probe_transition("test-id", result, intent_repo, hub_repo)

        intent_repo.write_state_change.assert_called_once()
        call = intent_repo.write_state_change.call_args[1]
        assert call["from_status"] == "ACKNOWLEDGED"
        assert call["to_status"] == "ACTIVE"

    @pytest.mark.asyncio
    async def test_skips_when_update_returns_none(self, intent_repo, hub_repo):
        intent_repo.get_by_id.return_value = {
            "id": "test-id", "@type": "ProbeIntent", "lifecycleStatus": "ACKNOWLEDGED"
        }
        intent_repo.update.return_value = None
        result = {"intentHandlingState": "Fulfilled"}

        with patch("src.handler.dispatcher.NotificationService"):
            await _try_probe_transition("test-id", result, intent_repo, hub_repo)

        intent_repo.write_state_change.assert_not_called()


# ── Flow 3: Best/Propose bound substitution ──────────────────────────────────

class TestTryBestPropose:
    @pytest.mark.asyncio
    async def test_patches_expression_with_best_effort_bound(self, intent_repo, hub_repo):
        intent_repo.get_by_id.return_value = {
            "id": "test-id",
            "@type": "Intent",
            "lifecycleStatus": "ACKNOWLEDGED",
            "expression": {"@type": "TurtleExpression", "expressionValue": _TURTLE},
        }
        result = {
            "intentHandlingState": "Degraded",
            "conditions": [
                {"type": "quanatLeast", "observed": Decimal("15.0"),
                 "bound": Decimal("10.0"), "passed": False}
            ],
        }

        with patch("src.handler.dispatcher.NotificationService") as mock_ns:
            await _try_best_propose("test-id", result, intent_repo, hub_repo)

        intent_repo.update.assert_called_once()
        update_call = intent_repo.update.call_args[0][1]
        assert update_call["expression"]["@type"] == "TurtleExpression"
        assert "15.0" in update_call["expression"]["expressionValue"]

    @pytest.mark.asyncio
    async def test_fires_attribute_value_change_notification(self, intent_repo, hub_repo):
        intent_repo.get_by_id.return_value = {
            "id": "test-id",
            "@type": "Intent",
            "lifecycleStatus": "ACKNOWLEDGED",
            "expression": {"@type": "TurtleExpression", "expressionValue": _TURTLE},
        }
        result = {
            "intentHandlingState": "Degraded",
            "conditions": [
                {"type": "quanatLeast", "observed": Decimal("15.0"),
                 "bound": Decimal("10.0"), "passed": False}
            ],
        }

        with patch("src.handler.dispatcher.NotificationService") as mock_ns:
            await _try_best_propose("test-id", result, intent_repo, hub_repo)

        mock_ns.return_value.schedule.assert_called_once()
        from src.services.notification_service import EventType
        assert mock_ns.return_value.schedule.call_args[0][0] == EventType.INTENT_ATTRIBUTE_VALUE_CHANGE

    @pytest.mark.asyncio
    async def test_skips_jsonld_expression(self, intent_repo, hub_repo):
        intent_repo.get_by_id.return_value = {
            "id": "test-id",
            "@type": "Intent",
            "lifecycleStatus": "ACKNOWLEDGED",
            "expression": {"@type": "JsonLdExpression", "expressionValue": {"@context": {}}},
        }
        result = {"intentHandlingState": "Degraded", "conditions": []}

        await _try_best_propose("test-id", result, intent_repo, hub_repo)

        intent_repo.update.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_probe_intent(self, intent_repo, hub_repo):
        intent_repo.get_by_id.return_value = {
            "id": "test-id",
            "@type": "ProbeIntent",
            "lifecycleStatus": "ACKNOWLEDGED",
            "expression": {"@type": "TurtleExpression", "expressionValue": _TURTLE},
        }
        result = {
            "intentHandlingState": "Degraded",
            "conditions": [
                {"type": "quanatLeast", "observed": Decimal("15.0"),
                 "bound": Decimal("10.0"), "passed": False}
            ],
        }

        await _try_best_propose("test-id", result, intent_repo, hub_repo)

        intent_repo.update.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_no_bound_substitution_possible(self, intent_repo, hub_repo):
        intent_repo.get_by_id.return_value = {
            "id": "test-id",
            "@type": "Intent",
            "lifecycleStatus": "ACKNOWLEDGED",
            "expression": {"@type": "TurtleExpression", "expressionValue": _TURTLE},
        }
        # All conditions passed — nothing to substitute.
        result = {
            "intentHandlingState": "Degraded",
            "conditions": [
                {"type": "quanatLeast", "observed": Decimal("15.0"),
                 "bound": Decimal("10.0"), "passed": True}
            ],
        }

        await _try_best_propose("test-id", result, intent_repo, hub_repo)

        intent_repo.update.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_lifecyclestatus_not_eligible(self, intent_repo, hub_repo):
        intent_repo.get_by_id.return_value = {
            "id": "test-id",
            "@type": "Intent",
            "lifecycleStatus": "TERMINATED",
            "expression": {"@type": "TurtleExpression", "expressionValue": _TURTLE},
        }
        result = {
            "intentHandlingState": "Degraded",
            "conditions": [
                {"type": "quanatLeast", "observed": Decimal("15.0"),
                 "bound": Decimal("10.0"), "passed": False}
            ],
        }

        await _try_best_propose("test-id", result, intent_repo, hub_repo)

        intent_repo.update.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_update_returns_none(self, intent_repo, hub_repo):
        intent_repo.get_by_id.return_value = {
            "id": "test-id",
            "@type": "Intent",
            "lifecycleStatus": "ACKNOWLEDGED",
            "expression": {"@type": "TurtleExpression", "expressionValue": _TURTLE},
        }
        intent_repo.update.return_value = None
        result = {
            "intentHandlingState": "Degraded",
            "conditions": [
                {"type": "quanatLeast", "observed": Decimal("15.0"),
                 "bound": Decimal("10.0"), "passed": False}
            ],
        }

        with patch("src.handler.dispatcher.NotificationService") as mock_ns:
            await _try_best_propose("test-id", result, intent_repo, hub_repo)

        mock_ns.return_value.schedule.assert_not_called()
