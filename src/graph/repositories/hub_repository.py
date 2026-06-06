"""
SPARQL-backed repository for Hub (event subscription) resources.

All Hub triples live in a single shared named graph: tmf:hubs.
"""
from __future__ import annotations

from typing import Any

from src.graph.namespaces import HUBS_GRAPH
from src.graph.repositories.base_repository import PREFIXES, BaseRepository

_HUBS_GRAPH = str(HUBS_GRAPH)
_HUB_BASE = "http://tmforum.org/api/v5/hubs/"

_GET_SELECT = "?id ?callback ?query ?href"
_GET_WHERE = """\
        <{uri}> rdf:type tmf:Hub .
        OPTIONAL {{ <{uri}> tmf:id ?id }}
        OPTIONAL {{ <{uri}> tmf:href ?href }}
        OPTIONAL {{ <{uri}> tmf:callback ?callback }}
        OPTIONAL {{ <{uri}> tmf:query ?query }}"""


class HubRepository(BaseRepository):

    def _hub_uri(self, hub_id: str) -> str:
        return f"{_HUB_BASE}{hub_id}"

    def _bindings_to_hub(self, row: dict) -> dict:
        return {
            "id": self._v(row, "id"),
            "href": self._v(row, "href"),
            "callback": self._v(row, "callback"),
            "query": self._v(row, "query"),
        }

    async def create(self, data: dict) -> dict:
        """
        Add a Hub subscription triple set to the shared hubs graph.
        Caller must supply: id, href, callback. query is optional.
        """
        hub_id = data["id"]
        hub_uri = self._hub_uri(hub_id)
        e = self._esc
        lines: list[str] = [
            f"        <{hub_uri}> rdf:type tmf:Hub .",
            f"        <{hub_uri}> tmf:id \"{e(hub_id)}\" .",
            f"        <{hub_uri}> tmf:href \"{e(data['href'])}\" .",
            f"        <{hub_uri}> tmf:callback \"{e(data['callback'])}\" .",
        ]
        if data.get("query"):
            lines.append(f"        <{hub_uri}> tmf:query \"{e(data['query'])}\" .")

        triples = "\n".join(lines)
        sparql = (
            f"{PREFIXES}\n"
            f"INSERT DATA {{\n"
            f"    GRAPH <{_HUBS_GRAPH}> {{\n{triples}\n    }}\n"
            f"}}"
        )
        await self._client.update(sparql)
        return data

    async def get_by_id(self, hub_id: str) -> dict[str, Any] | None:
        """Return a Hub dict, or None if not found."""
        hub_uri = self._hub_uri(hub_id)
        patterns = _GET_WHERE.format(uri=hub_uri)
        sparql = (
            f"{PREFIXES}\n"
            f"SELECT {_GET_SELECT}\n"
            f"WHERE {{\n"
            f"    GRAPH <{_HUBS_GRAPH}> {{\n{patterns}\n    }}\n"
            f"}}"
        )
        rows = await self._client.query(sparql)
        if not rows:
            return None
        return self._bindings_to_hub(rows[0])

    async def list_all(self) -> list[dict[str, Any]]:
        """Return all Hub subscriptions (used for notification fan-out)."""
        sparql = (
            f"{PREFIXES}\n"
            f"SELECT {_GET_SELECT}\n"
            f"WHERE {{\n"
            f"    GRAPH <{_HUBS_GRAPH}> {{\n"
            f"        ?hubUri rdf:type tmf:Hub .\n"
            f"        OPTIONAL {{ ?hubUri tmf:id ?id }}\n"
            f"        OPTIONAL {{ ?hubUri tmf:href ?href }}\n"
            f"        OPTIONAL {{ ?hubUri tmf:callback ?callback }}\n"
            f"        OPTIONAL {{ ?hubUri tmf:query ?query }}\n"
            f"    }}\n"
            f"}}"
        )
        rows = await self._client.query(sparql)
        return [self._bindings_to_hub(r) for r in rows]

    async def delete(self, hub_id: str) -> bool:
        """Delete a Hub subscription. Returns True if it existed."""
        hub_uri = self._hub_uri(hub_id)
        ask = (
            f"{PREFIXES}\n"
            f"ASK {{ GRAPH <{_HUBS_GRAPH}> {{ <{hub_uri}> rdf:type tmf:Hub }} }}"
        )
        exists = await self._client.ask(ask)
        if not exists:
            return False
        sparql = (
            f"{PREFIXES}\n"
            f"DELETE WHERE {{\n"
            f"    GRAPH <{_HUBS_GRAPH}> {{\n"
            f"        <{hub_uri}> ?p ?o .\n"
            f"    }}\n"
            f"}}"
        )
        await self._client.update(sparql)
        return True
