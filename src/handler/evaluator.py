"""
Intent Handler evaluator.

Flow:
  1. Query the intent's named graph for expressionValue and expression type.
  2. Load expressionValue as Turtle into a temporary evaluation named graph.
  3. Query the evaluation graph for intentHandlingState.
  4. Drop the evaluation graph.
  5. Return a dict with intentHandlingState and an optional reason.

Only TurtleExpression content is loaded for evaluation.  JsonLdExpression
content is stored opaquely by the API layer and is not materialised here;
if no Turtle is available the handler defaults to Degraded.
"""
from __future__ import annotations

import logging

from src.graph.nodes import eval_graph_uri, intent_graph_uri, intent_node
from src.graph.repositories.base_repository import PREFIXES
from src.graph.store import FusekiClient

logger = logging.getLogger(__name__)

_IMO = "http://tio.models.tmforum.org/tio/v3.6.0/IntentManagmentOntology#"

_INTENT_QUERY = """\
{prefixes}
SELECT ?exprType ?exprValue
WHERE {{
    GRAPH <{graph}> {{
        <{uri}> tmf:hasExpression ?exprNode .
        ?exprNode rdf:type ?exprType .
        OPTIONAL {{ ?exprNode tmf:expressionValue ?exprValue }}
    }}
}}
"""

_STATE_QUERY = """\
PREFIX imo: <{imo}>
SELECT ?state
WHERE {{
    GRAPH <{eval_graph}> {{
        ?s imo:intentHandlingState ?state .
    }}
}}
LIMIT 1
"""


async def evaluate_intent(intent_id: str, client: FusekiClient) -> dict:
    """
    Evaluate an intent and return its inferred intentHandlingState.

    Returns a dict: {"intentHandlingState": str, "reason": str | None}
    """
    graph_uri = str(intent_graph_uri(intent_id))
    node_uri = str(intent_node(intent_id))
    eval_graph = str(eval_graph_uri(intent_id))

    # Step 1 — fetch expressionType and expressionValue from the intent graph
    rows = await client.query(
        _INTENT_QUERY.format(
            prefixes=PREFIXES,
            graph=graph_uri,
            uri=node_uri,
        )
    )

    if not rows:
        logger.warning("evaluate_intent: intent %s has no expression", intent_id)
        return {"intentHandlingState": "Degraded", "reason": "Intent has no expression"}

    row = rows[0]
    expr_type_uri = (row.get("exprType") or {}).get("value", "")
    expr_type = expr_type_uri.rsplit("/", 1)[-1].rsplit("#", 1)[-1]
    expr_value_node = row.get("exprValue")
    expr_value: str | None = expr_value_node["value"] if expr_value_node else None

    if expr_type != "TurtleExpression" or not expr_value:
        logger.info(
            "evaluate_intent: intent %s uses %s — no Turtle to load; defaulting to Degraded",
            intent_id,
            expr_type,
        )
        return {
            "intentHandlingState": "Degraded",
            "reason": f"No Turtle expression to evaluate (type={expr_type})",
        }

    # Step 2 — load expressionValue Turtle into the evaluation named graph
    try:
        await client.gsp_post(eval_graph, expr_value)
    except Exception as exc:
        logger.error("evaluate_intent: gsp_post failed for %s: %s", intent_id, exc)
        return {"intentHandlingState": "Degraded", "reason": f"Expression load failed: {exc}"}

    # Step 3 — query inferred intentHandlingState
    try:
        state_rows = await client.query(
            _STATE_QUERY.format(imo=_IMO, eval_graph=eval_graph)
        )
        if state_rows:
            raw = (state_rows[0].get("state") or {}).get("value", "")
            state = raw.rsplit("#", 1)[-1].rsplit("/", 1)[-1] or "Degraded"
            return {"intentHandlingState": state, "reason": None}
        return {
            "intentHandlingState": "Degraded",
            "reason": "No intentHandlingState inferred from expression",
        }
    finally:
        # Step 4 — always drop the evaluation graph
        try:
            await client.update(f"DROP SILENT GRAPH <{eval_graph}>")
        except Exception as exc:
            logger.warning("evaluate_intent: failed to drop eval graph %s: %s", eval_graph, exc)
