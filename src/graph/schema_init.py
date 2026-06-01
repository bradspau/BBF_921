"""
Graph schema initialisation for the TMF921 Fuseki dataset.
Called once from FastAPI lifespan after the FusekiClient is open.
"""
import logging
from pathlib import Path

from src.graph.namespaces import ONTOLOGY_GRAPH
from src.graph.store import FusekiClient

logger = logging.getLogger(__name__)

ONTOLOGY_DIR = Path("ontology")


async def create_dataset(client: FusekiClient) -> None:
    """Ensure the Fuseki dataset exists (idempotent)."""
    await client.ensure_dataset()
    logger.info("Fuseki dataset ready: %s", client._dataset)


async def load_ontology(client: FusekiClient) -> None:
    """
    Merge all .ttl files from ontology/ into the read-only ontology named graph.
    Skips silently if the directory is absent or empty.
    """
    if not ONTOLOGY_DIR.exists():
        logger.warning("Ontology directory not found: %s — skipping", ONTOLOGY_DIR)
        return

    ttl_files = sorted(ONTOLOGY_DIR.glob("*.ttl"))
    if not ttl_files:
        logger.warning("No .ttl files in %s — skipping ontology load", ONTOLOGY_DIR)
        return

    for ttl_file in ttl_files:
        content = ttl_file.read_text(encoding="utf-8")
        await client.gsp_post(str(ONTOLOGY_GRAPH), content)
        logger.info("Loaded %s into ontology graph", ttl_file.name)


async def initialise_schema(client: FusekiClient) -> None:
    """
    Full schema initialisation sequence.
    Order: ensure dataset exists → load ontology TTL files.
    """
    await create_dataset(client)
    await load_ontology(client)
    logger.info("Schema initialisation complete")
