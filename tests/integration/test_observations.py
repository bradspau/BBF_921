"""
Integration tests: metric observation submission endpoint.

Tests cover:
  - POST /intent/{id}/observation happy path
  - Input validation (invalid UUID, missing fields)
  - Re-evaluation is scheduled after observation write
"""
from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
import respx

from tests.integration.conftest import BASE, DATASET, FUSEKI

INTENT_ID = "aaaaaaaa-1111-2222-3333-bbbbbbbbbbbb"
METRIC_URI = "http://broadband-forum.org/Intent#DownstreamBandwidthMetric"
OBS_URL = f"{BASE}/intent/{INTENT_ID}/observation"

_NO_OBS = {"results": {"bindings": []}}


def _mock_observation_write() -> None:
    """Mock the GSP POST and the pruning SPARQL SELECT (no old observations)."""
    respx.post(f"{FUSEKI}/{DATASET}/data").mock(return_value=httpx.Response(200))
    respx.post(f"{FUSEKI}/{DATASET}/sparql").mock(
        return_value=httpx.Response(200, json=_NO_OBS)
    )


class TestObservationEndpoint:
    @respx.mock
    def test_post_observation_returns_201(self, tc):
        _mock_observation_write()
        resp = tc.post(OBS_URL, json={"metricUri": METRIC_URI, "value": 95.5})
        assert resp.status_code == 201

    @respx.mock
    def test_response_contains_expected_fields(self, tc):
        _mock_observation_write()
        resp = tc.post(OBS_URL, json={"metricUri": METRIC_URI, "value": 95.5})
        body = resp.json()
        assert body["metricUri"] == METRIC_URI
        assert body["value"] == 95.5
        assert body["intentId"] == INTENT_ID
        assert "observationId" in body

    @respx.mock
    def test_post_observation_with_obtained_at(self, tc):
        _mock_observation_write()
        resp = tc.post(OBS_URL, json={
            "metricUri": METRIC_URI,
            "value": 95.5,
            "obtainedAt": "2026-06-04T10:00:00Z",
        })
        assert resp.status_code == 201

    def test_invalid_intent_id_returns_422(self, tc):
        resp = tc.post(
            f"{BASE}/intent/not-a-uuid/observation",
            json={"metricUri": METRIC_URI, "value": 95.5},
        )
        assert resp.status_code == 422

    def test_missing_metric_uri_returns_422(self, tc):
        resp = tc.post(OBS_URL, json={"value": 95.5})
        assert resp.status_code == 422

    def test_missing_value_returns_422(self, tc):
        resp = tc.post(OBS_URL, json={"metricUri": METRIC_URI})
        assert resp.status_code == 422

    @respx.mock
    def test_schedule_evaluation_called_after_write(self, tc):
        _mock_observation_write()
        with patch("src.api.routers.observation.schedule_evaluation") as mock_sched:
            mock_sched.return_value = None
            tc.post(OBS_URL, json={"metricUri": METRIC_URI, "value": 95.5})
        mock_sched.assert_called_once()
        call_kwargs = mock_sched.call_args[0]
        assert call_kwargs[0] == INTENT_ID

    @respx.mock
    def test_observation_id_is_uuid(self, tc):
        import re
        _mock_observation_write()
        resp = tc.post(OBS_URL, json={"metricUri": METRIC_URI, "value": 95.5})
        obs_id = resp.json()["observationId"]
        assert re.match(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", obs_id
        )
