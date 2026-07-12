"""
Intent handler state writer.

Persists evaluation results as RDF facts in the intent's handlerState
named graph via the Graph Store Protocol (PUT replaces atomically).

This graph is the OODA loop's working memory — the Orient and Decide steps
query it directly via SPARQL without touching the API layer.  The IntentReport
is a separate outward-facing projection for API consumers.

Named graph URI:
    http://tmforum.org/api/v5/intents/{uuid}/handlerState

Schema written on every evaluation cycle:

    <intent_node>
        imo:intentHandlingState  imo:Fulfilled | imo:Degraded ;
        imo:lastEvaluated        "..."^^xsd:dateTime ;
        imo:hasConditionResult   <condition/0>, <condition/1>, ... .

    # two-argument operators (quanatLeast, quanatMost, quangreater,
    #                         quansmaller, quanexactly):
    <condition/N>
        a                        quan:{type} ;
        imo:conditionPassed      "true"|"false"^^xsd:boolean ;
        imo:observedValue        "..."^^xsd:decimal ;
        imo:boundValue           "..."^^xsd:decimal ;
        imo:evaluatedAt          "..."^^xsd:dateTime .

    # quan:quaninRange:
    <condition/N>
        a                        quan:quaninRange ;
        imo:conditionPassed      "..."^^xsd:boolean ;
        imo:observedValue        "..."^^xsd:decimal ;
        imo:lowerBound           "..."^^xsd:decimal ;
        imo:upperBound           "..."^^xsd:decimal ;
        imo:evaluatedAt          "..."^^xsd:dateTime .

    # DeliveryExpectation with a set-constructor pool (icm:chooseFrom):
    <condition/N>
        a                        quan:DeliveryExpectation ;
        imo:conditionPassed      "true"|"false"^^xsd:boolean ;
        imo:selectedResource     <resource-uri> ;         # omitted when no candidate
        imo:evaluatedAt          "..."^^xsd:dateTime .

    # structural error conditions:
    <condition/N>
        a                        quan:{type} ;
        imo:conditionPassed      "false"^^xsd:boolean ;
        imo:conditionError       "..."^^xsd:string ;
        imo:evaluatedAt          "..."^^xsd:dateTime .

    # ObservationReportingExpectation / GuaranteeReportingExpectation /
    # ValidityReportingExpectation — the icm:result already asserted on the
    # source expression node is mirrored back onto the condition node so the
    # rule-firing outcome is queryable from handlerState directly. Omitted
    # when the source node had no icm:result asserted (nothing to mirror).
    <condition/N>
        a                        quan:{type} ;
        imo:conditionPassed      "true"|"false"^^xsd:boolean ;
        icm:result               "true"|"false"^^xsd:boolean ;
        imo:evaluatedAt          "..."^^xsd:dateTime .
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from src.graph.nodes import (
    handler_state_condition_uri,
    handler_state_graph_uri,
    intent_node,
)
from src.graph.store import FusekiClient

logger = logging.getLogger(__name__)

_IMO  = "http://tio.models.tmforum.org/tio/v3.6.0/IntentManagementOntology/"
_QUAN = "http://tio.models.tmforum.org/tio/v3.6.0/QuantityOntology/"
_ICM  = "http://tio.models.tmforum.org/tio/v3.6.0/IntentCommonModel/"

_PREFIXES = (
    f"@prefix imo:  <{_IMO}> .\n"
    f"@prefix quan: <{_QUAN}> .\n"
    f"@prefix icm:  <{_ICM}> .\n"
    "@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .\n"
)


def _po_block(subject_uri: str, pairs: list[tuple[str, str]]) -> str:
    """Serialise a subject with predicate-object pairs as a Turtle block."""
    lines = [f"<{subject_uri}>"]
    for i, (pred, obj) in enumerate(pairs):
        sep = " ;" if i < len(pairs) - 1 else " ."
        lines.append(f"    {pred} {obj}{sep}")
    return "\n".join(lines)


def _intent_block(intent_id: str, state: str, timestamp: str, conditions: list[dict]) -> str:
    pairs: list[tuple[str, str]] = [
        ("imo:intentHandlingState", f"imo:{state}"),
        ("imo:lastEvaluated", f'"{timestamp}"^^xsd:dateTime'),
    ]
    if conditions:
        cond_list = ", ".join(
            f"<{handler_state_condition_uri(intent_id, i)}>"
            for i in range(len(conditions))
        )
        pairs.append(("imo:hasConditionResult", cond_list))
    return _po_block(str(intent_node(intent_id)), pairs)


def _condition_block(intent_id: str, index: int, c: dict, timestamp: str) -> str:
    passed = "true" if c["passed"] else "false"
    pairs: list[tuple[str, str]] = [
        ("a", f"quan:{c['type']}"),
        ("imo:conditionPassed", f'"{passed}"^^xsd:boolean'),
    ]

    if "error" in c:
        pairs.append(("imo:conditionError", f'"{c["error"]}"^^xsd:string'))
    elif c["type"] == "quaninRange":
        pairs += [
            ("imo:observedValue", f'"{c["observed"]}"^^xsd:decimal'),
            ("imo:lowerBound",    f'"{c["lower"]}"^^xsd:decimal'),
            ("imo:upperBound",    f'"{c["upper"]}"^^xsd:decimal'),
        ]
    elif "observed" in c:
        # Two-argument quantity conditions (quanatLeast, quansmaller, etc.)
        pairs += [
            ("imo:observedValue", f'"{c["observed"]}"^^xsd:decimal'),
            ("imo:boundValue",    f'"{c["bound"]}"^^xsd:decimal'),
        ]
    elif c.get("selected"):
        # DeliveryExpectation: record which resource the handler selected.
        pairs.append(("imo:selectedResource", f'<{c["selected"]}>'))
    elif "result" in c and c["result"] is not None:
        # Observation/Guarantee/ValidityReportingExpectation: mirror the
        # icm:result already asserted on the source node back onto the
        # condition node so the rule-firing outcome is queryable here too.
        pairs.append(("icm:result", f'"{c["result"]}"^^xsd:boolean'))

    pairs.append(("imo:evaluatedAt", f'"{timestamp}"^^xsd:dateTime'))
    return _po_block(str(handler_state_condition_uri(intent_id, index)), pairs)


def build_handler_state_turtle(intent_id: str, result: dict, timestamp: str) -> str:
    """
    Build the Turtle document for the handlerState graph from an evaluation result.

    Exposed for testing; production callers use write_handler_state().
    """
    state = result.get("intentHandlingState", "Degraded")
    conditions = result.get("conditions", [])

    blocks = [_PREFIXES, _intent_block(intent_id, state, timestamp, conditions)]
    for i, c in enumerate(conditions):
        blocks.append(_condition_block(intent_id, i, c, timestamp))

    return "\n\n".join(blocks) + "\n"


_PON = "http://broadband-forum.org/ont/pon-resource#"


async def write_resource_allocation(
    intent_id: str,
    result: dict,
    client: FusekiClient,
) -> None:
    """
    After a Fulfilled evaluation, mark each selected resource as in-use in the
    resource inventory graph.

    Reads DeliveryExpectation conditions that carry a "selected" URI and issues
    a SPARQL UPDATE to the RESOURCES_GRAPH: sets pon:inUse true and
    pon:assignedToService to the intent UUID.  Idempotent — the WHERE clause
    requires pon:inUse false so re-evaluation does not double-allocate.
    """
    from src.graph.namespaces import RESOURCES_GRAPH

    conditions = result.get("conditions", [])
    allocated = [
        c for c in conditions
        if c.get("type") == "DeliveryExpectation" and "selected" in c
    ]
    if not allocated:
        return

    for cond in allocated:
        resource_uri = cond["selected"]
        sparql = (
            f"PREFIX pon: <{_PON}>\n"
            f"WITH <{RESOURCES_GRAPH}>\n"
            f"DELETE {{ <{resource_uri}> pon:inUse false }}\n"
            f"INSERT {{ <{resource_uri}> pon:inUse true ;\n"
            f'                           pon:assignedToService "{intent_id}" }}\n'
            f"WHERE  {{ <{resource_uri}> pon:inUse false }}"
        )
        try:
            await client.update(sparql)
            logger.info(
                "write_resource_allocation: allocated %s to intent %s",
                resource_uri,
                intent_id,
            )
        except Exception as exc:
            logger.error(
                "write_resource_allocation: failed for %s / intent %s: %s",
                resource_uri,
                intent_id,
                exc,
            )


async def write_handler_state(
    intent_id: str,
    result: dict,
    client: FusekiClient,
) -> None:
    """
    Persist an evaluation result to the intent's handlerState named graph.

    Uses GSP PUT which replaces the graph atomically — stale facts from the
    previous cycle are never visible alongside new ones.  Write failures are
    logged and swallowed so a graph outage does not block IntentReport creation
    or API notifications.
    """
    graph_uri = str(handler_state_graph_uri(intent_id))
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        turtle = build_handler_state_turtle(intent_id, result, timestamp)
        await client.gsp_put(graph_uri, turtle)
        logger.debug(
            "write_handler_state: %s conditions for intent %s → %s",
            len(result.get("conditions", [])),
            intent_id,
            result.get("intentHandlingState", "Degraded"),
        )
    except Exception as exc:
        logger.error(
            "write_handler_state: failed for intent %s: %s",
            intent_id,
            exc,
        )
