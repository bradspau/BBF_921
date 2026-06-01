"""
Unit tests for TMF921 generated + patched Pydantic v2 models.
Place in: tests/unit/test_generated_models.py
"""
import pytest
from pydantic import ValidationError

# Adjust import path to your project structure
# from src.api.schemas.generated import (
#     Intent, IntentCreate, IntentPatch,
#     JsonLdExpression, TurtleExpression,
#     LifecycleStatus, IntentReport,
# )


# ─── Minimal valid POST /intent body ─────────────────────────────────────────

MINIMAL_INTENT = {
    "name": "EventLiveBroadcast",
    "@type": "Intent",
    "expression": {
        "@type": "JsonLdExpression",
        "iri": "http://tio.models.tmforum.org/tio/v2.0.0/IntentCommonModel",
        "expressionValue": {
            "@context": {"icm": "http://tio.models.tmforum.org/tio/v3.4.0/IntentCommonModel"},
            "@graph": [{"@id": "ex:intent1", "@type": "icm:Intent"}],
        },
        "@baseType": "Expression",
    },
}

TURTLE_INTENT = {
    "name": "SliceIntent",
    "@type": "ProbeIntent",
    "expression": {
        "@type": "TurtleExpression",
        "iri": "http://tio.models.tmforum.org/tio/v1.0.0/IntentCommonModel",
        "expressionValue": "@prefix icm: <http://tio.models.tmforum.org/tio/v1.0.0/IntentCommonModel> .\n:Intent1 a icm:Intent .",
        "@baseType": "Expression",
    },
}


class TestMandatoryFieldEnforcement:
    """POST /intent mandatory attribute conformance (TMF921B §POST)."""

    def test_missing_name_raises(self, Intent):
        data = {**MINIMAL_INTENT}
        del data["name"]
        with pytest.raises(ValidationError, match="name"):
            Intent(**data)

    def test_missing_type_raises(self, Intent):
        data = {**MINIMAL_INTENT}
        del data["@type"]
        with pytest.raises(ValidationError, match="type"):
            Intent(**data)

    def test_missing_expression_raises(self, Intent):
        data = {**MINIMAL_INTENT}
        del data["expression"]
        with pytest.raises(ValidationError, match="expression"):
            Intent(**data)

    def test_missing_expression_iri_raises(self, Intent):
        data = {**MINIMAL_INTENT}
        del data["expression"]["iri"]
        with pytest.raises(ValidationError, match="iri"):
            Intent(**data)

    def test_missing_expression_type_raises(self, Intent):
        data = {**MINIMAL_INTENT}
        del data["expression"]["@type"]
        with pytest.raises(ValidationError, match="type"):
            Intent(**data)

    def test_valid_minimal_intent_passes(self, Intent):
        intent = Intent(**MINIMAL_INTENT)
        assert intent.name == "EventLiveBroadcast"

    def test_turtle_expression_value_is_string(self, Intent):
        intent = Intent(**TURTLE_INTENT)
        assert isinstance(intent.expression.expression_value, str)

    def test_jsonld_expression_value_is_dict(self, Intent):
        intent = Intent(**MINIMAL_INTENT)
        assert isinstance(intent.expression.expression_value, dict)


class TestPatchConformance:
    """PATCH non-patchable field rejection (TMF921B §PATCH)."""

    NON_PATCHABLE = [
        "id", "href", "creationDate", "lastUpdate",
        "statusChangeDate", "@type", "@baseType", "@schemaLocation",
    ]

    @pytest.mark.parametrize("field", NON_PATCHABLE)
    def test_non_patchable_field_rejected(self, IntentPatch, field):
        with pytest.raises(ValidationError, match="Non-patchable"):
            IntentPatch(**{field: "some-value"})

    def test_patchable_description_accepted(self, IntentPatch):
        patch = IntentPatch(description="updated description")
        assert patch.description == "updated description"

    def test_patchable_lifecycle_status_accepted(self, IntentPatch):
        patch = IntentPatch(lifecycle_status="ACTIVE")
        assert patch.lifecycle_status == LifecycleStatus.ACTIVE


class TestLifecycleStateMachine:
    """State transition rules (TMF921A §lifecycle)."""

    def test_valid_transition_acknowledged_to_active(self):
        assert LifecycleStatus.ACKNOWLEDGED.can_transition_to(LifecycleStatus.ACTIVE)

    def test_valid_transition_active_to_fulfilled(self):
        assert LifecycleStatus.ACTIVE.can_transition_to(LifecycleStatus.FULFILLED)

    def test_valid_transition_active_to_degraded(self):
        assert LifecycleStatus.ACTIVE.can_transition_to(LifecycleStatus.DEGRADED)

    def test_invalid_transition_acknowledged_to_fulfilled(self):
        assert not LifecycleStatus.ACKNOWLEDGED.can_transition_to(LifecycleStatus.FULFILLED)

    def test_terminal_state_no_transitions(self):
        assert not LifecycleStatus.TERMINATED.can_transition_to(LifecycleStatus.ACTIVE)
        assert not LifecycleStatus.TERMINATED.can_transition_to(LifecycleStatus.FULFILLED)

    def test_all_active_substates_can_reach_terminated(self):
        for state in [LifecycleStatus.ACTIVE, LifecycleStatus.FULFILLED,
                      LifecycleStatus.DEGRADED, LifecycleStatus.SUSPENDED]:
            assert state.can_transition_to(LifecycleStatus.TERMINATED)


class TestServerSideFieldStripping:
    """Server-side fields must be stripped from inbound POST payloads."""

    SERVER_SIDE = ["id", "href", "creationDate", "lastUpdate", "statusChangeDate"]

    @pytest.mark.parametrize("field", SERVER_SIDE)
    def test_server_side_field_stripped_on_create(self, IntentCreate, field):
        data = {**MINIMAL_INTENT, field: "client-supplied-value"}
        intent = IntentCreate(**data)
        assert getattr(intent, field.replace("Date", "_date").lower(), None) is None


class TestAtFieldAliases:
    """JSON @-prefixed field names round-trip correctly."""

    def test_type_field_aliases_at_type(self, Intent):
        intent = Intent(**MINIMAL_INTENT)
        serialised = intent.model_dump(by_alias=True)
        assert "@type" in serialised
        assert "type_" not in serialised

    def test_base_type_aliases_at_base_type(self, Intent):
        data = {**MINIMAL_INTENT, "@baseType": "Intent"}
        intent = Intent(**data)
        serialised = intent.model_dump(by_alias=True)
        assert "@baseType" in serialised
