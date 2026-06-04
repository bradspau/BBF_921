"""
Metric observation storage for the TMF921 intent handler.

Observations are persisted as met:Observation nodes in a per-intent named
graph: http://tmforum.org/api/v5/intents/{uuid}/observations

Each observation records:
  met:observedMetric  — the metric URI
  rdf:value           — the decimal reading
  met:obtainedAt      — ISO-8601 timestamp of measurement

Multiple observations for the same metric may exist; the evaluator picks
the most recent one by met:obtainedAt when resolving metric references.

The evaluation pipeline merges this graph with the expression Turtle before
running comparators so metric references in quantity conditions resolve to
actual values.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

import httpx

from src.graph.nodes import observations_graph_uri
from src.graph.store import FusekiClient

logger = logging.getLogger(__name__)

_MET_NS = "http://tio.models.tmforum.org/tio/v3.6.0/MetricsAndObservations/"
_PREFIXES = (
    f"@prefix met: <{_MET_NS}> .\n"
    "@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .\n"
    "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .\n"
)


def build_observation_turtle(
    intent_id: str,
    metric_uri: str,
    value: float,
    obs_id: str,
    timestamp: str,
) -> str:
    """Build a Turtle document for a single met:Observation record."""
    obs_uri = f"http://tmforum.org/api/v5/intents/{intent_id}/observations/{obs_id}"
    return (
        _PREFIXES
        + f"\n<{obs_uri}>\n"
        f"    a met:Observation ;\n"
        f"    met:observedMetric <{metric_uri}> ;\n"
        f'    rdf:value "{value}"^^xsd:decimal ;\n'
        f'    met:obtainedAt "{timestamp}"^^xsd:dateTime .\n'
    )


async def write_observation(
    intent_id: str,
    metric_uri: str,
    value: float,
    client: FusekiClient,
    obtained_at: str | None = None,
) -> str:
    """
    Append a metric observation to the intent's observation graph.

    Uses GSP POST (merge) so observations for different metrics accumulate.
    Returns the new observation UUID.
    """
    obs_id = str(uuid.uuid4())
    timestamp = obtained_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    turtle = build_observation_turtle(intent_id, metric_uri, value, obs_id, timestamp)
    graph_uri = str(observations_graph_uri(intent_id))
    await client.gsp_post(graph_uri, turtle)
    logger.debug("write_observation: intent=%s metric=%s value=%s", intent_id, metric_uri, value)
    return obs_id


async def get_observations_turtle(intent_id: str, client: FusekiClient) -> str:
    """
    Return the observation graph as Turtle, or empty string if not yet populated.
    """
    graph_uri = str(observations_graph_uri(intent_id))
    try:
        return await client.gsp_get(graph_uri)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return ""
        raise
