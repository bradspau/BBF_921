"""
System limits provider for Flow 3 (Best/Propose) negotiation.

Operator-declared capacity constraints are loaded from HANDLER_LIMITS_JSON at
startup. The dispatcher uses these as fallback best-effort bounds when the
evaluator has no observed value for a failed condition.

Format: JSON object mapping TIO condition type short names to numeric limits.
Example:
    HANDLER_LIMITS_JSON='{"quanatLeast": 25.0, "quanatMost": 500.0}'

apply_best_effort_bounds() uses a two-step fallback for each failed condition:
  1. conditions[].observed (actual measured value) — primary source
  2. HANDLER_LIMITS_JSON[condition_type] — operator-declared capacity
"""
from __future__ import annotations

import json
import logging
import os
from decimal import Decimal, InvalidOperation

import rdflib
from rdflib.namespace import RDF, XSD

logger = logging.getLogger(__name__)

_QUAN_BASE = "http://tio.models.tmforum.org/tio/v3.6.0/QuantityOntology/"

# Short type name → URIRef, covering both legacy (quanat*) and short-name aliases.
_QUAN_TYPE_URIS: dict[str, rdflib.URIRef] = {
    "quanatLeast": rdflib.URIRef(_QUAN_BASE + "quanatLeast"),
    "atLeast":     rdflib.URIRef(_QUAN_BASE + "atLeast"),
    "quanatMost":  rdflib.URIRef(_QUAN_BASE + "quanatMost"),
    "atMost":      rdflib.URIRef(_QUAN_BASE + "atMost"),
    "quangreater": rdflib.URIRef(_QUAN_BASE + "quangreater"),
    "greater":     rdflib.URIRef(_QUAN_BASE + "greater"),
    "quansmaller": rdflib.URIRef(_QUAN_BASE + "quansmaller"),
    "smaller":     rdflib.URIRef(_QUAN_BASE + "smaller"),
    "quanexactly": rdflib.URIRef(_QUAN_BASE + "quanexactly"),
    "exactly":     rdflib.URIRef(_QUAN_BASE + "exactly"),
}


def _load_limits() -> dict[str, Decimal]:
    raw = os.getenv("HANDLER_LIMITS_JSON", "{}")
    try:
        data = json.loads(raw)
        result: dict[str, Decimal] = {}
        for k, v in data.items():
            try:
                result[k] = Decimal(str(v))
            except InvalidOperation:
                logger.warning("HANDLER_LIMITS_JSON: non-numeric value for key %r — skipped", k)
        return result
    except json.JSONDecodeError as exc:
        logger.warning("HANDLER_LIMITS_JSON parse error: %s", exc)
        return {}


_LIMITS: dict[str, Decimal] = _load_limits()


def get_limit(condition_type: str) -> Decimal | None:
    """Return operator-declared best-effort bound for a condition type, or None."""
    return _LIMITS.get(condition_type)


def apply_best_effort_bounds(
    turtle: str,
    conditions: list[dict],
    _limits: dict[str, Decimal] | None = None,
) -> tuple[str | None, bool]:
    """
    Parse a Turtle expression and substitute best-effort bounds for failed conditions.

    For each failed two-argument quantity condition node:
      1. Use conditions[].observed as the new bound (actual measured value).
      2. Fall back to HANDLER_LIMITS_JSON[condition_type] (operator-declared capacity).

    Returns (new_turtle, True) when at least one bound was updated, (None, False) otherwise.

    The _limits parameter is for testing without module reloads; production code
    leaves it None and the global _LIMITS dict is used.
    """
    limits = _limits if _limits is not None else _LIMITS

    g = rdflib.Graph()
    try:
        g.parse(data=turtle, format="turtle")
    except Exception as exc:
        logger.warning("apply_best_effort_bounds: Turtle parse error: %s", exc)
        return None, False

    # Best-effort value keyed by condition type, built from evaluator output.
    best_by_type: dict[str, Decimal] = {}
    for cond in conditions:
        if cond.get("passed"):
            continue
        type_name = cond.get("type", "")
        if type_name not in _QUAN_TYPE_URIS:
            continue
        observed = cond.get("observed")
        if observed is not None:
            try:
                best_by_type[type_name] = Decimal(str(observed))
                continue
            except InvalidOperation:
                pass
        fallback = limits.get(type_name)
        if fallback is not None:
            best_by_type[type_name] = fallback

    if not best_by_type:
        return None, False

    changed = False
    for type_name, best_val in best_by_type.items():
        type_uri = _QUAN_TYPE_URIS[type_name]
        for node in list(g.subjects(RDF.type, type_uri)):
            # Navigate two-arg structure: node → rdf:rest → rdf:first (bound node)
            rest = g.value(node, RDF.rest)
            if rest is None:
                continue
            bnd_node = g.value(rest, RDF.first)
            if bnd_node is None:
                continue
            old_lit = g.value(bnd_node, RDF.value)
            if old_lit is None:
                continue
            try:
                if Decimal(str(old_lit)) == best_val:
                    continue
            except InvalidOperation:
                pass
            g.remove((bnd_node, RDF.value, old_lit))
            g.add((bnd_node, RDF.value, rdflib.Literal(str(best_val), datatype=XSD.decimal)))
            changed = True

    if not changed:
        return None, False

    return g.serialize(format="turtle"), True
