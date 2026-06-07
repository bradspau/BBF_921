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
import os
import uuid
from datetime import datetime, timezone

import httpx

from src.graph.nodes import observations_graph_uri
from src.graph.store import FusekiClient

logger = logging.getLogger(__name__)

_MET_NS = "http://tio.models.tmforum.org/tio/v3.6.0/MetricsAndObservations/"
_MAX_OBS_PER_METRIC: int = int(os.getenv("MAX_OBS_PER_METRIC", "10"))
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


async def _prune_old_observations(
    client: FusekiClient, graph_uri: str, metric_uri: str, keep_n: int
) -> None:
    """
    Delete old met:Observation nodes for metric_uri in graph_uri beyond the keep_n newest.

    Queries by met:obtainedAt descending; deletes surplus nodes via a VALUES-targeted
    SPARQL DELETE.  Silently skips if the graph doesn't exist or has fewer than keep_n
    observations for the metric.
    """
    rows = await client.query(
        f"SELECT ?obs WHERE {{\n"
        f"    GRAPH <{graph_uri}> {{\n"
        f"        ?obs a <{_MET_NS}Observation> ;\n"
        f"             <{_MET_NS}observedMetric> <{metric_uri}> ;\n"
        f"             <{_MET_NS}obtainedAt> ?t .\n"
        f"    }}\n"
        f"}} ORDER BY DESC(?t)"
    )
    to_delete = [r["obs"]["value"] for r in rows[keep_n:]]
    if not to_delete:
        return
    values_clause = " ".join(f"(<{u}>)" for u in to_delete)
    await client.update(
        f"DELETE {{ GRAPH <{graph_uri}> {{ ?obs ?p ?v . }} }}\n"
        f"WHERE {{\n"
        f"    GRAPH <{graph_uri}> {{\n"
        f"        ?obs ?p ?v .\n"
        f"        VALUES (?obs) {{ {values_clause} }}\n"
        f"    }}\n"
        f"}}"
    )
    logger.debug(
        "write_observation: pruned %d old observations for metric=%s intent graph=%s",
        len(to_delete), metric_uri, graph_uri,
    )


async def write_observation(
    intent_id: str,
    metric_uri: str,
    value: float,
    client: FusekiClient,
    obtained_at: str | None = None,
) -> str:
    """
    Write a metric observation to the intent's observation graph and prune old ones.

    Keeps at most MAX_OBS_PER_METRIC (default 10) observations per metric so the
    graph stays bounded as an intent accumulates telemetry.
    Returns the new observation UUID.
    """
    obs_id = str(uuid.uuid4())
    timestamp = obtained_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    turtle = build_observation_turtle(intent_id, metric_uri, value, obs_id, timestamp)
    graph_uri = str(observations_graph_uri(intent_id))
    await client.gsp_post(graph_uri, turtle)
    await _prune_old_observations(client, graph_uri, metric_uri, _MAX_OBS_PER_METRIC)
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
