"""
Integration tests for TMF921A negotiation flows.

ProbeIntent flow: POST Intent (normal) → POST ProbeIntent (with intentRelationship) →
PATCH ProbeIntent (accept/reject via lifecycleStatus).

ProbeIntent inherits all Intent mandatory attributes and operations.
"""
from __future__ import annotations

import httpx
import respx

from tests.integration.conftest import (
    BASE,
    DATASET,
    FUSEKI,
    ask_response,
    intent_row,
    sparql_bindings,
)

INTENT_ID = "owner-intent-001"
PROBE_ID  = "probe-intent-001"

SPARQL = f"{FUSEKI}/{DATASET}/sparql"
UPDATE = f"{FUSEKI}/{DATASET}/update"

_OWNER_INTENT = {
    "name": "Owner Intent",
    "@type": "Intent",
    "expression": {
        "@type": "JsonLdExpression",
        "iri":  "http://tio.example.org/model/v1",
        "expressionValue": {"@context": {}},
    },
}

_PROBE_INTENT = {
    "name": "Probe Intent",
    "@type": "ProbeIntent",
    "expression": {
        "@type": "JsonLdExpression",
        "iri":  "http://tio.example.org/model/v1",
        "expressionValue": {"@context": {}},
    },
    "intentRelationship": [
        {
            "@type":          "IntentRelationship",
            "id":             INTENT_ID,
            "relationshipType": "relatesTo",
            "referredType":   "Intent",
        }
    ],
}


def probe_row(intent_id: str, lifecycle_status: str = "ACKNOWLEDGED") -> dict:
    row = intent_row(intent_id, lifecycle_status=lifecycle_status)
    row["type"] = {"type": "uri", "value": "http://tmforum.org/api/v5/ProbeIntent"}
    row["baseType"] = {"type": "literal", "value": "Intent"}
    return row


class TestProbeIntentCreate:
    @respx.mock
    def test_create_probe_intent_returns_201(self, tc):
        row = probe_row(PROBE_ID)
        respx.post(UPDATE).mock(return_value=httpx.Response(200))
        respx.post(SPARQL).mock(
            return_value=httpx.Response(200, json=sparql_bindings(row))
        )

        resp = tc.post(f"{BASE}/intent", json=_PROBE_INTENT)

        assert resp.status_code == 201
        body = resp.json()
        assert body["@type"] == "ProbeIntent"

    @respx.mock
    def test_create_probe_intent_with_relationship(self, tc):
        row = probe_row(PROBE_ID)
        respx.post(UPDATE).mock(return_value=httpx.Response(200))
        respx.post(SPARQL).mock(
            return_value=httpx.Response(200, json=sparql_bindings(row))
        )

        resp = tc.post(f"{BASE}/intent", json=_PROBE_INTENT)

        assert resp.status_code == 201

    @respx.mock
    def test_probe_intent_starts_acknowledged(self, tc):
        row = probe_row(PROBE_ID, lifecycle_status="ACKNOWLEDGED")
        respx.post(UPDATE).mock(return_value=httpx.Response(200))
        respx.post(SPARQL).mock(
            return_value=httpx.Response(200, json=sparql_bindings(row))
        )

        resp = tc.post(f"{BASE}/intent", json=_PROBE_INTENT)

        assert resp.status_code == 201
        assert resp.json()["lifecycleStatus"] == "ACKNOWLEDGED"


class TestProbeIntentNegotiationFlow:
    @respx.mock
    def test_accept_probe_via_status_patch(self, tc):
        """Owner patches ProbeIntent to ACTIVE to accept proposed terms."""
        existing_probe = probe_row(PROBE_ID, lifecycle_status="ACKNOWLEDGED")
        active_probe   = probe_row(PROBE_ID, lifecycle_status="ACTIVE")

        call_count = [0]

        def sparql_seq(request, **_):
            call_count[0] += 1
            if call_count[0] == 1:
                return httpx.Response(200, json=sparql_bindings(existing_probe))
            return httpx.Response(200, json=sparql_bindings(active_probe))

        respx.post(SPARQL).mock(side_effect=sparql_seq)
        respx.post(UPDATE).mock(return_value=httpx.Response(200))

        resp = tc.patch(
            f"{BASE}/intent/{PROBE_ID}",
            json={"lifecycleStatus": "ACTIVE"},
            headers={"Content-Type": "application/merge-patch+json"},
        )

        assert resp.status_code == 200
        assert resp.json()["lifecycleStatus"] == "ACTIVE"

    @respx.mock
    def test_terminate_probe_via_status_patch(self, tc):
        """Handler terminates a ProbeIntent by transitioning to TERMINATED."""
        existing_probe   = probe_row(PROBE_ID, lifecycle_status="ACTIVE")
        terminated_probe = probe_row(PROBE_ID, lifecycle_status="TERMINATED")

        call_count = [0]

        def sparql_seq(request, **_):
            call_count[0] += 1
            if call_count[0] == 1:
                return httpx.Response(200, json=sparql_bindings(existing_probe))
            return httpx.Response(200, json=sparql_bindings(terminated_probe))

        respx.post(SPARQL).mock(side_effect=sparql_seq)
        respx.post(UPDATE).mock(return_value=httpx.Response(200))

        resp = tc.patch(
            f"{BASE}/intent/{PROBE_ID}",
            json={"lifecycleStatus": "TERMINATED"},
            headers={"Content-Type": "application/merge-patch+json"},
        )

        assert resp.status_code == 200
        assert resp.json()["lifecycleStatus"] == "TERMINATED"

    @respx.mock
    def test_delete_probe_intent(self, tc):
        respx.post(SPARQL).mock(
            return_value=httpx.Response(200, json=ask_response(True))
        )
        respx.post(UPDATE).mock(return_value=httpx.Response(200))

        resp = tc.delete(f"{BASE}/intent/{PROBE_ID}")

        assert resp.status_code == 204

    @respx.mock
    def test_probe_inherits_intent_patch_restrictions(self, tc):
        """ProbeIntent must also reject non-patchable fields."""
        resp = tc.patch(
            f"{BASE}/intent/{PROBE_ID}",
            json={"href": "http://attacker.example.com/hijack"},
            headers={"Content-Type": "application/merge-patch+json"},
        )
        assert resp.status_code == 400


class TestJudgePreferenceFlow:
    @respx.mock
    def test_owner_updates_expression_value(self, tc):
        """Owner sends new preference via PATCH expressionValue."""
        existing = intent_row(INTENT_ID, lifecycle_status="DEGRADED")
        updated  = intent_row(INTENT_ID, lifecycle_status="DEGRADED")

        call_count = [0]

        def sparql_seq(request, **_):
            call_count[0] += 1
            if call_count[0] == 1:
                return httpx.Response(200, json=sparql_bindings(existing))
            return httpx.Response(200, json=sparql_bindings(updated))

        respx.post(SPARQL).mock(side_effect=sparql_seq)
        respx.post(UPDATE).mock(return_value=httpx.Response(200))

        resp = tc.patch(
            f"{BASE}/intent/{INTENT_ID}",
            json={
                "expression": {
                    "@type": "JsonLdExpression",
                    "iri":  "http://tio.example.org/model/v1",
                    "expressionValue": {"@context": {}, "newPreference": "value"},
                }
            },
            headers={"Content-Type": "application/merge-patch+json"},
        )

        assert resp.status_code == 200
