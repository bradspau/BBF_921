"""
SPARQL-backed repository for IntentSpecification resources.

Each spec lives in its own named graph: tmf:intentSpecifications/{spec_id}.
"""
from __future__ import annotations

from typing import Any

from src.graph.nodes import spec_graph_uri, spec_node
from src.graph.repositories.base_repository import PREFIXES, BaseRepository

_GET_SELECT = """\
    ?id ?href ?name ?type ?baseType ?schemaLocation
    ?lifecycleStatus ?created ?modified ?description ?version ?isBundle"""

_GET_WHERE = """\
        <{uri}> rdf:type tmf:IntentSpecification .
        OPTIONAL {{ <{uri}> tmf:id ?id }}
        OPTIONAL {{ <{uri}> tmf:href ?href }}
        OPTIONAL {{ <{uri}> tmf:name ?name }}
        OPTIONAL {{ <{uri}> rdf:type ?type }}
        OPTIONAL {{ <{uri}> tmf:baseType ?baseType }}
        OPTIONAL {{ <{uri}> tmf:schemaLocation ?schemaLocation }}
        OPTIONAL {{ <{uri}> tmf:lifecycleStatus ?lifecycleStatus }}
        OPTIONAL {{ <{uri}> dcterms:created ?created }}
        OPTIONAL {{ <{uri}> dcterms:modified ?modified }}
        OPTIONAL {{ <{uri}> tmf:description ?description }}
        OPTIONAL {{ <{uri}> tmf:version ?version }}
        OPTIONAL {{ <{uri}> tmf:isBundle ?isBundle }}"""

_PATCHABLE: dict[str, tuple[str, bool, str]] = {
    "name":            ("tmf:name",            False, ""),
    "description":     ("tmf:description",     False, ""),
    "lifecycleStatus": ("tmf:lifecycleStatus", False, ""),
    "version":         ("tmf:version",         False, ""),
}


class IntentSpecRepository(BaseRepository):

    def _bindings_to_spec(self, row: dict) -> dict:
        type_uri = self._v(row, "type") or ""
        return {
            "id": self._v(row, "id"),
            "href": self._v(row, "href"),
            "@type": self._local(type_uri) if type_uri else "IntentSpecification",
            "@baseType": self._v(row, "baseType"),
            "@schemaLocation": self._v(row, "schemaLocation"),
            "name": self._v(row, "name"),
            "description": self._v(row, "description"),
            "lifecycleStatus": self._v(row, "lifecycleStatus"),
            "lastUpdate": self._v(row, "modified"),
            "version": self._v(row, "version"),
            "isBundle": self._vbool(row, "isBundle"),
        }

    async def create(self, data: dict) -> dict:
        """
        Persist a new IntentSpecification.
        Caller must supply: id, href, @type, name, lastUpdate.
        """
        spec_id = data["id"]
        graph_uri = str(spec_graph_uri(spec_id))
        uri = str(spec_node(spec_id))
        e = self._esc

        lines: list[str] = [
            f"        <{uri}> rdf:type tmf:IntentSpecification .",
            f"        <{uri}> tmf:id \"{e(data['id'])}\" .",
            f"        <{uri}> tmf:href \"{e(data['href'])}\" .",
            f"        <{uri}> tmf:name \"{e(data['name'])}\" .",
            f"        <{uri}> dcterms:modified \"{e(data['lastUpdate'])}\"^^xsd:dateTime .",
        ]
        for field, pred in [
            ("@baseType",       "tmf:baseType"),
            ("@schemaLocation", "tmf:schemaLocation"),
            ("description",     "tmf:description"),
            ("version",         "tmf:version"),
            ("lifecycleStatus", "tmf:lifecycleStatus"),
        ]:
            if data.get(field):
                lines.append(f"        <{uri}> {pred} \"{e(str(data[field]))}\" .")
        if data.get("isBundle") is not None:
            val = "true" if data["isBundle"] else "false"
            lines.append(f"        <{uri}> tmf:isBundle \"{val}\"^^xsd:boolean .")

        triples = "\n".join(lines)
        sparql = (
            f"{PREFIXES}\n"
            f"INSERT DATA {{\n    GRAPH <{graph_uri}> {{\n{triples}\n    }}\n}}"
        )
        await self._client.update(sparql)
        return data

    async def get_by_id(self, spec_id: str) -> dict[str, Any] | None:
        """Return the IntentSpecification dict, or None if not found."""
        graph_uri = str(spec_graph_uri(spec_id))
        uri = str(spec_node(spec_id))
        patterns = _GET_WHERE.format(uri=uri)
        sparql = (
            f"{PREFIXES}\n"
            f"SELECT {_GET_SELECT}\n"
            f"WHERE {{\n    GRAPH <{graph_uri}> {{\n{patterns}\n    }}\n}}"
        )
        rows = await self._client.query(sparql)
        if not rows:
            return None
        return self._bindings_to_spec(rows[0])

    async def list(
        self,
        limit: int = 20,
        offset: int = 0,
        filters: dict | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """Return (page_items, total_count) for all intent specifications."""
        filter_clauses = self._build_filter_clauses(filters or {})

        count_sparql = (
            f"{PREFIXES}\n"
            f"SELECT (COUNT(DISTINCT ?specUri) AS ?count)\n"
            f"WHERE {{\n"
            f"    GRAPH ?g {{\n"
            f"        ?specUri rdf:type tmf:IntentSpecification .\n"
            f"{filter_clauses}"
            f"    }}\n"
            f"}}"
        )
        count_rows = await self._client.query(count_sparql)
        total = int(self._v(count_rows[0], "count") or "0") if count_rows else 0
        if total == 0:
            return [], 0

        list_sparql = (
            f"{PREFIXES}\n"
            f"SELECT {_GET_SELECT}\n"
            f"WHERE {{\n"
            f"    GRAPH ?g {{\n"
            f"        ?specUri rdf:type tmf:IntentSpecification .\n"
            f"        OPTIONAL {{ ?specUri tmf:id ?id }}\n"
            f"        OPTIONAL {{ ?specUri tmf:href ?href }}\n"
            f"        OPTIONAL {{ ?specUri tmf:name ?name }}\n"
            f"        OPTIONAL {{ ?specUri rdf:type ?type }}\n"
            f"        OPTIONAL {{ ?specUri tmf:baseType ?baseType }}\n"
            f"        OPTIONAL {{ ?specUri tmf:schemaLocation ?schemaLocation }}\n"
            f"        OPTIONAL {{ ?specUri tmf:lifecycleStatus ?lifecycleStatus }}\n"
            f"        OPTIONAL {{ ?specUri dcterms:created ?created }}\n"
            f"        OPTIONAL {{ ?specUri dcterms:modified ?modified }}\n"
            f"        OPTIONAL {{ ?specUri tmf:description ?description }}\n"
            f"        OPTIONAL {{ ?specUri tmf:version ?version }}\n"
            f"        OPTIONAL {{ ?specUri tmf:isBundle ?isBundle }}\n"
            f"{filter_clauses}"
            f"    }}\n"
            f"}}\n"
            f"ORDER BY ?id\n"
            f"LIMIT {limit} OFFSET {offset}"
        )
        rows = await self._client.query(list_sparql)
        return [self._bindings_to_spec(r) for r in rows], total

    async def update(
        self,
        spec_id: str,
        updates: dict,
        modified_at: str,
    ) -> dict[str, Any] | None:
        """
        Apply a partial update using DELETE/INSERT WHERE.
        Returns the updated spec dict, or None if it does not exist.
        """
        graph_uri = str(spec_graph_uri(spec_id))
        uri = str(spec_node(spec_id))
        e = self._esc

        delete_lines = [f"        <{uri}> dcterms:modified ?oldMod ."]
        insert_lines = [
            f"        <{uri}> dcterms:modified \"{e(modified_at)}\"^^xsd:dateTime ."
        ]
        where_lines = [f"        OPTIONAL {{ <{uri}> dcterms:modified ?oldMod }}"]

        for field, (pred, typed, dtype) in _PATCHABLE.items():
            if field not in updates:
                continue
            var = field.replace("@", "")
            val = str(updates[field])
            delete_lines.append(f"        <{uri}> {pred} ?old_{var} .")
            if typed:  # pragma: no cover — _PATCHABLE has no typed fields currently
                insert_lines.append(
                    f"        <{uri}> {pred} \"{e(val)}\"^^{dtype} ."
                )
            else:
                insert_lines.append(f"        <{uri}> {pred} \"{e(val)}\" .")
            where_lines.append(
                f"        OPTIONAL {{ <{uri}> {pred} ?old_{var} }}"
            )

        sparql = (
            f"{PREFIXES}\n"
            f"DELETE {{\n    GRAPH <{graph_uri}> {{\n"
            + "\n".join(delete_lines) + "\n"
            f"    }}\n}}\n"
            f"INSERT {{\n    GRAPH <{graph_uri}> {{\n"
            + "\n".join(insert_lines) + "\n"
            f"    }}\n}}\n"
            f"WHERE {{\n    GRAPH <{graph_uri}> {{\n"
            + "\n".join(where_lines) + "\n"
            "    }}\n}}"
        )
        await self._client.update(sparql)
        return await self.get_by_id(spec_id)

    async def delete(self, spec_id: str) -> bool:
        """Drop the spec named graph. Returns True if it existed."""
        graph_uri = str(spec_graph_uri(spec_id))
        uri = str(spec_node(spec_id))
        ask = (
            f"{PREFIXES}\n"
            f"ASK {{ GRAPH <{graph_uri}> {{ <{uri}> rdf:type ?t }} }}"
        )
        exists = await self._client.ask(ask)
        if not exists:
            return False
        await self._client.update(f"DROP GRAPH <{graph_uri}>")
        return True

    def _build_filter_clauses(self, filters: dict) -> str:
        lines: list[str] = []
        pred_map = {
            "lifecycleStatus": "tmf:lifecycleStatus",
            "name":            "tmf:name",
        }
        for field, pred in pred_map.items():
            if field in filters:
                lines.append(
                    f"        ?specUri {pred} \"{self._esc(str(filters[field]))}\" .\n"
                )
        return "".join(lines)
