"""
Live integration tests: HSI intent condition evaluation via the TIO eval dataset.

Each test:
  1. Clears the tmf921-eval default graph.
  2. POSTs minimal Turtle representing one HSI condition with an observed value.
  3. Queries the eval dataset for rdf:value "true"^^xsd:boolean on the condition node.
  4. Asserts the result — true for in-range, absent for out-of-range.

Requires a running Fuseki with the tmf921-eval dataset loaded (docker compose up).

KNOWN LIMITATION — Fuseki 5.x GenericRuleReasoner:
  In-range tests are marked xfail.  Fuseki's SPARQL execution engine operates on
  DatasetGraph internally; the ja:InfModel wrapper (and its GenericRuleReasoner) is
  bypassed at query time.  Data is stored correctly but inferred triples never appear
  in SPARQL results.  The out-of-range tests (ASK returns false = no inference) pass
  correctly and act as regression guards for the negative case.

  Fix required (tracked in bd issue): either reconfigure the eval dataset so Fuseki
  routes SPARQL through the InfModel, or move quantity comparison evaluation to the
  Python application layer in evaluator.py.

NOTE ON NAMESPACE/FORMAT:
  The current HSI seed expressionValue uses quan:greaterOrEqual as a *predicate*
  with prefix <https://tio.models.tmforum.org/QuantityModel/>.  The TIO Jena rules
  fire on *typed blank nodes* (quan:quanatLeast / quan:quansmaller) with prefix
  <http://tio.models.tmforum.org/tio/v3.6.0/QuantityOntology/>.  These are
  incompatible — the seed expression will never trigger the quantity rules as-is.
  These tests use the rules-correct typed-blank-node format so inference actually
  fires.  The seed expression would need to be rewritten to use the same format
  before end-to-end compliance evaluation can work.
"""
from __future__ import annotations

import pytest
import httpx

_INFERENCE_XFAIL = pytest.mark.xfail(
    reason=(
        "Fuseki 5.x GenericRuleReasoner does not expose inferred triples via SPARQL — "
        "DatasetGraph bypasses the ja:InfModel wrapper at query time"
    ),
    strict=True,
)

FUSEKI_EVAL = "http://localhost:3030/tmf921-eval"

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
    """quan:quanatLeast — fires when observed >= bound."""
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
    """quan:quansmaller — fires when observed < bound."""
    return (
        PREFIXES
        + f"<{CMP}> a quan:quansmaller ;\n"
        f"    rdf:first <{OBS}> ;\n"
        f"    rdf:rest  <{RST}> .\n"
        f"<{RST}> rdf:first <{BND}> .\n"
        f"<{OBS}> rdf:value \"{observed}\"^^xsd:decimal .\n"
        f"<{BND}> rdf:value \"{bound}\"^^xsd:decimal .\n"
    )


def _ask_result_true() -> str:
    return f"ASK {{ <{CMP}> <{RDF}value> \"true\"^^<{XSD}boolean> }}"


@pytest.fixture(autouse=True)
async def clear_eval():
    """Clear the eval default graph before and after every test."""
    async with httpx.AsyncClient() as http:
        await http.post(
            f"{FUSEKI_EVAL}/update",
            content="CLEAR DEFAULT",
            headers={"Content-Type": "application/sparql-update"},
        )
    yield
    async with httpx.AsyncClient() as http:
        await http.post(
            f"{FUSEKI_EVAL}/update",
            content="CLEAR DEFAULT",
            headers={"Content-Type": "application/sparql-update"},
        )


async def _post_turtle(turtle: str) -> None:
    async with httpx.AsyncClient() as http:
        resp = await http.post(
            f"{FUSEKI_EVAL}/data",
            content=turtle.encode(),
            headers={"Content-Type": "text/turtle"},
        )
        resp.raise_for_status()


async def _ask(query: str) -> bool:
    async with httpx.AsyncClient() as http:
        resp = await http.post(
            f"{FUSEKI_EVAL}/sparql",
            content=query.encode(),
            headers={
                "Content-Type": "application/sparql-query",
                "Accept": "application/sparql-results+json",
            },
        )
        resp.raise_for_status()
        return resp.json().get("boolean", False)


# ── Downstream bandwidth ≥ 100 Mbps ──────────────────────────────────────────

class TestDownstreamBandwidth:
    """quan:greaterOrEqual 100 Mbps → rules form: quan:quanatLeast."""

    @_INFERENCE_XFAIL
    async def test_in_range_120mbps_fires_result_true(self):
        await _post_turtle(_at_least_turtle("120", "100"))
        assert await _ask(_ask_result_true()), \
            "Expected rdf:value true for 120 Mbps >= 100 Mbps"

    @_INFERENCE_XFAIL
    async def test_at_boundary_100mbps_fires_result_true(self):
        await _post_turtle(_at_least_turtle("100", "100"))
        assert await _ask(_ask_result_true()), \
            "Expected rdf:value true for 100 Mbps >= 100 Mbps (boundary)"

    async def test_out_of_range_80mbps_no_result(self):
        await _post_turtle(_at_least_turtle("80", "100"))
        assert not await _ask(_ask_result_true()), \
            "Expected no rdf:value true for 80 Mbps < 100 Mbps"


# ── Upstream bandwidth ≥ 20 Mbps ─────────────────────────────────────────────

class TestUpstreamBandwidth:
    """quan:greaterOrEqual 20 Mbps → rules form: quan:quanatLeast."""

    @_INFERENCE_XFAIL
    async def test_in_range_25mbps_fires_result_true(self):
        await _post_turtle(_at_least_turtle("25", "20"))
        assert await _ask(_ask_result_true()), \
            "Expected rdf:value true for 25 Mbps >= 20 Mbps"

    @_INFERENCE_XFAIL
    async def test_at_boundary_20mbps_fires_result_true(self):
        await _post_turtle(_at_least_turtle("20", "20"))
        assert await _ask(_ask_result_true()), \
            "Expected rdf:value true for 20 Mbps >= 20 Mbps (boundary)"

    async def test_out_of_range_15mbps_no_result(self):
        await _post_turtle(_at_least_turtle("15", "20"))
        assert not await _ask(_ask_result_true()), \
            "Expected no rdf:value true for 15 Mbps < 20 Mbps"


# ── Latency < 25 ms ───────────────────────────────────────────────────────────

class TestLatency:
    """quan:smaller 25 ms → rules form: quan:quansmaller."""

    @_INFERENCE_XFAIL
    async def test_in_range_10ms_fires_result_true(self):
        await _post_turtle(_smaller_turtle("10", "25"))
        assert await _ask(_ask_result_true()), \
            "Expected rdf:value true for 10 ms < 25 ms"

    async def test_out_of_range_30ms_no_result(self):
        await _post_turtle(_smaller_turtle("30", "25"))
        assert not await _ask(_ask_result_true()), \
            "Expected no rdf:value true for 30 ms >= 25 ms"

    async def test_at_boundary_25ms_no_result(self):
        # quan:quansmaller requires strictly less-than
        await _post_turtle(_smaller_turtle("25", "25"))
        assert not await _ask(_ask_result_true()), \
            "Expected no rdf:value true for 25 ms == 25 ms (strict less-than)"


# ── Jitter < 3 ms ─────────────────────────────────────────────────────────────

class TestJitter:
    """quan:smaller 3 ms → rules form: quan:quansmaller."""

    @_INFERENCE_XFAIL
    async def test_in_range_1ms_fires_result_true(self):
        await _post_turtle(_smaller_turtle("1", "3"))
        assert await _ask(_ask_result_true()), \
            "Expected rdf:value true for 1 ms < 3 ms"

    async def test_out_of_range_5ms_no_result(self):
        await _post_turtle(_smaller_turtle("5", "3"))
        assert not await _ask(_ask_result_true()), \
            "Expected no rdf:value true for 5 ms >= 3 ms"

    async def test_at_boundary_3ms_no_result(self):
        await _post_turtle(_smaller_turtle("3", "3"))
        assert not await _ask(_ask_result_true()), \
            "Expected no rdf:value true for 3 ms == 3 ms (strict less-than)"


# ── Packet loss < 0.1 % ───────────────────────────────────────────────────────

class TestPacketLoss:
    """quan:smaller 0.1 percent → rules form: quan:quansmaller."""

    @_INFERENCE_XFAIL
    async def test_in_range_0_05pct_fires_result_true(self):
        await _post_turtle(_smaller_turtle("0.05", "0.1"))
        assert await _ask(_ask_result_true()), \
            "Expected rdf:value true for 0.05% < 0.1%"

    async def test_out_of_range_0_2pct_no_result(self):
        await _post_turtle(_smaller_turtle("0.2", "0.1"))
        assert not await _ask(_ask_result_true()), \
            "Expected no rdf:value true for 0.2% >= 0.1%"

    async def test_at_boundary_0_1pct_no_result(self):
        await _post_turtle(_smaller_turtle("0.1", "0.1"))
        assert not await _ask(_ask_result_true()), \
            "Expected no rdf:value true for 0.1% == 0.1% (strict less-than)"
