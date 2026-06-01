"""
SPARQL-backed repository for IntentReport sub-resources.

Each report lives in its own named graph: tmf:reports/{report_id}.
A tmf:parentIntent triple links the report back to its owning Intent.
"""
from __future__ import annotations

from typing import Any

from src.graph.nodes import report_graph_uri, report_node, intent_node
from src.graph.repositories.base_repository import PREFIXES, BaseRepository

_GET_SELECT = """\
    ?id ?href ?name ?type ?baseType ?schemaLocation
    ?created ?exprType ?exprIri ?exprValue ?parentIntent"""

_GET_WHERE = """\
        <{uri}> rdf:type tmf:IntentReport .
        OPTIONAL {{ <{uri}> tmf:id ?id }}
        OPTIONAL {{ <{uri}> tmf:href ?href }}
        OPTIONAL {{ <{uri}> tmf:name ?name }}
        OPTIONAL {{ <{uri}> rdf:type ?type }}
        OPTIONAL {{ <{uri}> tmf:baseType ?baseType }}
        OPTIONAL {{ <{uri}> tmf:schemaLocation ?schemaLocation }}
        OPTIONAL {{ <{uri}> dcterms:created ?created }}
        OPTIONAL {{ <{uri}> tmf:parentIntent ?parentIntent }}
        OPTIONAL {{
            <{uri}> tmf:hasExpression ?exprUri .
            ?exprUri rdf:type ?exprType .
            OPTIONAL {{ ?exprUri tmf:expressionIri ?exprIri }}
            OPTIONAL {{ ?exprUri tmf:expressionValue ?exprValue }}
        }}"""


class IntentReportRepository(BaseRepository):

    def _expr_uri(self, report_id: str) -> str:
        return f"http://tmforum.org/api/v5/reports/{report_id}/expression"

    def _bindings_to_report(self, row: dict) -> dict:
        e_type_uri = self._v(row, "exprType")
        e_type = self._local(e_type_uri) if e_type_uri else None
        type_uri = self._v(row, "type") or ""
        parent = self._v(row, "parentIntent")
        return {
            "id": self._v(row, "id"),
            "href": self._v(row, "href"),
            "@type": self._local(type_uri) if type_uri else "IntentReport",
            "@baseType": self._v(row, "baseType"),
            "@schemaLocation": self._v(row, "schemaLocation"),
            "name": self._v(row, "name"),
            "creationDate": self._v(row, "created"),
            "intentId": self._local(parent) if parent else None,
            "expression": {
                "@type": e_type,
                "iri": self._v(row, "exprIri"),
                "expressionValue": self._deser_expr_value(
                    e_type or "", self._v(row, "exprValue")
                ),
            } if e_type else None,
        }

    async def create(self, intent_id: str, data: dict) -> dict:
        """
        Persist a new IntentReport for the given intent.
        Caller must supply: id, href, @type, name, creationDate, expression.
        """
        report_id = data["id"]
        graph_uri = str(report_graph_uri(report_id))
        report_uri = str(report_node(report_id))
        intent_uri = str(intent_node(intent_id))
        expr_uri = self._expr_uri(report_id)
        e = self._esc

        lines: list[str] = [
            f"        <{report_uri}> rdf:type tmf:IntentReport .",
            f"        <{report_uri}> tmf:id \"{e(data['id'])}\" .",
            f"        <{report_uri}> tmf:href \"{e(data['href'])}\" .",
            f"        <{report_uri}> tmf:name \"{e(data['name'])}\" .",
            f"        <{report_uri}> dcterms:created \"{e(data['creationDate'])}\"^^xsd:dateTime .",
            f"        <{report_uri}> tmf:parentIntent <{intent_uri}> .",
        ]
        if data.get("@baseType"):
            lines.append(f"        <{report_uri}> tmf:baseType \"{e(data['@baseType'])}\" .")

        expr = data.get("expression")
        if expr:
            expr_type = expr.get("@type", "JsonLdExpression")
            lines += [
                f"        <{report_uri}> tmf:hasExpression <{expr_uri}> .",
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

        triples = "\n".join(lines)
        sparql = (
            f"{PREFIXES}\n"
            f"INSERT DATA {{\n"
            f"    GRAPH <{graph_uri}> {{\n{triples}\n    }}\n"
            f"}}"
        )
        await self._client.update(sparql)
        return data

    async def get_by_id(
        self, intent_id: str, report_id: str
    ) -> dict[str, Any] | None:
        """Return the IntentReport dict, or None if not found."""
        graph_uri = str(report_graph_uri(report_id))
        uri = str(report_node(report_id))
        intent_uri = str(intent_node(intent_id))
        patterns = _GET_WHERE.format(uri=uri)

        # Verify it belongs to the correct parent intent
        sparql = (
            f"{PREFIXES}\n"
            f"SELECT {_GET_SELECT}\n"
            f"WHERE {{\n"
            f"    GRAPH <{graph_uri}> {{\n"
            f"{patterns}\n"
            f"        FILTER(?parentIntent = <{intent_uri}>)\n"
            f"    }}\n"
            f"}}"
        )
        rows = await self._client.query(sparql)
        if not rows:
            return None
        return self._bindings_to_report(rows[0])

    async def list(
        self,
        intent_id: str,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """Return (page_items, total_count) for all reports of an intent."""
        intent_uri = str(intent_node(intent_id))

        count_sparql = (
            f"{PREFIXES}\n"
            f"SELECT (COUNT(DISTINCT ?reportUri) AS ?count)\n"
            f"WHERE {{\n"
            f"    GRAPH ?g {{\n"
            f"        ?reportUri rdf:type tmf:IntentReport .\n"
            f"        ?reportUri tmf:parentIntent <{intent_uri}> .\n"
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
            f"        ?reportUri rdf:type tmf:IntentReport .\n"
            f"        ?reportUri tmf:parentIntent <{intent_uri}> .\n"
            f"        BIND(?reportUri AS ?uri__)\n"
            f"        OPTIONAL {{ ?reportUri tmf:id ?id }}\n"
            f"        OPTIONAL {{ ?reportUri tmf:href ?href }}\n"
            f"        OPTIONAL {{ ?reportUri tmf:name ?name }}\n"
            f"        OPTIONAL {{ ?reportUri rdf:type ?type }}\n"
            f"        OPTIONAL {{ ?reportUri tmf:baseType ?baseType }}\n"
            f"        OPTIONAL {{ ?reportUri tmf:schemaLocation ?schemaLocation }}\n"
            f"        OPTIONAL {{ ?reportUri dcterms:created ?created }}\n"
            f"        OPTIONAL {{ ?reportUri tmf:parentIntent ?parentIntent }}\n"
            f"        OPTIONAL {{\n"
            f"            ?reportUri tmf:hasExpression ?exprUri .\n"
            f"            ?exprUri rdf:type ?exprType .\n"
            f"            OPTIONAL {{ ?exprUri tmf:expressionIri ?exprIri }}\n"
            f"            OPTIONAL {{ ?exprUri tmf:expressionValue ?exprValue }}\n"
            f"        }}\n"
            f"    }}\n"
            f"}}\n"
            f"ORDER BY ?id\n"
            f"LIMIT {limit} OFFSET {offset}"
        )
        rows = await self._client.query(list_sparql)
        return [self._bindings_to_report(r) for r in rows], total

    async def delete(self, intent_id: str, report_id: str) -> bool:
        """Drop the report named graph. Returns True if it existed and belonged to the intent."""
        report = await self.get_by_id(intent_id, report_id)
        if report is None:
            return False
        graph_uri = str(report_graph_uri(report_id))
        await self._client.update(f"DROP GRAPH <{graph_uri}>")
        return True
