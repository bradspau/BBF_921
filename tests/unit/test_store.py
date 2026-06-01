"""
Unit tests for the Fuseki graph layer — Phase 1.

All HTTP calls are intercepted by respx; no live Fuseki instance required.
Coverage target: ≥80% of src/graph/.
"""
import pytest
import respx
import httpx

from src.graph import namespaces as ns
from src.graph import nodes
from src.graph import store
from src.graph.store import FusekiClient, get_client, init_client, close_client
from src.graph.schema_init import create_dataset, load_ontology, initialise_schema

FUSEKI = "http://localhost:3030"
DATASET = "tmf921"

SPARQL_RESULTS = {
    "results": {
        "bindings": [
            {"id": {"type": "literal", "value": "abc-123"}}
        ]
    }
}

ASK_TRUE = {"boolean": True}
ASK_FALSE = {"boolean": False}


# ── Namespaces ────────────────────────────────────────────────────────────────

class TestNamespaces:
    def test_tmf_namespace(self):
        uri = str(ns.TMF.Intent)
        assert uri == "http://tmforum.org/api/v5/Intent"

    def test_ontology_graph_uri(self):
        assert str(ns.ONTOLOGY_GRAPH) == "http://tmforum.org/api/v5/ontology"

    def test_hubs_graph_uri(self):
        assert str(ns.HUBS_GRAPH) == "http://tmforum.org/api/v5/hubs"

    def test_class_constants_are_uriref(self):
        from rdflib import URIRef
        assert isinstance(ns.CLASS_INTENT, URIRef)
        assert isinstance(ns.CLASS_PROBE_INTENT, URIRef)

    def test_predicate_constants(self):
        assert str(ns.PRED_ID) == "http://tmforum.org/api/v5/id"
        assert str(ns.PRED_LIFECYCLE_STATUS) == "http://tmforum.org/api/v5/lifecycleStatus"


# ── Nodes ─────────────────────────────────────────────────────────────────────

class TestNodes:
    def test_intent_graph_uri(self):
        uri = nodes.intent_graph_uri("abc-123")
        assert str(uri) == "http://tmforum.org/api/v5/intents/abc-123"

    def test_report_graph_uri(self):
        uri = nodes.report_graph_uri("rpt-456")
        assert str(uri) == "http://tmforum.org/api/v5/reports/rpt-456"

    def test_spec_graph_uri(self):
        uri = nodes.spec_graph_uri("spec-789")
        assert str(uri) == "http://tmforum.org/api/v5/intentSpecifications/spec-789"

    def test_audit_graph_uri(self):
        uri = nodes.audit_graph_uri("abc-123")
        assert str(uri) == "http://tmforum.org/api/v5/audit/abc-123"

    def test_eval_graph_uri(self):
        uri = nodes.eval_graph_uri("abc-123")
        assert str(uri) == "http://tmforum.org/api/v5/eval/abc-123"

    def test_intent_node_equals_intent_graph(self):
        assert nodes.intent_node("x") == nodes.intent_graph_uri("x")

    def test_report_node_equals_report_graph(self):
        assert nodes.report_node("y") == nodes.report_graph_uri("y")

    def test_spec_node_equals_spec_graph(self):
        assert nodes.spec_node("z") == nodes.spec_graph_uri("z")


# ── FusekiClient lifecycle ────────────────────────────────────────────────────

class TestFusekiClientLifecycle:
    async def test_context_manager_opens_and_closes(self):
        async with FusekiClient(FUSEKI, DATASET) as client:
            assert client._http is not None
        assert client._http is None

    async def test_assert_open_raises_when_not_entered(self):
        client = FusekiClient(FUSEKI, DATASET)
        with pytest.raises(RuntimeError, match="not open"):
            client._assert_open()

    async def test_double_exit_is_safe(self):
        client = FusekiClient(FUSEKI, DATASET)
        await client.__aenter__()
        await client.__aexit__(None, None, None)
        await client.__aexit__(None, None, None)  # second exit must not raise


# ── SPARQL query ──────────────────────────────────────────────────────────────

class TestSparqlQuery:
    @respx.mock
    async def test_query_returns_bindings(self):
        respx.post(f"{FUSEKI}/{DATASET}/sparql").mock(
            return_value=httpx.Response(200, json=SPARQL_RESULTS)
        )
        async with FusekiClient(FUSEKI, DATASET) as client:
            result = await client.query("SELECT * WHERE { ?s ?p ?o } LIMIT 1")
        assert result == SPARQL_RESULTS["results"]["bindings"]

    @respx.mock
    async def test_query_raises_on_http_error(self):
        respx.post(f"{FUSEKI}/{DATASET}/sparql").mock(
            return_value=httpx.Response(500)
        )
        async with FusekiClient(FUSEKI, DATASET) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await client.query("SELECT * WHERE { ?s ?p ?o }")

    @respx.mock
    async def test_ask_returns_true(self):
        respx.post(f"{FUSEKI}/{DATASET}/sparql").mock(
            return_value=httpx.Response(200, json=ASK_TRUE)
        )
        async with FusekiClient(FUSEKI, DATASET) as client:
            result = await client.ask("ASK { ?s ?p ?o }")
        assert result is True

    @respx.mock
    async def test_ask_returns_false(self):
        respx.post(f"{FUSEKI}/{DATASET}/sparql").mock(
            return_value=httpx.Response(200, json=ASK_FALSE)
        )
        async with FusekiClient(FUSEKI, DATASET) as client:
            result = await client.ask("ASK { <urn:x> <urn:y> <urn:z> }")
        assert result is False


# ── SPARQL update ─────────────────────────────────────────────────────────────

class TestSparqlUpdate:
    @respx.mock
    async def test_update_succeeds(self):
        route = respx.post(f"{FUSEKI}/{DATASET}/update").mock(
            return_value=httpx.Response(200)
        )
        async with FusekiClient(FUSEKI, DATASET) as client:
            await client.update("INSERT DATA { <urn:s> <urn:p> <urn:o> }")
        assert route.called

    @respx.mock
    async def test_update_raises_on_bad_sparql(self):
        respx.post(f"{FUSEKI}/{DATASET}/update").mock(
            return_value=httpx.Response(400)
        )
        async with FusekiClient(FUSEKI, DATASET) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await client.update("NOT VALID SPARQL")


# ── Graph Store Protocol ──────────────────────────────────────────────────────

class TestGraphStoreProtocol:
    GRAPH = "http://tmforum.org/api/v5/intents/test-id"
    TURTLE = "@prefix tmf: <http://tmforum.org/api/v5/> . <urn:x> a tmf:Intent ."

    @respx.mock
    async def test_gsp_put_sends_put_request(self):
        route = respx.put(f"{FUSEKI}/{DATASET}/data").mock(
            return_value=httpx.Response(201)
        )
        async with FusekiClient(FUSEKI, DATASET) as client:
            await client.gsp_put(self.GRAPH, self.TURTLE)
        assert route.called

    @respx.mock
    async def test_gsp_post_sends_post_request(self):
        route = respx.post(f"{FUSEKI}/{DATASET}/data").mock(
            return_value=httpx.Response(200)
        )
        async with FusekiClient(FUSEKI, DATASET) as client:
            await client.gsp_post(self.GRAPH, self.TURTLE)
        assert route.called

    @respx.mock
    async def test_gsp_get_returns_turtle(self):
        respx.get(f"{FUSEKI}/{DATASET}/data").mock(
            return_value=httpx.Response(200, text=self.TURTLE)
        )
        async with FusekiClient(FUSEKI, DATASET) as client:
            text = await client.gsp_get(self.GRAPH)
        assert text == self.TURTLE

    @respx.mock
    async def test_gsp_delete_sends_delete_request(self):
        route = respx.delete(f"{FUSEKI}/{DATASET}/data").mock(
            return_value=httpx.Response(200)
        )
        async with FusekiClient(FUSEKI, DATASET) as client:
            await client.gsp_delete(self.GRAPH)
        assert route.called

    @respx.mock
    async def test_gsp_put_raises_on_error(self):
        respx.put(f"{FUSEKI}/{DATASET}/data").mock(
            return_value=httpx.Response(404)
        )
        async with FusekiClient(FUSEKI, DATASET) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await client.gsp_put(self.GRAPH, self.TURTLE)


# ── Admin / health ────────────────────────────────────────────────────────────

class TestAdminAndHealth:
    @respx.mock
    async def test_health_returns_true_when_up(self):
        respx.get(f"{FUSEKI}/$/ping").mock(return_value=httpx.Response(200))
        async with FusekiClient(FUSEKI, DATASET) as client:
            assert await client.health() is True

    @respx.mock
    async def test_health_returns_false_on_connect_error(self):
        respx.get(f"{FUSEKI}/$/ping").mock(side_effect=httpx.ConnectError("refused"))
        async with FusekiClient(FUSEKI, DATASET) as client:
            assert await client.health() is False

    @respx.mock
    async def test_health_returns_false_on_non_200(self):
        respx.get(f"{FUSEKI}/$/ping").mock(return_value=httpx.Response(503))
        async with FusekiClient(FUSEKI, DATASET) as client:
            assert await client.health() is False

    @respx.mock
    async def test_ensure_dataset_201_succeeds(self):
        route = respx.post(f"{FUSEKI}/$/datasets").mock(
            return_value=httpx.Response(201)
        )
        async with FusekiClient(FUSEKI, DATASET) as client:
            await client.ensure_dataset()
        assert route.called

    @respx.mock
    async def test_ensure_dataset_409_is_ok(self):
        respx.post(f"{FUSEKI}/$/datasets").mock(
            return_value=httpx.Response(409)
        )
        async with FusekiClient(FUSEKI, DATASET) as client:
            await client.ensure_dataset()  # must not raise

    @respx.mock
    async def test_ensure_dataset_500_raises(self):
        respx.post(f"{FUSEKI}/$/datasets").mock(
            return_value=httpx.Response(500)
        )
        async with FusekiClient(FUSEKI, DATASET) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await client.ensure_dataset()


# ── Module singleton ──────────────────────────────────────────────────────────

class TestModuleSingleton:
    async def test_get_client_raises_before_init(self):
        # Ensure clean state
        store._client = None
        with pytest.raises(RuntimeError, match="not initialised"):
            get_client()

    @respx.mock
    async def test_init_and_close_client(self):
        respx.get(f"{FUSEKI}/$/ping").mock(return_value=httpx.Response(200))
        store._client = None
        await init_client(FUSEKI, DATASET)
        client = get_client()
        assert client is not None
        assert await client.health() is True
        await close_client()
        assert store._client is None

    async def test_close_client_noop_when_not_initialised(self):
        store._client = None
        await close_client()  # must not raise


# ── Schema init ───────────────────────────────────────────────────────────────

class TestSchemaInit:
    @respx.mock
    async def test_create_dataset_calls_ensure(self):
        route = respx.post(f"{FUSEKI}/$/datasets").mock(
            return_value=httpx.Response(201)
        )
        async with FusekiClient(FUSEKI, DATASET) as client:
            await create_dataset(client)
        assert route.called

    @respx.mock
    async def test_load_ontology_skips_missing_dir(self, tmp_path, monkeypatch):
        import src.graph.schema_init as si
        monkeypatch.setattr(si, "ONTOLOGY_DIR", tmp_path / "nonexistent")
        async with FusekiClient(FUSEKI, DATASET) as client:
            await load_ontology(client)  # must not raise

    @respx.mock
    async def test_load_ontology_skips_empty_dir(self, tmp_path, monkeypatch):
        import src.graph.schema_init as si
        monkeypatch.setattr(si, "ONTOLOGY_DIR", tmp_path)
        async with FusekiClient(FUSEKI, DATASET) as client:
            await load_ontology(client)  # must not raise

    @respx.mock
    async def test_load_ontology_posts_ttl_files(self, tmp_path, monkeypatch):
        import src.graph.schema_init as si
        (tmp_path / "test.ttl").write_text("@prefix ex: <urn:ex:> . ex:a a ex:B .")
        monkeypatch.setattr(si, "ONTOLOGY_DIR", tmp_path)
        route = respx.post(f"{FUSEKI}/{DATASET}/data").mock(
            return_value=httpx.Response(200)
        )
        async with FusekiClient(FUSEKI, DATASET) as client:
            await load_ontology(client)
        assert route.called

    @respx.mock
    async def test_initialise_schema_runs_full_sequence(self, tmp_path, monkeypatch):
        import src.graph.schema_init as si
        monkeypatch.setattr(si, "ONTOLOGY_DIR", tmp_path)  # empty → skip ontology
        respx.post(f"{FUSEKI}/$/datasets").mock(return_value=httpx.Response(201))
        async with FusekiClient(FUSEKI, DATASET) as client:
            await initialise_schema(client)  # must not raise
