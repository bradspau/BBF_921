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

Extension type-propagation (tmf_ext_eval rules Python port):
  _derive_ext_types materialises inferred rdf:type triples for Utility,
  Preference, and Proposal nodes so that SPARQL queries and future evaluators
  see correct supertypes without needing Jena inference in Fuseki.

Guarantee evaluation (tmf_guarantee_eval rules Python port):
  Pre-processing: _derive_guarantee_states walks ig:igGuaranteeReport nodes and
                  materialises ig:igstate by matching ig:igGuaranteeAccepted or
                  ig:igGuaranteeRejected events (linked via imo:imoeventIssuedFor
                  and icm:icmabout) before any condition is evaluated.
  ig:igGuaranteeReport — passes iff ig:igstate == ig:igGuaranteeStateCompliant;
                         fails if state is ig:igGuaranteeStateDegraded or absent.

IntentSpecification evaluation (tmf_insp_eval rules Python port):
  insp:inspvalueSelected    — passes if any value in OT's inspallowedValues
                              container is also a member of any allowed container
                              in the rdf:rest list.
  insp:inspvalueSelectedFor — passes if for the IntentElement (rdf:first) and
                              OT (rdf:rest/rdf:first), OT's inspallowedValues
                              shares a member with any container in rdf:rest/rdf:rest.
  insp:inspchosenAny        — passes if any rdfs:member ContentTemplate of the
                              function node has an insp:inspcontent property.
  insp:inspchosenAll        — passes if ALL ContentTemplate args (rdf:list off
                              function node) are chosen (have insp:inspcontent or
                              insp:chosenHandlingDomain).
  insp:inspchosenAllFor     — passes if for the IntentElement (rdf:first), ALL
                              ContentTemplate args (rdf:rest list) are chosen.
  insp:inspchosenAnyFor     — passes if for the IntentElement (rdf:first), ANY
                              ContentTemplate arg (rdf:rest list) is chosen.
  insp:inspusedVocabularyFor — passes if any term from any vocabulary container
                              in the rdf:rest list appears as a predicate on the
                              intent element given in rdf:first.

Reporting expectation types:
  ig:GuaranteeReportingExpectation  — passes if icm:result "true"^^xsd:boolean
                                      is asserted; defaults to Degraded when absent.
  iv:ValidityReportingExpectation   — passes if icm:result "true"^^xsd:boolean
                                      is asserted; defaults to Degraded when absent.

Math function pre-processing (tmf_mathfn_eval.rules Python port):
  _compute_math_functions runs after metric resolution; it finds mf:mflogistic,
  mf:mfpoly, mf:mfmapping, and quan: arithmetic nodes and materialises their
  output as rdf:value so downstream quantity evaluators can use the computed
  result as an operand.
  mf:mflogistic      — L / (1 + exp(-k*(x-x0))) + c
  mf:mfpoly          — l * sum(coeff_i * x^i) + c
  mf:mfmapping       — piecewise lookup: returns result whose source list contains x
  quan:sum           — arg1 + arg2  (rdf:first / rdf:rest chain)
  quan:difference    — arg1 - arg2
  quan:division      — arg1 / arg2
  quan:multiplication — arg1 * arg2
  quan:mean          — arithmetic mean of n args (rdf:list)
  quan:median        — median of n args
  quan:greatest      — max of n args
  quan:smallest      — min of n args
  quan:sumOfSet / multiplicationOfSet / meanOfSet / medianOfSet /
  quan:greatestInSet / smallestInSet — same aggregations over rdfs:Container members

Metric resolution patterns (replaces Jena tmf_metrics_eval.rules):
  A) rdf:first → <metric URI>       — direct ref; resolved via met:Observation
  B) rdf:first → met:metlastValue   — latest observation for linked metric
  C) rdf:first → met:metobservedValue — value of a directly referenced observation
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from operator import ge, gt, le, lt, eq
from typing import Callable

import rdflib
from rdflib.namespace import RDF, RDFS, XSD

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
_IG   = rdflib.Namespace("http://tio.models.tmforum.org/tio/v3.6.0/IntentGuaranteeOntology/")
_IMO  = rdflib.Namespace("http://tio.models.tmforum.org/tio/v3.6.0/IntentManagementOntology/")
_INSP = rdflib.Namespace("http://tio.models.tmforum.org/tio/v3.6.0/IntentSpecification/")
_MF   = rdflib.Namespace("http://tio.models.tmforum.org/tio/v3.6.0/MathFunctions/")
_UT   = rdflib.Namespace("http://tio.models.tmforum.org/tio/v3.6.0/Utility/")
_PRE  = rdflib.Namespace("http://tio.models.tmforum.org/tio/v3.6.0/PreferenceOfHandlingOutcomes/")
_PBI  = rdflib.Namespace("http://tio.models.tmforum.org/tio/v3.6.0/ProposalBestIntent/")

# (rdf_type, comparator, display_symbol) — two-argument quantity pattern
# Each operator appears twice: once under the quan:quanat* URI (legacy) and once
# under the short-name URI defined in QuantityOntology.ttl (quan:atLeast, etc.).
_TWO_ARG_OPS: list[tuple[rdflib.URIRef, object, str]] = [
    (_QUAN.quanatLeast, ge, ">="),
    (_QUAN.atLeast,     ge, ">="),
    (_QUAN.quanatMost,  le, "<="),
    (_QUAN.atMost,      le, "<="),
    (_QUAN.quangreater, gt, ">"),
    (_QUAN.greater,     gt, ">"),
    (_QUAN.quansmaller, lt, "<"),
    (_QUAN.smaller,     lt, "<"),
    (_QUAN.quanexactly, eq, "=="),
    (_QUAN.exactly,     eq, "=="),
]

# (log property, Python combinator over list[bool])
# TIO spec: allOf/anyOf/oneOf return False for empty; noneOf returns True for empty.
_LOG_COMBINATORS: list[tuple[rdflib.URIRef, Callable]] = [
    (_LOG.allOf,  lambda bs: bool(bs) and all(bs)),
    (_LOG.anyOf,  lambda bs: bool(bs) and any(bs)),
    (_LOG.noneOf, lambda bs: not any(bs)),
    (_LOG.oneOf,  lambda bs: sum(bs) == 1),
]

_LOG_MATCH_PREDS: tuple[rdflib.URIRef, ...] = (
    _LOG.match, _LOG.matchAll, _LOG.matchAny,
    _LOG.matchNone, _LOG.matchOne, _LOG.matchStatement,
)

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

# ── Math function pre-processing (tmf_mathfn_eval) ───────────────────────────

def _rdf_decimal(g: rdflib.Graph, node: rdflib.term.Node) -> Decimal | None:
    """
    Extract a Decimal from a graph node.
    Handles direct Literals and nodes that carry rdf:value.
    Returns None on missing/non-numeric data.
    """
    if node is None:
        return None
    if isinstance(node, rdflib.Literal):
        try:
            return Decimal(str(node))
        except InvalidOperation:
            return None
    val = g.value(node, RDF.value)
    if val is None:
        return None
    try:
        return Decimal(str(val))
    except InvalidOperation:
        return None


def _mf_param(
    g: rdflib.Graph,
    fn: rdflib.term.Node,
    prop: rdflib.URIRef,
    default: Decimal,
) -> Decimal:
    """Read a numeric mf parameter, falling back to default when absent."""
    v = _rdf_decimal(g, g.value(fn, prop))
    return v if v is not None else default


def _compute_math_functions(g: rdflib.Graph) -> None:
    """
    Compute mf:mflogistic, mf:mfpoly, mf:mfmapping, and quan: arithmetic nodes
    and materialise their output as rdf:value so that downstream quantity
    evaluators (which read rdf:value from rdf:first operand nodes) can compare
    the result.

    Must run after _resolve_metric_refs so that metric-sourced inputs are
    already resolved before the math is applied.
    """
    # ── mf:mflogistic: L / (1 + exp(-k*(x-x0))) + c ──────────────────────────
    for fn in list(g.subjects(RDF.type, _MF.mflogistic)):
        if g.value(fn, RDF.value) is not None:
            continue
        x = _rdf_decimal(g, g.value(fn, _MF.mfinput))
        if x is None:
            continue
        k     = _mf_param(g, fn, _MF.mfk,  Decimal("1"))
        scale = _mf_param(g, fn, _MF.mfl,  Decimal("1"))
        c     = _mf_param(g, fn, _MF.mfc,  Decimal("0"))
        x0    = _mf_param(g, fn, _MF.mfx0, Decimal("0"))
        try:
            exp_val = Decimal(str(math.exp(float(-k * (x - x0)))))
            result = scale / (Decimal("1") + exp_val) + c
        except (ZeroDivisionError, OverflowError, InvalidOperation):
            continue
        g.set((fn, RDF.value, rdflib.Literal(result, datatype=XSD.decimal)))

    # ── mf:mfpoly: l * (c0 + c1*x + c2*x² + ...) + c ────────────────────────
    for fn in list(g.subjects(RDF.type, _MF.mfpoly)):
        if g.value(fn, RDF.value) is not None:
            continue
        x = _rdf_decimal(g, g.value(fn, _MF.mfinput))
        if x is None:
            continue
        coeff_head = g.value(fn, _MF.mfcoefficients)
        if coeff_head is None:
            continue
        coeffs: list[Decimal] = []
        for item in _iter_rdf_list(g, coeff_head):
            v = _rdf_decimal(g, item)
            coeffs.append(v if v is not None else Decimal("0"))
        if not coeffs:
            continue
        scale = _mf_param(g, fn, _MF.mfl, Decimal("1"))
        c     = _mf_param(g, fn, _MF.mfc, Decimal("0"))
        try:
            # Avoid Decimal("0") ** 0 which raises InvalidOperation; i=0 term is always coeff.
            poly_val: Decimal = Decimal("0")
            for i, coeff in enumerate(coeffs):
                poly_val += coeff if i == 0 else coeff * (x ** i)
            result = scale * poly_val + c
        except (InvalidOperation, OverflowError):
            continue
        g.set((fn, RDF.value, rdflib.Literal(result, datatype=XSD.decimal)))

    # ── mf:mfmapping: piecewise lookup ───────────────────────────────────────
    for fn in list(g.subjects(RDF.type, _MF.mfmapping)):
        if g.value(fn, RDF.value) is not None:
            continue
        input_node = g.value(fn, _MF.mfinput)
        if input_node is None:
            continue
        map_node = g.value(fn, _MF.mfmap)
        if map_node is None:
            continue
        input_dec = _rdf_decimal(g, input_node)
        for entry in _iter_rdf_list(g, map_node):
            items = list(_iter_rdf_list(g, entry))
            if len(items) < 2:
                continue
            result_node, *src_nodes = items
            matched = False
            for src in src_nodes:
                src_dec = _rdf_decimal(g, src)
                if src_dec is not None and input_dec is not None and src_dec == input_dec:
                    matched = True
                elif src == input_node:
                    matched = True
            if matched:
                result_val = _rdf_decimal(g, result_node)
                if result_val is not None:
                    g.set((fn, RDF.value, rdflib.Literal(result_val, datatype=XSD.decimal)))
                break

    # ── quan: binary arithmetic (sum, difference, division, multiplication) ───
    # Arguments are in an rdf:first / rdf:rest chain identical to comparators.
    _ARITH_OPS: list[tuple[rdflib.URIRef, object]] = [
        (_QUAN.sum,            lambda a, b: a + b),
        (_QUAN.difference,     lambda a, b: a - b),
        (_QUAN.division,       lambda a, b: a / b),
        (_QUAN.multiplication, lambda a, b: a * b),
    ]
    for rdf_type, op in _ARITH_OPS:
        for fn in list(g.subjects(RDF.type, rdf_type)):
            if g.value(fn, RDF.value) is not None:
                continue
            arg1_node = g.value(fn, RDF.first)
            rest_node = g.value(fn, RDF.rest)
            arg2_node = g.value(rest_node, RDF.first) if rest_node is not None else None
            a = _rdf_decimal(g, arg1_node)
            b = _rdf_decimal(g, arg2_node)
            if a is None or b is None:
                continue
            try:
                result = op(a, b)
            except (ZeroDivisionError, InvalidOperation, OverflowError):
                continue
            g.set((fn, RDF.value, rdflib.Literal(result, datatype=XSD.decimal)))

    # ── quan: n-ary aggregation (mean, median, greatest, smallest) ────────────
    # Arguments form the rdf:list headed at the function node itself.
    for rdf_type, agg_fn in (
        (_QUAN.mean,     lambda vs: sum(vs) / len(vs)),
        (_QUAN.median,   lambda vs: sorted(vs)[len(vs) // 2] if len(vs) % 2
                         else (sorted(vs)[len(vs) // 2 - 1] + sorted(vs)[len(vs) // 2]) / 2),
        (_QUAN.greatest, max),
        (_QUAN.smallest, min),
    ):
        for fn in list(g.subjects(RDF.type, rdf_type)):
            if g.value(fn, RDF.value) is not None:
                continue
            vals: list[Decimal] = []
            for item in _iter_rdf_list(g, fn):
                v = _rdf_decimal(g, item)
                if v is not None:
                    vals.append(v)
            if not vals:
                continue
            try:
                result = agg_fn(vals)
            except (ZeroDivisionError, InvalidOperation, OverflowError):
                continue
            g.set((fn, RDF.value, rdflib.Literal(Decimal(str(result)), datatype=XSD.decimal)))

    # ── quan: set-aggregation (sumOfSet, multiplicationOfSet, meanOfSet, etc.) ─
    # Each function takes one or more rdfs:Container args (an rdf:list off the fn
    # node). Members of all containers are unioned; rdf:value is read from each.
    def _prod(vs: list[Decimal]) -> Decimal:
        r = Decimal("1")
        for v in vs:
            r *= v
        return r

    _SET_AGG_OPS: list[tuple[rdflib.URIRef, object]] = [
        (_QUAN.sumOfSet,            lambda vs: sum(vs)),
        (_QUAN.multiplicationOfSet, _prod),
        (_QUAN.meanOfSet,           lambda vs: sum(vs) / len(vs)),
        (_QUAN.medianOfSet,         lambda vs: sorted(vs)[len(vs) // 2] if len(vs) % 2
                                    else (sorted(vs)[len(vs) // 2 - 1] + sorted(vs)[len(vs) // 2]) / 2),
        (_QUAN.greatestInSet,       max),
        (_QUAN.smallestInSet,       min),
    ]
    for rdf_type, agg_fn in _SET_AGG_OPS:
        for fn in list(g.subjects(RDF.type, rdf_type)):
            if g.value(fn, RDF.value) is not None:
                continue
            vals: list[Decimal] = []
            for container in _iter_rdf_list(g, fn):
                for member in g.objects(container, RDFS.member):
                    v = _rdf_decimal(g, member)
                    if v is not None:
                        vals.append(v)
            if not vals:
                continue
            try:
                result = agg_fn(vals)
            except (ZeroDivisionError, InvalidOperation, OverflowError):
                continue
            g.set((fn, RDF.value, rdflib.Literal(Decimal(str(result)), datatype=XSD.decimal)))


# ── Metric resolution ─────────────────────────────────────────────────────────

def _parse_timestamp(raw: str) -> datetime:
    """Parse an ISO-8601 timestamp to a timezone-aware datetime for ordering.

    Handles both the 'Z' suffix written by observation_store and the '+00:00'
    form that RDFLib normalises to on round-trip (per CLAUDE.md gotchas).
    Falls back to datetime.min on parse failure so malformed timestamps sort last.
    """
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def _build_obs_index(
    g: rdflib.Graph,
) -> dict[rdflib.term.Node, rdflib.term.Literal]:
    """
    Build a metric_uri → latest_value index in one pass over met:Observation nodes.

    Replaces the previous per-metric full-scan (_latest_observation_value) with an
    O(observations) single pass, reducing _resolve_metric_refs from
    O(conditions × observations) to O(observations + conditions).
    """
    best: dict[rdflib.term.Node, tuple[datetime, rdflib.term.Literal]] = {}
    for obs in g.subjects(RDF.type, _MET.Observation):
        metric = g.value(obs, _MET.observedMetric)
        if metric is None:
            continue
        val = g.value(obs, RDF.value)
        if val is None:
            continue
        obtained_at = g.value(obs, _MET.obtainedAt)
        dt = (
            _parse_timestamp(str(obtained_at))
            if obtained_at is not None
            else datetime.min.replace(tzinfo=timezone.utc)
        )
        existing = best.get(metric)
        if existing is None or dt > existing[0]:
            best[metric] = (dt, val)
    return {m: v for m, (_, v) in best.items()}


def _normalize_qty_predicates(g: rdflib.Graph) -> None:
    """
    Normalise predicate-form quantity conditions to the type form expected by
    _eval_two_arg and _resolve_metric_refs.

    Canonical TIO usage applies the function URI as a predicate:
        ?cond  quan:atLeast  ( ?metric  ?bound ) .

    This normaliser rewrites that to the type form:
        ?cond  a             quan:atLeast .
        ?cond  rdf:first     ?metric .
        ?cond  rdf:rest      <rest-node> .

    so the rest of the pipeline sees a single representation.  Range
    conditions (quan:inRange / quan:quaninRange) use the same three-element
    list and are handled identically.  Already-normalised type-form nodes
    (those that already have rdf:first) are skipped.
    """
    range_types = (_QUAN.quaninRange, _QUAN.inRange)
    for rdf_type, _, _ in _TWO_ARG_OPS:
        for cond, list_node in list(g.subject_objects(rdf_type)):
            if g.value(cond, RDF.first) is not None:
                continue
            arg1 = g.value(list_node, RDF.first)
            if arg1 is None:
                continue
            g.add((cond, RDF.type, rdf_type))
            g.add((cond, RDF.first, arg1))
            rest = g.value(list_node, RDF.rest)
            if rest is not None:
                g.add((cond, RDF.rest, rest))
    for rdf_type in range_types:
        for cond, list_node in list(g.subject_objects(rdf_type)):
            if g.value(cond, RDF.first) is not None:
                continue
            arg1 = g.value(list_node, RDF.first)
            if arg1 is None:
                continue
            g.add((cond, RDF.type, rdf_type))
            g.add((cond, RDF.first, arg1))
            rest = g.value(list_node, RDF.rest)
            if rest is not None:
                g.add((cond, RDF.rest, rest))


def _normalize_set_predicates(g: rdflib.Graph) -> None:
    """
    Normalise predicate-form set constructors to rdfs:member triples.

    TIO canonical usage applies the constructor URI as a predicate:
        ?target  set:resourcesOfType              ?class .
        ?target  set:resourcesWithPropertyObject  ( prop obj ) .

    _compute_set_constructors expects typed blank nodes (type-form).  This
    normaliser skips that intermediate form and materialises rdfs:member
    directly on each target by chaining the filters as AND conditions:

      1. Start with every resource typed as any class in resourcesOfType.
      2. Intersect with each resourcesWithPropertyObject ( prop obj ) filter.
      3. Add target rdfs:member ?resource for each surviving resource.

    Idempotent — skips targets that already have rdfs:member triples.
    """
    targets = set(g.subjects(_SET.resourcesOfType, None)) | set(
        g.subjects(_SET.resourcesWithPropertyObject, None)
    )
    for target in targets:
        if list(g.objects(target, RDFS.member)):
            continue  # already materialised
        type_classes = list(g.objects(target, _SET.resourcesOfType))
        if type_classes:
            members: set | None = set()
            for cls in type_classes:
                members |= set(g.subjects(RDF.type, cls))
        else:
            members = None  # no type filter — refined by property filters
        for list_node in g.objects(target, _SET.resourcesWithPropertyObject):
            args = list(_iter_rdf_list(g, list_node))
            if len(args) < 2:
                continue
            prop = args[0]
            filter_set: set = set()
            for obj in args[1:]:
                filter_set |= set(g.subjects(prop, obj))
            members = filter_set if members is None else members & filter_set
        if members:
            for m in members:
                g.add((target, RDFS.member, m))


def _resolve_metric_refs(g: rdflib.Graph) -> None:
    """
    Inject rdf:value on metric/function nodes so quantity comparators can fire.

    Pattern B — met:metlastValue node:
        _:fn a met:metlastValue ; rdfs:member <metric> .

    Pattern C — met:metobservedValue node:
        _:fn a met:metobservedValue ; rdf:first <obs> .

    Pattern A — direct metric URI used as rdf:first in a quantity condition.
    """
    obs_index = _build_obs_index(g)

    for fn in g.subjects(RDF.type, _MET.metlastValue):
        if g.value(fn, RDF.value) is not None:
            continue
        metric = g.value(fn, RDFS.member)
        if metric is None:
            continue
        val = obs_index.get(metric)
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

    all_qty_types = [rdf_type for rdf_type, _, _ in _TWO_ARG_OPS] + [_QUAN.quaninRange, _QUAN.inRange]
    seen: set = set()
    for rdf_type in all_qty_types:
        for cond in g.subjects(RDF.type, rdf_type):
            val_node = g.value(cond, RDF.first)
            if val_node is None or val_node in seen:
                continue
            seen.add(val_node)
            if g.value(val_node, RDF.value) is not None:
                continue
            val = obs_index.get(val_node)
            if val is not None:
                g.set((val_node, RDF.value, val))


# ── Validity chain pre-processing ────────────────────────────────────────────

def _resolve_validity_chains(g: rdflib.Graph) -> None:
    """
    Propagate iv:ivisValid through iv:ivsameValidityAs chains (ivSameValidityAs rule).

    Rule: (?X iv:ivsameValidityAs ?Y) (?Y iv:ivisValid ?B) → (?X iv:ivisValid ?B)

    Runs to fixed-point to handle chains of arbitrary depth.  Nodes that
    receive conflicting values from two or more targets are marked invalid so
    the loop always terminates (bounded by the number of unresolved nodes).
    """
    _FALSE = rdflib.Literal(False)
    changed = True
    while changed:
        changed = False
        # Collect proposed values for each subject; track conflicts separately.
        proposed: dict = {}
        conflicted: set = set()
        for x, y in list(g.subject_objects(_IV.ivsameValidityAs)):
            b = g.value(y, _IV.ivisValid)
            if b is None:
                continue
            if x in conflicted:
                continue
            if x in proposed:
                if proposed[x] != b:
                    conflicted.add(x)
                    del proposed[x]
            else:
                proposed[x] = b
        # Mark conflicted nodes invalid.
        for x in conflicted:
            existing = g.value(x, _IV.ivisValid)
            if existing != _FALSE:
                g.set((x, _IV.ivisValid, _FALSE))
                changed = True
        # Apply non-conflicting proposals.
        for x, b in proposed.items():
            existing = g.value(x, _IV.ivisValid)
            if existing != b:
                g.set((x, _IV.ivisValid, b))
                changed = True


# ── Guarantee state pre-processing ───────────────────────────────────────────

def _derive_guarantee_states(g: rdflib.Graph) -> None:
    """
    Apply tmf_guarantee_eval rules: materialise ig:igstate on ig:igGuaranteeReport nodes.

    igReportStateCompliant:
      (?R ig:igGuaranteeReport) + (?E ig:igGuaranteeAccepted) +
      (?E imo:imoeventIssuedFor ?I) + (?R icm:icmabout ?I)
      → (?R ig:igstate ig:igGuaranteeStateCompliant)

    igReportStateDegraded:
      same pattern with ig:igGuaranteeRejected → ig:igGuaranteeStateDegraded

    Nodes that already carry ig:igstate (set by the expression itself) are left
    unchanged. Compliant takes precedence when both events are present.
    """
    for report in list(g.subjects(RDF.type, _IG.igGuaranteeReport)):
        if g.value(report, _IG.igstate) is not None:
            continue
        intent = g.value(report, _ICM.icmabout)
        if intent is None:
            continue
        for event in g.subjects(RDF.type, _IG.igGuaranteeAccepted):
            if (event, _IMO.imoeventIssuedFor, intent) in g:
                g.set((report, _IG.igstate, _IG.igGuaranteeStateCompliant))
                break
        if g.value(report, _IG.igstate) is None:
            for event in g.subjects(RDF.type, _IG.igGuaranteeRejected):
                if (event, _IMO.imoeventIssuedFor, intent) in g:
                    g.set((report, _IG.igstate, _IG.igGuaranteeStateDegraded))
                    break


# ── Extension type-propagation (tmf_ext_eval) ────────────────────────────────

def _derive_ext_types(g: rdflib.Graph) -> None:
    """
    Apply tmf_ext_eval rules: materialise inferred rdf:type triples for Utility,
    Preference, and Proposal/BestIntent nodes.

    Rules ported (all are property-triggered or subclass-propagation):

    Utility:
      (?X ut:ututility ?U)          → (?U rdf:type ut:utUtilityInformation)
      (?X ut:ututilityProfile ?P)   → (?P rdf:type ut:utUtilityProfile)
      (?U rdf:type ut:utUtilityInformation) → (?U rdf:type icm:icmInformation)

    Preference:
      (?X pre:prepreference ?P)       → (?P rdf:type pre:prePreference)
      (?X pre:prejudgementRequest ?J) → (?J rdf:type pre:preJudgementRequest)
      (?J rdf:type pre:preJudgementRequest) → (?J rdf:type rdfs:Container)

    Proposal/BestIntent:
      (?X pbi:pbiproposal ?R)              → (?R rdf:type pbi:pbiBestProposalReport)
      (?R rdf:type pbi:pbiBestProposalReport) → (?R rdf:type icm:icmExpectationReport)
      (?R pbi:pbiproposed ?P)              → (?P rdf:type pbi:pbiProposal)
      (?R rdf:type pbi:pbiBestProposalExpectation) → (?R rdf:type icm:icmReportingExpectation)
    """
    # ── Utility ───────────────────────────────────────────────────────────────
    for _, u in list(g.subject_objects(_UT.ututility)):
        g.add((u, RDF.type, _UT.utUtilityInformation))
    for _, p in list(g.subject_objects(_UT.ututilityProfile)):
        g.add((p, RDF.type, _UT.utUtilityProfile))
    for u in list(g.subjects(RDF.type, _UT.utUtilityInformation)):
        g.add((u, RDF.type, _ICM.icmInformation))

    # ── Preference ────────────────────────────────────────────────────────────
    for _, p in list(g.subject_objects(_PRE.prepreference)):
        g.add((p, RDF.type, _PRE.prePreference))
    for _, j in list(g.subject_objects(_PRE.prejudgementRequest)):
        g.add((j, RDF.type, _PRE.preJudgementRequest))
    for j in list(g.subjects(RDF.type, _PRE.preJudgementRequest)):
        g.add((j, RDF.type, RDFS.Container))

    # ── Proposal / BestIntent ─────────────────────────────────────────────────
    for _, r in list(g.subject_objects(_PBI.pbiproposal)):
        g.add((r, RDF.type, _PBI.pbiBestProposalReport))
    for r in list(g.subjects(RDF.type, _PBI.pbiBestProposalReport)):
        g.add((r, RDF.type, _ICM.icmExpectationReport))
    for _, p in list(g.subject_objects(_PBI.pbiproposed)):
        g.add((p, RDF.type, _PBI.pbiProposal))
    for r in list(g.subjects(RDF.type, _PBI.pbiBestProposalExpectation)):
        g.add((r, RDF.type, _ICM.icmReportingExpectation))


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
    if obs_lit is None:
        metric_name = str(val_node).rsplit("#", 1)[-1].rsplit("/", 1)[-1]
        return {"type": type_name, "operator": sym, "error": f"no observation: {metric_name}", "passed": False}
    if bnd_lit is None:
        return {"type": type_name, "operator": sym, "error": "missing bound value", "passed": False}
    try:
        obs = Decimal(str(obs_lit))
        bnd = Decimal(str(bnd_lit))
    except InvalidOperation:
        return {"type": type_name, "operator": sym, "error": "non-numeric value", "passed": False}
    ok = bool(cmp_op(obs, bnd))  # type: ignore[operator]
    return {"type": type_name, "operator": sym, "observed": obs, "bound": bnd, "passed": ok}


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
    return {"type": "quaninRange", "operator": "<=<=", "observed": val, "lower": lo, "upper": hi, "passed": ok}


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


def _materialise_members(g: rdflib.Graph, node: rdflib.term.Node, members) -> None:
    """Attach computed members to node as rdfs:member triples."""
    for m in members:
        g.add((node, RDFS.member, m))


def _compute_set_constructors(g: rdflib.Graph) -> None:
    """
    Pre-processing pass: materialise derived containers for set constructor
    functions so that downstream set operators (setisMember, setforAll, …)
    see them as plain rdfs:Container nodes.

    Runs after _compute_math_functions in the evaluation pipeline.

    Constructors handled:
      set:union                   — union of all container args
      set:intersection            — intersection of all container args
      set:difference              — first container minus remaining containers
      set:newestMember            — single-member container: member with most recent timestamp
      set:oldestMember            — single-member container: member with oldest timestamp
      set:membersAfter            — members whose timestamp > time_ref
      set:membersBefore           — members whose timestamp < time_ref
      set:membersSameTime         — members whose timestamp == time_ref
      set:membersWhile            — members whose timestamp is within interval
      set:resourcesOfType         — subjects typed with any given class
      set:resourcesWithProperty   — subjects that have any given property
      set:resourcesWithPropertyObject — subjects where property = given object
      set:typesOfMembers          — rdf:types of all container members
      set:valuesOfObjectProperty  — values of a property on given resources
    """
    # ── Basic algebra ─────────────────────────────────────────────────────────
    for fn in list(g.subjects(RDF.type, _SET.union)):
        if list(g.objects(fn, RDFS.member)):
            continue
        items = list(_iter_rdf_list(g, fn))
        members: set = set()
        for c in items:
            members |= set(g.objects(c, RDFS.member))
        _materialise_members(g, fn, members)

    for fn in list(g.subjects(RDF.type, _SET.intersection)):
        if list(g.objects(fn, RDFS.member)):
            continue
        items = list(_iter_rdf_list(g, fn))
        if not items:
            continue
        members = set(g.objects(items[0], RDFS.member))
        for c in items[1:]:
            members &= set(g.objects(c, RDFS.member))
        _materialise_members(g, fn, members)

    for fn in list(g.subjects(RDF.type, _SET.difference)):
        if list(g.objects(fn, RDFS.member)):
            continue
        items = list(_iter_rdf_list(g, fn))
        if not items:
            continue
        members = set(g.objects(items[0], RDFS.member))
        for c in items[1:]:
            members -= set(g.objects(c, RDFS.member))
        _materialise_members(g, fn, members)

    # ── Temporal extrema ──────────────────────────────────────────────────────
    for rdf_type, reverse in ((_SET.newestMember, True), (_SET.oldestMember, False)):
        for fn in list(g.subjects(RDF.type, rdf_type)):
            if list(g.objects(fn, RDFS.member)):
                continue
            args = list(_iter_rdf_list(g, fn))
            if len(args) < 2:
                continue
            ts_prop = args[0]
            all_members: list = []
            for c in args[1:]:
                all_members.extend(g.objects(c, RDFS.member))
            best = None
            best_dt = None
            for m in all_members:
                raw = g.value(m, ts_prop)
                if raw is None:
                    continue
                dt = _parse_timestamp(str(raw))
                if best_dt is None or (reverse and dt > best_dt) or (not reverse and dt < best_dt):
                    best_dt = dt
                    best = m
            if best is not None:
                g.add((fn, RDFS.member, best))

    # ── Temporal filters ──────────────────────────────────────────────────────
    _TIME = rdflib.Namespace("http://www.w3.org/2006/time#")

    for rdf_type, cmp_fn in (
        (_SET.membersAfter,    lambda dt, ref, _begin, _end: dt > ref),
        (_SET.membersBefore,   lambda dt, ref, _begin, _end: dt < ref),
        (_SET.membersSameTime, lambda dt, ref, _begin, _end: dt == ref),
        (_SET.membersWhile,    lambda dt, _ref, begin, end:
                               (begin is None or dt >= begin) and (end is None or dt <= end)),
    ):
        for fn in list(g.subjects(RDF.type, rdf_type)):
            if list(g.objects(fn, RDFS.member)):
                continue
            args = list(_iter_rdf_list(g, fn))
            if len(args) < 3:
                continue
            ts_prop = args[0]
            time_arg = args[1]
            containers = args[2:]

            # Resolve reference time — literal or resource with rdf:value/time:inXSDDateTimeStamp
            ref_raw = g.value(time_arg, RDF.value) or g.value(time_arg, _TIME.inXSDDateTimeStamp) or time_arg
            ref_dt = _parse_timestamp(str(ref_raw)) if ref_raw is not None else None

            # For membersWhile: resolve interval begin/end
            begin_node = g.value(time_arg, _TIME.hasBeginning)
            end_node = g.value(time_arg, _TIME.hasEnd)
            begin_raw = g.value(begin_node, _TIME.inXSDDateTimeStamp) if begin_node else None
            end_raw = g.value(end_node, _TIME.inXSDDateTimeStamp) if end_node else None
            begin_dt = _parse_timestamp(str(begin_raw)) if begin_raw else None
            end_dt = _parse_timestamp(str(end_raw)) if end_raw else None

            members = []
            for c in containers:
                for m in g.objects(c, RDFS.member):
                    raw = g.value(m, ts_prop)
                    if raw is None:
                        continue
                    dt = _parse_timestamp(str(raw))
                    if cmp_fn(dt, ref_dt, begin_dt, end_dt):
                        members.append(m)
            _materialise_members(g, fn, members)

    # ── Graph-traversal builders ──────────────────────────────────────────────
    for fn in list(g.subjects(RDF.type, _SET.resourcesOfType)):
        if list(g.objects(fn, RDFS.member)):
            continue
        members = set()
        for cls in _iter_rdf_list(g, fn):
            members |= set(g.subjects(RDF.type, cls))
        _materialise_members(g, fn, members)

    for fn in list(g.subjects(RDF.type, _SET.resourcesWithProperty)):
        if list(g.objects(fn, RDFS.member)):
            continue
        members = set()
        for prop in _iter_rdf_list(g, fn):
            members |= set(g.subjects(prop, None))
        _materialise_members(g, fn, members)

    for fn in list(g.subjects(RDF.type, _SET.resourcesWithPropertyObject)):
        if list(g.objects(fn, RDFS.member)):
            continue
        args = list(_iter_rdf_list(g, fn))
        if not args:
            continue
        prop = args[0]
        members = set()
        for obj in args[1:]:
            members |= set(g.subjects(prop, obj))
        _materialise_members(g, fn, members)

    for fn in list(g.subjects(RDF.type, _SET.typesOfMembers)):
        if list(g.objects(fn, RDFS.member)):
            continue
        types: set = set()
        for c in _iter_rdf_list(g, fn):
            for m in g.objects(c, RDFS.member):
                types |= set(g.objects(m, RDF.type))
        _materialise_members(g, fn, types)

    for fn in list(g.subjects(RDF.type, _SET.valuesOfObjectProperty)):
        if list(g.objects(fn, RDFS.member)):
            continue
        args = list(_iter_rdf_list(g, fn))
        if not args:
            continue
        prop = args[0]
        values: set = set()
        for res in args[1:]:
            values |= set(g.objects(res, prop))
        _materialise_members(g, fn, values)


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

    # Split g's triples once: those referencing member_var (to be substituted)
    # and the rest (shared unchanged across all member iterations).  This avoids
    # re-scanning the full graph on every iteration (was O(N×|g|); now O(|g|+N×k)).
    var_triples:     list[tuple] = []
    non_var_triples: list[tuple] = []
    for s, p, o in g:
        if s == member_var or o == member_var:
            var_triples.append((s, p, o))
        else:
            non_var_triples.append((s, p, o))

    all_conds: list[dict] = []
    all_passed = True
    for member in members:
        g_sub = rdflib.Graph()
        for triple in non_var_triples:
            g_sub.add(triple)
        for s, p, o in var_triples:
            g_sub.add((member if s == member_var else s, p, member if o == member_var else o))
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
    if members:
        passed = any((m, RDF.type, delivery_type) in g for m in members)
        return passed, [{"type": "DeliveryExpectation",
                         "deliveryType": str(delivery_type),
                         "member_count": len(members),
                         "passed": passed}]

    # Target not yet populated — check icm:chooseFrom for available candidates.
    # Passes if the resource pool has ≥1 candidate (handler will select one).
    choose_from = g.value(node, _ICM.chooseFrom)
    if choose_from is not None:
        candidates = list(g.objects(choose_from, RDFS.member))
        passed = len(candidates) > 0
        cond: dict = {
            "type": "DeliveryExpectation",
            "deliveryType": str(delivery_type),
            "candidates": len(candidates),
            "passed": passed,
        }
        if candidates:
            cond["selected"] = str(candidates[0])
        return passed, [cond]

    return False, [{"type": "DeliveryExpectation",
                    "deliveryType": str(delivery_type),
                    "error": "empty target container", "passed": False}]


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


# ── Guarantee report leaf evaluator (tmf_guarantee_eval) ─────────────────────

def _eval_guarantee_report(g: rdflib.Graph, node: rdflib.term.Node) -> tuple[bool, list[dict]]:
    """
    ig:igGuaranteeReport — passes if ig:igstate is ig:igGuaranteeStateCompliant.
    Fails if state is ig:igGuaranteeStateDegraded or if no state was derived.
    """
    state = g.value(node, _IG.igstate)
    if state == _IG.igGuaranteeStateCompliant:
        return True, [{"type": "GuaranteeReport", "state": "igGuaranteeStateCompliant", "passed": True}]
    if state == _IG.igGuaranteeStateDegraded:
        return False, [{"type": "GuaranteeReport", "state": "igGuaranteeStateDegraded", "passed": False}]
    return False, [{"type": "GuaranteeReport", "error": "no ig:igstate derived", "passed": False}]


# ── IntentSpecification leaf evaluators (tmf_insp_eval) ──────────────────────

def _eval_value_selected(g: rdflib.Graph, node: rdflib.term.Node) -> tuple[bool, list[dict]]:
    """
    insp:inspvalueSelected — passes if the ObjectTemplate's inspallowedValues
    container shares at least one member with any allowed container in rdf:rest.

    Structure (RDF list):
      rdf:first → OT (ObjectTemplate with insp:inspallowedValues)
      rdf:rest  → container node whose rdfs:member items are allowed containers
    """
    ot = g.value(node, RDF.first)
    rest = g.value(node, RDF.rest)
    if ot is None:
        return False, [{"type": "valueSelected", "error": "missing rdf:first (OT)", "passed": False}]
    vals_container = g.value(ot, _INSP.inspallowedValues)
    if vals_container is None:
        return False, [{"type": "valueSelected", "error": "OT missing insp:inspallowedValues", "passed": False}]
    chosen = frozenset(g.objects(vals_container, RDFS.member))
    if not chosen:
        return False, [{"type": "valueSelected", "error": "empty inspallowedValues container", "passed": False}]
    allowed_containers = list(g.objects(rest, RDFS.member)) if rest is not None else []
    if not allowed_containers:
        return False, [{"type": "valueSelected", "error": "no allowed containers in rdf:rest", "passed": False}]
    passed = any(chosen & frozenset(g.objects(ac, RDFS.member)) for ac in allowed_containers)
    return passed, [{"type": "valueSelected", "passed": passed}]


def _eval_chosen_any(g: rdflib.Graph, node: rdflib.term.Node) -> tuple[bool, list[dict]]:
    """
    insp:inspchosenAny — passes if any rdfs:member of the function node is an
    insp:inspContentTemplate that has an insp:inspcontent property (i.e. was chosen).
    """
    members = list(g.objects(node, RDFS.member))
    if not members:
        return False, [{"type": "chosenAny", "error": "no rdfs:member items", "passed": False}]
    passed = any(
        (m, RDF.type, _INSP.inspContentTemplate) in g and g.value(m, _INSP.inspcontent) is not None
        for m in members
    )
    return passed, [{"type": "chosenAny", "member_count": len(members), "passed": passed}]


def _eval_used_vocabulary_for(g: rdflib.Graph, node: rdflib.term.Node) -> tuple[bool, list[dict]]:
    """
    insp:inspusedVocabularyFor — passes if any term from any vocabulary container
    (members of the rdf:rest list) is used as a predicate on the intent element
    (rdf:first).

    Structure:
      rdf:first → IntentElem
      rdf:rest  → container whose rdfs:member items are VocabularyContainers;
                  each VocabularyContainer has rdfs:member terms (predicates)
    """
    intent_elem = g.value(node, RDF.first)
    vocab_list = g.value(node, RDF.rest)
    if intent_elem is None:
        return False, [{"type": "usedVocabularyFor", "error": "missing rdf:first (IntentElem)", "passed": False}]
    if vocab_list is None:
        return False, [{"type": "usedVocabularyFor", "error": "missing rdf:rest (VocabList)", "passed": False}]
    vocab_containers = list(g.objects(vocab_list, RDFS.member))
    if not vocab_containers:
        return False, [{"type": "usedVocabularyFor", "error": "empty vocabulary list", "passed": False}]
    elem_predicates = frozenset(g.predicates(intent_elem))
    passed = any(
        frozenset(g.objects(vc, RDFS.member)) & elem_predicates
        for vc in vocab_containers
    )
    return passed, [{"type": "usedVocabularyFor", "passed": passed}]


def _eval_value_selected_for(g: rdflib.Graph, node: rdflib.term.Node) -> tuple[bool, list[dict]]:
    """
    insp:inspvalueSelectedFor — passes if OT's inspallowedValues shares a member
    with any allowed container in the remaining args.

    Structure (RDF list):
      rdf:first             → IntentElement (scoping context)
      rdf:rest/rdf:first    → OT (ObjectTemplate with insp:inspallowedValues)
      rdf:rest/rdf:rest     → node whose rdfs:member items are allowed containers
    """
    rest = g.value(node, RDF.rest)
    ot = g.value(rest, RDF.first) if rest is not None else None
    rest2 = g.value(rest, RDF.rest) if rest is not None else None
    if ot is None:
        return False, [{"type": "valueSelectedFor", "error": "missing OT (rdf:rest/rdf:first)", "passed": False}]
    vals_container = g.value(ot, _INSP.inspallowedValues)
    if vals_container is None:
        return False, [{"type": "valueSelectedFor", "error": "OT missing insp:inspallowedValues", "passed": False}]
    chosen = frozenset(g.objects(vals_container, RDFS.member))
    if not chosen:
        return False, [{"type": "valueSelectedFor", "error": "empty inspallowedValues container", "passed": False}]
    allowed_containers = list(g.objects(rest2, RDFS.member)) if rest2 is not None else []
    if not allowed_containers:
        return False, [{"type": "valueSelectedFor", "error": "no allowed containers in remaining args", "passed": False}]
    passed = any(chosen & frozenset(g.objects(ac, RDFS.member)) for ac in allowed_containers)
    return passed, [{"type": "valueSelectedFor", "passed": passed}]


def _is_ct_chosen(g: rdflib.Graph, ct: rdflib.term.Node) -> bool:
    """True if a ContentTemplate node is considered 'chosen'."""
    return (
        (ct, RDF.type, _INSP.inspContentTemplate) in g
        and (
            g.value(ct, _INSP.inspcontent) is not None
            or g.value(ct, _INSP.chosenHandlingDomain) is not None
        )
    )


def _eval_chosen_all(g: rdflib.Graph, node: rdflib.term.Node) -> tuple[bool, list[dict]]:
    """
    insp:inspchosenAll — passes if ALL ContentTemplate args (rdf:list off the
    function node) are chosen (have insp:inspcontent or insp:chosenHandlingDomain).
    """
    args = list(_iter_rdf_list(g, node))
    if not args:
        return False, [{"type": "chosenAll", "error": "no ContentTemplate args", "passed": False}]
    passed = all(_is_ct_chosen(g, a) for a in args)
    return passed, [{"type": "chosenAll", "arg_count": len(args), "passed": passed}]


def _eval_chosen_all_for(g: rdflib.Graph, node: rdflib.term.Node) -> tuple[bool, list[dict]]:
    """
    insp:inspchosenAllFor — passes if for the IntentElement (rdf:first), ALL
    ContentTemplate args in the rdf:rest list are chosen.
    """
    rest = g.value(node, RDF.rest)
    if rest is None:
        return False, [{"type": "chosenAllFor", "error": "missing rdf:rest", "passed": False}]
    templates = list(_iter_rdf_list(g, rest))
    if not templates:
        return False, [{"type": "chosenAllFor", "error": "no ContentTemplate args", "passed": False}]
    passed = all(_is_ct_chosen(g, t) for t in templates)
    return passed, [{"type": "chosenAllFor", "template_count": len(templates), "passed": passed}]


def _eval_chosen_any_for(g: rdflib.Graph, node: rdflib.term.Node) -> tuple[bool, list[dict]]:
    """
    insp:inspchosenAnyFor — passes if for the IntentElement (rdf:first), ANY
    ContentTemplate arg in the rdf:rest list is chosen.
    """
    rest = g.value(node, RDF.rest)
    if rest is None:
        return False, [{"type": "chosenAnyFor", "error": "missing rdf:rest", "passed": False}]
    templates = list(_iter_rdf_list(g, rest))
    if not templates:
        return False, [{"type": "chosenAnyFor", "error": "no ContentTemplate args", "passed": False}]
    passed = any(_is_ct_chosen(g, t) for t in templates)
    return passed, [{"type": "chosenAnyFor", "template_count": len(templates), "passed": passed}]


def _eval_element_of(g: rdflib.Graph, node: rdflib.term.Node) -> tuple[bool, list[dict]]:
    """
    set:elementOf — true if rdf:first resource is an rdfs:member of ALL
    remaining container args (rdf:rest list).
    """
    args = list(_iter_rdf_list(g, node))
    if len(args) < 2:
        return False, [{"type": "elementOf", "error": f"expected ≥2 args, got {len(args)}", "passed": False}]
    resource = args[0]
    containers = args[1:]
    passed = all(resource in g.objects(c, RDFS.member) for c in containers)
    return passed, [{"type": "elementOf", "resource": str(resource),
                     "container_count": len(containers), "passed": passed}]


def _eval_empty_set(g: rdflib.Graph, node: rdflib.term.Node) -> tuple[bool, list[dict]]:
    """
    set:empty — true if every container arg has zero rdfs:members.
    """
    containers = list(_iter_rdf_list(g, node))
    if not containers:
        return False, [{"type": "empty", "error": "no container args", "passed": False}]
    passed = all(not list(g.objects(c, RDFS.member)) for c in containers)
    return passed, [{"type": "empty", "container_count": len(containers), "passed": passed}]


def _eval_observation_reporting_expectation(
    g: rdflib.Graph, node: rdflib.term.Node
) -> tuple[bool, list[dict]]:
    """
    icm:ObservationReportingExpectation — passes if icm:result "true"^^xsd:boolean
    has been asserted on the node (set by the dispatcher when a matching
    observation report is generated).  Absent result → Degraded.
    """
    result = g.value(node, _ICM.result)
    passed = result == rdflib.Literal(True)
    return passed, [{"type": "ObservationReportingExpectation",
                     "result": str(result) if result is not None else None,
                     "passed": passed}]


def _eval_guarantee_reporting_expectation(
    g: rdflib.Graph, node: rdflib.term.Node
) -> tuple[bool, list[dict]]:
    """
    ig:GuaranteeReportingExpectation — passes if icm:result "true"^^xsd:boolean
    has been asserted on the node.  Absent result → Degraded.
    """
    result = g.value(node, _ICM.result)
    passed = result == rdflib.Literal(True)
    return passed, [{"type": "GuaranteeReportingExpectation",
                     "result": str(result) if result is not None else None,
                     "passed": passed}]


def _eval_validity_reporting_expectation(
    g: rdflib.Graph, node: rdflib.term.Node
) -> tuple[bool, list[dict]]:
    """
    iv:ValidityReportingExpectation — passes if icm:result "true"^^xsd:boolean
    has been asserted on the node.  Absent result → Degraded.
    """
    result = g.value(node, _ICM.result)
    passed = result == rdflib.Literal(True)
    return passed, [{"type": "ValidityReportingExpectation",
                     "result": str(result) if result is not None else None,
                     "passed": passed}]


# ── Type-dispatch table ───────────────────────────────────────────────────────
# Pre-computed {rdf_type → handler(g, node) → (bool, list[dict])} used by
# _eval_node to replace ~30 sequential membership tests with a single type
# fetch + O(1) dict lookup.

def _two_arg_handler(
    rdf_type: rdflib.URIRef, cmp_op: Callable, sym: str
) -> Callable:
    def _h(g: rdflib.Graph, node: rdflib.term.Node) -> tuple[bool, list[dict]]:
        cond = _eval_two_arg(g, node, rdf_type, cmp_op, sym)
        return cond["passed"], [cond]
    return _h


def _range_handler(g: rdflib.Graph, node: rdflib.term.Node) -> tuple[bool, list[dict]]:
    cond = _eval_range(g, node)
    return cond["passed"], [cond]


_TYPE_DISPATCH: dict[rdflib.URIRef, Callable] = {
    **{rdf_type: _two_arg_handler(rdf_type, cmp_op, sym)
       for rdf_type, cmp_op, sym in _TWO_ARG_OPS},
    _QUAN.quaninRange:                          _range_handler,
    _QUAN.inRange:                              _range_handler,
    _SET.setisMember:                           _eval_is_member,
    _SET.setintersectsWith:                     _eval_intersects_with,
    _SET.setincludedIn:                         _eval_included_in,
    _SET.setforAll:                             _eval_for_all,
    _SET.elementOf:                             _eval_element_of,
    _SET.empty:                                 _eval_empty_set,
    _ICM.DeliveryExpectation:                   _eval_delivery_expectation,
    _ICM.PropertyExpectation:                   _eval_property_expectation,
    _ICM.ObservationReportingExpectation:       _eval_observation_reporting_expectation,
    _IG.GuaranteeReportingExpectation:          _eval_guarantee_reporting_expectation,
    _IV.ValidityReportingExpectation:           _eval_validity_reporting_expectation,
    _IV.ivvalidityOf:                           _eval_validity_of,
    _IG.igGuaranteeReport:                      _eval_guarantee_report,
    _INSP.inspvalueSelected:                    _eval_value_selected,
    _INSP.inspvalueSelectedFor:                 _eval_value_selected_for,
    _INSP.inspchosenAny:                        _eval_chosen_any,
    _INSP.inspchosenAll:                        _eval_chosen_all,
    _INSP.inspchosenAllFor:                     _eval_chosen_all_for,
    _INSP.inspchosenAnyFor:                     _eval_chosen_any_for,
    _INSP.inspusedVocabularyFor:                _eval_used_vocabulary_for,
}


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
        # When the list is non-empty but every child was opaque (no conditions
        # produced), the expression is unevaluable — fail rather than silently
        # pass. Empty-list combinators (e.g. noneOf rdf:nil) are vacuously valid
        # and skip this guard.
        if items and not all_conds:
            return False, [{"type": "opaqueChildren",
                            "count": len(items),
                            "error": "no evaluable conditions in children",
                            "passed": False}]
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

    # ── Type-based dispatch (single fetch, O(1) lookup) ──────────────────────
    for t in g.objects(node, RDF.type):
        handler = _TYPE_DISPATCH.get(t)
        if handler is not None:
            return handler(g, node)

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
    # Bare log:match-family nodes (no enclosing combinator) are also valid roots.
    for pred in _LOG_MATCH_PREDS:
        for subj in g.subjects(pred, None):
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
        _QUAN.quaninRange, _QUAN.inRange,
        _SET.setisMember, _SET.setintersectsWith, _SET.setincludedIn, _SET.setforAll,
        _SET.elementOf, _SET.empty,
        _ICM.DeliveryExpectation, _ICM.PropertyExpectation, _ICM.ObservationReportingExpectation,
        _IG.GuaranteeReportingExpectation, _IV.ValidityReportingExpectation,
        _IV.ivvalidityOf,
        _IG.igGuaranteeReport,
        _INSP.inspvalueSelected, _INSP.inspvalueSelectedFor,
        _INSP.inspchosenAny, _INSP.inspchosenAll, _INSP.inspchosenAllFor, _INSP.inspchosenAnyFor,
        _INSP.inspusedVocabularyFor,
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
    "elementOf", "empty",
    "DeliveryExpectation", "PropertyExpectation", "ObservationReportingExpectation",
    "GuaranteeReportingExpectation", "ValidityReportingExpectation",
    "validityOf", "validityGate",
    "GuaranteeReport",
    "valueSelected", "valueSelectedFor",
    "chosenAny", "chosenAll", "chosenAllFor", "chosenAnyFor",
    "usedVocabularyFor",
    "opaqueChildren",
])


def _fail_label(c: dict) -> str:
    if "error" in c:
        return f"{c.get('type', '?')}: {c['error']}"
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

_MAX_TURTLE_BYTES: int = int(os.getenv("EVAL_MAX_TURTLE_BYTES", str(512 * 1024)))


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
    if len(turtle_str.encode()) > _MAX_TURTLE_BYTES:
        return {
            "intentHandlingState": "Degraded",
            "reason": f"Expression exceeds size limit ({_MAX_TURTLE_BYTES} bytes)",
            "conditions": [],
        }

    g = rdflib.Graph()
    try:
        g.parse(data=turtle_str, format="turtle")
    except Exception as exc:
        return {"intentHandlingState": "Degraded", "reason": f"Turtle parse error: {exc}", "conditions": []}

    _normalize_qty_predicates(g)
    _normalize_set_predicates(g)
    _resolve_metric_refs(g)
    _compute_math_functions(g)
    _compute_set_constructors(g)
    _resolve_validity_chains(g)
    _derive_guarantee_states(g)
    _derive_ext_types(g)

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

    # Merge resource inventory so set:resourcesOfType / set:resourcesWithPropertyObject
    # can resolve against domain resource data loaded at startup.
    resources_turtle: str | None = None
    try:
        from src.graph.namespaces import RESOURCES_GRAPH
        resources_turtle = await client.gsp_get(str(RESOURCES_GRAPH))
    except Exception:
        pass  # resources graph absent or empty — evaluation continues without it

    parts = [expr_value]
    if obs_turtle:
        parts.append(obs_turtle)
    if resources_turtle:
        parts.append(resources_turtle)
    combined = "\n".join(parts)

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, evaluate_turtle_conditions, combined)
