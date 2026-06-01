"""
Unit tests for Phase 4 API routers.

All service / repository calls are mocked — no live Fuseki required.
Covers:
  - All documented status codes per endpoint
  - X-Total-Count / X-Result-Count on list responses
  - PATCH content-type acceptance (application/json + application/merge-patch+json)
  - PATCH content-type rejection (415)
  - ?fields= first-level projection (href always preserved)
  - Error body format (TMF {"code","reason","message","@type":"Error"})
  - GET /health UP and DOWN states
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from src.main import app
from src.api import deps

# ── Shared intent fixture data ────────────────────────────────────────────────

INTENT_ID  = "intent-aaa"
REPORT_ID  = "report-bbb"
SPEC_ID    = "spec-ccc"
HUB_ID     = "hub-ddd"

_INTENT = {
    "id":              INTENT_ID,
    "href":            f"http://host/intent/{INTENT_ID}",
    "@type":           "Intent",
    "name":            "Test Intent",
    "lifecycleStatus": "ACKNOWLEDGED",
    "creationDate":    "2024-01-01T00:00:00+00:00",
    "lastUpdate":      "2024-01-01T00:00:00+00:00",
}

_REPORT = {
    "id":           REPORT_ID,
    "href":         f"http://host/intentReport/{REPORT_ID}",
    "@type":        "IntentReport",
    "name":         "Report 1",
    "creationDate": "2024-01-01T00:00:00+00:00",
}

_SPEC = {
    "id":         SPEC_ID,
    "href":       f"http://host/intentSpec/{SPEC_ID}",
    "@type":      "IntentSpecification",
    "name":       "Spec 1",
    "lastUpdate": "2024-01-01T00:00:00+00:00",
}

_HUB = {
    "id":       HUB_ID,
    "href":     f"http://host/hub/{HUB_ID}",
    "callback": "http://listener.example.com/events",
}

BASE = "/tmf-api/intentManagement/v5"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mock_intent_service(
    *,
    create=None, get=None, list_result=None, update=None, delete=None,
) -> MagicMock:
    svc = MagicMock()
    svc.create    = AsyncMock(return_value=create or _INTENT)
    svc.get_by_id = AsyncMock(return_value=get or _INTENT)
    svc.list      = AsyncMock(return_value=list_result or ([_INTENT], 1))
    svc.update    = AsyncMock(return_value=update or _INTENT)
    svc.delete    = AsyncMock(return_value=None)
    return svc


def _mock_report_service(
    *,
    get=None, list_result=None, delete=None,
) -> MagicMock:
    svc = MagicMock()
    svc.get_by_id = AsyncMock(return_value=get or _REPORT)
    svc.list      = AsyncMock(return_value=list_result or ([_REPORT], 1))
    svc.delete    = AsyncMock(return_value=None)
    return svc


def _mock_spec_service(
    *,
    create=None, get=None, list_result=None, update=None, delete=None,
) -> MagicMock:
    svc = MagicMock()
    svc.create    = AsyncMock(return_value=create or _SPEC)
    svc.get_by_id = AsyncMock(return_value=get or _SPEC)
    svc.list      = AsyncMock(return_value=list_result or ([_SPEC], 1))
    svc.update    = AsyncMock(return_value=update or _SPEC)
    svc.delete    = AsyncMock(return_value=None)
    return svc


def _mock_hub_repo(*, create=None, delete=None) -> MagicMock:
    repo = MagicMock()
    repo.create = AsyncMock(return_value=create or _HUB)
    repo.delete = AsyncMock(return_value=delete if delete is not None else True)
    return repo


# ── Intent router ─────────────────────────────────────────────────────────────

class TestIntentRouter:
    def test_list_200_with_headers(self) -> None:
        svc = _mock_intent_service()
        with TestClient(app) as client:
            app.dependency_overrides[deps.get_intent_service] = lambda: svc
            r = client.get(f"{BASE}/intent")
        app.dependency_overrides.clear()
        assert r.status_code == 200
        assert r.headers["x-total-count"] == "1"
        assert r.headers["x-result-count"] == "1"
        assert isinstance(r.json(), list)

    def test_list_empty_returns_200(self) -> None:
        svc = _mock_intent_service(list_result=([], 0))
        with TestClient(app) as client:
            app.dependency_overrides[deps.get_intent_service] = lambda: svc
            r = client.get(f"{BASE}/intent")
        app.dependency_overrides.clear()
        assert r.status_code == 200
        assert r.json() == []
        assert r.headers["x-total-count"] == "0"

    def test_list_fields_projection(self) -> None:
        svc = _mock_intent_service()
        with TestClient(app) as client:
            app.dependency_overrides[deps.get_intent_service] = lambda: svc
            r = client.get(f"{BASE}/intent?fields=id,name")
        app.dependency_overrides.clear()
        assert r.status_code == 200
        item = r.json()[0]
        assert "id" in item
        assert "name" in item
        assert "href" in item          # always preserved
        assert "lifecycleStatus" not in item

    def test_get_200(self) -> None:
        svc = _mock_intent_service()
        with TestClient(app) as client:
            app.dependency_overrides[deps.get_intent_service] = lambda: svc
            r = client.get(f"{BASE}/intent/{INTENT_ID}")
        app.dependency_overrides.clear()
        assert r.status_code == 200
        assert r.json()["id"] == INTENT_ID

    def test_get_404(self) -> None:
        svc = _mock_intent_service()
        svc.get_by_id = AsyncMock(
            side_effect=HTTPException(404, f"Intent {INTENT_ID!r} not found")
        )
        with TestClient(app) as client:
            app.dependency_overrides[deps.get_intent_service] = lambda: svc
            r = client.get(f"{BASE}/intent/missing")
        app.dependency_overrides.clear()
        assert r.status_code == 404
        body = r.json()
        assert body["code"] == "404"
        assert body["@type"] == "Error"

    def test_get_fields_href_preserved(self) -> None:
        svc = _mock_intent_service()
        with TestClient(app) as client:
            app.dependency_overrides[deps.get_intent_service] = lambda: svc
            r = client.get(f"{BASE}/intent/{INTENT_ID}?fields=name")
        app.dependency_overrides.clear()
        assert r.status_code == 200
        body = r.json()
        assert "href" in body
        assert "id" not in body

    def test_post_201(self) -> None:
        svc = _mock_intent_service()
        with TestClient(app) as client:
            app.dependency_overrides[deps.get_intent_service] = lambda: svc
            r = client.post(
                f"{BASE}/intent",
                json={"@type": "Intent", "name": "X"},
            )
        app.dependency_overrides.clear()
        assert r.status_code == 201
        assert r.json()["id"] == INTENT_ID

    def test_patch_200_json(self) -> None:
        svc = _mock_intent_service()
        with TestClient(app) as client:
            app.dependency_overrides[deps.get_intent_service] = lambda: svc
            r = client.patch(
                f"{BASE}/intent/{INTENT_ID}",
                json={"name": "Updated"},
                headers={"Content-Type": "application/json"},
            )
        app.dependency_overrides.clear()
        assert r.status_code == 200

    def test_patch_200_merge_patch_content_type(self) -> None:
        svc = _mock_intent_service()
        with TestClient(app) as client:
            app.dependency_overrides[deps.get_intent_service] = lambda: svc
            r = client.patch(
                f"{BASE}/intent/{INTENT_ID}",
                content=b'{"name":"Updated"}',
                headers={"Content-Type": "application/merge-patch+json"},
            )
        app.dependency_overrides.clear()
        assert r.status_code == 200

    def test_patch_415_unsupported_content_type(self) -> None:
        svc = _mock_intent_service()
        with TestClient(app) as client:
            app.dependency_overrides[deps.get_intent_service] = lambda: svc
            r = client.patch(
                f"{BASE}/intent/{INTENT_ID}",
                content=b"data",
                headers={"Content-Type": "text/plain"},
            )
        app.dependency_overrides.clear()
        assert r.status_code == 415

    def test_patch_400_non_patchable_field(self) -> None:
        svc = _mock_intent_service()
        svc.update = AsyncMock(
            side_effect=HTTPException(400, "Fields are not patchable: ['id']")
        )
        with TestClient(app) as client:
            app.dependency_overrides[deps.get_intent_service] = lambda: svc
            r = client.patch(
                f"{BASE}/intent/{INTENT_ID}",
                json={"id": "hacked"},
                headers={"Content-Type": "application/json"},
            )
        app.dependency_overrides.clear()
        assert r.status_code == 400

    def test_patch_400_invalid_lifecycle_transition(self) -> None:
        svc = _mock_intent_service()
        svc.update = AsyncMock(
            side_effect=HTTPException(400, "Invalid lifecycle transition")
        )
        with TestClient(app) as client:
            app.dependency_overrides[deps.get_intent_service] = lambda: svc
            r = client.patch(
                f"{BASE}/intent/{INTENT_ID}",
                json={"lifecycleStatus": "FULFILLED"},
                headers={"Content-Type": "application/json"},
            )
        app.dependency_overrides.clear()
        assert r.status_code == 400

    def test_patch_404(self) -> None:
        svc = _mock_intent_service()
        svc.update = AsyncMock(
            side_effect=HTTPException(404, f"Intent {INTENT_ID!r} not found")
        )
        with TestClient(app) as client:
            app.dependency_overrides[deps.get_intent_service] = lambda: svc
            r = client.patch(
                f"{BASE}/intent/missing",
                json={"name": "x"},
                headers={"Content-Type": "application/json"},
            )
        app.dependency_overrides.clear()
        assert r.status_code == 404

    def test_delete_204(self) -> None:
        svc = _mock_intent_service()
        with TestClient(app) as client:
            app.dependency_overrides[deps.get_intent_service] = lambda: svc
            r = client.delete(f"{BASE}/intent/{INTENT_ID}")
        app.dependency_overrides.clear()
        assert r.status_code == 204

    def test_delete_404(self) -> None:
        svc = _mock_intent_service()
        svc.delete = AsyncMock(
            side_effect=HTTPException(404, f"Intent {INTENT_ID!r} not found")
        )
        with TestClient(app) as client:
            app.dependency_overrides[deps.get_intent_service] = lambda: svc
            r = client.delete(f"{BASE}/intent/missing")
        app.dependency_overrides.clear()
        assert r.status_code == 404


# ── IntentReport router ───────────────────────────────────────────────────────

class TestIntentReportRouter:
    def test_list_200_with_headers(self) -> None:
        svc = _mock_report_service()
        with TestClient(app) as client:
            app.dependency_overrides[deps.get_intent_report_service] = lambda: svc
            r = client.get(f"{BASE}/intent/{INTENT_ID}/intentReport")
        app.dependency_overrides.clear()
        assert r.status_code == 200
        assert r.headers["x-total-count"] == "1"
        assert r.headers["x-result-count"] == "1"

    def test_list_empty_200(self) -> None:
        svc = _mock_report_service(list_result=([], 0))
        with TestClient(app) as client:
            app.dependency_overrides[deps.get_intent_report_service] = lambda: svc
            r = client.get(f"{BASE}/intent/{INTENT_ID}/intentReport")
        app.dependency_overrides.clear()
        assert r.status_code == 200
        assert r.json() == []

    def test_list_fields_href_preserved(self) -> None:
        svc = _mock_report_service()
        with TestClient(app) as client:
            app.dependency_overrides[deps.get_intent_report_service] = lambda: svc
            r = client.get(
                f"{BASE}/intent/{INTENT_ID}/intentReport?fields=id"
            )
        app.dependency_overrides.clear()
        assert r.status_code == 200
        assert "href" in r.json()[0]

    def test_get_200(self) -> None:
        svc = _mock_report_service()
        with TestClient(app) as client:
            app.dependency_overrides[deps.get_intent_report_service] = lambda: svc
            r = client.get(
                f"{BASE}/intent/{INTENT_ID}/intentReport/{REPORT_ID}"
            )
        app.dependency_overrides.clear()
        assert r.status_code == 200
        assert r.json()["id"] == REPORT_ID

    def test_get_404(self) -> None:
        svc = _mock_report_service()
        svc.get_by_id = AsyncMock(
            side_effect=HTTPException(404, "IntentReport not found")
        )
        with TestClient(app) as client:
            app.dependency_overrides[deps.get_intent_report_service] = lambda: svc
            r = client.get(
                f"{BASE}/intent/{INTENT_ID}/intentReport/missing"
            )
        app.dependency_overrides.clear()
        assert r.status_code == 404
        assert r.json()["@type"] == "Error"

    def test_delete_204(self) -> None:
        svc = _mock_report_service()
        with TestClient(app) as client:
            app.dependency_overrides[deps.get_intent_report_service] = lambda: svc
            r = client.delete(
                f"{BASE}/intent/{INTENT_ID}/intentReport/{REPORT_ID}"
            )
        app.dependency_overrides.clear()
        assert r.status_code == 204

    def test_delete_404(self) -> None:
        svc = _mock_report_service()
        svc.delete = AsyncMock(
            side_effect=HTTPException(404, "IntentReport not found")
        )
        with TestClient(app) as client:
            app.dependency_overrides[deps.get_intent_report_service] = lambda: svc
            r = client.delete(
                f"{BASE}/intent/{INTENT_ID}/intentReport/missing"
            )
        app.dependency_overrides.clear()
        assert r.status_code == 404


# ── IntentSpecification router ────────────────────────────────────────────────

class TestIntentSpecRouter:
    def test_list_200_with_headers(self) -> None:
        svc = _mock_spec_service()
        with TestClient(app) as client:
            app.dependency_overrides[deps.get_intent_spec_service] = lambda: svc
            r = client.get(f"{BASE}/intentSpecification")
        app.dependency_overrides.clear()
        assert r.status_code == 200
        assert r.headers["x-total-count"] == "1"
        assert r.headers["x-result-count"] == "1"

    def test_list_empty_200(self) -> None:
        svc = _mock_spec_service(list_result=([], 0))
        with TestClient(app) as client:
            app.dependency_overrides[deps.get_intent_spec_service] = lambda: svc
            r = client.get(f"{BASE}/intentSpecification")
        app.dependency_overrides.clear()
        assert r.status_code == 200
        assert r.json() == []

    def test_get_200(self) -> None:
        svc = _mock_spec_service()
        with TestClient(app) as client:
            app.dependency_overrides[deps.get_intent_spec_service] = lambda: svc
            r = client.get(f"{BASE}/intentSpecification/{SPEC_ID}")
        app.dependency_overrides.clear()
        assert r.status_code == 200
        assert r.json()["id"] == SPEC_ID

    def test_get_404(self) -> None:
        svc = _mock_spec_service()
        svc.get_by_id = AsyncMock(
            side_effect=HTTPException(404, f"IntentSpecification {SPEC_ID!r} not found")
        )
        with TestClient(app) as client:
            app.dependency_overrides[deps.get_intent_spec_service] = lambda: svc
            r = client.get(f"{BASE}/intentSpecification/missing")
        app.dependency_overrides.clear()
        assert r.status_code == 404
        assert r.json()["@type"] == "Error"

    def test_post_201(self) -> None:
        svc = _mock_spec_service()
        with TestClient(app) as client:
            app.dependency_overrides[deps.get_intent_spec_service] = lambda: svc
            r = client.post(
                f"{BASE}/intentSpecification",
                json={"@type": "IntentSpecification", "name": "My Spec"},
            )
        app.dependency_overrides.clear()
        assert r.status_code == 201

    def test_patch_200_json(self) -> None:
        svc = _mock_spec_service()
        with TestClient(app) as client:
            app.dependency_overrides[deps.get_intent_spec_service] = lambda: svc
            r = client.patch(
                f"{BASE}/intentSpecification/{SPEC_ID}",
                json={"name": "Updated"},
                headers={"Content-Type": "application/json"},
            )
        app.dependency_overrides.clear()
        assert r.status_code == 200

    def test_patch_200_merge_patch(self) -> None:
        svc = _mock_spec_service()
        with TestClient(app) as client:
            app.dependency_overrides[deps.get_intent_spec_service] = lambda: svc
            r = client.patch(
                f"{BASE}/intentSpecification/{SPEC_ID}",
                content=b'{"name":"Updated"}',
                headers={"Content-Type": "application/merge-patch+json"},
            )
        app.dependency_overrides.clear()
        assert r.status_code == 200

    def test_patch_415_unsupported_type(self) -> None:
        svc = _mock_spec_service()
        with TestClient(app) as client:
            app.dependency_overrides[deps.get_intent_spec_service] = lambda: svc
            r = client.patch(
                f"{BASE}/intentSpecification/{SPEC_ID}",
                content=b"data",
                headers={"Content-Type": "text/xml"},
            )
        app.dependency_overrides.clear()
        assert r.status_code == 415

    def test_patch_400_non_patchable_field(self) -> None:
        svc = _mock_spec_service()
        svc.update = AsyncMock(
            side_effect=HTTPException(400, "Fields are not patchable: ['href']")
        )
        with TestClient(app) as client:
            app.dependency_overrides[deps.get_intent_spec_service] = lambda: svc
            r = client.patch(
                f"{BASE}/intentSpecification/{SPEC_ID}",
                json={"href": "hacked"},
                headers={"Content-Type": "application/json"},
            )
        app.dependency_overrides.clear()
        assert r.status_code == 400

    def test_patch_404(self) -> None:
        svc = _mock_spec_service()
        svc.update = AsyncMock(
            side_effect=HTTPException(404, "IntentSpecification not found")
        )
        with TestClient(app) as client:
            app.dependency_overrides[deps.get_intent_spec_service] = lambda: svc
            r = client.patch(
                f"{BASE}/intentSpecification/missing",
                json={"name": "x"},
                headers={"Content-Type": "application/json"},
            )
        app.dependency_overrides.clear()
        assert r.status_code == 404

    def test_delete_204(self) -> None:
        svc = _mock_spec_service()
        with TestClient(app) as client:
            app.dependency_overrides[deps.get_intent_spec_service] = lambda: svc
            r = client.delete(f"{BASE}/intentSpecification/{SPEC_ID}")
        app.dependency_overrides.clear()
        assert r.status_code == 204

    def test_delete_404(self) -> None:
        svc = _mock_spec_service()
        svc.delete = AsyncMock(
            side_effect=HTTPException(404, "IntentSpecification not found")
        )
        with TestClient(app) as client:
            app.dependency_overrides[deps.get_intent_spec_service] = lambda: svc
            r = client.delete(f"{BASE}/intentSpecification/missing")
        app.dependency_overrides.clear()
        assert r.status_code == 404


# ── Hub router ────────────────────────────────────────────────────────────────

class TestHubRouter:
    def test_post_201(self) -> None:
        repo = _mock_hub_repo()
        with TestClient(app) as client:
            app.dependency_overrides[deps.get_hub_repo] = lambda: repo
            r = client.post(
                f"{BASE}/hub",
                json={"callback": "http://listener.example.com/events"},
            )
        app.dependency_overrides.clear()
        assert r.status_code == 201
        assert "id" in r.json()

    def test_post_400_missing_callback(self) -> None:
        repo = _mock_hub_repo()
        with TestClient(app) as client:
            app.dependency_overrides[deps.get_hub_repo] = lambda: repo
            r = client.post(f"{BASE}/hub", json={})
        app.dependency_overrides.clear()
        assert r.status_code == 400
        assert r.json()["@type"] == "Error"

    def test_delete_204(self) -> None:
        repo = _mock_hub_repo(delete=True)
        with TestClient(app) as client:
            app.dependency_overrides[deps.get_hub_repo] = lambda: repo
            r = client.delete(f"{BASE}/hub/{HUB_ID}")
        app.dependency_overrides.clear()
        assert r.status_code == 204

    def test_delete_404(self) -> None:
        repo = _mock_hub_repo(delete=False)
        with TestClient(app) as client:
            app.dependency_overrides[deps.get_hub_repo] = lambda: repo
            r = client.delete(f"{BASE}/hub/missing")
        app.dependency_overrides.clear()
        assert r.status_code == 404
        assert r.json()["@type"] == "Error"


# ── Health endpoint ───────────────────────────────────────────────────────────

class TestHealthEndpoint:
    def _make_health_client(self, graph_up: bool) -> TestClient:
        """
        Return a TestClient whose lifespan is bypassed so we control _client.
        We patch init_client / close_client to no-ops and inject the mock directly.
        """
        import src.graph.store as store_module
        from unittest.mock import patch

        mock_client = MagicMock()
        mock_client.health = AsyncMock(return_value=graph_up)

        # Patch lifespan helpers so they don't touch the real Fuseki
        with (
            patch.object(store_module, "init_client", new=AsyncMock()),
            patch.object(store_module, "close_client", new=AsyncMock()),
        ):
            store_module._client = mock_client
            tc = TestClient(app)
            try:
                tc.__enter__()
                r = tc.app_state_get = lambda: None  # keep ref alive
            finally:
                pass
            store_module._client = mock_client   # restore after lifespan ran
            return tc, mock_client

    def test_health_up(self) -> None:
        import src.graph.store as store_module
        from unittest.mock import patch

        mock_client = MagicMock()
        mock_client.health = AsyncMock(return_value=True)

        with (
            patch.object(store_module, "init_client", new=AsyncMock()),
            patch.object(store_module, "close_client", new=AsyncMock()),
        ):
            store_module._client = mock_client
            with TestClient(app) as client:
                store_module._client = mock_client   # re-inject after startup
                r = client.get("/health")

        assert r.status_code == 200
        assert r.json() == {"status": "UP", "graph": "UP"}

    def test_health_graph_down(self) -> None:
        import src.graph.store as store_module
        from unittest.mock import patch

        mock_client = MagicMock()
        mock_client.health = AsyncMock(return_value=False)

        with (
            patch.object(store_module, "init_client", new=AsyncMock()),
            patch.object(store_module, "close_client", new=AsyncMock()),
        ):
            store_module._client = mock_client
            with TestClient(app) as client:
                store_module._client = mock_client
                r = client.get("/health")

        assert r.status_code == 200
        assert r.json() == {"status": "UP", "graph": "DOWN"}


# ── Error body format ─────────────────────────────────────────────────────────

class TestErrorBodyFormat:
    def test_404_body_has_required_fields(self) -> None:
        svc = _mock_intent_service()
        svc.get_by_id = AsyncMock(
            side_effect=HTTPException(404, f"Intent {INTENT_ID!r} not found")
        )
        with TestClient(app) as client:
            app.dependency_overrides[deps.get_intent_service] = lambda: svc
            r = client.get(f"{BASE}/intent/missing")
        app.dependency_overrides.clear()
        body = r.json()
        assert body["code"] == "404"
        assert body["reason"] == "Not Found"
        assert "missing" in body["message"] or INTENT_ID in body["message"]
        assert body["@type"] == "Error"

    def test_400_body_format(self) -> None:
        svc = _mock_intent_service()
        svc.update = AsyncMock(
            side_effect=HTTPException(400, "Fields are not patchable: ['id']")
        )
        with TestClient(app) as client:
            app.dependency_overrides[deps.get_intent_service] = lambda: svc
            r = client.patch(
                f"{BASE}/intent/{INTENT_ID}",
                json={"id": "x"},
                headers={"Content-Type": "application/json"},
            )
        app.dependency_overrides.clear()
        body = r.json()
        assert body["code"] == "400"
        assert body["reason"] == "Bad Request"
        assert body["@type"] == "Error"


# ── fields_filter unit tests ──────────────────────────────────────────────────

class TestFieldsFilter:
    def test_no_fields_returns_all(self) -> None:
        from src.api.middleware.fields_filter import apply_fields
        data = {"id": "1", "name": "x", "href": "h"}
        assert apply_fields(data, None) == data

    def test_fields_projection_excludes_unrequested(self) -> None:
        from src.api.middleware.fields_filter import apply_fields
        data = {"id": "1", "name": "x", "href": "h", "extra": "y"}
        result = apply_fields(data, "id")
        assert "id" in result
        assert "href" in result       # always included
        assert "extra" not in result
        assert "name" not in result

    def test_href_always_included(self) -> None:
        from src.api.middleware.fields_filter import apply_fields
        data = {"id": "1", "href": "h"}
        result = apply_fields(data, "id")
        assert "href" in result

    def test_list_projection(self) -> None:
        from src.api.middleware.fields_filter import apply_fields
        data = [{"id": "1", "href": "h", "extra": "y"}]
        result = apply_fields(data, "id")
        assert result[0] == {"id": "1", "href": "h"}

    def test_empty_fields_string_returns_all(self) -> None:
        from src.api.middleware.fields_filter import apply_fields
        data = {"id": "1", "name": "x"}
        assert apply_fields(data, "") == data
