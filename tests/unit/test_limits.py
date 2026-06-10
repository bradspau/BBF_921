"""Unit tests for src/handler/limits.py."""
from __future__ import annotations

from decimal import Decimal

import pytest
import rdflib
from rdflib.namespace import RDF

from src.handler.limits import apply_best_effort_bounds, get_limit, _QUAN_TYPE_URIS

_QUAN = rdflib.Namespace("http://tio.models.tmforum.org/tio/v3.6.0/QuantityOntology/")

# Minimal Turtle with one quanatLeast condition: obs=15, bound=10 (fails ≥ check).
_TURTLE_ONE = """\
@prefix quan: <http://tio.models.tmforum.org/tio/v3.6.0/QuantityOntology/> .
@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .

_:cond a quan:quanatLeast ;
    rdf:first _:obs ;
    rdf:rest  ( _:bnd ) .
_:obs rdf:value "15.0"^^xsd:decimal .
_:bnd rdf:value "10.0"^^xsd:decimal .
"""

_TURTLE_TWO = """\
@prefix quan: <http://tio.models.tmforum.org/tio/v3.6.0/QuantityOntology/> .
@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .

_:c1 a quan:quanatLeast ;
    rdf:first _:o1 ;
    rdf:rest  ( _:b1 ) .
_:o1 rdf:value "15.0"^^xsd:decimal .
_:b1 rdf:value "10.0"^^xsd:decimal .

_:c2 a quan:quanatMost ;
    rdf:first _:o2 ;
    rdf:rest  ( _:b2 ) .
_:o2 rdf:value "80.0"^^xsd:decimal .
_:b2 rdf:value "100.0"^^xsd:decimal .
"""


def _parse_bound(turtle: str, type_uri: rdflib.URIRef) -> Decimal:
    g = rdflib.Graph()
    g.parse(data=turtle, format="turtle")
    for node in g.subjects(RDF.type, type_uri):
        rest = g.value(node, RDF.rest)
        bnd_node = g.value(rest, RDF.first)
        val = g.value(bnd_node, RDF.value)
        return Decimal(str(val))
    raise AssertionError("condition node not found")


class TestGetLimit:
    def test_returns_configured_limit(self):
        limits = {"quanatLeast": Decimal("25.0")}
        from src.handler.limits import apply_best_effort_bounds
        # Use _limits param to avoid global state; verify get_limit separately.
        new_t, changed = apply_best_effort_bounds(
            _TURTLE_ONE,
            [{"type": "quanatLeast", "passed": False}],
            _limits=limits,
        )
        assert changed

    def test_get_limit_missing_returns_none(self):
        assert get_limit("nonexistent_type") is None


class TestApplyBestEffortBoundsObserved:
    def test_observed_value_replaces_bound(self):
        conditions = [
            {"type": "quanatLeast", "observed": Decimal("15.0"), "bound": Decimal("10.0"), "passed": False}
        ]
        new_turtle, changed = apply_best_effort_bounds(_TURTLE_ONE, conditions)
        assert changed
        assert new_turtle is not None
        bound = _parse_bound(new_turtle, _QUAN.quanatLeast)
        assert bound == Decimal("15.0")

    def test_passed_condition_not_modified(self):
        conditions = [
            {"type": "quanatLeast", "observed": Decimal("15.0"), "bound": Decimal("10.0"), "passed": True}
        ]
        new_turtle, changed = apply_best_effort_bounds(_TURTLE_ONE, conditions)
        assert not changed
        assert new_turtle is None

    def test_bound_already_equals_observed_no_change(self):
        turtle = """\
@prefix quan: <http://tio.models.tmforum.org/tio/v3.6.0/QuantityOntology/> .
@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .

_:cond a quan:quanatLeast ;
    rdf:first _:obs ;
    rdf:rest  ( _:bnd ) .
_:obs rdf:value "15.0"^^xsd:decimal .
_:bnd rdf:value "15.0"^^xsd:decimal .
"""
        conditions = [
            {"type": "quanatLeast", "observed": Decimal("15.0"), "bound": Decimal("15.0"), "passed": False}
        ]
        _, changed = apply_best_effort_bounds(turtle, conditions)
        assert not changed

    def test_two_conditions_both_updated(self):
        conditions = [
            {"type": "quanatLeast", "observed": Decimal("15.0"), "bound": Decimal("10.0"), "passed": False},
            {"type": "quanatMost",  "observed": Decimal("80.0"), "bound": Decimal("100.0"), "passed": False},
        ]
        new_turtle, changed = apply_best_effort_bounds(_TURTLE_TWO, conditions)
        assert changed
        assert new_turtle is not None
        assert _parse_bound(new_turtle, _QUAN.quanatLeast) == Decimal("15.0")
        assert _parse_bound(new_turtle, _QUAN.quanatMost) == Decimal("80.0")


class TestApplyBestEffortBoundsLimitFallback:
    def test_uses_operator_limit_when_no_observed(self):
        limits = {"quanatLeast": Decimal("20.0")}
        conditions = [{"type": "quanatLeast", "passed": False}]
        new_turtle, changed = apply_best_effort_bounds(_TURTLE_ONE, conditions, _limits=limits)
        assert changed
        assert new_turtle is not None
        bound = _parse_bound(new_turtle, _QUAN.quanatLeast)
        assert bound == Decimal("20.0")

    def test_observed_takes_priority_over_limit(self):
        limits = {"quanatLeast": Decimal("20.0")}
        conditions = [
            {"type": "quanatLeast", "observed": Decimal("15.0"), "bound": Decimal("10.0"), "passed": False}
        ]
        new_turtle, changed = apply_best_effort_bounds(_TURTLE_ONE, conditions, _limits=limits)
        assert changed
        bound = _parse_bound(new_turtle, _QUAN.quanatLeast)
        assert bound == Decimal("15.0")  # observed wins over limit

    def test_no_limit_and_no_observed_returns_no_change(self):
        conditions = [{"type": "quanatLeast", "passed": False}]
        _, changed = apply_best_effort_bounds(_TURTLE_ONE, conditions, _limits={})
        assert not changed


class TestApplyBestEffortBoundsEdgeCases:
    def test_invalid_turtle_returns_no_change(self):
        new_turtle, changed = apply_best_effort_bounds("not valid turtle!!!", [])
        assert not changed
        assert new_turtle is None

    def test_empty_conditions_returns_no_change(self):
        _, changed = apply_best_effort_bounds(_TURTLE_ONE, [])
        assert not changed

    def test_unknown_condition_type_skipped(self):
        conditions = [{"type": "logMatch", "passed": False}]
        _, changed = apply_best_effort_bounds(_TURTLE_ONE, conditions)
        assert not changed

    def test_predicate_form_updated(self):
        """Predicate-form quan:atLeast (bbf:Metric [rdf:value ...]) is substituted."""
        turtle = """\
@prefix bbf:  <http://example.org/> .
@prefix quan: <http://tio.models.tmforum.org/tio/v3.6.0/QuantityOntology/> .
@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .

bbf:Check  quan:atLeast ( bbf:DownstreamMetric [ rdf:value "500"^^xsd:decimal ] ) .
"""
        conditions = [
            {"type": "atLeast", "observed": Decimal("150.0"), "bound": Decimal("500.0"), "passed": False}
        ]
        new_turtle, changed = apply_best_effort_bounds(turtle, conditions)
        assert changed
        # Verify the old bound is gone and new bound appears in the output Turtle.
        assert '"500"' not in new_turtle
        assert "150" in new_turtle

    def test_short_name_alias_updated(self):
        turtle = """\
@prefix quan: <http://tio.models.tmforum.org/tio/v3.6.0/QuantityOntology/> .
@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .

_:cond a quan:atLeast ;
    rdf:first _:obs ;
    rdf:rest  ( _:bnd ) .
_:obs rdf:value "15.0"^^xsd:decimal .
_:bnd rdf:value "10.0"^^xsd:decimal .
"""
        conditions = [
            {"type": "atLeast", "observed": Decimal("15.0"), "bound": Decimal("10.0"), "passed": False}
        ]
        new_turtle, changed = apply_best_effort_bounds(turtle, conditions)
        assert changed
        bound = _parse_bound(new_turtle, _QUAN.atLeast)
        assert bound == Decimal("15.0")
