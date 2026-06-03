"""
Intent Handler evaluator.

Flow:
  1. Query the intent's named graph for expressionValue and expression type.
  2. Parse the TurtleExpression with RDFLib.
  3. Evaluate all TIO quantity conditions in Python.
  4. Return intentHandlingState: Fulfilled if all conditions pass, Degraded otherwise.

Only TurtleExpression content is evaluated. JsonLdExpression is stored
opaquely; if no Turtle is present the handler defaults to Degraded.

Quantity operators supported (TIO QuantityOntology v3.6.0):
  quan:quanatLeast  — observed >= bound
  quan:quanatMost   — observed <= bound
  quan:quangreater  — observed >  bound
  quan:quansmaller  — observed <  bound
  quan:quanexactly  — observed == bound
  quan:quaninRange  — lower <= observed <= upper
"""
from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from operator import ge, gt, le, lt, eq

import rdflib
from rdflib.namespace import RDF

from src.graph.nodes import intent_graph_uri, intent_node
from src.graph.repositories.base_repository import PREFIXES
from src.graph.store import FusekiClient

logger = logging.getLogger(__name__)

_QUAN = rdflib.Namespace("http://tio.models.tmforum.org/tio/v3.6.0/QuantityOntology/")

# (rdf_type, comparator, label)  — two-argument pattern
_TWO_ARG_OPS: list[tuple[rdflib.URIRef, object, str]] = [
    (_QUAN.quanatLeast, ge, ">="),
    (_QUAN.quanatMost,  le, "<="),
    (_QUAN.quangreater, gt, ">"),
    (_QUAN.quansmaller, lt, "<"),
    (_QUAN.quanexactly, eq, "=="),
]

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


def evaluate_turtle_conditions(turtle_str: str) -> dict:
    """
    Parse TIO Turtle and evaluate all quantity conditions in Python.

    Returns {"intentHandlingState": "Fulfilled"|"Degraded", "reason": str|None}.
    """
    g = rdflib.Graph()
    try:
        g.parse(data=turtle_str, format="turtle")
    except Exception as exc:
        return {"intentHandlingState": "Degraded", "reason": f"Turtle parse error: {exc}"}

    results: list[tuple[bool, str]] = []

    # ── Two-argument operators ────────────────────────────────────────────────
    for rdf_type, cmp_op, sym in _TWO_ARG_OPS:
        for node in g.subjects(RDF.type, rdf_type):
            val_node = g.value(node, RDF.first)
            rest = g.value(node, RDF.rest)
            bnd_node = g.value(rest, RDF.first) if rest is not None else None
            if val_node is None or bnd_node is None:
                results.append((False, f"{rdf_type.split('/')[-1]}: missing operand nodes"))
                continue
            obs_lit = g.value(val_node, RDF.value)
            bnd_lit = g.value(bnd_node, RDF.value)
            if obs_lit is None or bnd_lit is None:
                results.append((False, f"{rdf_type.split('/')[-1]}: missing rdf:value"))
                continue
            try:
                obs = Decimal(str(obs_lit))
                bnd = Decimal(str(bnd_lit))
            except InvalidOperation:
                results.append((False, f"{rdf_type.split('/')[-1]}: non-numeric value"))
                continue
            ok = bool(cmp_op(obs, bnd))
            results.append((ok, f"{obs} {sym} {bnd}: {'pass' if ok else 'FAIL'}"))

    # ── quan:quaninRange: lower <= value <= upper ─────────────────────────────
    for node in g.subjects(RDF.type, _QUAN.quaninRange):
        val_node = g.value(node, RDF.first)
        r1 = g.value(node, RDF.rest)
        lo_node = g.value(r1, RDF.first) if r1 is not None else None
        r2 = g.value(r1, RDF.rest) if r1 is not None else None
        hi_node = g.value(r2, RDF.first) if r2 is not None else None
        if val_node is None or lo_node is None or hi_node is None:
            results.append((False, "quaninRange: missing operand nodes"))
            continue
        val_lit = g.value(val_node, RDF.value)
        lo_lit = g.value(lo_node, RDF.value)
        hi_lit = g.value(hi_node, RDF.value)
        if val_lit is None or lo_lit is None or hi_lit is None:
            results.append((False, "quaninRange: missing rdf:value"))
            continue
        try:
            val = Decimal(str(val_lit))
            lo = Decimal(str(lo_lit))
            hi = Decimal(str(hi_lit))
        except InvalidOperation:
            results.append((False, "quaninRange: non-numeric value"))
            continue
        ok = bool(lo <= val <= hi)
        results.append((ok, f"{lo} <= {val} <= {hi}: {'pass' if ok else 'FAIL'}"))

    if not results:
        return {
            "intentHandlingState": "Degraded",
            "reason": "No quantity conditions found in expression",
        }

    failed = [reason for ok, reason in results if not ok]
    if failed:
        return {
            "intentHandlingState": "Degraded",
            "reason": f"Conditions not met: {'; '.join(failed)}",
        }
    return {"intentHandlingState": "Fulfilled", "reason": None}


async def evaluate_intent(intent_id: str, client: FusekiClient) -> dict:
    """
    Evaluate an intent and return its intentHandlingState.

    Returns a dict: {"intentHandlingState": str, "reason": str | None}
    """
    graph_uri = str(intent_graph_uri(intent_id))
    node_uri = str(intent_node(intent_id))

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

    return evaluate_turtle_conditions(expr_value)
