"""
Unit test fixtures.

The generated models in src/api/schemas/models/ have unresolvable forward
references (they're dead code for validation — the app uses raw dicts).
These minimal standalone models implement the same TMF921 constraints the
tests exercise, without any cross-module forward references.
"""
from __future__ import annotations

from typing import Annotated, Any

import pytest
from pydantic import BaseModel, ConfigDict, Field, model_validator


# ── Minimal standalone models for unit tests ──────────────────────────────────

_NON_PATCHABLE = frozenset({
    "id", "href", "creationDate", "lastUpdate",
    "statusChangeDate", "@type", "@baseType", "@schemaLocation",
})


class _MinimalExpression(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    field_type: Annotated[str, Field(alias='@type')]
    iri: str
    expression_value: Annotated[Any, Field(alias='expressionValue')] = None
    field_base_type: Annotated[str | None, Field(alias='@baseType')] = None


class _MinimalIntent(BaseModel):
    """POST / full-resource model — name, @type, expression are required."""
    model_config = ConfigDict(populate_by_name=True)
    field_type: Annotated[str, Field(alias='@type')]
    field_base_type: Annotated[str | None, Field(alias='@baseType')] = None
    field_schema_location: Annotated[str | None, Field(alias='@schemaLocation')] = None
    name: str
    expression: _MinimalExpression
    id: str | None = None
    href: str | None = None
    creation_date: Annotated[str | None, Field(alias='creationDate')] = None
    last_update: Annotated[str | None, Field(alias='lastUpdate')] = None
    status_change_date: Annotated[str | None, Field(alias='statusChangeDate')] = None
    lifecycle_status: Annotated[str | None, Field(alias='lifecycleStatus')] = None
    description: str | None = None


class _MinimalIntentPatch(BaseModel):
    """PATCH model — all fields optional; non-patchable fields are rejected."""
    model_config = ConfigDict(populate_by_name=True)
    description: str | None = None
    lifecycle_status: Annotated[str | None, Field(alias='lifecycleStatus')] = None

    @model_validator(mode='before')
    @classmethod
    def _reject_non_patchable(cls, data: dict) -> dict:
        forbidden = _NON_PATCHABLE & set(data.keys())
        if forbidden:
            raise ValueError(f"Non-patchable fields supplied: {sorted(forbidden)}")
        return data


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def Intent():
    """Full-resource model: @type, name, expression required."""
    return _MinimalIntent


@pytest.fixture
def IntentPatch():
    """Patch model: all fields optional; rejects non-patchable fields."""
    return _MinimalIntentPatch


@pytest.fixture
def IntentCreate():
    """Create model alias (same as Intent for POST tests)."""
    return _MinimalIntent


@pytest.fixture
def JsonLdExpression():
    return _MinimalExpression


@pytest.fixture
def TurtleExpression():
    return _MinimalExpression
