"""
Unit tests: HSI intent quantity condition evaluation (Python RDFLib layer).

These tests call evaluate_turtle_conditions() directly — no live Fuseki required.
Covers all TIO quantity operators used in HSI service intents:
  quan:quanatLeast  — downstream/upstream bandwidth (observed >= bound)
  quan:quansmaller  — latency, jitter, packet loss (observed < bound)

All tests pass without xfail: evaluation is now done in Python, not Fuseki inference.
"""
from __future__ import annotations

from src.handler.evaluator import evaluate_turtle_conditions

QUAN = "http://tio.models.tmforum.org/tio/v3.6.0/QuantityOntology/"
RDF  = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
XSD  = "http://www.w3.org/2001/XMLSchema#"

PREFIXES = """\
@prefix quan: <http://tio.models.tmforum.org/tio/v3.6.0/QuantityOntology/> .
@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .
"""

CMP = "urn:test:cmp"
OBS = "urn:test:observed"
RST = "urn:test:rest"
BND = "urn:test:bound"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _at_least_turtle(observed: str, bound: str) -> str:
    """quan:quanatLeast — Fulfilled when observed >= bound."""
    return (
        PREFIXES
        + f"<{CMP}> a quan:quanatLeast ;\n"
        f"    rdf:first <{OBS}> ;\n"
        f"    rdf:rest  <{RST}> .\n"
        f"<{RST}> rdf:first <{BND}> .\n"
        f"<{OBS}> rdf:value \"{observed}\"^^xsd:decimal .\n"
        f"<{BND}> rdf:value \"{bound}\"^^xsd:decimal .\n"
    )


def _smaller_turtle(observed: str, bound: str) -> str:
    """quan:quansmaller — Fulfilled when observed < bound."""
    return (
        PREFIXES
        + f"<{CMP}> a quan:quansmaller ;\n"
        f"    rdf:first <{OBS}> ;\n"
        f"    rdf:rest  <{RST}> .\n"
        f"<{RST}> rdf:first <{BND}> .\n"
        f"<{OBS}> rdf:value \"{observed}\"^^xsd:decimal .\n"
        f"<{BND}> rdf:value \"{bound}\"^^xsd:decimal .\n"
    )


def _assert_fulfilled(turtle: str, msg: str) -> None:
    result = evaluate_turtle_conditions(turtle)
    assert result["intentHandlingState"] == "Fulfilled", f"{msg} — got: {result}"
    assert len(result["conditions"]) == 1
    assert result["conditions"][0]["passed"] is True


def _assert_degraded(turtle: str, msg: str) -> None:
    result = evaluate_turtle_conditions(turtle)
    assert result["intentHandlingState"] == "Degraded", f"{msg} — got: {result}"
    assert len(result["conditions"]) == 1
    assert result["conditions"][0]["passed"] is False


# ── Downstream bandwidth ≥ 100 Mbps ──────────────────────────────────────────

class TestDownstreamBandwidth:
    """quan:greaterOrEqual 100 Mbps → rules form: quan:quanatLeast."""

    def test_in_range_120mbps_fires_result_true(self):
        _assert_fulfilled(_at_least_turtle("120", "100"), "Expected Fulfilled for 120 Mbps >= 100 Mbps")

    def test_at_boundary_100mbps_fires_result_true(self):
        _assert_fulfilled(_at_least_turtle("100", "100"), "Expected Fulfilled for 100 Mbps >= 100 Mbps (boundary)")

    def test_out_of_range_80mbps_no_result(self):
        _assert_degraded(_at_least_turtle("80", "100"), "Expected Degraded for 80 Mbps < 100 Mbps")


# ── Upstream bandwidth ≥ 20 Mbps ─────────────────────────────────────────────

class TestUpstreamBandwidth:
    """quan:greaterOrEqual 20 Mbps → rules form: quan:quanatLeast."""

    def test_in_range_25mbps_fires_result_true(self):
        _assert_fulfilled(_at_least_turtle("25", "20"), "Expected Fulfilled for 25 Mbps >= 20 Mbps")

    def test_at_boundary_20mbps_fires_result_true(self):
        _assert_fulfilled(_at_least_turtle("20", "20"), "Expected Fulfilled for 20 Mbps >= 20 Mbps (boundary)")

    def test_out_of_range_15mbps_no_result(self):
        _assert_degraded(_at_least_turtle("15", "20"), "Expected Degraded for 15 Mbps < 20 Mbps")


# ── Latency < 25 ms ───────────────────────────────────────────────────────────

class TestLatency:
    """quan:smaller 25 ms → rules form: quan:quansmaller."""

    def test_in_range_10ms_fires_result_true(self):
        _assert_fulfilled(_smaller_turtle("10", "25"), "Expected Fulfilled for 10 ms < 25 ms")

    def test_out_of_range_30ms_no_result(self):
        _assert_degraded(_smaller_turtle("30", "25"), "Expected Degraded for 30 ms >= 25 ms")

    def test_at_boundary_25ms_no_result(self):
        _assert_degraded(_smaller_turtle("25", "25"), "Expected Degraded for 25 ms == 25 ms (strict less-than)")


# ── Jitter < 3 ms ─────────────────────────────────────────────────────────────

class TestJitter:
    """quan:smaller 3 ms → rules form: quan:quansmaller."""

    def test_in_range_1ms_fires_result_true(self):
        _assert_fulfilled(_smaller_turtle("1", "3"), "Expected Fulfilled for 1 ms < 3 ms")

    def test_out_of_range_5ms_no_result(self):
        _assert_degraded(_smaller_turtle("5", "3"), "Expected Degraded for 5 ms >= 3 ms")

    def test_at_boundary_3ms_no_result(self):
        _assert_degraded(_smaller_turtle("3", "3"), "Expected Degraded for 3 ms == 3 ms (strict less-than)")


# ── Packet loss < 0.1 % ───────────────────────────────────────────────────────

class TestPacketLoss:
    """quan:smaller 0.1 percent → rules form: quan:quansmaller."""

    def test_in_range_0_05pct_fires_result_true(self):
        _assert_fulfilled(_smaller_turtle("0.05", "0.1"), "Expected Fulfilled for 0.05% < 0.1%")

    def test_out_of_range_0_2pct_no_result(self):
        _assert_degraded(_smaller_turtle("0.2", "0.1"), "Expected Degraded for 0.2% >= 0.1%")

    def test_at_boundary_0_1pct_no_result(self):
        _assert_degraded(_smaller_turtle("0.1", "0.1"), "Expected Degraded for 0.1% == 0.1% (strict less-than)")
