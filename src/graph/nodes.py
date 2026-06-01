"""
URI constructor functions for TMF921 RDF resource nodes and named graphs.

Each intent, report, and spec lives in its own named graph.
The named graph URI doubles as the primary resource node URI.
"""
from rdflib import URIRef

_BASE = "http://tmforum.org/api/v5"


def intent_graph_uri(intent_id: str) -> URIRef:
    """Named graph URI for an Intent or ProbeIntent."""
    return URIRef(f"{_BASE}/intents/{intent_id}")


def report_graph_uri(report_id: str) -> URIRef:
    """Named graph URI for an IntentReport."""
    return URIRef(f"{_BASE}/reports/{report_id}")


def spec_graph_uri(spec_id: str) -> URIRef:
    """Named graph URI for an IntentSpecification."""
    return URIRef(f"{_BASE}/intentSpecifications/{spec_id}")


def audit_graph_uri(intent_id: str) -> URIRef:
    """Named graph URI for an immutable StateChange audit record."""
    return URIRef(f"{_BASE}/audit/{intent_id}")


def eval_graph_uri(intent_id: str) -> URIRef:
    """Named graph URI for a temporary intent handler evaluation graph."""
    return URIRef(f"{_BASE}/eval/{intent_id}")


# Resource node URIs (same as named graph URIs — each resource is its own graph subject)
def intent_node(intent_id: str) -> URIRef:
    return intent_graph_uri(intent_id)


def report_node(report_id: str) -> URIRef:
    return report_graph_uri(report_id)


def spec_node(spec_id: str) -> URIRef:
    return spec_graph_uri(spec_id)
