"""
Integration tests for Intent CRUD lifecycle.

Tests the full stack: HTTP → router → service → repository → FusekiClient.
Fuseki HTTP calls are intercepted by respx.
"""
from __future__ import annotations

import httpx
import respx

from tests.integration.conftest import (
    BASE,
    DATASET,
    FUSEKI,
    ask_response,
    count_row,
    intent_row,
    sequential,
    sparql_bindings,
)

INTENT_ID  = "eeeeeeee-eeee-4eee-aeee-eeeeeeeeeeee"
MISSING_ID = "ffffffff-ffff-4fff-bfff-ffffffffffff"
SPARQL = f"{FUSEKI}/{DATASET}/sparql"
UPDATE = f"{FUSEKI}/{DATASET}/update"
DATA   = f"{FUSEKI}/{DATASET}/data"

_MINIMAL_INTENT = {
    "name": "My Intent",
    "@type": "Intent",
    "expression": {
        "@type": "JsonLdExpression",
        "iri":  "http://tio.example.org/model/v1",
        "expressionValue": {"@context": {}},
    },
}


class TestCreateIntent:
    @respx.mock
    def test_create_returns_201(self, tc):
        row = intent_row(INTENT_ID)
        respx.post(UPDATE).mock(return_value=httpx.Response(200))
        respx.post(SPARQL).mock(
            return_value=httpx.Response(200, json=sparql_bindings(row))
        )
        # Background notification: hub list query returns empty
        respx.post(SPARQL).mock(
            return_value=httpx.Response(200, json=sparql_bindings())
        )

        resp = tc.post(f"{BASE}/intent", json=_MINIMAL_INTENT)

        assert resp.status_code == 201
        body = resp.json()
        assert body["@type"] == "Intent"
        assert body["name"] == "My Intent"
        assert "id" in body
        assert "href" in body

    @respx.mock
    def test_create_sets_acknowledged_status(self, tc):
        row = intent_row(INTENT_ID, lifecycle_status="ACKNOWLEDGED")
        respx.post(UPDATE).mock(return_value=httpx.Response(200))
        respx.post(SPARQL).mock(
            return_value=httpx.Response(200, json=sparql_bindings(row))
        )

        resp = tc.post(f"{BASE}/intent", json=_MINIMAL_INTENT)

        assert resp.status_code == 201
        assert resp.json()["lifecycleStatus"] == "ACKNOWLEDGED"

    @respx.mock
    def test_create_strips_server_side_fields(self, tc):
        row = intent_row(INTENT_ID)
        respx.post(UPDATE).mock(return_value=httpx.Response(200))
        respx.post(SPARQL).mock(
            return_value=httpx.Response(200, json=sparql_bindings(row))
        )

        body_with_id = {**_MINIMAL_INTENT, "id": "should-be-stripped"}
        resp = tc.post(f"{BASE}/intent", json=body_with_id)

        assert resp.status_code == 201
        assert resp.json()["id"] != "should-be-stripped"

    @respx.mock
    def test_create_includes_expression(self, tc):
        row = intent_row(INTENT_ID)
        respx.post(UPDATE).mock(return_value=httpx.Response(200))
        respx.post(SPARQL).mock(
            return_value=httpx.Response(200, json=sparql_bindings(row))
        )

        resp = tc.post(f"{BASE}/intent", json=_MINIMAL_INTENT)

        assert resp.status_code == 201
        body = resp.json()
        assert "expression" in body
        assert body["expression"]["@type"] == "JsonLdExpression"


class TestGetIntent:
    @respx.mock
    def test_get_existing_intent(self, tc):
        row = intent_row(INTENT_ID, name="My Intent")
        respx.post(SPARQL).mock(
            return_value=httpx.Response(200, json=sparql_bindings(row))
        )

        resp = tc.get(f"{BASE}/intent/{INTENT_ID}")

        assert resp.status_code == 200
        body = resp.json()
        assert body["@type"] == "Intent"
        assert body["name"] == "My Intent"
        assert body["id"] == INTENT_ID

    @respx.mock
    def test_get_nonexistent_returns_404(self, tc):
        respx.post(SPARQL).mock(
            return_value=httpx.Response(200, json=sparql_bindings())
        )

        resp = tc.get(f"{BASE}/intent/{MISSING_ID}")

        assert resp.status_code == 404
        body = resp.json()
        assert body["@type"] == "Error"
        assert "code" in body
        assert "reason" in body

    @respx.mock
    def test_get_with_fields_projection(self, tc):
        row = intent_row(INTENT_ID)
        respx.post(SPARQL).mock(
            return_value=httpx.Response(200, json=sparql_bindings(row))
        )

        resp = tc.get(f"{BASE}/intent/{INTENT_ID}?fields=name")

        assert resp.status_code == 200
        body = resp.json()
        assert "name" in body
        assert "href" in body       # href always returned
        assert "lifecycleStatus" not in body

    @respx.mock
    def test_get_error_body_is_tmf_format(self, tc):
        respx.post(SPARQL).mock(
            return_value=httpx.Response(200, json=sparql_bindings())
        )

        resp = tc.get(f"{BASE}/intent/{MISSING_ID}")

        assert resp.status_code == 404
        body = resp.json()
        assert set(body.keys()) >= {"code", "reason", "@type"}
        assert body["@type"] == "Error"


class TestListIntents:
    @respx.mock
    def test_list_returns_200(self, tc):
        row = intent_row(INTENT_ID)
        respx.post(SPARQL).mock(
            side_effect=sequential(
                httpx.Response(200, json=sparql_bindings(count_row(1))),
                httpx.Response(200, json=sparql_bindings(row)),
            )
        )

        resp = tc.get(f"{BASE}/intent")

        assert resp.status_code == 200
        assert isinstance(resp.json(), list)
        assert len(resp.json()) == 1

    @respx.mock
    def test_list_returns_pagination_headers(self, tc):
        row = intent_row(INTENT_ID)
        respx.post(SPARQL).mock(
            side_effect=sequential(
                httpx.Response(200, json=sparql_bindings(count_row(5))),
                httpx.Response(200, json=sparql_bindings(row)),
            )
        )

        resp = tc.get(f"{BASE}/intent?limit=1&offset=0")

        assert resp.status_code == 200
        assert resp.headers["x-total-count"] == "5"
        assert resp.headers["x-result-count"] == "1"

    @respx.mock
    def test_list_empty_returns_empty_array(self, tc):
        respx.post(SPARQL).mock(
            return_value=httpx.Response(200, json=sparql_bindings(count_row(0)))
        )

        resp = tc.get(f"{BASE}/intent")

        assert resp.status_code == 200
        assert resp.json() == []

    @respx.mock
    def test_list_filter_by_lifecycleStatus(self, tc):
        row = intent_row(INTENT_ID, lifecycle_status="ACTIVE")
        respx.post(SPARQL).mock(
            side_effect=sequential(
                httpx.Response(200, json=sparql_bindings(count_row(1))),
                httpx.Response(200, json=sparql_bindings(row)),
            )
        )

        resp = tc.get(f"{BASE}/intent?lifecycleStatus=ACTIVE")

        assert resp.status_code == 200
        assert resp.json()[0]["lifecycleStatus"] == "ACTIVE"

    @respx.mock
    def test_list_filter_by_name(self, tc):
        row = intent_row(INTENT_ID, name="HSI Intent")
        respx.post(SPARQL).mock(
            side_effect=sequential(
                httpx.Response(200, json=sparql_bindings(count_row(1))),
                httpx.Response(200, json=sparql_bindings(row)),
            )
        )

        resp = tc.get(f"{BASE}/intent?name=HSI+Intent")

        assert resp.status_code == 200
        assert resp.json()[0]["name"] == "HSI Intent"


class TestPatchIntent:
    @respx.mock
    def test_patch_description_returns_200(self, tc):
        existing = intent_row(INTENT_ID, name="Old Name")
        updated  = intent_row(INTENT_ID, name="Old Name")
        updated["description"] = {"type": "literal", "value": "New description"}

        respx.post(SPARQL).mock(
            side_effect=sequential(
                httpx.Response(200, json=sparql_bindings(existing)),  # get_by_id for validate
                httpx.Response(200, json=sparql_bindings(updated)),   # get_by_id after update
            )
        )
        respx.post(UPDATE).mock(return_value=httpx.Response(200))

        resp = tc.patch(
            f"{BASE}/intent/{INTENT_ID}",
            json={"description": "New description"},
            headers={"Content-Type": "application/merge-patch+json"},
        )

        assert resp.status_code == 200

    @respx.mock
    def test_patch_non_patchable_field_returns_400(self, tc):
        resp = tc.patch(
            f"{BASE}/intent/{INTENT_ID}",
            json={"id": "new-id"},
            headers={"Content-Type": "application/merge-patch+json"},
        )
        assert resp.status_code == 400
        assert resp.json()["@type"] == "Error"

    @respx.mock
    def test_patch_wrong_content_type_returns_415(self, tc):
        resp = tc.patch(
            f"{BASE}/intent/{INTENT_ID}",
            json={"name": "New Name"},
            headers={"Content-Type": "text/plain"},
        )
        assert resp.status_code == 415

    @respx.mock
    def test_patch_nonexistent_returns_404(self, tc):
        respx.post(SPARQL).mock(
            return_value=httpx.Response(200, json=sparql_bindings())
        )

        resp = tc.patch(
            f"{BASE}/intent/{MISSING_ID}",
            json={"name": "New Name"},
            headers={"Content-Type": "application/merge-patch+json"},
        )
        assert resp.status_code == 404

    @respx.mock
    def test_patch_lifecycle_status_transition(self, tc):
        existing = intent_row(INTENT_ID, lifecycle_status="ACKNOWLEDGED")
        active   = intent_row(INTENT_ID, lifecycle_status="ACTIVE")

        call_count = [0]

        def sparql_side_effect(request, **_):
            call_count[0] += 1
            if call_count[0] == 1:
                return httpx.Response(200, json=sparql_bindings(existing))
            return httpx.Response(200, json=sparql_bindings(active))

        respx.post(SPARQL).mock(side_effect=sparql_side_effect)
        respx.post(UPDATE).mock(return_value=httpx.Response(200))

        resp = tc.patch(
            f"{BASE}/intent/{INTENT_ID}",
            json={"lifecycleStatus": "ACTIVE"},
            headers={"Content-Type": "application/merge-patch+json"},
        )

        assert resp.status_code == 200
        assert resp.json()["lifecycleStatus"] == "ACTIVE"

    @respx.mock
    def test_patch_invalid_status_transition_returns_400(self, tc):
        existing = intent_row(INTENT_ID, lifecycle_status="ACKNOWLEDGED")

        respx.post(SPARQL).mock(
            return_value=httpx.Response(200, json=sparql_bindings(existing))
        )

        resp = tc.patch(
            f"{BASE}/intent/{INTENT_ID}",
            json={"lifecycleStatus": "FULFILLED"},  # invalid: ACKNOWLEDGED → FULFILLED
            headers={"Content-Type": "application/merge-patch+json"},
        )
        assert resp.status_code == 400


class TestDeleteIntent:
    @respx.mock
    def test_delete_existing_intent_returns_204(self, tc):
        respx.post(SPARQL).mock(
            return_value=httpx.Response(200, json=ask_response(True))
        )
        respx.post(UPDATE).mock(return_value=httpx.Response(200))

        resp = tc.delete(f"{BASE}/intent/{INTENT_ID}")

        assert resp.status_code == 204
        assert resp.content == b""

    @respx.mock
    def test_delete_nonexistent_returns_404(self, tc):
        respx.post(SPARQL).mock(
            return_value=httpx.Response(200, json=ask_response(False))
        )

        resp = tc.delete(f"{BASE}/intent/{MISSING_ID}")

        assert resp.status_code == 404
        assert resp.json()["@type"] == "Error"


class TestFullLifecycle:
    @respx.mock
    def test_create_get_patch_delete(self, tc):
        """End-to-end: POST → GET → PATCH (status) → DELETE."""
        row_acknowledged = intent_row(INTENT_ID, lifecycle_status="ACKNOWLEDGED")
        row_active       = intent_row(INTENT_ID, lifecycle_status="ACTIVE")

        sparql_call = [0]

        def sparql_seq(request, **_):
            sparql_call[0] += 1
            n = sparql_call[0]
            if n == 1:
                # POST: get_by_id after create
                return httpx.Response(200, json=sparql_bindings(row_acknowledged))
            if n == 2:
                # GET: get_by_id
                return httpx.Response(200, json=sparql_bindings(row_acknowledged))
            if n == 3:
                # PATCH: get existing for validation
                return httpx.Response(200, json=sparql_bindings(row_acknowledged))
            if n == 4:
                # PATCH: get_by_id after update
                return httpx.Response(200, json=sparql_bindings(row_active))
            # DELETE: ASK check
            return httpx.Response(200, json=ask_response(True))

        respx.post(SPARQL).mock(side_effect=sparql_seq)
        respx.post(UPDATE).mock(return_value=httpx.Response(200))

        # POST
        r1 = tc.post(f"{BASE}/intent", json=_MINIMAL_INTENT)
        assert r1.status_code == 201
        assert r1.json()["lifecycleStatus"] == "ACKNOWLEDGED"

        # GET
        r2 = tc.get(f"{BASE}/intent/{INTENT_ID}")
        assert r2.status_code == 200

        # PATCH (status transition)
        r3 = tc.patch(
            f"{BASE}/intent/{INTENT_ID}",
            json={"lifecycleStatus": "ACTIVE"},
            headers={"Content-Type": "application/merge-patch+json"},
        )
        assert r3.status_code == 200
        assert r3.json()["lifecycleStatus"] == "ACTIVE"

        # DELETE
        r4 = tc.delete(f"{BASE}/intent/{INTENT_ID}")
        assert r4.status_code == 204


class TestListIntentSpecs:
    @respx.mock
    def test_list_specs_filter_by_lifecycleStatus(self, tc):
        from tests.integration.conftest import spec_row
        row = spec_row(INTENT_ID, name="My Spec")
        respx.post(SPARQL).mock(
            side_effect=sequential(
                httpx.Response(200, json=sparql_bindings(count_row(1))),
                httpx.Response(200, json=sparql_bindings(row)),
            )
        )

        resp = tc.get(f"{BASE}/intentSpecification?lifecycleStatus=ACTIVE")

        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    @respx.mock
    def test_list_specs_filter_by_name(self, tc):
        from tests.integration.conftest import spec_row
        row = spec_row(INTENT_ID, name="HSI Spec")
        respx.post(SPARQL).mock(
            side_effect=sequential(
                httpx.Response(200, json=sparql_bindings(count_row(1))),
                httpx.Response(200, json=sparql_bindings(row)),
            )
        )

        resp = tc.get(f"{BASE}/intentSpecification?name=HSI+Spec")

        assert resp.status_code == 200
        assert resp.json()[0]["name"] == "HSI Spec"
