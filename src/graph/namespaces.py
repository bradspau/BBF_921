"""
Namespace constants and named graph URIs for the TMF921 RDF store.
"""
from rdflib import Namespace, URIRef

TMF = Namespace("http://tmforum.org/api/v5/")
DCTERMS = Namespace("http://purl.org/dc/terms/")

# Named graph constants (static, not per-resource)
ONTOLOGY_GRAPH = URIRef("http://tmforum.org/api/v5/ontology")
HUBS_GRAPH = URIRef("http://tmforum.org/api/v5/hubs")

# RDF class URIs
CLASS_INTENT = TMF.Intent
CLASS_PROBE_INTENT = TMF.ProbeIntent
CLASS_INTENT_REPORT = TMF.IntentReport
CLASS_INTENT_SPECIFICATION = TMF.IntentSpecification
CLASS_HUB = TMF.Hub
CLASS_STATE_CHANGE = TMF.StateChange

# Predicate URIs
PRED_ID = TMF.id
PRED_HREF = TMF.href
PRED_NAME = TMF.name
PRED_DESCRIPTION = TMF.description
PRED_LIFECYCLE_STATUS = TMF.lifecycleStatus
PRED_STATUS_CHANGE_DATE = TMF.statusChangeDate
PRED_BASE_TYPE = TMF.baseType
PRED_SCHEMA_LOCATION = TMF.schemaLocation
PRED_HAS_EXPRESSION = TMF.hasExpression
PRED_EXPRESSION_IRI = TMF.expressionIri
PRED_EXPRESSION_TYPE = TMF.expressionType
PRED_EXPRESSION_VALUE = TMF.expressionValue
PRED_IS_BUNDLE = TMF.isBundle
PRED_VERSION = TMF.version
PRED_PRIORITY = TMF.priority
PRED_CONTEXT = TMF.context
PRED_CREATED = DCTERMS.created
PRED_MODIFIED = DCTERMS.modified
