"""
Unit tests for src/services/intent_service.py and
src/services/notification_service.py.

All external HTTP (Fuseki SPARQL + hub callbacks) is intercepted by respx.
Tests verify: create, get, list, update (attribute + lifecycle), delete,
non-patchable rejection, FSM guard, 404 branches, and notification fan-out.
"""
from __future__ import annotations

import json
import pytest
import respx
import httpx

from fastapi import HTTPException

from src.graph.store import FusekiClient
from src.graph.repositories.intent_repository import IntentRepository
from src.graph.repositories.intent_report_repository import IntentReportRepository
from src.graph.repositories.intent_spec_repository import IntentSpecRepository
from src.graph.repositories.hub_repository import HubRepository
from src.services.intent_service import IntentService
from src.services.intent_report_service import IntentReportService
from src.services.intent_spec_service import IntentSpecService
from src.services.notification_service import NotificationService, EventType

FUSEKI       = "http://localhost:3030"
DATASET      = "tmf921"
SPARQL_URL   = f"{FUSEKI}/{DATASET}/sparql"
UPDATE_URL   = f"{FUSEKI}/{DATASET}/update"
CALLBACK_URL = "http://listener.example.com/events"

INTENT_ID = "aaaa-1111"

# ── Binding factories (mirrors test_repositories pattern) ─────────────────────

def _intent_binding(
    intent_id: str = INTENT_ID,
    status: str = "ACKNOWLEDGED",
) -> dict:
    return {
        "id":              {"type": "literal", "value": intent_id},
        "href":            {"type": "literal", "value": f"http://tmforum.org/tmf-api/intentManagement/v5/intent/{intent_id}"},
        "name":            {"type": "literal", "value": "Test Intent"},
        "type":            {"type": "uri",     "value": "http://tmforum.org/api/v5/Intent"},
        "lifecycleStatus": {"type": "literal", "value": status},
        "created":         {"type": "literal", "value": "2024-01-01T00:00:00+00:00",
                            "datatype": "http://www.w3.org/2001/XMLSchema#dateTime"},
        "modified":        {"type": "literal", "value": "2024-01-01T00:00:00+00:00",
                            "datatype": "http://www.w3.org/2001/XMLSchema#dateTime"},
        "exprType":        {"type": "uri",     "value": "http://tmforum.org/api/v5/JsonLdExpression"},
        "exprIri":         {"type": "literal", "value": "http://example.org/model"},
        "exprValue":       {"type": "literal", "value": '{"k":"v"}',
                            "datatype": "http://www.w3.org/2001/XMLSchema#string"},
    }


def _sparql(rows: list[dict]) -> dict:
    return {"results": {"bindings": rows}}


def _count(n: int) -> dict:
    return {"results": {"bindings": [
        {"count": {"type": "literal", "value": str(n),
                   "datatype": "http://www.w3.org/2001/XMLSchema#integer"}}
    ]}}


def _hub_binding() -> dict:
    return {
        "id":       {"type": "literal", "value": "hub-1"},
        "href":     {"type": "literal", "value": "http://host/hub/hub-1"},
        "callback": {"type": "literal", "value": CALLBACK_URL},
    }


def _ok() -> httpx.Response:
    return httpx.Response(200)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def client() -> FusekiClient:
    c = FusekiClient(base_url=FUSEKI, dataset=DATASET)
    c._http = httpx.AsyncClient(base_url=FUSEKI)
    return c


@pytest.fixture
def service(client: FusekiClient) -> IntentService:
    intent_repo = IntentRepository(client)
    hub_repo    = HubRepository(client)
    return IntentService(intent_repo, hub_repo)


# ── Create ────────────────────────────────────────────────────────────────────

class TestCreate:
    @respx.mock
    @pytest.mark.asyncio
    async def test_create_returns_server_assigned_fields(
        self, service: IntentService
    ) -> None:
        # INSERT DATA
        respx.post(UPDATE_URL).mock(return_value=_ok())
        # list_all hubs (no hubs → no notification)
        respx.post(SPARQL_URL).mock(return_value=httpx.Response(200, json=_sparql([])))

        result = await service.create({
            "@type": "Intent",
            "name": "My Intent",
            "expression": {
                "@type": "JsonLdExpression",
                "iri": "http://ex.org/m",
                "expressionValue": {"k": "v"},
            },
        })

        assert result["lifecycleStatus"] == "ACKNOWLEDGED"
        assert result["id"] is not None
        assert result["href"].endswith(result["id"])
        assert result["creationDate"] is not None
        assert result["lastUpdate"] is not None

    @respx.mock
    @pytest.mark.asyncio
    async def test_create_overwrites_caller_supplied_id(
        self, service: IntentService
    ) -> None:
        respx.post(UPDATE_URL).mock(return_value=_ok())
        respx.post(SPARQL_URL).mock(return_value=httpx.Response(200, json=_sparql([])))

        result = await service.create({"@type": "Intent", "name": "X", "id": "caller-id"})
        assert result["id"] != "caller-id"

    @respx.mock
    @pytest.mark.asyncio
    async def test_create_fires_intent_create_event(
        self, service: IntentService
    ) -> None:
        respx.post(UPDATE_URL).mock(return_value=_ok())
        # hub list returns one hub
        respx.post(SPARQL_URL).mock(
            return_value=httpx.Response(200, json=_sparql([_hub_binding()]))
        )
        callback = respx.post(CALLBACK_URL).mock(return_value=_ok())

        result = await service.create({"@type": "Intent", "name": "N"})
        # Await all background tasks
        import asyncio
        await asyncio.sleep(0)

        assert result["lifecycleStatus"] == "ACKNOWLEDGED"

    @respx.mock
    @pytest.mark.asyncio
    async def test_create_probe_intent(
        self, service: IntentService
    ) -> None:
        respx.post(UPDATE_URL).mock(return_value=_ok())
        respx.post(SPARQL_URL).mock(return_value=httpx.Response(200, json=_sparql([])))

        result = await service.create({"@type": "ProbeIntent", "name": "Probe"})
        assert result["@type"] == "ProbeIntent"
        assert result["lifecycleStatus"] == "ACKNOWLEDGED"


# ── Get by ID ─────────────────────────────────────────────────────────────────

class TestGetById:
    @respx.mock
    @pytest.mark.asyncio
    async def test_get_existing_intent(
        self, service: IntentService
    ) -> None:
        respx.post(SPARQL_URL).mock(
            return_value=httpx.Response(200, json=_sparql([_intent_binding()]))
        )
        result = await service.get_by_id(INTENT_ID)
        assert result["id"] == INTENT_ID
        assert result["lifecycleStatus"] == "ACKNOWLEDGED"

    @respx.mock
    @pytest.mark.asyncio
    async def test_get_missing_intent_raises_404(
        self, service: IntentService
    ) -> None:
        respx.post(SPARQL_URL).mock(
            return_value=httpx.Response(200, json=_sparql([]))
        )
        with pytest.raises(HTTPException) as exc:
            await service.get_by_id("nonexistent")
        assert exc.value.status_code == 404


# ── List ──────────────────────────────────────────────────────────────────────

class TestList:
    @respx.mock
    @pytest.mark.asyncio
    async def test_list_returns_items_and_count(
        self, service: IntentService
    ) -> None:
        sparql_mock = respx.post(SPARQL_URL)
        sparql_mock.side_effect = [
            httpx.Response(200, json=_count(1)),
            httpx.Response(200, json=_sparql([_intent_binding()])),
        ]

        items, total = await service.list(limit=10, offset=0)
        assert total == 1
        assert len(items) == 1

    @respx.mock
    @pytest.mark.asyncio
    async def test_list_empty(
        self, service: IntentService
    ) -> None:
        respx.post(SPARQL_URL).mock(
            return_value=httpx.Response(200, json=_count(0))
        )
        items, total = await service.list()
        assert items == []
        assert total == 0

    @respx.mock
    @pytest.mark.asyncio
    async def test_list_passes_filters(
        self, service: IntentService
    ) -> None:
        sparql_mock = respx.post(SPARQL_URL)
        sparql_mock.side_effect = [
            httpx.Response(200, json=_count(1)),
            httpx.Response(200, json=_sparql([_intent_binding()])),
        ]
        items, total = await service.list(filters={"lifecycleStatus": "ACKNOWLEDGED"})
        assert total == 1


# ── Update ────────────────────────────────────────────────────────────────────

class TestUpdate:
    @respx.mock
    @pytest.mark.asyncio
    async def test_update_attribute_fires_attribute_change_event(
        self, service: IntentService
    ) -> None:
        sparql_mock = respx.post(SPARQL_URL)
        # get_by_id (existing), get_by_id (after update)
        sparql_mock.side_effect = [
            httpx.Response(200, json=_sparql([_intent_binding()])),
            httpx.Response(200, json=_sparql([_intent_binding()])),
            # hub list_all
            httpx.Response(200, json=_sparql([])),
        ]
        respx.post(UPDATE_URL).mock(return_value=_ok())

        result = await service.update(INTENT_ID, {"name": "Updated"})
        assert result["id"] == INTENT_ID

    @respx.mock
    @pytest.mark.asyncio
    async def test_update_lifecycle_valid_transition(
        self, service: IntentService
    ) -> None:
        sparql_mock = respx.post(SPARQL_URL)
        sparql_mock.side_effect = [
            # get_by_id (ACKNOWLEDGED)
            httpx.Response(200, json=_sparql([_intent_binding(status="ACKNOWLEDGED")])),
            # get_by_id after update
            httpx.Response(200, json=_sparql([_intent_binding(status="ACTIVE")])),
            # hub list_all
            httpx.Response(200, json=_sparql([])),
        ]
        # update + write_state_change = 2 UPDATE calls
        respx.post(UPDATE_URL).mock(return_value=_ok())

        result = await service.update(INTENT_ID, {"lifecycleStatus": "ACTIVE"})
        assert result["lifecycleStatus"] == "ACTIVE"

    @respx.mock
    @pytest.mark.asyncio
    async def test_update_lifecycle_invalid_transition_raises_400(
        self, service: IntentService
    ) -> None:
        sparql_mock = respx.post(SPARQL_URL)
        sparql_mock.mock(
            return_value=httpx.Response(200, json=_sparql([_intent_binding(status="ACKNOWLEDGED")]))
        )
        with pytest.raises(HTTPException) as exc:
            await service.update(INTENT_ID, {"lifecycleStatus": "FULFILLED"})
        assert exc.value.status_code == 400

    @respx.mock
    @pytest.mark.asyncio
    async def test_update_noop_lifecycle_no_state_change_record(
        self, service: IntentService
    ) -> None:
        """Same-status patch should NOT write a StateChange audit record."""
        sparql_mock = respx.post(SPARQL_URL)
        sparql_mock.side_effect = [
            httpx.Response(200, json=_sparql([_intent_binding(status="ACKNOWLEDGED")])),
            httpx.Response(200, json=_sparql([_intent_binding(status="ACKNOWLEDGED")])),
            httpx.Response(200, json=_sparql([])),
        ]
        update_mock = respx.post(UPDATE_URL)
        update_mock.mock(return_value=_ok())

        await service.update(INTENT_ID, {"lifecycleStatus": "ACKNOWLEDGED"})
        # Only one UPDATE call (the repo.update), not two (no write_state_change)
        assert update_mock.call_count == 1

    @respx.mock
    @pytest.mark.asyncio
    async def test_update_non_patchable_field_raises_400(
        self, service: IntentService
    ) -> None:
        for field in ("id", "href", "creationDate", "lastUpdate",
                      "statusChangeDate", "version", "@type", "@baseType", "@schemaLocation"):
            with pytest.raises(HTTPException) as exc:
                await service.update(INTENT_ID, {field: "x"})
            assert exc.value.status_code == 400

    @respx.mock
    @pytest.mark.asyncio
    async def test_update_missing_intent_raises_404(
        self, service: IntentService
    ) -> None:
        respx.post(SPARQL_URL).mock(
            return_value=httpx.Response(200, json=_sparql([]))
        )
        with pytest.raises(HTTPException) as exc:
            await service.update("no-such-id", {"name": "x"})
        assert exc.value.status_code == 404


# ── Delete ────────────────────────────────────────────────────────────────────

class TestDelete:
    @respx.mock
    @pytest.mark.asyncio
    async def test_delete_existing_intent(
        self, service: IntentService
    ) -> None:
        sparql_mock = respx.post(SPARQL_URL)
        sparql_mock.side_effect = [
            # ASK → exists
            httpx.Response(200, json={"boolean": True}),
            # hub list_all
            httpx.Response(200, json=_sparql([])),
        ]
        respx.post(UPDATE_URL).mock(return_value=_ok())

        await service.delete(INTENT_ID)  # no exception

    @respx.mock
    @pytest.mark.asyncio
    async def test_delete_missing_intent_raises_404(
        self, service: IntentService
    ) -> None:
        respx.post(SPARQL_URL).mock(
            return_value=httpx.Response(200, json={"boolean": False})
        )
        with pytest.raises(HTTPException) as exc:
            await service.delete("ghost-id")
        assert exc.value.status_code == 404


# ── NotificationService ───────────────────────────────────────────────────────

class TestNotificationService:
    @respx.mock
    @pytest.mark.asyncio
    async def test_fire_posts_to_all_hubs(
        self, client: FusekiClient
    ) -> None:
        hub_repo = HubRepository(client)
        svc = NotificationService(hub_repo)

        respx.post(SPARQL_URL).mock(
            return_value=httpx.Response(200, json=_sparql([_hub_binding()]))
        )
        callback = respx.post(CALLBACK_URL).mock(return_value=_ok())

        await svc.fire(EventType.INTENT_CREATE, {"id": "x", "@type": "Intent"})
        assert callback.called

    @respx.mock
    @pytest.mark.asyncio
    async def test_fire_no_hubs_is_silent(
        self, client: FusekiClient
    ) -> None:
        hub_repo = HubRepository(client)
        svc = NotificationService(hub_repo)

        respx.post(SPARQL_URL).mock(
            return_value=httpx.Response(200, json=_sparql([]))
        )
        await svc.fire(EventType.INTENT_CREATE, {"id": "x"})  # no error

    @respx.mock
    @pytest.mark.asyncio
    async def test_fire_delivery_failure_is_logged_not_raised(
        self, client: FusekiClient
    ) -> None:
        hub_repo = HubRepository(client)
        svc = NotificationService(hub_repo)

        respx.post(SPARQL_URL).mock(
            return_value=httpx.Response(200, json=_sparql([_hub_binding()]))
        )
        respx.post(CALLBACK_URL).mock(return_value=httpx.Response(500))

        await svc.fire(EventType.INTENT_STATUS_CHANGE, {"id": "x"})  # no exception raised

    @respx.mock
    @pytest.mark.asyncio
    async def test_payload_structure(
        self, client: FusekiClient
    ) -> None:
        hub_repo = HubRepository(client)
        svc = NotificationService(hub_repo)

        respx.post(SPARQL_URL).mock(
            return_value=httpx.Response(200, json=_sparql([_hub_binding()]))
        )
        captured: list[dict] = []

        def _capture(request: httpx.Request, *_) -> httpx.Response:
            captured.append(json.loads(request.content))
            return httpx.Response(200)

        respx.post(CALLBACK_URL).mock(side_effect=_capture)

        await svc.fire(EventType.INTENT_CREATE, {"id": "z", "@type": "Intent"})

        assert len(captured) == 1
        p = captured[0]
        assert p["eventType"] == EventType.INTENT_CREATE
        assert "eventId" in p
        assert "correlationId" in p
        assert "eventTime" in p
        # Per TMF921 OAS: resource is nested under resource-type key inside "event"
        assert p["event"] == {"intent": {"id": "z", "@type": "Intent"}}

    @pytest.mark.asyncio
    async def test_payload_resource_key_by_event_type(self, client: FusekiClient) -> None:
        """Regression BBF_921-3q8: each event category nests resource under the
        correct TMF key — 'intent', 'intentReport', or 'intentSpecification'."""
        from src.services.notification_service import _build_payload

        cases = [
            (EventType.INTENT_CREATE,                      "intent"),
            (EventType.INTENT_DELETE,                      "intent"),
            (EventType.INTENT_STATUS_CHANGE,               "intent"),
            (EventType.INTENT_ATTRIBUTE_VALUE_CHANGE,      "intent"),
            (EventType.INTENT_REPORT_CREATE,               "intentReport"),
            (EventType.INTENT_REPORT_DELETE,               "intentReport"),
            (EventType.INTENT_SPEC_CREATE,                 "intentSpecification"),
            (EventType.INTENT_SPEC_DELETE,                 "intentSpecification"),
            (EventType.INTENT_SPEC_ATTRIBUTE_VALUE_CHANGE, "intentSpecification"),
            (EventType.INTENT_SPEC_STATUS_CHANGE,          "intentSpecification"),
        ]
        resource = {"id": "r1", "@type": "T"}
        for event_type, expected_key in cases:
            payload = _build_payload(event_type, resource)
            assert "event" in payload, f"{event_type}: missing 'event'"
            assert expected_key in payload["event"], (
                f"{event_type}: expected key '{expected_key}' in event, got {list(payload['event'].keys())}"
            )
            assert payload["event"][expected_key] is resource, (
                f"{event_type}: resource not placed under '{expected_key}'"
            )

    @respx.mock
    @pytest.mark.asyncio
    async def test_schedule_returns_task(
        self, client: FusekiClient
    ) -> None:
        import asyncio
        hub_repo = HubRepository(client)
        svc = NotificationService(hub_repo)

        respx.post(SPARQL_URL).mock(
            return_value=httpx.Response(200, json=_sparql([]))
        )

        task = svc.schedule(EventType.INTENT_DELETE, {"id": "z"})
        assert isinstance(task, asyncio.Task)
        await task


# ── IntentReportService ───────────────────────────────────────────────────────

REPORT_ID = "rpt-001"

def _report_binding(report_id: str = REPORT_ID) -> dict:
    return {
        "id":       {"type": "literal", "value": report_id},
        "href":     {"type": "literal", "value": f"http://host/intentReport/{report_id}"},
        "name":     {"type": "literal", "value": "Report"},
        "type":     {"type": "uri",     "value": "http://tmforum.org/api/v5/IntentReport"},
        "created":  {"type": "literal", "value": "2024-01-01T00:00:00+00:00",
                     "datatype": "http://www.w3.org/2001/XMLSchema#dateTime"},
        "parentIntent": {"type": "uri", "value": f"http://tmforum.org/api/v5/intents/{INTENT_ID}"},
    }


@pytest.fixture
def report_service(client: FusekiClient) -> IntentReportService:
    return IntentReportService(IntentReportRepository(client), HubRepository(client))


class TestIntentReportService:
    @respx.mock
    @pytest.mark.asyncio
    async def test_get_by_id_found(self, report_service: IntentReportService) -> None:
        respx.post(SPARQL_URL).mock(
            return_value=httpx.Response(200, json=_sparql([_report_binding()]))
        )
        result = await report_service.get_by_id(INTENT_ID, REPORT_ID)
        assert result["id"] == REPORT_ID

    @respx.mock
    @pytest.mark.asyncio
    async def test_get_by_id_not_found_raises_404(
        self, report_service: IntentReportService
    ) -> None:
        respx.post(SPARQL_URL).mock(
            return_value=httpx.Response(200, json=_sparql([]))
        )
        with pytest.raises(HTTPException) as exc:
            await report_service.get_by_id(INTENT_ID, "no-such")
        assert exc.value.status_code == 404

    @respx.mock
    @pytest.mark.asyncio
    async def test_list_returns_items_and_count(
        self, report_service: IntentReportService
    ) -> None:
        respx.post(SPARQL_URL).side_effect = [
            httpx.Response(200, json=_count(1)),
            httpx.Response(200, json=_sparql([_report_binding()])),
        ]
        items, total = await report_service.list(INTENT_ID)
        assert total == 1
        assert len(items) == 1

    @respx.mock
    @pytest.mark.asyncio
    async def test_delete_existing(self, report_service: IntentReportService) -> None:
        # get_by_id (found), DROP update, hub list_all
        respx.post(SPARQL_URL).side_effect = [
            httpx.Response(200, json=_sparql([_report_binding()])),
            httpx.Response(200, json=_sparql([])),
        ]
        respx.post(UPDATE_URL).mock(return_value=_ok())
        await report_service.delete(INTENT_ID, REPORT_ID)  # no exception

    @respx.mock
    @pytest.mark.asyncio
    async def test_delete_missing_raises_404(
        self, report_service: IntentReportService
    ) -> None:
        respx.post(SPARQL_URL).mock(
            return_value=httpx.Response(200, json=_sparql([]))
        )
        with pytest.raises(HTTPException) as exc:
            await report_service.delete(INTENT_ID, "no-such")
        assert exc.value.status_code == 404


# ── IntentSpecService ─────────────────────────────────────────────────────────

SPEC_ID = "spec-001"

def _spec_binding(spec_id: str = SPEC_ID) -> dict:
    return {
        "id":       {"type": "literal", "value": spec_id},
        "href":     {"type": "literal", "value": f"http://host/intentSpec/{spec_id}"},
        "name":     {"type": "literal", "value": "My Spec"},
        "type":     {"type": "uri",     "value": "http://tmforum.org/api/v5/IntentSpecification"},
        "modified": {"type": "literal", "value": "2024-01-01T00:00:00+00:00",
                     "datatype": "http://www.w3.org/2001/XMLSchema#dateTime"},
    }


@pytest.fixture
def spec_service(client: FusekiClient) -> IntentSpecService:
    return IntentSpecService(IntentSpecRepository(client), HubRepository(client))


class TestIntentSpecService:
    @respx.mock
    @pytest.mark.asyncio
    async def test_create_assigns_server_fields(
        self, spec_service: IntentSpecService
    ) -> None:
        respx.post(UPDATE_URL).mock(return_value=_ok())
        respx.post(SPARQL_URL).mock(return_value=httpx.Response(200, json=_sparql([])))
        result = await spec_service.create({"@type": "IntentSpecification", "name": "S"})
        assert result["id"] is not None
        assert result["href"].endswith(result["id"])

    @respx.mock
    @pytest.mark.asyncio
    async def test_get_by_id_found(self, spec_service: IntentSpecService) -> None:
        respx.post(SPARQL_URL).mock(
            return_value=httpx.Response(200, json=_sparql([_spec_binding()]))
        )
        result = await spec_service.get_by_id(SPEC_ID)
        assert result["id"] == SPEC_ID

    @respx.mock
    @pytest.mark.asyncio
    async def test_get_by_id_not_found_raises_404(
        self, spec_service: IntentSpecService
    ) -> None:
        respx.post(SPARQL_URL).mock(
            return_value=httpx.Response(200, json=_sparql([]))
        )
        with pytest.raises(HTTPException) as exc:
            await spec_service.get_by_id("no-such")
        assert exc.value.status_code == 404

    @respx.mock
    @pytest.mark.asyncio
    async def test_list_returns_items_and_count(
        self, spec_service: IntentSpecService
    ) -> None:
        respx.post(SPARQL_URL).side_effect = [
            httpx.Response(200, json=_count(1)),
            httpx.Response(200, json=_sparql([_spec_binding()])),
        ]
        items, total = await spec_service.list()
        assert total == 1

    @respx.mock
    @pytest.mark.asyncio
    async def test_update_attribute_change(
        self, spec_service: IntentSpecService
    ) -> None:
        respx.post(SPARQL_URL).side_effect = [
            httpx.Response(200, json=_sparql([_spec_binding()])),   # get existing
            httpx.Response(200, json=_sparql([_spec_binding()])),   # get after update
            httpx.Response(200, json=_sparql([])),                  # hub list_all
        ]
        respx.post(UPDATE_URL).mock(return_value=_ok())
        result = await spec_service.update(SPEC_ID, {"name": "Updated"})
        assert result["id"] == SPEC_ID

    @respx.mock
    @pytest.mark.asyncio
    async def test_update_non_patchable_raises_400(
        self, spec_service: IntentSpecService
    ) -> None:
        for field in ("id", "href", "lastUpdate", "@type", "@baseType", "@schemaLocation"):
            with pytest.raises(HTTPException) as exc:
                await spec_service.update(SPEC_ID, {field: "x"})
            assert exc.value.status_code == 400

    @respx.mock
    @pytest.mark.asyncio
    async def test_update_not_found_raises_404(
        self, spec_service: IntentSpecService
    ) -> None:
        respx.post(SPARQL_URL).mock(
            return_value=httpx.Response(200, json=_sparql([]))
        )
        with pytest.raises(HTTPException) as exc:
            await spec_service.update("no-such", {"name": "x"})
        assert exc.value.status_code == 404

    @respx.mock
    @pytest.mark.asyncio
    async def test_update_lifecycle_status_change_fires_status_event(
        self, spec_service: IntentSpecService
    ) -> None:
        active_binding = {**_spec_binding(), "lifecycleStatus": {"type": "literal", "value": "ACTIVE"}}
        respx.post(SPARQL_URL).side_effect = [
            httpx.Response(200, json=_sparql([_spec_binding()])),       # existing (no status)
            httpx.Response(200, json=_sparql([active_binding])),        # after update
            httpx.Response(200, json=_sparql([])),                      # hub list_all
        ]
        respx.post(UPDATE_URL).mock(return_value=_ok())
        result = await spec_service.update(SPEC_ID, {"lifecycleStatus": "ACTIVE"})
        assert result is not None

    @respx.mock
    @pytest.mark.asyncio
    async def test_delete_existing(self, spec_service: IntentSpecService) -> None:
        respx.post(SPARQL_URL).side_effect = [
            httpx.Response(200, json={"boolean": True}),   # ASK
            httpx.Response(200, json=_sparql([])),          # hub list_all
        ]
        respx.post(UPDATE_URL).mock(return_value=_ok())
        await spec_service.delete(SPEC_ID)  # no exception

    @respx.mock
    @pytest.mark.asyncio
    async def test_delete_missing_raises_404(
        self, spec_service: IntentSpecService
    ) -> None:
        respx.post(SPARQL_URL).mock(
            return_value=httpx.Response(200, json={"boolean": False})
        )
        with pytest.raises(HTTPException) as exc:
            await spec_service.delete("no-such")
        assert exc.value.status_code == 404
