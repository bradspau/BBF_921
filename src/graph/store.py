"""
Async Jena Fuseki SPARQL-over-HTTP client for the TMF921 API.

Python communicates with Fuseki over SPARQL 1.1 and the Graph Store Protocol
exclusively — no embedded RDF runtime runs in the application process.
"""
import os
import logging
import httpx

logger = logging.getLogger(__name__)

FUSEKI_BASE_URL: str = os.getenv("FUSEKI_BASE_URL", "http://localhost:3030")
FUSEKI_DATASET: str = os.getenv("FUSEKI_DATASET", "tmf921")


class FusekiClient:
    """
    Async wrapper around Fuseki SPARQL 1.1 endpoints and the Graph Store Protocol.

    Manages a single httpx.AsyncClient lifecycle via async context manager.
    Endpoints used:
      - SPARQL query:  POST /{dataset}/sparql  (form: query=…)
      - SPARQL update: POST /{dataset}/update  (form: update=…)
      - GSP read:      GET  /{dataset}/data?graph=<uri>
      - GSP write:     PUT/POST /{dataset}/data?graph=<uri>
      - GSP delete:    DELETE /{dataset}/data?graph=<uri>
      - Admin/health:  GET /$/ping
      - Create DS:     POST /$/datasets  (form: dbName=…&dbType=tdb2)
    """

    def __init__(
        self,
        base_url: str = FUSEKI_BASE_URL,
        dataset: str = FUSEKI_DATASET,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._dataset = dataset
        self._http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "FusekiClient":
        self._http = httpx.AsyncClient(base_url=self._base_url, timeout=30.0)
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    def _assert_open(self) -> httpx.AsyncClient:
        if self._http is None:
            raise RuntimeError("FusekiClient not open — use 'async with FusekiClient()'")
        return self._http

    # ── SPARQL query ──────────────────────────────────────────────────────────

    async def query(self, sparql: str) -> list[dict]:
        """Execute a SPARQL SELECT; return list of binding dicts."""
        http = self._assert_open()
        resp = await http.post(
            f"/{self._dataset}/sparql",
            data={"query": sparql},
            headers={"Accept": "application/sparql-results+json"},
        )
        resp.raise_for_status()
        return resp.json()["results"]["bindings"]

    async def ask(self, sparql: str) -> bool:
        """Execute a SPARQL ASK; return boolean."""
        http = self._assert_open()
        resp = await http.post(
            f"/{self._dataset}/sparql",
            data={"query": sparql},
            headers={"Accept": "application/sparql-results+json"},
        )
        resp.raise_for_status()
        return bool(resp.json()["boolean"])

    # ── SPARQL update ─────────────────────────────────────────────────────────

    async def update(self, sparql: str) -> None:
        """Execute a SPARQL UPDATE (INSERT DATA / DELETE / DROP)."""
        http = self._assert_open()
        resp = await http.post(
            f"/{self._dataset}/update",
            data={"update": sparql},
        )
        resp.raise_for_status()

    # ── Graph Store Protocol ──────────────────────────────────────────────────

    async def gsp_put(self, graph_uri: str, turtle: str) -> None:
        """Replace a named graph entirely with the given Turtle content."""
        http = self._assert_open()
        resp = await http.put(
            f"/{self._dataset}/data",
            params={"graph": graph_uri},
            content=turtle.encode(),
            headers={"Content-Type": "text/turtle"},
        )
        resp.raise_for_status()

    async def gsp_post(self, graph_uri: str, turtle: str) -> None:
        """Merge Turtle triples into a named graph (additive)."""
        http = self._assert_open()
        resp = await http.post(
            f"/{self._dataset}/data",
            params={"graph": graph_uri},
            content=turtle.encode(),
            headers={"Content-Type": "text/turtle"},
        )
        resp.raise_for_status()

    async def gsp_get(self, graph_uri: str) -> str:
        """Return a named graph serialised as Turtle."""
        http = self._assert_open()
        resp = await http.get(
            f"/{self._dataset}/data",
            params={"graph": graph_uri},
            headers={"Accept": "text/turtle"},
        )
        resp.raise_for_status()
        return resp.text

    async def gsp_delete(self, graph_uri: str) -> None:
        """Delete a named graph."""
        http = self._assert_open()
        resp = await http.delete(
            f"/{self._dataset}/data",
            params={"graph": graph_uri},
        )
        resp.raise_for_status()

    # ── Admin ─────────────────────────────────────────────────────────────────

    async def ensure_dataset(self) -> None:
        """Create the Fuseki dataset if it does not exist (idempotent)."""
        http = self._assert_open()
        resp = await http.post(
            "/$/datasets",
            data={"dbName": self._dataset, "dbType": "tdb2"},
        )
        # 200/201 = created; 409 = already exists; 401 = admin auth required
        # (dataset was pre-created via --loc at startup, proceed regardless)
        if resp.status_code not in (200, 201, 409, 401):
            resp.raise_for_status()
        logger.debug("Dataset ready: %s", self._dataset)

    async def health(self) -> bool:
        """Return True if Fuseki answers the /$/ping endpoint."""
        http = self._assert_open()
        try:
            resp = await http.get("/$/ping")
            return resp.status_code == 200
        except httpx.HTTPError:
            return False


# ── Module-level singleton ────────────────────────────────────────────────────

_client: FusekiClient | None = None


def get_client() -> FusekiClient:
    """Return the open module-level FusekiClient. Raises if not initialised."""
    if _client is None:
        raise RuntimeError(
            "FusekiClient not initialised — call await init_client() from app lifespan"
        )
    return _client


async def init_client(
    base_url: str = FUSEKI_BASE_URL,
    dataset: str = FUSEKI_DATASET,
) -> None:
    """Open the module-level FusekiClient. Call once from FastAPI lifespan startup."""
    global _client
    _client = FusekiClient(base_url, dataset)
    await _client.__aenter__()
    logger.info("FusekiClient ready: %s / %s", base_url, dataset)


async def close_client() -> None:
    """Close and discard the module-level FusekiClient."""
    global _client
    if _client is not None:
        await _client.__aexit__(None, None, None)
        _client = None
        logger.info("FusekiClient closed")
