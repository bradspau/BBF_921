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

INTENT_ID = "cccccccc-cccc-4ccc-cccc-cccccccccccc"
PROBE_ID  = "dddddddd-dddd-4ddd-dddd-dddddddddddd"

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


# ── Full 3-step Probe Intent negotiation flow ─────────────────────────────────

class TestFullProbeIntentNegotiationFlow:
    """
    TMF921A §4.2 Probe Intent Flow — end-to-end integration.

    Per docs/06-negotiation.md:
      1. Owner   POST /intent  (@type: Intent)
      2. Handler POST /intent  (@type: ProbeIntent, intentRelationship → owner id)
      3. Owner   PATCH /intent/{probeId}  (accept or reject via lifecycleStatus)

    IntentRepository.create() returns the input payload dict directly (no SELECT
    after INSERT), so the server-assigned UUID from step 1/2 is the real id.
    All SPARQL calls in steps 1 & 2 are background hub-list queries; we return
    empty so they are no-ops.  Steps 3 sequences three specific SPARQL responses.
    """

    @respx.mock
    def test_three_step_owner_post_probe_post_accept(self, tc):
        """Step 3 accepts the ProbeIntent by transitioning to ACTIVE."""
        probe_acked  = probe_row("probe-aaa", lifecycle_status="ACKNOWLEDGED")
        probe_active = probe_row("probe-aaa", lifecycle_status="ACTIVE")

        intent_select_n = [0]

        def sparql_dispatch(request, **_):
            body = request.content.decode()
            # Hub list queries contain the hubs graph URI — always return no hubs.
            # This handles both the pre-patch background notifications AND the
            # post-patch notification background task, regardless of timing.
            if "hubs" in body:
                return httpx.Response(200, json=sparql_bindings())
            # All other SELECT calls are intent get_by_id queries from the PATCH step.
            intent_select_n[0] += 1
            n = intent_select_n[0]
            if n == 1:
                # service.update get_by_id existing (ACKNOWLEDGED)
                return httpx.Response(200, json=sparql_bindings(probe_acked))
            if n == 2:
                # repo.update internal get_by_id after UPDATE (ACTIVE)
                return httpx.Response(200, json=sparql_bindings(probe_active))
            return httpx.Response(200, json=sparql_bindings())

        respx.post(SPARQL).mock(side_effect=sparql_dispatch)
        respx.post(UPDATE).mock(return_value=httpx.Response(200))

        # ── Step 1: Owner creates parent Intent ───────────────────────────────
        r1 = tc.post(f"{BASE}/intent", json=_OWNER_INTENT)
        assert r1.status_code == 201
        owner_id = r1.json()["id"]
        assert r1.json()["@type"] == "Intent"
        assert owner_id  # server-assigned UUID

        # ── Step 2: Handler creates ProbeIntent referencing the owner ─────────
        probe_payload = {
            "name": "Handler Probe",
            "@type": "ProbeIntent",
            "expression": _OWNER_INTENT["expression"],
            "intentRelationship": [{
                "@type":            "IntentRelationship",
                "id":               owner_id,
                "relationshipType": "relatesTo",
                "referredType":     "Intent",
            }],
        }
        r2 = tc.post(f"{BASE}/intent", json=probe_payload)
        assert r2.status_code == 201
        probe_id = r2.json()["id"]
        assert r2.json()["@type"] == "ProbeIntent"
        assert probe_id != owner_id  # each resource gets a distinct server UUID

        # ── Step 3: Owner accepts ProbeIntent → ACTIVE ────────────────────────
        r3 = tc.patch(
            f"{BASE}/intent/{probe_id}",
            json={"lifecycleStatus": "ACTIVE"},
            headers={"Content-Type": "application/merge-patch+json"},
        )
        assert r3.status_code == 200
        assert r3.json()["lifecycleStatus"] == "ACTIVE"

    @respx.mock
    def test_three_step_owner_post_probe_post_reject(self, tc):
        """Step 3 rejects the ProbeIntent by transitioning to TERMINATED."""
        probe_acked      = probe_row("probe-bbb", lifecycle_status="ACKNOWLEDGED")
        probe_terminated = probe_row("probe-bbb", lifecycle_status="TERMINATED")

        intent_select_n = [0]

        def sparql_dispatch(request, **_):
            body = request.content.decode()
            if "hubs" in body:
                return httpx.Response(200, json=sparql_bindings())
            intent_select_n[0] += 1
            n = intent_select_n[0]
            if n == 1:
                return httpx.Response(200, json=sparql_bindings(probe_acked))
            if n == 2:
                return httpx.Response(200, json=sparql_bindings(probe_terminated))
            return httpx.Response(200, json=sparql_bindings())

        respx.post(SPARQL).mock(side_effect=sparql_dispatch)
        respx.post(UPDATE).mock(return_value=httpx.Response(200))

        r1 = tc.post(f"{BASE}/intent", json=_OWNER_INTENT)
        assert r1.status_code == 201
        owner_id = r1.json()["id"]

        probe_payload = {
            "name": "Handler Probe to Reject",
            "@type": "ProbeIntent",
            "expression": _OWNER_INTENT["expression"],
            "intentRelationship": [{
                "@type":            "IntentRelationship",
                "id":               owner_id,
                "relationshipType": "relatesTo",
                "referredType":     "Intent",
            }],
        }
        r2 = tc.post(f"{BASE}/intent", json=probe_payload)
        assert r2.status_code == 201
        probe_id = r2.json()["id"]

        # Owner rejects (ACKNOWLEDGED → TERMINATED is a valid FSM transition)
        r3 = tc.patch(
            f"{BASE}/intent/{probe_id}",
            json={"lifecycleStatus": "TERMINATED"},
            headers={"Content-Type": "application/merge-patch+json"},
        )
        assert r3.status_code == 200
        assert r3.json()["lifecycleStatus"] == "TERMINATED"
