"""
SPARQL-backed repository for Intent and ProbeIntent resources.

All persistence uses the Phase 1 FusekiClient:
  - create  → INSERT DATA into a per-Intent named graph
  - get     → SELECT … WHERE { GRAPH <g> { … } }
  - list    → COUNT(*) + SELECT LIMIT/OFFSET across all intent graphs
  - update  → DELETE/INSERT WHERE for changed predicates only
  - delete  → DROP GRAPH <g>

expressionValue is stored as an opaque xsd:string literal.
"""
from __future__ import annotations

from typing import Any

from src.graph.nodes import intent_graph_uri, intent_node, audit_graph_uri
from src.graph.repositories.base_repository import PREFIXES, BaseRepository

_GET_SELECT = """\
    ?id ?href ?name ?type ?baseType ?schemaLocation
    ?lifecycleStatus ?statusChangeDate ?created ?modified
    ?description ?priority ?context ?version ?isBundle
    ?exprType ?exprIri ?exprValue"""

_GET_WHERE_PATTERNS = """\
        <{uri}> rdf:type ?type .
        OPTIONAL {{ <{uri}> tmf:id ?id }}
        OPTIONAL {{ <{uri}> tmf:href ?href }}
        OPTIONAL {{ <{uri}> tmf:name ?name }}
        OPTIONAL {{ <{uri}> tmf:lifecycleStatus ?lifecycleStatus }}
        OPTIONAL {{ <{uri}> tmf:statusChangeDate ?statusChangeDate }}
        OPTIONAL {{ <{uri}> dcterms:created ?created }}
        OPTIONAL {{ <{uri}> dcterms:modified ?modified }}
        OPTIONAL {{ <{uri}> tmf:baseType ?baseType }}
        OPTIONAL {{ <{uri}> tmf:schemaLocation ?schemaLocation }}
        OPTIONAL {{ <{uri}> tmf:description ?description }}
        OPTIONAL {{ <{uri}> tmf:priority ?priority }}
        OPTIONAL {{ <{uri}> tmf:context ?context }}
        OPTIONAL {{ <{uri}> tmf:version ?version }}
        OPTIONAL {{ <{uri}> tmf:isBundle ?isBundle }}
        OPTIONAL {{
            <{uri}> tmf:hasExpression ?exprUri .
            ?exprUri rdf:type ?exprType .
            OPTIONAL {{ ?exprUri tmf:expressionIri ?exprIri }}
            OPTIONAL {{ ?exprUri tmf:expressionValue ?exprValue }}
        }}"""

# Field → (SPARQL predicate, typed?, datatype)
_PATCHABLE: dict[str, tuple[str, bool, str]] = {
    "name":             ("tmf:name",             False, ""),
    "description":      ("tmf:description",      False, ""),
    "priority":         ("tmf:priority",         False, ""),
    "context":          ("tmf:context",          False, ""),
    "lifecycleStatus":  ("tmf:lifecycleStatus",  False, ""),
    "statusChangeDate": ("tmf:statusChangeDate", True,  "xsd:dateTime"),
    "version":          ("tmf:version",          False, ""),
}


class IntentRepository(BaseRepository):

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _expr_uri(self, intent_id: str) -> str:
        return f"http://tmforum.org/api/v5/intents/{intent_id}/expression"

    def _build_intent_triples(
        self, data: dict, intent_uri: str, expr_uri: str
    ) -> str:
        e = self._esc
        type_name = data["@type"]
        lines: list[str] = [
            f"        <{intent_uri}> rdf:type tmf:{type_name} .",
            f"        <{intent_uri}> tmf:id \"{e(data['id'])}\" .",
            f"        <{intent_uri}> tmf:href \"{e(data['href'])}\" .",
            f"        <{intent_uri}> tmf:name \"{e(data['name'])}\" .",
            f"        <{intent_uri}> dcterms:created \"{e(data['creationDate'])}\"^^xsd:dateTime .",
            f"        <{intent_uri}> dcterms:modified \"{e(data['lastUpdate'])}\"^^xsd:dateTime .",
            f"        <{intent_uri}> tmf:lifecycleStatus \"{e(data['lifecycleStatus'])}\" .",
        ]
        for field, pred in [
            ("@baseType",       "tmf:baseType"),
            ("@schemaLocation", "tmf:schemaLocation"),
            ("description",     "tmf:description"),
            ("priority",        "tmf:priority"),
            ("context",         "tmf:context"),
        ]:
            if data.get(field):
                lines.append(
                    f"        <{intent_uri}> {pred} \"{e(str(data[field]))}\" ."
                )
        if data.get("isBundle") is not None:
            val = "true" if data["isBundle"] else "false"
            lines.append(
                f"        <{intent_uri}> tmf:isBundle \"{val}\"^^xsd:boolean ."
            )
        if data.get("statusChangeDate"):
            lines.append(
                f"        <{intent_uri}> tmf:statusChangeDate "
                f"\"{e(data['statusChangeDate'])}\"^^xsd:dateTime ."
            )
        expr = data.get("expression")
        if expr:
            expr_type = expr.get("@type", "JsonLdExpression")
            lines += [
                f"        <{intent_uri}> tmf:hasExpression <{expr_uri}> .",
                f"        <{expr_uri}> rdf:type tmf:{e(expr_type)} .",
            ]
            if expr.get("iri"):
                lines.append(
                    f"        <{expr_uri}> tmf:expressionIri \"{e(expr['iri'])}\" ."
                )
            if expr.get("expressionValue") is not None:
                raw = self._ser_expr_value(expr["expressionValue"])
                lines.append(
                    f"        <{expr_uri}> tmf:expressionValue \"{e(raw)}\"^^xsd:string ."
                )
        return "\n".join(lines)

    def _bindings_to_intent(self, row: dict) -> dict:
        e_type_uri = self._v(row, "exprType")
        e_type = self._local(e_type_uri) if e_type_uri else None
        raw_expr_val = self._v(row, "exprValue")
        type_uri = self._v(row, "type") or ""
        return {
            "id": self._v(row, "id"),
            "href": self._v(row, "href"),
            "@type": self._local(type_uri),
            "@baseType": self._v(row, "baseType"),
            "@schemaLocation": self._v(row, "schemaLocation"),
            "name": self._v(row, "name"),
            "description": self._v(row, "description"),
            "lifecycleStatus": self._v(row, "lifecycleStatus"),
            "statusChangeDate": self._v(row, "statusChangeDate"),
            "creationDate": self._v(row, "created"),
            "lastUpdate": self._v(row, "modified"),
            "priority": self._v(row, "priority"),
            "context": self._v(row, "context"),
            "version": self._v(row, "version"),
            "isBundle": self._vbool(row, "isBundle"),
            "expression": {
                "@type": e_type,
                "iri": self._v(row, "exprIri"),
                "expressionValue": self._deser_expr_value(
                    e_type or "", raw_expr_val
                ),
            } if e_type else None,
        }

    # ── Public API ────────────────────────────────────────────────────────────

    async def create(self, data: dict) -> dict:
        """
        Persist a new Intent in its own named graph.
        Caller must supply: id, href, @type, name, creationDate, lastUpdate,
        lifecycleStatus, and expression.
        """
        intent_id = data["id"]
        graph_uri = str(intent_graph_uri(intent_id))
        intent_uri = str(intent_node(intent_id))
        expr_uri = self._expr_uri(intent_id)
        triples = self._build_intent_triples(data, intent_uri, expr_uri)
        sparql = f"{PREFIXES}\nINSERT DATA {{\n    GRAPH <{graph_uri}> {{\n{triples}\n    }}\n}}"
        await self._client.update(sparql)
        return data

    async def get_by_id(self, intent_id: str) -> dict[str, Any] | None:
        """Return the intent dict, or None if not found."""
        graph_uri = str(intent_graph_uri(intent_id))
        uri = str(intent_node(intent_id))
        patterns = _GET_WHERE_PATTERNS.format(uri=uri)
        sparql = (
            f"{PREFIXES}\n"
            f"SELECT {_GET_SELECT}\n"
            f"WHERE {{\n    GRAPH <{graph_uri}> {{\n{patterns}\n    }}\n}}"
        )
        rows = await self._client.query(sparql)
        if not rows:
            return None
        return self._bindings_to_intent(rows[0])

    async def list(
        self,
        limit: int = 20,
        offset: int = 0,
        filters: dict | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """
        Return (page_items, total_count) for all intents.
        filters: optional dict of {fieldName: value} for equality filtering.
        """
        filter_clauses = self._build_filter_clauses(filters or {})

        count_sparql = (
            f"{PREFIXES}\n"
            f"SELECT (COUNT(DISTINCT ?intentUri) AS ?count)\n"
            f"WHERE {{\n"
            f"    GRAPH ?g {{\n"
            f"        ?intentUri rdf:type ?type .\n"
            f"        VALUES ?type {{ tmf:Intent tmf:ProbeIntent }}\n"
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
            f"        ?intentUri rdf:type ?type .\n"
            f"        VALUES ?type {{ tmf:Intent tmf:ProbeIntent }}\n"
            f"        BIND(?intentUri AS ?uri__)\n"
            f"        OPTIONAL {{ ?intentUri tmf:id ?id }}\n"
            f"        OPTIONAL {{ ?intentUri tmf:href ?href }}\n"
            f"        OPTIONAL {{ ?intentUri tmf:name ?name }}\n"
            f"        OPTIONAL {{ ?intentUri tmf:lifecycleStatus ?lifecycleStatus }}\n"
            f"        OPTIONAL {{ ?intentUri tmf:statusChangeDate ?statusChangeDate }}\n"
            f"        OPTIONAL {{ ?intentUri dcterms:created ?created }}\n"
            f"        OPTIONAL {{ ?intentUri dcterms:modified ?modified }}\n"
            f"        OPTIONAL {{ ?intentUri tmf:baseType ?baseType }}\n"
            f"        OPTIONAL {{ ?intentUri tmf:schemaLocation ?schemaLocation }}\n"
            f"        OPTIONAL {{ ?intentUri tmf:description ?description }}\n"
            f"        OPTIONAL {{ ?intentUri tmf:priority ?priority }}\n"
            f"        OPTIONAL {{ ?intentUri tmf:context ?context }}\n"
            f"        OPTIONAL {{ ?intentUri tmf:version ?version }}\n"
            f"        OPTIONAL {{ ?intentUri tmf:isBundle ?isBundle }}\n"
            f"        OPTIONAL {{\n"
            f"            ?intentUri tmf:hasExpression ?exprUri .\n"
            f"            ?exprUri rdf:type ?exprType .\n"
            f"            OPTIONAL {{ ?exprUri tmf:expressionIri ?exprIri }}\n"
            f"            OPTIONAL {{ ?exprUri tmf:expressionValue ?exprValue }}\n"
            f"        }}\n"
            f"{filter_clauses}"
            f"    }}\n"
            f"}}\n"
            f"ORDER BY ?id\n"
            f"LIMIT {limit} OFFSET {offset}"
        )
        rows = await self._client.query(list_sparql)
        items = [self._bindings_to_intent(r) for r in rows]
        return items, total

    async def update(
        self,
        intent_id: str,
        updates: dict,
        modified_at: str,
    ) -> dict[str, Any] | None:
        """
        Apply a partial update using DELETE/INSERT WHERE.
        Only the predicates in `updates` are touched; dcterms:modified is always refreshed.
        Returns the updated intent dict, or None if the intent does not exist.
        """
        graph_uri = str(intent_graph_uri(intent_id))
        uri = str(intent_node(intent_id))
        e = self._esc

        delete_lines: list[str] = []
        insert_lines: list[str] = []
        where_lines: list[str] = []

        # dcterms:modified is always refreshed
        delete_lines.append(f"        <{uri}> dcterms:modified ?oldMod .")
        insert_lines.append(f"        <{uri}> dcterms:modified \"{e(modified_at)}\"^^xsd:dateTime .")
        where_lines.append(f"        OPTIONAL {{ <{uri}> dcterms:modified ?oldMod }}")

        for field, (pred, typed, dtype) in _PATCHABLE.items():
            if field not in updates:
                continue
            var = field.replace("@", "").replace(":", "_")
            val = str(updates[field])
            delete_lines.append(f"        <{uri}> {pred} ?old_{var} .")
            if typed:
                insert_lines.append(
                    f"        <{uri}> {pred} \"{e(val)}\"^^{dtype} ."
                )
            else:
                insert_lines.append(f"        <{uri}> {pred} \"{e(val)}\" .")
            where_lines.append(
                f"        OPTIONAL {{ <{uri}> {pred} ?old_{var} }}"
            )

        # expression update
        expr = updates.get("expression")
        if expr:
            expr_uri = self._expr_uri(intent_id)
            expr_type = expr.get("@type", "JsonLdExpression")
            delete_lines += [
                "        ?exprUri rdf:type ?oldExprType .",
                "        ?exprUri tmf:expressionIri ?oldIri .",
                "        ?exprUri tmf:expressionValue ?oldExprVal .",
            ]
            insert_lines += [
                f"        <{expr_uri}> rdf:type tmf:{e(expr_type)} .",
            ]
            if expr.get("iri"):
                insert_lines.append(
                    f"        <{expr_uri}> tmf:expressionIri \"{e(expr['iri'])}\" ."
                )
            if expr.get("expressionValue") is not None:
                raw = self._ser_expr_value(expr["expressionValue"])
                insert_lines.append(
                    f"        <{expr_uri}> tmf:expressionValue \"{e(raw)}\"^^xsd:string ."
                )
            where_lines += [
                f"        OPTIONAL {{",
                f"            <{uri}> tmf:hasExpression ?exprUri .",
                f"            OPTIONAL {{ ?exprUri rdf:type ?oldExprType }}",
                f"            OPTIONAL {{ ?exprUri tmf:expressionIri ?oldIri }}",
                f"            OPTIONAL {{ ?exprUri tmf:expressionValue ?oldExprVal }}",
                f"        }}",
            ]

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
            f"    }}\n}}"
        )
        await self._client.update(sparql)
        return await self.get_by_id(intent_id)

    async def delete(self, intent_id: str) -> bool:
        """
        Drop the intent named graph. Returns True if the intent existed.
        Uses ASK before DROP to detect existence.
        """
        graph_uri = str(intent_graph_uri(intent_id))
        uri = str(intent_node(intent_id))
        ask = (
            f"{PREFIXES}\n"
            f"ASK {{ GRAPH <{graph_uri}> {{ <{uri}> rdf:type ?t }} }}"
        )
        exists = await self._client.ask(ask)
        if not exists:
            return False
        await self._client.update(f"DROP GRAPH <{graph_uri}>")
        return True

    async def write_state_change(
        self,
        intent_id: str,
        change_id: str,
        from_status: str,
        to_status: str,
        timestamp: str,
    ) -> None:
        """
        Write a write-once StateChange audit named graph.
        Never updated or deleted after creation.
        """
        audit_uri = str(audit_graph_uri(change_id))
        sc_uri = f"http://tmforum.org/api/v5/statechanges/{change_id}"
        intent_uri = str(intent_node(intent_id))
        e = self._esc
        sparql = (
            f"{PREFIXES}\n"
            f"INSERT DATA {{\n"
            f"    GRAPH <{audit_uri}> {{\n"
            f"        <{sc_uri}> rdf:type tmf:StateChange .\n"
            f"        <{sc_uri}> tmf:forIntent <{intent_uri}> .\n"
            f"        <{sc_uri}> tmf:fromStatus \"{e(from_status)}\" .\n"
            f"        <{sc_uri}> tmf:toStatus \"{e(to_status)}\" .\n"
            f"        <{sc_uri}> dcterms:created \"{e(timestamp)}\"^^xsd:dateTime .\n"
            f"    }}\n"
            f"}}"
        )
        await self._client.update(sparql)

    # ── Private ───────────────────────────────────────────────────────────────

    def _build_filter_clauses(self, filters: dict) -> str:
        lines: list[str] = []
        pred_map = {
            "lifecycleStatus": "tmf:lifecycleStatus",
            "name":            "tmf:name",
        }
        for field, pred in pred_map.items():
            if field in filters:
                lines.append(
                    f"        ?intentUri {pred} \"{self._esc(str(filters[field]))}\" .\n"
                )
        if "@type" in filters:
            type_name = self._esc(str(filters["@type"]))
            lines.append(
                f"        FILTER(?type = tmf:{type_name})\n"
            )
        return "".join(lines)
