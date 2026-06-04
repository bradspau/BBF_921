"""
Unit tests for the logical operator tree evaluator in src/handler/evaluator.py.

Tests cover:
  - log:allOf / log:anyOf / log:noneOf / log:oneOf combinators
  - log:match (triple check)
  - log:matchAll / matchAny / matchNone / matchOne (container-based triple checks)
  - log:matchStatement (reified statement checks)
  - Nested / mixed combinator trees
  - Root-finding: outermost combinator is the entry point
  - Flat-scan fallback when no log:* combinators are present
  - anyOf semantics: overall Fulfilled even when some children fail
"""
from __future__ import annotations

import pytest

from src.handler.evaluator import evaluate_turtle_conditions

LOG  = "http://tio.models.tmforum.org/tio/v3.6.0/LogicalOperators/"
QUAN = "http://tio.models.tmforum.org/tio/v3.6.0/QuantityOntology/"

_PREFIXES = """\
@prefix log:  <http://tio.models.tmforum.org/tio/v3.6.0/LogicalOperators/> .
@prefix quan: <http://tio.models.tmforum.org/tio/v3.6.0/QuantityOntology/> .
@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .
@prefix ex:   <urn:test:> .
"""

# ── Helpers ───────────────────────────────────────────────────────────────────

def _at_least(name: str, obs: str, bnd: str) -> str:
    """Snippet: a named quan:quanatLeast condition node."""
    return (
        f"ex:{name} a quan:quanatLeast ;\n"
        f"    rdf:first ex:{name}_obs ; rdf:rest [ rdf:first ex:{name}_bnd ] .\n"
        f"ex:{name}_obs rdf:value \"{obs}\"^^xsd:decimal .\n"
        f"ex:{name}_bnd rdf:value \"{bnd}\"^^xsd:decimal .\n"
    )


def _smaller(name: str, obs: str, bnd: str) -> str:
    """Snippet: a named quan:quansmaller condition node."""
    return (
        f"ex:{name} a quan:quansmaller ;\n"
        f"    rdf:first ex:{name}_obs ; rdf:rest [ rdf:first ex:{name}_bnd ] .\n"
        f"ex:{name}_obs rdf:value \"{obs}\"^^xsd:decimal .\n"
        f"ex:{name}_bnd rdf:value \"{bnd}\"^^xsd:decimal .\n"
    )


def _run(turtle_body: str) -> dict:
    return evaluate_turtle_conditions(_PREFIXES + turtle_body)


# ── log:allOf (AND) ───────────────────────────────────────────────────────────

class TestAllOf:
    def test_all_pass_fulfilled(self):
        t = (
            _at_least("dl", "120", "100")
            + _at_least("ul", "25", "20")
            + "ex:root log:allOf ( ex:dl ex:ul ) .\n"
        )
        result = _run(t)
        assert result["intentHandlingState"] == "Fulfilled"
        assert len(result["conditions"]) == 2
        assert all(c["passed"] for c in result["conditions"])

    def test_one_fail_degraded(self):
        t = (
            _at_least("dl", "80", "100")   # FAILS
            + _at_least("ul", "25", "20")   # passes
            + "ex:root log:allOf ( ex:dl ex:ul ) .\n"
        )
        result = _run(t)
        assert result["intentHandlingState"] == "Degraded"
        passed  = [c for c in result["conditions"] if c["passed"]]
        failed  = [c for c in result["conditions"] if not c["passed"]]
        assert len(passed) == 1
        assert len(failed) == 1

    def test_all_fail_degraded(self):
        t = (
            _at_least("dl", "80", "100")
            + _at_least("ul", "10", "20")
            + "ex:root log:allOf ( ex:dl ex:ul ) .\n"
        )
        assert _run(t)["intentHandlingState"] == "Degraded"

    def test_empty_list_degraded(self):
        t = "ex:root log:allOf rdf:nil .\n"
        result = _run(t)
        assert result["intentHandlingState"] == "Degraded"


# ── log:anyOf (OR) ────────────────────────────────────────────────────────────

class TestAnyOf:
    def test_one_pass_fulfilled_even_if_other_fails(self):
        t = (
            _at_least("dl", "80", "100")   # FAILS
            + _at_least("ul", "25", "20")   # passes
            + "ex:root log:anyOf ( ex:dl ex:ul ) .\n"
        )
        result = _run(t)
        assert result["intentHandlingState"] == "Fulfilled"
        # conditions list still contains the failing one for diagnostics
        assert len(result["conditions"]) == 2

    def test_all_fail_degraded(self):
        t = (
            _at_least("dl", "80", "100")
            + _at_least("ul", "10", "20")
            + "ex:root log:anyOf ( ex:dl ex:ul ) .\n"
        )
        assert _run(t)["intentHandlingState"] == "Degraded"

    def test_all_pass_fulfilled(self):
        t = (
            _at_least("dl", "120", "100")
            + _at_least("ul", "25", "20")
            + "ex:root log:anyOf ( ex:dl ex:ul ) .\n"
        )
        assert _run(t)["intentHandlingState"] == "Fulfilled"

    def test_empty_list_degraded(self):
        assert _run("ex:root log:anyOf rdf:nil .\n")["intentHandlingState"] == "Degraded"


# ── log:noneOf (NOR) ──────────────────────────────────────────────────────────

class TestNoneOf:
    def test_all_fail_is_fulfilled(self):
        t = (
            _at_least("dl", "80", "100")
            + _at_least("ul", "10", "20")
            + "ex:root log:noneOf ( ex:dl ex:ul ) .\n"
        )
        assert _run(t)["intentHandlingState"] == "Fulfilled"

    def test_any_pass_is_degraded(self):
        t = (
            _at_least("dl", "120", "100")  # passes → noneOf fails
            + _at_least("ul", "10", "20")
            + "ex:root log:noneOf ( ex:dl ex:ul ) .\n"
        )
        assert _run(t)["intentHandlingState"] == "Degraded"

    def test_all_pass_is_degraded(self):
        t = (
            _at_least("dl", "120", "100")
            + _at_least("ul", "25", "20")
            + "ex:root log:noneOf ( ex:dl ex:ul ) .\n"
        )
        assert _run(t)["intentHandlingState"] == "Degraded"

    def test_empty_list_fulfilled(self):
        # noneOf empty → vacuously none pass → True
        assert _run("ex:root log:noneOf rdf:nil .\n")["intentHandlingState"] == "Fulfilled"


# ── log:oneOf (XOR) ───────────────────────────────────────────────────────────

class TestOneOf:
    def test_exactly_one_passes_fulfilled(self):
        t = (
            _at_least("dl", "120", "100")  # passes
            + _at_least("ul", "10", "20")   # fails
            + "ex:root log:oneOf ( ex:dl ex:ul ) .\n"
        )
        assert _run(t)["intentHandlingState"] == "Fulfilled"

    def test_both_pass_degraded(self):
        t = (
            _at_least("dl", "120", "100")
            + _at_least("ul", "25", "20")
            + "ex:root log:oneOf ( ex:dl ex:ul ) .\n"
        )
        assert _run(t)["intentHandlingState"] == "Degraded"

    def test_none_pass_degraded(self):
        t = (
            _at_least("dl", "80", "100")
            + _at_least("ul", "10", "20")
            + "ex:root log:oneOf ( ex:dl ex:ul ) .\n"
        )
        assert _run(t)["intentHandlingState"] == "Degraded"

    def test_three_items_exactly_one_pass(self):
        t = (
            _at_least("a", "120", "100")  # passes
            + _at_least("b", "80", "100")  # fails
            + _at_least("c", "70", "100")  # fails
            + "ex:root log:oneOf ( ex:a ex:b ex:c ) .\n"
        )
        assert _run(t)["intentHandlingState"] == "Fulfilled"

    def test_empty_list_degraded(self):
        assert _run("ex:root log:oneOf rdf:nil .\n")["intentHandlingState"] == "Degraded"


# ── log:match ─────────────────────────────────────────────────────────────────

class TestLogMatch:
    def test_triple_exists_fulfilled(self):
        t = (
            "ex:iface ex:state ex:Up .\n"
            "ex:cond log:match ( ex:iface ex:state ex:Up ) .\n"
            "ex:root log:allOf ( ex:cond ) .\n"
        )
        assert _run(t)["intentHandlingState"] == "Fulfilled"

    def test_triple_absent_degraded(self):
        t = (
            # triple NOT in graph
            "ex:cond log:match ( ex:iface ex:state ex:Up ) .\n"
            "ex:root log:allOf ( ex:cond ) .\n"
        )
        assert _run(t)["intentHandlingState"] == "Degraded"

    def test_match_condition_in_condition_list(self):
        result = evaluate_turtle_conditions(
            _PREFIXES
            + "ex:iface ex:state ex:Up .\n"
            + "ex:cond log:match ( ex:iface ex:state ex:Up ) .\n"
            + "ex:root log:allOf ( ex:cond ) .\n"
        )
        assert result["conditions"][0]["type"] == "logMatch"
        assert result["conditions"][0]["passed"] is True

    def test_malformed_match_two_args_degraded(self):
        t = (
            "ex:cond log:match ( ex:iface ex:state ) .\n"  # only 2 args
            "ex:root log:allOf ( ex:cond ) .\n"
        )
        result = _run(t)
        assert result["intentHandlingState"] == "Degraded"
        assert "error" in result["conditions"][0]

    def test_match_combined_with_quantity(self):
        """log:allOf mixing log:match and quantity conditions."""
        t = (
            "ex:iface ex:state ex:Up .\n"
            "ex:match_cond log:match ( ex:iface ex:state ex:Up ) .\n"
            + _at_least("dl", "120", "100")
            + "ex:root log:allOf ( ex:match_cond ex:dl ) .\n"
        )
        result = _run(t)
        assert result["intentHandlingState"] == "Fulfilled"
        assert len(result["conditions"]) == 2


# ── log:matchAll / matchAny / matchNone / matchOne ────────────────────────────

class TestMatchContainer:
    def _with_members(self, members: list[str], all_have_state: list[bool]) -> str:
        lines = []
        for i, (name, has_state) in enumerate(zip(members, all_have_state)):
            lines.append(f"ex:{name} a rdfs:Resource .")
            if has_state:
                lines.append(f"ex:{name} ex:state ex:Up .")
            lines.append(f"ex:container rdfs:member ex:{name} .")
        return "\n".join(lines) + "\n"

    def test_match_all_all_present_fulfilled(self):
        t = (
            self._with_members(["m1", "m2"], [True, True])
            + "ex:cond log:matchAll ( ex:container ex:state ex:Up ) .\n"
            + "ex:root log:allOf ( ex:cond ) .\n"
        )
        assert _run(t)["intentHandlingState"] == "Fulfilled"

    def test_match_all_one_missing_degraded(self):
        t = (
            self._with_members(["m1", "m2"], [True, False])
            + "ex:cond log:matchAll ( ex:container ex:state ex:Up ) .\n"
            + "ex:root log:allOf ( ex:cond ) .\n"
        )
        assert _run(t)["intentHandlingState"] == "Degraded"

    def test_match_any_one_present_fulfilled(self):
        t = (
            self._with_members(["m1", "m2"], [False, True])
            + "ex:cond log:matchAny ( ex:container ex:state ex:Up ) .\n"
            + "ex:root log:allOf ( ex:cond ) .\n"
        )
        assert _run(t)["intentHandlingState"] == "Fulfilled"

    def test_match_any_none_present_degraded(self):
        t = (
            self._with_members(["m1", "m2"], [False, False])
            + "ex:cond log:matchAny ( ex:container ex:state ex:Up ) .\n"
            + "ex:root log:allOf ( ex:cond ) .\n"
        )
        assert _run(t)["intentHandlingState"] == "Degraded"

    def test_match_none_none_present_fulfilled(self):
        t = (
            self._with_members(["m1", "m2"], [False, False])
            + "ex:cond log:matchNone ( ex:container ex:state ex:Up ) .\n"
            + "ex:root log:allOf ( ex:cond ) .\n"
        )
        assert _run(t)["intentHandlingState"] == "Fulfilled"

    def test_match_none_one_present_degraded(self):
        t = (
            self._with_members(["m1", "m2"], [True, False])
            + "ex:cond log:matchNone ( ex:container ex:state ex:Up ) .\n"
            + "ex:root log:allOf ( ex:cond ) .\n"
        )
        assert _run(t)["intentHandlingState"] == "Degraded"

    def test_match_one_exactly_one_present_fulfilled(self):
        t = (
            self._with_members(["m1", "m2", "m3"], [True, False, False])
            + "ex:cond log:matchOne ( ex:container ex:state ex:Up ) .\n"
            + "ex:root log:allOf ( ex:cond ) .\n"
        )
        assert _run(t)["intentHandlingState"] == "Fulfilled"

    def test_match_one_two_present_degraded(self):
        t = (
            self._with_members(["m1", "m2", "m3"], [True, True, False])
            + "ex:cond log:matchOne ( ex:container ex:state ex:Up ) .\n"
            + "ex:root log:allOf ( ex:cond ) .\n"
        )
        assert _run(t)["intentHandlingState"] == "Degraded"

    def test_empty_container_degraded(self):
        t = (
            "ex:cond log:matchAll ( ex:empty_container ex:state ex:Up ) .\n"
            + "ex:root log:allOf ( ex:cond ) .\n"
        )
        result = _run(t)
        assert result["intentHandlingState"] == "Degraded"
        assert result["conditions"][0]["error"] == "empty container"


# ── log:matchStatement ────────────────────────────────────────────────────────

class TestMatchStatement:
    def test_reified_statement_exists_fulfilled(self):
        t = (
            "ex:iface ex:state ex:Up .\n"
            "ex:stmt a rdf:Statement ;\n"
            "    rdf:subject ex:iface ; rdf:predicate ex:state ; rdf:object ex:Up .\n"
            "ex:cond log:matchStatement ( ex:stmt ) .\n"
            "ex:root log:allOf ( ex:cond ) .\n"
        )
        assert _run(t)["intentHandlingState"] == "Fulfilled"

    def test_reified_statement_absent_degraded(self):
        t = (
            # triple NOT in graph
            "ex:stmt a rdf:Statement ;\n"
            "    rdf:subject ex:iface ; rdf:predicate ex:state ; rdf:object ex:Up .\n"
            "ex:cond log:matchStatement ( ex:stmt ) .\n"
            "ex:root log:allOf ( ex:cond ) .\n"
        )
        assert _run(t)["intentHandlingState"] == "Degraded"

    def test_empty_statement_list_degraded(self):
        t = "ex:cond log:matchStatement rdf:nil .\nex:root log:allOf ( ex:cond ) .\n"
        assert _run(t)["intentHandlingState"] == "Degraded"


# ── Nested / mixed trees ──────────────────────────────────────────────────────

class TestNestedTrees:
    def test_allof_containing_anyof(self):
        """allOf( anyOf(fail, pass), pass ) → Fulfilled."""
        t = (
            _at_least("a", "80", "100")   # fails
            + _at_least("b", "25", "20")   # passes
            + _at_least("c", "120", "100") # passes
            + "ex:inner log:anyOf ( ex:a ex:b ) .\n"
            + "ex:root  log:allOf ( ex:inner ex:c ) .\n"
        )
        result = _run(t)
        assert result["intentHandlingState"] == "Fulfilled"
        assert len(result["conditions"]) == 3

    def test_allof_containing_anyof_both_fail(self):
        """allOf( anyOf(fail, fail), pass ) → Degraded."""
        t = (
            _at_least("a", "80", "100")   # fails
            + _at_least("b", "10", "20")   # fails
            + _at_least("c", "120", "100") # passes
            + "ex:inner log:anyOf ( ex:a ex:b ) .\n"
            + "ex:root  log:allOf ( ex:inner ex:c ) .\n"
        )
        assert _run(t)["intentHandlingState"] == "Degraded"

    def test_anyof_containing_allof(self):
        """anyOf( allOf(fail, fail), pass ) → Fulfilled."""
        t = (
            _at_least("a", "80", "100")   # fails
            + _at_least("b", "10", "20")   # fails
            + _at_least("c", "120", "100") # passes
            + "ex:inner log:allOf ( ex:a ex:b ) .\n"
            + "ex:root  log:anyOf ( ex:inner ex:c ) .\n"
        )
        assert _run(t)["intentHandlingState"] == "Fulfilled"

    def test_three_levels_deep(self):
        """allOf( allOf( anyOf(fail, pass) ), pass ) → Fulfilled."""
        t = (
            _at_least("leaf1", "80", "100")    # fails
            + _at_least("leaf2", "25", "20")    # passes
            + _at_least("leaf3", "120", "100")  # passes
            + "ex:l1 log:anyOf ( ex:leaf1 ex:leaf2 ) .\n"
            + "ex:l2 log:allOf ( ex:l1 ) .\n"
            + "ex:root log:allOf ( ex:l2 ex:leaf3 ) .\n"
        )
        assert _run(t)["intentHandlingState"] == "Fulfilled"

    def test_multiple_roots_both_must_pass(self):
        """Two separate root groupers — both must pass (AND semantics)."""
        t = (
            _at_least("a", "120", "100")
            + _at_least("b", "25", "20")
            + "ex:root1 log:allOf ( ex:a ) .\n"
            + "ex:root2 log:allOf ( ex:b ) .\n"
        )
        assert _run(t)["intentHandlingState"] == "Fulfilled"

    def test_multiple_roots_one_fails(self):
        t = (
            _at_least("a", "80", "100")   # fails
            + _at_least("b", "25", "20")   # passes
            + "ex:root1 log:allOf ( ex:a ) .\n"
            + "ex:root2 log:allOf ( ex:b ) .\n"
        )
        assert _run(t)["intentHandlingState"] == "Degraded"

    def test_match_in_allof_with_quantity(self):
        """Match + quantity in same allOf — both must pass."""
        t = (
            "ex:iface ex:state ex:Up .\n"
            "ex:match_cond log:match ( ex:iface ex:state ex:Up ) .\n"
            + _at_least("dl", "120", "100")
            + "ex:root log:allOf ( ex:match_cond ex:dl ) .\n"
        )
        result = _run(t)
        assert result["intentHandlingState"] == "Fulfilled"
        types = {c["type"] for c in result["conditions"]}
        assert "logMatch" in types
        assert "quanatLeast" in types


# ── Root-finding and fallback ─────────────────────────────────────────────────

class TestRootFinding:
    def test_flat_scan_fallback_no_log_structure(self):
        """Bare quantity nodes without any log:* → flat scan → same result as before."""
        t = _at_least("dl", "120", "100") + _at_least("ul", "25", "20")
        assert _run(t)["intentHandlingState"] == "Fulfilled"

    def test_flat_scan_fallback_one_fail(self):
        t = _at_least("dl", "80", "100") + _at_least("ul", "25", "20")
        assert _run(t)["intentHandlingState"] == "Degraded"

    def test_inner_nodes_not_treated_as_roots(self):
        """Inner allOf/anyOf nodes are only reachable via the outer root."""
        t = (
            _at_least("a", "120", "100")
            + _at_least("b", "25", "20")
            + "ex:inner log:allOf ( ex:a ) .\n"
            + "ex:outer log:anyOf ( ex:inner ex:b ) .\n"
        )
        result = _run(t)
        # outer is the only root; inner is an item in outer's list
        assert result["intentHandlingState"] == "Fulfilled"
        # exactly 2 conditions from the 2 quantity leaves
        assert len(result["conditions"]) == 2

    def test_opaque_nodes_pass_silently(self):
        """Nodes in a list that have no evaluable content don't fail the intent."""
        t = (
            _at_least("dl", "120", "100")
            + "ex:opaque a ex:SomeUnknownType .\n"  # unknown, no log:* or quan:*
            + "ex:root log:allOf ( ex:dl ex:opaque ) .\n"
        )
        result = _run(t)
        assert result["intentHandlingState"] == "Fulfilled"
        # only the quantity condition is in the list
        assert len(result["conditions"]) == 1
