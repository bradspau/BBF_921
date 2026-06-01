"""
Shared SPARQL helpers, prefix constants, and binding utilities
used by all TMF921 repositories.
"""
from __future__ import annotations

import json
from typing import Any

from src.graph.store import FusekiClient

PREFIXES = """\
PREFIX rdf:     <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX xsd:     <http://www.w3.org/2001/XMLSchema#>
PREFIX tmf:     <http://tmforum.org/api/v5/>
PREFIX dcterms: <http://purl.org/dc/terms/>"""

_TMF_BASE = "http://tmforum.org/api/v5/"


class BaseRepository:
    def __init__(self, client: FusekiClient) -> None:
        self._client = client

    # ── SPARQL string helpers ─────────────────────────────────────────────────

    @staticmethod
    def _esc(s: str) -> str:
        """Escape a string for safe embedding in a double-quoted SPARQL literal."""
        return (
            s.replace("\\", "\\\\")
             .replace('"', '\\"')
             .replace("\n", "\\n")
             .replace("\r", "\\r")
             .replace("\t", "\\t")
        )

    @staticmethod
    def _local(uri: str) -> str:
        """Return the local name of a URI (fragment or last path segment)."""
        after_hash = uri.rsplit("#", 1)[-1]
        return after_hash.rsplit("/", 1)[-1]

    # ── Binding extraction helpers ────────────────────────────────────────────

    @staticmethod
    def _v(binding: dict, key: str) -> str | None:
        """Return the value string for a SPARQL binding variable, or None."""
        node = binding.get(key)
        return node["value"] if node else None

    @staticmethod
    def _vbool(binding: dict, key: str) -> bool | None:
        """Return a boolean value from a SPARQL binding, or None."""
        node = binding.get(key)
        if not node:
            return None
        return node["value"].lower() in ("true", "1")

    # ── Expression value serialisation ───────────────────────────────────────

    @staticmethod
    def _ser_expr_value(value: Any) -> str:
        """Serialise an expressionValue to a string for SPARQL storage."""
        if isinstance(value, dict):
            return json.dumps(value, separators=(",", ":"))
        return str(value) if value is not None else ""

    @staticmethod
    def _deser_expr_value(expr_type: str, raw: str | None) -> Any:
        """
        Deserialise an expressionValue from storage.
        JsonLdExpression returns a dict; TurtleExpression returns a string.
        """
        if raw is None:
            return None
        if expr_type == "JsonLdExpression":
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return raw
        return raw

    # ── Optional triple builder ───────────────────────────────────────────────

    @staticmethod
    def _opt_triple(subject: str, predicate: str, value: str | None) -> str:
        """Return a SPARQL triple string if value is not None, else empty string."""
        if value is None:
            return ""
        return f"        {subject} {predicate} \"{BaseRepository._esc(value)}\" .\n"

    @staticmethod
    def _opt_typed_triple(
        subject: str, predicate: str, value: str | None, datatype: str
    ) -> str:
        """Return a typed SPARQL triple string if value is not None, else empty string."""
        if value is None:
            return ""
        return (
            f"        {subject} {predicate} "
            f"\"{BaseRepository._esc(value)}\"^^{datatype} .\n"
        )
