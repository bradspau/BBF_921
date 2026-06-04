"""
Intent Handler evaluator.

Flow:
  1. Query the intent's named graph for expressionValue and expression type.
  2. Fetch the intent's observation graph and merge with expression Turtle.
  3. Resolve metric references (met:Observation records) onto quantity condition nodes.
  4. Evaluate the TIO expression tree recursively.
  5. Return intentHandlingState: Fulfilled | Degraded.

Only TurtleExpression content is evaluated. JsonLdExpression is stored
opaquely; if no Turtle is present the handler defaults to Degraded.

Evaluation architecture (replaces Jena inference):
  - Tree traversal: starts from outermost logical combinator nodes, recurses
    into child nodes, resolves to leaf quantity conditions or log:match checks.
  - Flat-scan fallback: when no logical structure is present (bare quantity
    conditions), all conditions are collected and ANDed — backward compatible.

Logical operators supported (TIO LogicalOperators v3.6.0):
  log:allOf        — AND  — all items in RDF list must pass
  log:anyOf        — OR   — at least one item must pass
  log:noneOf       — NOR  — no item may pass
  log:oneOf        — XOR  — exactly one item must pass
  log:match        — checks a (subject, predicate, object) triple exists in graph
  log:matchAll     — all rdfs:members of container satisfy (M, predicate, object)
  log:matchAny     — any member satisfies the triple pattern
  log:matchNone    — no member satisfies the triple pattern
  log:matchOne     — exactly one member satisfies the triple pattern
  log:matchStatement — all reified rdf:Statement arguments are true in graph

Quantity operators supported (TIO QuantityOntology v3.6.0):
  quan:quanatLeast  — observed >= bound
  quan:quanatMost   — observed <= bound
  quan:quangreater  — observed >  bound
  quan:quansmaller  — observed <  bound
  quan:quanexactly  — observed == bound
  quan:quaninRange  — lower <= observed <= upper

Set operators supported (TIO SetOperators v3.6.0):
  set:setisMember      — true if resource (rdf:first) is a member of ANY container in
                         rdf:rest.  Containers listed via rdfs:member or RDF list.
  set:setintersectsWith — true if C1 and C2 share at least one rdfs:member.
                         Args: (C1, C2) as rdf:first / rdf:rest/rdf:first.
  set:setincludedIn    — true if every member of C1 is also in each remaining container.
                         Args: (C1, C2, ...) as RDF list starting at the function node.
  set:setforAll        — for every member M of container (2nd arg), evaluate condition
                         (3rd arg) with the member variable (1st arg / rdf:first)
                         substituted by M.  Empty container → vacuously True.

ICM expectation types (tio_core + tmf_icm_eval):
  icm:DeliveryExpectation — passes if icm:target container has ≥1 member whose
                            rdf:type matches icm:deliveryType (icmDeliveryFulfilled).
                            Also passes when icm:result true is already asserted.
  icm:PropertyExpectation — passes if rdf:value is "true"^^xsd:boolean
                            (icmPropertyResult rule).

Validity evaluation (tmf_validity_eval):
  Pre-processing: iv:ivsameValidityAs chains are resolved so that iv:ivisValid
                  is propagated to all linked nodes before any condition is
                  evaluated (_resolve_validity_chains).
  iv:ivvalidityOf — boolean function: passes iff ALL rdfs:member resources carry
                    iv:ivisValid "true"^^xsd:boolean (ivValidityOf rule).
  iv:ivvalidIf gate — any condition node that has iv:ivvalidIf pointing to a
                      validity context whose iv:ivisValid is absent or false will
                      fail immediately (validityGate condition), regardless of the
                      condition's own value.

Metric resolution patterns (replaces Jena tmf_metrics_eval.rules):
  A) rdf:first → <metric URI>       — direct ref; resolved via met:Observation
  B) rdf:first → met:metlastValue   — latest observation for linked metric
  C) rdf:first → met:metobservedValue — value of a directly referenced observation
"""
from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from operator import ge, gt, le, lt, eq
from typing import Callable

import rdflib
from rdflib.namespace import RDF, RDFS

from src.graph.nodes import intent_graph_uri, intent_node
from src.graph.repositories.base_repository import PREFIXES
from src.graph.store import FusekiClient
from src.handler.observation_store import get_observations_turtle

logger = logging.getLogger(__name__)

_QUAN = rdflib.Namespace("http://tio.models.tmforum.org/tio/v3.6.0/QuantityOntology/")
_MET  = rdflib.Namespace("http://tio.models.tmforum.org/tio/v3.6.0/MetricsAndObservations/")
_LOG  = rdflib.Namespace("http://tio.models.tmforum.org/tio/v3.6.0/LogicalOperators/")
_SET  = rdflib.Namespace("http://tio.models.tmforum.org/tio/v3.6.0/SetOperators/")
_ICM  = rdflib.Namespace("http://tio.models.tmforum.org/tio/v3.6.0/IntentCommonModel/")
_IV   = rdflib.Namespace("http://tio.models.tmforum.org/tio/v3.6.0/IntentValidityOntology/")

# (rdf_type, comparator, display_symbol) — two-argument quantity pattern
_TWO_ARG_OPS: list[tuple[rdflib.URIRef, object, str]] = [
    (_QUAN.quanatLeast, ge, ">="),
    (_QUAN.quanatMost,  le, "<="),
    (_QUAN.quangreater, gt, ">"),
    (_QUAN.quansmaller, lt, "<"),
    (_QUAN.quanexactly, eq, "=="),
]

# (log property, Python combinator over list[bool])
# TIO spec: allOf/anyOf/oneOf return False for empty; noneOf returns True for empty.
_LOG_COMBINATORS: list[tuple[rdflib.URIRef, Callable]] = [
    (_LOG.allOf,  lambda bs: bool(bs) and all(bs)),
    (_LOG.anyOf,  lambda bs: bool(bs) and any(bs)),
    (_LOG.noneOf, lambda bs: not any(bs)),
    (_LOG.oneOf,  lambda bs: sum(bs) == 1),
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

# ── Metric resolution ─────────────────────────────────────────────────────────

def _latest_observation_value(
    g: rdflib.Graph, metric_uri: rdflib.term.Node
) -> rdflib.term.Literal | None:
    """Return the rdf:value of the most recent met:Observation for a metric."""
    best_time: str | None = None
    best_val = None
    for obs in g.subjects(RDF.type, _MET.Observation):
        if (obs, _MET.observedMetric, metric_uri) not in g:
            continue
        val = g.value(obs, RDF.value)
        if val is None:
            continue
        obtained_at = g.value(obs, _MET.obtainedAt)
        t = str(obtained_at) if obtained_at is not None else ""
        if best_time is None or t > best_time:
            best_time = t
            best_val = val
    return best_val


def _resolve_metric_refs(g: rdflib.Graph) -> None:
    """
    Inject rdf:value on metric/function nodes so quantity comparators can fire.

    Pattern B — met:metlastValue node:
        _:fn a met:metlastValue ; rdfs:member <metric> .

    Pattern C — met:metobservedValue node:
        _:fn a met:metobservedValue ; rdf:first <obs> .

    Pattern A — direct metric URI used as rdf:first in a quantity condition.
    """
    for fn in g.subjects(RDF.type, _MET.metlastValue):
        if g.value(fn, RDF.value) is not None:
            continue
        metric = g.value(fn, RDFS.member)
        if metric is None:
            continue
        val = _latest_observation_value(g, metric)
        if val is not None:
            g.set((fn, RDF.value, val))

    for fn in g.subjects(RDF.type, _MET.metobservedValue):
        if g.value(fn, RDF.value) is not None:
            continue
        obs = g.value(fn, RDF.first)
        if obs is None:
            continue
        val = g.value(obs, RDF.value)
        if val is not None:
            g.set((fn, RDF.value, val))

    all_qty_types = [rdf_type for rdf_type, _, _ in _TWO_ARG_OPS] + [_QUAN.quaninRange]
    seen: set = set()
    for rdf_type in all_qty_types:
        for cond in g.subjects(RDF.type, rdf_type):
            val_node = g.value(cond, RDF.first)
            if val_node is None or val_node in seen:
                continue
            seen.add(val_node)
            if g.value(val_node, RDF.value) is not None:
                continue
            val = _latest_observation_value(g, val_node)
            if val is not None:
                g.set((val_node, RDF.value, val))


# ── Validity chain pre-processing ────────────────────────────────────────────

def _resolve_validity_chains(g: rdflib.Graph) -> None:
    """
    Propagate iv:ivisValid through iv:ivsameValidityAs chains (ivSameValidityAs rule).

    Rule: (?X iv:ivsameValidityAs ?Y) (?Y iv:ivisValid ?B) → (?X iv:ivisValid ?B)

    Runs to fixed-point to handle chains of arbitrary depth.
    """
    changed = True
    while changed:
        changed = False
        for x, y in list(g.subject_objects(_IV.ivsameValidityAs)):
            b = g.value(y, _IV.ivisValid)
            if b is None:
                continue
            existing = g.value(x, _IV.ivisValid)
            if existing != b:
                g.set((x, _IV.ivisValid, b))
                changed = True


# ── RDF list iteration ────────────────────────────────────────────────────────

def _iter_rdf_list(g: rdflib.Graph, list_node: rdflib.term.Node):
    """Yield items from an RDF list, stopping at rdf:nil or a broken link."""
    node = list_node
    visited: set = set()
    while node is not None and node != RDF.nil:
        if node in visited:
            break
        visited.add(node)
        item = g.value(node, RDF.first)
        if item is not None:
            yield item
        node = g.value(node, RDF.rest)


# ── Quantity leaf evaluators ──────────────────────────────────────────────────

def _eval_two_arg(
    g: rdflib.Graph,
    node: rdflib.term.Node,
    rdf_type: rdflib.URIRef,
    cmp_op: object,
    sym: str,
) -> dict:
    """Evaluate a single two-argument quantity condition node."""
    type_name = str(rdf_type).split("/")[-1]
    val_node = g.value(node, RDF.first)
    rest = g.value(node, RDF.rest)
    bnd_node = g.value(rest, RDF.first) if rest is not None else None
    if val_node is None or bnd_node is None:
        return {"type": type_name, "operator": sym, "error": "missing operand nodes", "passed": False}
    obs_lit = g.value(val_node, RDF.value)
    bnd_lit = g.value(bnd_node, RDF.value)
    if obs_lit is None or bnd_lit is None:
        return {"type": type_name, "operator": sym, "error": "missing rdf:value", "passed": False}
    try:
        obs = Decimal(str(obs_lit))
        bnd = Decimal(str(bnd_lit))
    except InvalidOperation:
        return {"type": type_name, "operator": sym, "error": "non-numeric value", "passed": False}
    ok = bool(cmp_op(obs, bnd))  # type: ignore[operator]
    return {"type": type_name, "operator": sym, "observed": float(obs), "bound": float(bnd), "passed": ok}


def _eval_range(g: rdflib.Graph, node: rdflib.term.Node) -> dict:
    """Evaluate a quan:quaninRange condition node."""
    val_node = g.value(node, RDF.first)
    r1 = g.value(node, RDF.rest)
    lo_node = g.value(r1, RDF.first) if r1 is not None else None
    r2 = g.value(r1, RDF.rest) if r1 is not None else None
    hi_node = g.value(r2, RDF.first) if r2 is not None else None
    if val_node is None or lo_node is None or hi_node is None:
        return {"type": "quaninRange", "operator": "<=<=", "error": "missing operand nodes", "passed": False}
    val_lit = g.value(val_node, RDF.value)
    lo_lit  = g.value(lo_node, RDF.value)
    hi_lit  = g.value(hi_node, RDF.value)
    if val_lit is None or lo_lit is None or hi_lit is None:
        return {"type": "quaninRange", "operator": "<=<=", "error": "missing rdf:value", "passed": False}
    try:
        val = Decimal(str(val_lit))
        lo  = Decimal(str(lo_lit))
        hi  = Decimal(str(hi_lit))
    except InvalidOperation:
        return {"type": "quaninRange", "operator": "<=<=", "error": "non-numeric value", "passed": False}
    ok = bool(lo <= val <= hi)
    return {"type": "quaninRange", "operator": "<=<=", "observed": float(val), "lower": float(lo), "upper": float(hi), "passed": ok}


# ── Logical leaf evaluators ───────────────────────────────────────────────────

def _eval_match(g: rdflib.Graph, list_node: rdflib.term.Node) -> tuple[bool, list[dict]]:
    """Evaluate log:match (subject, predicate, object) — checks triple in graph."""
    items = list(_iter_rdf_list(g, list_node))
    if len(items) != 3:
        cond = {"type": "logMatch", "error": f"expected 3 args, got {len(items)}", "passed": False}
        return False, [cond]
    s, p, o = items
    passed = (s, p, o) in g
    return passed, [{"type": "logMatch", "subject": str(s), "predicate": str(p), "object": str(o), "passed": passed}]


def _eval_match_container(
    g: rdflib.Graph,
    list_node: rdflib.term.Node,
    op_name: str,
    combinator: Callable,
) -> tuple[bool, list[dict]]:
    """
    Evaluate log:matchAll/matchAny/matchNone/matchOne.

    Args list is (container, predicate, object). For each rdfs:member M of
    container, checks whether (M, predicate, object) is in the graph.
    Returns false for empty containers per the TIO closed-world spec.
    """
    items = list(_iter_rdf_list(g, list_node))
    if len(items) != 3:
        cond = {"type": op_name, "error": f"expected 3 args, got {len(items)}", "passed": False}
        return False, [cond]
    container, predicate, obj = items
    members = list(g.objects(container, RDFS.member))
    if not members:
        cond = {"type": op_name, "error": "empty container", "passed": False}
        return False, [cond]
    member_results = [(m, predicate, obj) in g for m in members]
    passed = combinator(member_results)
    return passed, [{"type": op_name, "predicate": str(predicate), "object": str(obj),
                     "member_count": len(members), "passed": passed}]


def _eval_match_statement(g: rdflib.Graph, list_node: rdflib.term.Node) -> tuple[bool, list[dict]]:
    """
    Evaluate log:matchStatement — each item in the RDF list must be a reified
    rdf:Statement whose subject/predicate/object triple exists in the graph.
    """
    stmts = list(_iter_rdf_list(g, list_node))
    if not stmts:
        return False, [{"type": "logMatchStatement", "error": "empty statement list", "passed": False}]
    results = []
    for stmt in stmts:
        s = g.value(stmt, RDF.subject)
        p = g.value(stmt, RDF.predicate)
        o = g.value(stmt, RDF.object)
        if s is None or p is None or o is None:
            results.append({"type": "logMatchStatement", "error": "incomplete reified statement", "passed": False})
        else:
            ok = (s, p, o) in g
            results.append({"type": "logMatchStatement", "subject": str(s), "predicate": str(p), "object": str(o), "passed": ok})
    overall = all(r["passed"] for r in results)
    return overall, results


# ── Set operator helpers ──────────────────────────────────────────────────────

def _container_members(g: rdflib.Graph, container: rdflib.term.Node) -> frozenset:
    """All rdfs:member items of a container node."""
    return frozenset(g.objects(container, RDFS.member))


def _substitute_node(
    g: rdflib.Graph,
    old: rdflib.term.Node,
    new: rdflib.term.Node,
) -> rdflib.Graph:
    """Return a copy of g with every subject/object occurrence of old replaced by new."""
    ng = rdflib.Graph()
    for s, p, o in g:
        ng.add((new if s == old else s, p, new if o == old else o))
    return ng


def _eval_is_member(g: rdflib.Graph, node: rdflib.term.Node) -> tuple[bool, list[dict]]:
    """
    set:setisMember — true if rdf:first resource is a member of ANY container
    in rdf:rest.  Supports rdfs:member encoding and RDF-list encoding for the
    container list.
    """
    resource = g.value(node, RDF.first)
    rest = g.value(node, RDF.rest)
    if resource is None or rest is None:
        return False, [{"type": "setIsMember", "error": "missing rdf:first or rdf:rest", "passed": False}]

    # Canonical TIO encoding: rest node has rdfs:member pointing to each container.
    # Fall back to interpreting rest as an RDF list of containers.
    containers = list(g.objects(rest, RDFS.member))
    if not containers:
        containers = list(_iter_rdf_list(g, rest))

    if not containers:
        return False, [{"type": "setIsMember", "error": "no containers specified", "passed": False}]

    passed = any(resource in _container_members(g, c) for c in containers)
    return passed, [{"type": "setIsMember", "resource": str(resource),
                     "container_count": len(containers), "passed": passed}]


def _eval_intersects_with(g: rdflib.Graph, node: rdflib.term.Node) -> tuple[bool, list[dict]]:
    """
    set:setintersectsWith — true if C1 and C2 share at least one rdfs:member.
    Args encoded as RDF list: rdf:first → C1, rdf:rest/rdf:first → C2.
    """
    c1 = g.value(node, RDF.first)
    rest = g.value(node, RDF.rest)
    c2 = g.value(rest, RDF.first) if rest is not None else None
    if c1 is None or c2 is None:
        return False, [{"type": "setIntersectsWith", "error": "missing C1 or C2", "passed": False}]

    m1 = _container_members(g, c1)
    m2 = _container_members(g, c2)
    passed = bool(m1 & m2)
    return passed, [{"type": "setIntersectsWith", "c1_size": len(m1),
                     "c2_size": len(m2), "passed": passed}]


def _eval_included_in(g: rdflib.Graph, node: rdflib.term.Node) -> tuple[bool, list[dict]]:
    """
    set:setincludedIn — true if every member of C1 is also in each remaining
    container.  Args are the RDF list starting at the function node itself:
    rdf:first → C1, rdf:rest/rdf:first → C2, ...
    """
    items = list(_iter_rdf_list(g, node))
    if len(items) < 2:
        return False, [{"type": "setIncludedIn",
                        "error": f"expected ≥2 args, got {len(items)}", "passed": False}]

    c1 = items[0]
    rest_containers = items[1:]
    m1 = _container_members(g, c1)

    if not m1:
        # Empty set is vacuously included in anything.
        return True, [{"type": "setIncludedIn", "c1_size": 0, "passed": True}]

    passed = all(m1 <= _container_members(g, c) for c in rest_containers)
    return passed, [{"type": "setIncludedIn", "c1_size": len(m1),
                     "target_count": len(rest_containers), "passed": passed}]


def _eval_for_all(g: rdflib.Graph, node: rdflib.term.Node) -> tuple[bool, list[dict]]:
    """
    set:setforAll — for every member M of container, evaluate condition with the
    member variable substituted by M.

    Structure (RDF list encoding):
      rdf:first         → member_var   (the placeholder URI)
      rdf:rest/rdf:first → container
      rdf:rest/rdf:rest/rdf:first → condition node

    Empty container → vacuously True.
    """
    member_var = g.value(node, RDF.first)
    rest = g.value(node, RDF.rest)
    container = g.value(rest, RDF.first) if rest is not None else None
    rest2 = g.value(rest, RDF.rest) if rest is not None else None
    condition_orig = g.value(rest2, RDF.first) if rest2 is not None else None

    if member_var is None or container is None or condition_orig is None:
        return False, [{"type": "setForAll",
                        "error": "missing member_var, container, or condition", "passed": False}]

    members = list(_container_members(g, container))
    if not members:
        # Vacuously true: no member can violate the condition.
        return True, [{"type": "setForAll", "member_count": 0, "passed": True}]

    all_conds: list[dict] = []
    all_passed = True
    for member in members:
        g_sub = _substitute_node(g, member_var, member)
        cond_node = member if condition_orig == member_var else condition_orig
        m_passed, m_conds = _eval_node(g_sub, cond_node)
        if not m_passed:
            all_passed = False
        all_conds.extend(m_conds)

    return all_passed, all_conds or [{"type": "setForAll", "member_count": len(members),
                                      "passed": all_passed}]


# ── ICM expectation evaluators (tio_core + tmf_icm_eval) ─────────────────────

def _eval_delivery_expectation(g: rdflib.Graph, node: rdflib.term.Node) -> tuple[bool, list[dict]]:
    """
    icm:DeliveryExpectation — passes if icm:target contains ≥1 member whose
    rdf:type matches icm:deliveryType (icmDeliveryFulfilled rule), OR if
    icm:result "true"^^xsd:boolean is already asserted on the node.
    """
    if (node, _ICM.result, rdflib.Literal(True)) in g:
        return True, [{"type": "DeliveryExpectation", "passed": True}]

    target = g.value(node, _ICM.target)
    delivery_type = g.value(node, _ICM.deliveryType)

    if target is None:
        return False, [{"type": "DeliveryExpectation",
                        "error": "missing icm:target", "passed": False}]
    if delivery_type is None:
        return False, [{"type": "DeliveryExpectation",
                        "error": "missing icm:deliveryType", "passed": False}]

    members = list(g.objects(target, RDFS.member))
    if not members:
        return False, [{"type": "DeliveryExpectation",
                        "deliveryType": str(delivery_type),
                        "error": "empty target container", "passed": False}]

    passed = any((m, RDF.type, delivery_type) in g for m in members)
    return passed, [{"type": "DeliveryExpectation",
                     "deliveryType": str(delivery_type),
                     "member_count": len(members),
                     "passed": passed}]


def _eval_property_expectation(g: rdflib.Graph, node: rdflib.term.Node) -> tuple[bool, list[dict]]:
    """
    icm:PropertyExpectation — passes if rdf:value is "true"^^xsd:boolean
    (icmPropertyResult rule).
    """
    val = g.value(node, RDF.value)
    passed = val == rdflib.Literal(True)
    return passed, [{"type": "PropertyExpectation",
                     "value": str(val) if val is not None else None,
                     "passed": passed}]


# ── Validity function evaluator (tmf_validity_eval) ──────────────────────────

def _eval_validity_of(g: rdflib.Graph, node: rdflib.term.Node) -> tuple[bool, list[dict]]:
    """
    iv:ivvalidityOf — passes iff ALL rdfs:member resources have
    iv:ivisValid "true"^^xsd:boolean (ivValidityOf rule).
    Empty member set → vacuously True.
    """
    members = list(g.objects(node, RDFS.member))
    if not members:
        return True, [{"type": "validityOf", "member_count": 0, "passed": True}]

    passed = all(
        g.value(m, _IV.ivisValid) == rdflib.Literal(True)
        for m in members
    )
    return passed, [{"type": "validityOf", "member_count": len(members), "passed": passed}]


# ── Recursive tree evaluator ──────────────────────────────────────────────────

def _eval_node(g: rdflib.Graph, node: rdflib.term.Node) -> tuple[bool, list[dict]]:
    """
    Recursively evaluate a node in the TIO expression tree.

    Returns (passed, conditions) where conditions is the flat list of all
    leaf evaluations performed under this node (for diagnostic reporting).

    Dispatch order:
    1. Logical combinators (log:allOf/anyOf/noneOf/oneOf as properties)
    2. Logical match checks (log:match/matchAll/matchAny/matchNone/matchOne/matchStatement)
    3. Quantity conditions (quan:quanatLeast etc.)
    4. Unknown/opaque nodes → pass with no conditions (non-evaluable elements
       like icm:Context, icm:Target, icm:ReportingExpectation are ignored)
    """
    # ── iv:ivvalidIf gate ─────────────────────────────────────────────────────
    # If the node's validity context is not currently valid, fail immediately.
    validity_ctx = g.value(node, _IV.ivvalidIf)
    if validity_ctx is not None:
        if g.value(validity_ctx, _IV.ivisValid) != rdflib.Literal(True):
            return False, [{"type": "validityGate",
                            "context": str(validity_ctx), "passed": False}]

    # ── Combinators ───────────────────────────────────────────────────────────
    for prop, combinator in _LOG_COMBINATORS:
        list_node = g.value(node, prop)
        if list_node is None:
            continue
        items = list(_iter_rdf_list(g, list_node))
        child_results = [_eval_node(g, item) for item in items]
        passed = combinator([r[0] for r in child_results])
        all_conds: list[dict] = []
        for r in child_results:
            all_conds.extend(r[1])
        return passed, all_conds

    # ── log:match (subject predicate object) ─────────────────────────────────
    match_node = g.value(node, _LOG.match)
    if match_node is not None:
        return _eval_match(g, match_node)

    # ── log:matchAll/matchAny/matchNone/matchOne ──────────────────────────────
    _CONTAINER_OPS = [
        (_LOG.matchAll,  "logMatchAll",  lambda bs: all(bs)),
        (_LOG.matchAny,  "logMatchAny",  lambda bs: any(bs)),
        (_LOG.matchNone, "logMatchNone", lambda bs: not any(bs)),
        (_LOG.matchOne,  "logMatchOne",  lambda bs: sum(bs) == 1),
    ]
    for prop, op_name, comb in _CONTAINER_OPS:
        ln = g.value(node, prop)
        if ln is not None:
            return _eval_match_container(g, ln, op_name, comb)

    # ── log:matchStatement (reified statements) ───────────────────────────────
    ms_node = g.value(node, _LOG.matchStatement)
    if ms_node is not None:
        return _eval_match_statement(g, ms_node)

    # ── Quantity conditions ───────────────────────────────────────────────────
    for rdf_type, cmp_op, sym in _TWO_ARG_OPS:
        if (node, RDF.type, rdf_type) in g:
            cond = _eval_two_arg(g, node, rdf_type, cmp_op, sym)
            return cond["passed"], [cond]

    if (node, RDF.type, _QUAN.quaninRange) in g:
        cond = _eval_range(g, node)
        return cond["passed"], [cond]

    # ── Set boolean conditions ────────────────────────────────────────────────
    if (node, RDF.type, _SET.setisMember) in g:
        return _eval_is_member(g, node)
    if (node, RDF.type, _SET.setintersectsWith) in g:
        return _eval_intersects_with(g, node)
    if (node, RDF.type, _SET.setincludedIn) in g:
        return _eval_included_in(g, node)
    if (node, RDF.type, _SET.setforAll) in g:
        return _eval_for_all(g, node)

    # ── ICM expectation types ────────────────────────────────────────────────
    if (node, RDF.type, _ICM.DeliveryExpectation) in g:
        return _eval_delivery_expectation(g, node)
    if (node, RDF.type, _ICM.PropertyExpectation) in g:
        return _eval_property_expectation(g, node)

    # ── Validity function ─────────────────────────────────────────────────────
    if (node, RDF.type, _IV.ivvalidityOf) in g:
        return _eval_validity_of(g, node)

    # ── Unknown/opaque — pass silently (no evaluable content) ────────────────
    return True, []


def _find_evaluation_roots(g: rdflib.Graph) -> list[rdflib.term.Node]:
    """
    Find top-level nodes to start tree evaluation from.

    A root is a node that:
    - Has at least one log:allOf/anyOf/noneOf/oneOf predicate, AND
    - Is NOT itself an item within any other combinator's RDF list.

    Returns [] when no logical structure is present, triggering flat-scan fallback.
    """
    inner: set[rdflib.term.Node] = set()
    for prop, _ in _LOG_COMBINATORS:
        for _, _, list_node in g.triples((None, prop, None)):
            for item in _iter_rdf_list(g, list_node):
                inner.add(item)

    seen: set[rdflib.term.Node] = set()
    roots: list[rdflib.term.Node] = []
    for prop, _ in _LOG_COMBINATORS:
        for subj in g.subjects(prop, None):
            if subj not in inner and subj not in seen:
                seen.add(subj)
                roots.append(subj)
    return roots


# ── Flat-scan fallback ────────────────────────────────────────────────────────

def _condition_nodes_embedded_in_set_ops(g: rdflib.Graph) -> set[rdflib.term.Node]:
    """
    Return the set of condition nodes that are internal arguments to set ops.

    These nodes must not be evaluated independently by flat-scan: they reference
    the member variable (e.g. set:setforAll condition) and are only meaningful
    when evaluated with a substituted graph inside the set op evaluator.
    """
    embedded: set[rdflib.term.Node] = set()
    for node in g.subjects(RDF.type, _SET.setforAll):
        rest = g.value(node, RDF.rest)
        rest2 = g.value(rest, RDF.rest) if rest is not None else None
        cond = g.value(rest2, RDF.first) if rest2 is not None else None
        if cond is not None:
            embedded.add(cond)
    return embedded


def _flat_scan(g: rdflib.Graph) -> list[dict]:
    """
    Collect and evaluate every evaluable condition node in the graph, ignoring
    any logical structure. Used when the expression has no log:* combinators.
    Maintains backward compatibility with bare condition Turtle.

    All nodes are evaluated via _eval_node so that the iv:ivvalidIf gate and
    any other pre-checks apply uniformly.

    Condition nodes that are arguments inside set op structures (e.g. the
    condition subtree of a setforAll) are excluded — they are evaluated with
    proper variable substitution by the set op evaluator instead.
    """
    excluded = _condition_nodes_embedded_in_set_ops(g)
    conditions: list[dict] = []
    seen: set[rdflib.term.Node] = set()

    for rdf_type in (
        *(rt for rt, _, _ in _TWO_ARG_OPS),
        _QUAN.quaninRange,
        _SET.setisMember, _SET.setintersectsWith, _SET.setincludedIn, _SET.setforAll,
        _ICM.DeliveryExpectation, _ICM.PropertyExpectation,
        _IV.ivvalidityOf,
    ):
        for node in g.subjects(RDF.type, rdf_type):
            if node in excluded or node in seen:
                continue
            seen.add(node)
            _, conds = _eval_node(g, node)
            conditions.extend(conds)

    return conditions


# ── Reporting helpers ─────────────────────────────────────────────────────────

_SIMPLE_FAIL_TYPES = frozenset([
    "setIsMember", "setIntersectsWith", "setIncludedIn", "setForAll",
    "DeliveryExpectation", "PropertyExpectation",
    "validityOf", "validityGate",
])


def _fail_label(c: dict) -> str:
    if "error" in c:
        return c["error"]
    t = c["type"]
    if t in ("logMatch", "logMatchAll", "logMatchAny", "logMatchNone",
             "logMatchOne", "logMatchStatement"):
        pred = c.get("predicate", "?")
        obj  = c.get("object", "?")
        return f"{t}({pred}, {obj}): FAIL"
    if t in _SIMPLE_FAIL_TYPES:
        return f"{t}: FAIL"
    bnd = c["bound"] if "bound" in c else f"{c.get('lower')}…{c.get('upper')}"
    return f"{c.get('observed')} {c['operator']} {bnd}: FAIL"


# ── Public API ────────────────────────────────────────────────────────────────

def evaluate_turtle_conditions(turtle_str: str) -> dict:
    """
    Parse TIO Turtle and evaluate the expression tree in Python.

    Returns:
      {
        "intentHandlingState": "Fulfilled" | "Degraded",
        "reason": str | None,
        "conditions": [
          # quantity:   {"type", "operator", "observed", "bound", "passed"}
          # quaninRange:{"type", "operator", "observed", "lower", "upper", "passed"}
          # logMatch:   {"type", "subject", "predicate", "object", "passed"}
          # container:  {"type", "predicate", "object", "member_count", "passed"}
          # error:      {"type", ..., "error", "passed": False}
        ]
      }
    """
    g = rdflib.Graph()
    try:
        g.parse(data=turtle_str, format="turtle")
    except Exception as exc:
        return {"intentHandlingState": "Degraded", "reason": f"Turtle parse error: {exc}", "conditions": []}

    _resolve_metric_refs(g)
    _resolve_validity_chains(g)

    roots = _find_evaluation_roots(g)

    if roots:
        all_conditions: list[dict] = []
        root_passed: list[bool] = []
        for root in roots:
            passed, conds = _eval_node(g, root)
            root_passed.append(passed)
            all_conditions.extend(conds)
        overall_passed = all(root_passed) if root_passed else False
        conditions = all_conditions
    else:
        conditions = _flat_scan(g)
        if not conditions:
            return {
                "intentHandlingState": "Degraded",
                "reason": "No quantity conditions found in expression",
                "conditions": [],
            }
        overall_passed = all(c["passed"] for c in conditions)

    if not overall_passed:
        failed_labels = [_fail_label(c) for c in conditions if not c["passed"]]
        reason = f"Conditions not met: {'; '.join(failed_labels)}" if failed_labels else "Conditions not met"
        return {"intentHandlingState": "Degraded", "reason": reason, "conditions": conditions}

    return {"intentHandlingState": "Fulfilled", "reason": None, "conditions": conditions}


async def evaluate_intent(intent_id: str, client: FusekiClient) -> dict:
    """
    Evaluate an intent and return its intentHandlingState.

    Returns a dict: {"intentHandlingState": str, "reason": str | None}
    """
    graph_uri = str(intent_graph_uri(intent_id))
    node_uri  = str(intent_node(intent_id))

    rows = await client.query(
        _INTENT_QUERY.format(prefixes=PREFIXES, graph=graph_uri, uri=node_uri)
    )

    if not rows:
        logger.warning("evaluate_intent: intent %s has no expression", intent_id)
        return {"intentHandlingState": "Degraded", "reason": "Intent has no expression"}

    row = rows[0]
    expr_type_uri  = (row.get("exprType") or {}).get("value", "")
    expr_type      = expr_type_uri.rsplit("/", 1)[-1].rsplit("#", 1)[-1]
    expr_value_node = row.get("exprValue")
    expr_value: str | None = expr_value_node["value"] if expr_value_node else None

    if expr_type != "TurtleExpression" or not expr_value:
        logger.info(
            "evaluate_intent: intent %s uses %s — no Turtle; defaulting to Degraded",
            intent_id, expr_type,
        )
        return {
            "intentHandlingState": "Degraded",
            "reason": f"No Turtle expression to evaluate (type={expr_type})",
        }

    obs_turtle = await get_observations_turtle(intent_id, client)
    combined = expr_value + "\n" + obs_turtle if obs_turtle else expr_value
    return evaluate_turtle_conditions(combined)
