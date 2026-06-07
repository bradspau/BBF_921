"""
Unit tests for src/handler/observation_store.py.

All Fuseki HTTP calls are intercepted by respx — no live Fuseki required.
"""
from __future__ import annotations

import re

import httpx
import pytest
import respx
import rdflib

from src.graph.store import FusekiClient
from src.handler.observation_store import (
    build_observation_turtle,
    get_observations_turtle,
    write_observation,
)

FUSEKI = "http://localhost:3030"
DATASET = "tmf921"
INTENT_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
METRIC_URI = "http://broadband-forum.org/Intent#DownstreamBandwidthMetric"
OBS_GRAPH = f"http://tmforum.org/api/v5/intents/{INTENT_ID}/observations"
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


class TestBuildObservationTurtle:
    def test_contains_observation_type(self):
        t = build_observation_turtle(INTENT_ID, METRIC_URI, 95.5, "obs-1", "2026-06-04T10:00:00Z")
        assert "met:Observation" in t

    def test_contains_metric_uri(self):
        t = build_observation_turtle(INTENT_ID, METRIC_URI, 95.5, "obs-1", "2026-06-04T10:00:00Z")
        assert METRIC_URI in t

    def test_contains_value(self):
        t = build_observation_turtle(INTENT_ID, METRIC_URI, 95.5, "obs-1", "2026-06-04T10:00:00Z")
        assert "95.5" in t

    def test_contains_timestamp(self):
        t = build_observation_turtle(INTENT_ID, METRIC_URI, 95.5, "obs-1", "2026-06-04T10:00:00Z")
        assert "2026-06-04T10:00:00Z" in t

    def test_parses_as_valid_turtle(self):
        t = build_observation_turtle(INTENT_ID, METRIC_URI, 95.5, "obs-1", "2026-06-04T10:00:00Z")
        g = rdflib.Graph()
        g.parse(data=t, format="turtle")
        assert len(g) > 0

    def test_observation_uri_contains_intent_id(self):
        t = build_observation_turtle(INTENT_ID, METRIC_URI, 95.5, "obs-1", "2026-06-04T10:00:00Z")
        assert INTENT_ID in t

    def test_integer_value_serialised(self):
        t = build_observation_turtle(INTENT_ID, METRIC_URI, 100.0, "obs-1", "2026-06-04T10:00:00Z")
        assert "100.0" in t


_NO_OBS_BINDINGS = {"results": {"bindings": []}}


class TestWriteObservation:
    @respx.mock
    async def test_posts_to_observations_graph(self):
        route = respx.post(f"{FUSEKI}/{DATASET}/data").mock(return_value=httpx.Response(200))
        respx.post(f"{FUSEKI}/{DATASET}/sparql").mock(return_value=httpx.Response(200, json=_NO_OBS_BINDINGS))
        async with FusekiClient(FUSEKI, DATASET) as client:
            obs_id = await write_observation(INTENT_ID, METRIC_URI, 95.5, client)
        assert route.called
        called_url = str(route.calls[0].request.url)
        assert "observations" in called_url

    @respx.mock
    async def test_returns_uuid(self):
        respx.post(f"{FUSEKI}/{DATASET}/data").mock(return_value=httpx.Response(200))
        respx.post(f"{FUSEKI}/{DATASET}/sparql").mock(return_value=httpx.Response(200, json=_NO_OBS_BINDINGS))
        async with FusekiClient(FUSEKI, DATASET) as client:
            obs_id = await write_observation(INTENT_ID, METRIC_URI, 95.5, client)
        assert _UUID_RE.match(obs_id)

    @respx.mock
    async def test_custom_obtained_at_used(self):
        route = respx.post(f"{FUSEKI}/{DATASET}/data").mock(return_value=httpx.Response(200))
        respx.post(f"{FUSEKI}/{DATASET}/sparql").mock(return_value=httpx.Response(200, json=_NO_OBS_BINDINGS))
        async with FusekiClient(FUSEKI, DATASET) as client:
            await write_observation(INTENT_ID, METRIC_URI, 95.5, client, "2026-06-04T10:00:00Z")
        body = route.calls[0].request.content.decode()
        assert "2026-06-04T10:00:00Z" in body

    @respx.mock
    async def test_value_in_request_body(self):
        route = respx.post(f"{FUSEKI}/{DATASET}/data").mock(return_value=httpx.Response(200))
        respx.post(f"{FUSEKI}/{DATASET}/sparql").mock(return_value=httpx.Response(200, json=_NO_OBS_BINDINGS))
        async with FusekiClient(FUSEKI, DATASET) as client:
            await write_observation(INTENT_ID, METRIC_URI, 42.5, client)
        body = route.calls[0].request.content.decode()
        assert "42.5" in body

    @respx.mock
    async def test_prunes_old_observations_beyond_limit(self, monkeypatch):
        """When >MAX_OBS_PER_METRIC observations exist, old ones are deleted."""
        import src.handler.observation_store as obs_mod
        monkeypatch.setattr(obs_mod, "_MAX_OBS_PER_METRIC", 2)

        old_obs_uris = [
            f"http://tmforum.org/api/v5/intents/{INTENT_ID}/observations/old-{i}"
            for i in range(3)
        ]
        bindings = [
            {"obs": {"type": "uri", "value": u}}
            for u in old_obs_uris
        ]
        respx.post(f"{FUSEKI}/{DATASET}/data").mock(return_value=httpx.Response(200))
        respx.post(f"{FUSEKI}/{DATASET}/sparql").mock(
            return_value=httpx.Response(200, json={"results": {"bindings": bindings}})
        )
        update_route = respx.post(f"{FUSEKI}/{DATASET}/update").mock(return_value=httpx.Response(200))
        async with FusekiClient(FUSEKI, DATASET) as client:
            await write_observation(INTENT_ID, METRIC_URI, 99.0, client)

        assert update_route.called
        from urllib.parse import unquote_plus
        body = unquote_plus(update_route.calls[0].request.content.decode())
        assert old_obs_uris[2] in body

    @respx.mock
    async def test_no_delete_when_within_limit(self, monkeypatch):
        """When observation count ≤ MAX_OBS_PER_METRIC, no SPARQL DELETE is sent."""
        import src.handler.observation_store as obs_mod
        monkeypatch.setattr(obs_mod, "_MAX_OBS_PER_METRIC", 10)

        respx.post(f"{FUSEKI}/{DATASET}/data").mock(return_value=httpx.Response(200))
        respx.post(f"{FUSEKI}/{DATASET}/sparql").mock(
            return_value=httpx.Response(200, json=_NO_OBS_BINDINGS)
        )
        update_route = respx.post(f"{FUSEKI}/{DATASET}/update").mock(return_value=httpx.Response(200))
        async with FusekiClient(FUSEKI, DATASET) as client:
            await write_observation(INTENT_ID, METRIC_URI, 99.0, client)

        assert not update_route.called


class TestGetObservationsTurtle:
    @respx.mock
    async def test_returns_turtle_when_graph_exists(self):
        expected = "@prefix met: <http://example.org/> ."
        respx.get(f"{FUSEKI}/{DATASET}/data").mock(return_value=httpx.Response(200, text=expected))
        async with FusekiClient(FUSEKI, DATASET) as client:
            result = await get_observations_turtle(INTENT_ID, client)
        assert result == expected

    @respx.mock
    async def test_returns_empty_string_on_404(self):
        respx.get(f"{FUSEKI}/{DATASET}/data").mock(return_value=httpx.Response(404))
        async with FusekiClient(FUSEKI, DATASET) as client:
            result = await get_observations_turtle(INTENT_ID, client)
        assert result == ""

    @respx.mock
    async def test_raises_on_server_error(self):
        respx.get(f"{FUSEKI}/{DATASET}/data").mock(return_value=httpx.Response(500))
        async with FusekiClient(FUSEKI, DATASET) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await get_observations_turtle(INTENT_ID, client)
