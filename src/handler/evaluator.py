"""
Intent Handler evaluator.

Flow:
  1. Query the intent's named graph for expressionValue and expression type.
  2. Acquire the eval semaphore (one evaluation at a time against the shared
     in-memory inference dataset).
  3. Clear the eval dataset default graph.
  4. Load expressionValue Turtle into the eval dataset default graph.
  5. Query the eval dataset for rule-derived intentHandlingState.
  6. Clear the eval dataset default graph.
  7. Release semaphore and return result.

Only TurtleExpression content is evaluated. JsonLdExpression is stored
opaquely; if no Turtle is present the handler defaults to Degraded.

The eval dataset (tmf921-eval) is configured in fuseki-config.ttl with a
ja:GenericRuleReasoner loaded from ontology/jena-rules/tio_all.rules.  Rules
run inside Jena (Java) against the in-memory default graph.
"""
from __future__ import annotations

import asyncio
import logging

from src.graph.nodes import intent_graph_uri, intent_node
from src.graph.repositories.base_repository import PREFIXES
from src.graph.store import FusekiClient

logger = logging.getLogger(__name__)

_EVAL_DATASET = "tmf921-eval"

# One evaluation at a time: the eval dataset's default graph is shared and
# in-memory; concurrent writes would corrupt each other's triples.
_eval_semaphore = asyncio.Semaphore(1)

_ICM = "http://tio.models.tmforum.org/tio/v3.6.0/IntentCommonModel/"
_IMO = "http://tio.models.tmforum.org/tio/v3.6.0/IntentManagementOntology/"

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

# Query the eval dataset default graph.  The TIO GenericRuleReasoner derives
# imo:intentHandlingState from the expression triples; no explicit assertion needed.
_STATE_QUERY = f"""\
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX icm: <{_ICM}>
PREFIX imo: <{_IMO}>
SELECT ?state
WHERE {{
    {{ ?s icm:intentHandlingState ?state }}
    UNION
    {{ ?s imo:intentHandlingState ?state }}
    UNION
    {{ ?s imo:imohandlingState ?state }}
}}
LIMIT 1
"""


async def evaluate_intent(intent_id: str, client: FusekiClient) -> dict:
    """
    Evaluate an intent and return its rule-derived intentHandlingState.

    Returns a dict: {"intentHandlingState": str, "reason": str | None}
    """
    graph_uri = str(intent_graph_uri(intent_id))
    node_uri = str(intent_node(intent_id))

    # Step 1 — fetch expression type and value from the intent's named graph
    rows = await client.query(
        _INTENT_QUERY.format(prefixes=PREFIXES, graph=graph_uri, uri=node_uri)
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
            "evaluate_intent: intent %s uses %s — no Turtle; defaulting to Degraded",
            intent_id,
            expr_type,
        )
        return {
            "intentHandlingState": "Degraded",
            "reason": f"No Turtle expression to evaluate (type={expr_type})",
        }

    # Steps 2-6 — run inference in the shared eval dataset (serialised)
    async with _eval_semaphore:
        # Step 3 — clear any leftover triples from a previous evaluation
        try:
            await client.update("CLEAR DEFAULT", dataset=_EVAL_DATASET)
        except Exception as exc:
            logger.warning("evaluate_intent: pre-clear failed for %s: %s", intent_id, exc)

        # Step 4 — load expression Turtle into the eval dataset default graph
        try:
            await client.gsp_post(None, expr_value, dataset=_EVAL_DATASET)
        except Exception as exc:
            logger.error("evaluate_intent: gsp_post failed for %s: %s", intent_id, exc)
            return {"intentHandlingState": "Degraded", "reason": f"Expression load failed: {exc}"}

        # Step 5 — query for the rule-derived intentHandlingState
        try:
            state_rows = await client.query(_STATE_QUERY, dataset=_EVAL_DATASET)
            if state_rows:
                raw = (state_rows[0].get("state") or {}).get("value", "")
                state = raw.rsplit("#", 1)[-1].rsplit("/", 1)[-1] or "Degraded"
                return {"intentHandlingState": state, "reason": None}
            return {
                "intentHandlingState": "Degraded",
                "reason": "No intentHandlingState inferred from expression",
            }
        finally:
            # Step 6 — always clear the eval dataset after use
            try:
                await client.update("CLEAR DEFAULT", dataset=_EVAL_DATASET)
            except Exception as exc:
                logger.warning("evaluate_intent: post-clear failed for %s: %s", intent_id, exc)
