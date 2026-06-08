"""
Graph schema initialisation for the TMF921 Fuseki dataset.
Called once from FastAPI lifespan after the FusekiClient is open.
"""
import logging
import os
from pathlib import Path

from src.graph.namespaces import ONTOLOGY_GRAPH, RESOURCES_GRAPH
from src.graph.store import FusekiClient

logger = logging.getLogger(__name__)

ONTOLOGY_DIR  = Path("ontology")
_RESOURCE_DATA_DIR = os.getenv("RESOURCE_DATA_DIR", "")


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


async def load_resources(client: FusekiClient) -> None:
    """
    Load resource inventory TTL files into the resources named graph.

    Reads from the directory specified by the RESOURCE_DATA_DIR environment
    variable.  If the variable is unset or the directory is absent, startup
    continues without resource data (aggregation domain has no inventory).

    Access domain usage:
        RESOURCE_DATA_DIR=BBF_access
    """
    if not _RESOURCE_DATA_DIR:
        logger.info("RESOURCE_DATA_DIR not set — skipping resource inventory load")
        return

    resource_dir = Path(_RESOURCE_DATA_DIR)
    if not resource_dir.exists():
        logger.warning("Resource data directory not found: %s — skipping", resource_dir)
        return

    ttl_files = sorted(resource_dir.glob("*.ttl"))
    if not ttl_files:
        logger.warning("No .ttl files in %s — skipping resource load", resource_dir)
        return

    for ttl_file in ttl_files:
        content = ttl_file.read_text(encoding="utf-8")
        await client.gsp_post(str(RESOURCES_GRAPH), content)
        logger.info("Loaded resource data %s into resources graph", ttl_file.name)

    logger.info("Resource inventory loaded from %s (%d files)", resource_dir, len(ttl_files))


async def initialise_schema(client: FusekiClient) -> None:
    """
    Full schema initialisation sequence.
    Order: ensure dataset exists → load ontology TTL files → load resource inventory.
    """
    await create_dataset(client)
    await load_ontology(client)
    await load_resources(client)
    logger.info("Schema initialisation complete")
