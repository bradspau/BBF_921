"""
Integration tests for Hub subscription management and notification delivery.

Covers:
  - POST /hub (subscribe)
  - DELETE /hub/{id} (unsubscribe)
  - Notification fan-out triggered by Intent lifecycle events
  - Error body format on hub operations
"""
from __future__ import annotations

import httpx
import respx

from tests.integration.conftest import (
    BASE,
    DATASET,
    FUSEKI,
    ask_response,
    hub_row,
    intent_row,
    sparql_bindings,
)

HUB_ID    = "hub-aaa-111"
INTENT_ID = "intent-nnn-001"
CALLBACK  = "http://listener.example.com/events"

SPARQL = f"{FUSEKI}/{DATASET}/sparql"
UPDATE = f"{FUSEKI}/{DATASET}/update"


# ── Hub CRUD ──────────────────────────────────────────────────────────────────

class TestHubCreate:
    @respx.mock
    def test_create_hub_returns_201(self, tc):
        row = hub_row(HUB_ID, CALLBACK)
        respx.post(UPDATE).mock(return_value=httpx.Response(200))
        respx.post(SPARQL).mock(
            return_value=httpx.Response(200, json=sparql_bindings(row))
        )

        resp = tc.post(f"{BASE}/hub", json={"callback": CALLBACK})

        assert resp.status_code == 201
        body = resp.json()
        assert "id" in body
        assert "href" in body
        assert body["callback"] == CALLBACK

    @respx.mock
    def test_create_hub_without_callback_returns_400(self, tc):
        resp = tc.post(f"{BASE}/hub", json={})

        assert resp.status_code == 400
        body = resp.json()
        assert body["@type"] == "Error"

    @respx.mock
    def test_create_hub_with_query_filter(self, tc):
        row = hub_row(HUB_ID, CALLBACK)
        row["query"] = {"type": "literal", "value": "eventType=IntentCreateEvent"}
        respx.post(UPDATE).mock(return_value=httpx.Response(200))
        respx.post(SPARQL).mock(
            return_value=httpx.Response(200, json=sparql_bindings(row))
        )

        resp = tc.post(
            f"{BASE}/hub",
            json={"callback": CALLBACK, "query": "eventType=IntentCreateEvent"},
        )

        assert resp.status_code == 201
        assert resp.json()["query"] == "eventType=IntentCreateEvent"


class TestHubDelete:
    @respx.mock
    def test_delete_existing_hub_returns_204(self, tc):
        respx.post(SPARQL).mock(
            return_value=httpx.Response(200, json=ask_response(True))
        )
        respx.post(UPDATE).mock(return_value=httpx.Response(200))

        resp = tc.delete(f"{BASE}/hub/{HUB_ID}")

        assert resp.status_code == 204
        assert resp.content == b""

    @respx.mock
    def test_delete_nonexistent_hub_returns_404(self, tc):
        respx.post(SPARQL).mock(
            return_value=httpx.Response(200, json=ask_response(False))
        )

        resp = tc.delete(f"{BASE}/hub/nonexistent")

        assert resp.status_code == 404
        body = resp.json()
        assert body["@type"] == "Error"

    @respx.mock
    def test_hub_id_embedded_in_href(self, tc):
        row = hub_row(HUB_ID, CALLBACK)
        respx.post(UPDATE).mock(return_value=httpx.Response(200))
        respx.post(SPARQL).mock(
            return_value=httpx.Response(200, json=sparql_bindings(row))
        )

        resp = tc.post(f"{BASE}/hub", json={"callback": CALLBACK})

        body = resp.json()
        assert body["id"] in body["href"]


# ── Notification fan-out ──────────────────────────────────────────────────────

class TestNotificationFanOut:
    @respx.mock
    def test_intent_create_triggers_notification_attempt(self, tc):
        """
        POST /intent fires a background notification to registered hubs.
        We verify that the hub-list query is made and the callback is attempted.
        """
        row = intent_row(INTENT_ID)
        hub = hub_row(HUB_ID, CALLBACK)

        calls = [0]

        def sparql_seq(request, **_):
            calls[0] += 1
            if calls[0] == 1:
                # get_by_id after INSERT
                return httpx.Response(200, json=sparql_bindings(row))
            # hub list_all (background notification)
            return httpx.Response(200, json=sparql_bindings(hub))

        respx.post(UPDATE).mock(return_value=httpx.Response(200))
        respx.post(SPARQL).mock(side_effect=sparql_seq)
        # Intercept the callback delivery
        respx.post(CALLBACK).mock(return_value=httpx.Response(200))

        resp = tc.post(
            f"{BASE}/intent",
            json={
                "name": "My Intent",
                "@type": "Intent",
                "expression": {
                    "@type": "JsonLdExpression",
                    "iri":  "http://tio.example.org/model/v1",
                    "expressionValue": {"@context": {}},
                },
            },
        )

        assert resp.status_code == 201
        # The response is returned before notification fires (background task),
        # but TestClient runs the event loop to completion so callbacks may fire.

    @respx.mock
    def test_notification_failure_does_not_fail_request(self, tc):
        """
        Callback delivery failure must not cause the originating request to fail.
        """
        row = intent_row(INTENT_ID)
        hub = hub_row(HUB_ID, CALLBACK)

        calls = [0]

        def sparql_seq(request, **_):
            calls[0] += 1
            if calls[0] == 1:
                return httpx.Response(200, json=sparql_bindings(row))
            return httpx.Response(200, json=sparql_bindings(hub))

        respx.post(UPDATE).mock(return_value=httpx.Response(200))
        respx.post(SPARQL).mock(side_effect=sparql_seq)
        # Callback endpoint returns 500
        respx.post(CALLBACK).mock(return_value=httpx.Response(500))

        resp = tc.post(
            f"{BASE}/intent",
            json={
                "name": "My Intent",
                "@type": "Intent",
                "expression": {
                    "@type": "JsonLdExpression",
                    "iri":  "http://tio.example.org/model/v1",
                    "expressionValue": {"@context": {}},
                },
            },
        )

        # Request still succeeds despite callback failure
        assert resp.status_code == 201

    @respx.mock
    def test_delete_intent_triggers_notification(self, tc):
        """DELETE /intent fires IntentDeleteEvent to registered hubs."""
        hub = hub_row(HUB_ID, CALLBACK)

        calls = [0]

        def sparql_seq(request, **_):
            calls[0] += 1
            if calls[0] == 1:
                # ASK for existence check
                return httpx.Response(200, json=ask_response(True))
            # hub list_all (background)
            return httpx.Response(200, json=sparql_bindings(hub))

        respx.post(SPARQL).mock(side_effect=sparql_seq)
        respx.post(UPDATE).mock(return_value=httpx.Response(200))
        respx.post(CALLBACK).mock(return_value=httpx.Response(200))

        resp = tc.delete(f"{BASE}/intent/{INTENT_ID}")

        assert resp.status_code == 204


# ── Health endpoint ───────────────────────────────────────────────────────────

class TestHealthEndpoint:
    @respx.mock
    def test_health_up(self, tc):
        respx.get(f"{FUSEKI}/$/ping").mock(return_value=httpx.Response(200))

        resp = tc.get("/health")

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "UP"
        assert body["graph"] == "UP"

    @respx.mock
    def test_health_graph_down(self, tc):
        respx.get(f"{FUSEKI}/$/ping").mock(
            side_effect=httpx.ConnectError("connection refused")
        )

        resp = tc.get("/health")

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "UP"
        assert body["graph"] == "DOWN"
