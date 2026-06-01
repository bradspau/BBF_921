"""
Service for IntentSpecification resources — full CRUD with notification fan-out.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from src.graph.repositories.intent_spec_repository import IntentSpecRepository
from src.graph.repositories.hub_repository import HubRepository
from src.services.notification_service import EventType, NotificationService

_BASE_HREF = "http://tmforum.org/tmf-api/intentManagement/v5/intentSpecification"

_NON_PATCHABLE = frozenset({
    "id", "href", "lastUpdate",
    "@type", "@baseType", "@schemaLocation",
})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class IntentSpecService:
    def __init__(
        self,
        spec_repo: IntentSpecRepository,
        hub_repo: HubRepository,
    ) -> None:
        self._repo = spec_repo
        self._notifications = NotificationService(hub_repo)

    async def create(self, data: dict[str, Any]) -> dict[str, Any]:
        spec_id = str(uuid.uuid4())
        now = _now_iso()
        payload: dict[str, Any] = {
            **data,
            "id":        spec_id,
            "href":      f"{_BASE_HREF}/{spec_id}",
            "lastUpdate": now,
            "@type":     data.get("@type", "IntentSpecification"),
        }
        result = await self._repo.create(payload)
        self._notifications.schedule(EventType.INTENT_SPEC_CREATE, result)
        return result

    async def get_by_id(self, spec_id: str) -> dict[str, Any]:
        spec = await self._repo.get_by_id(spec_id)
        if spec is None:
            raise HTTPException(
                status_code=404,
                detail=f"IntentSpecification {spec_id!r} not found",
            )
        return spec

    async def list(
        self,
        limit: int = 20,
        offset: int = 0,
        filters: dict | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        return await self._repo.list(limit=limit, offset=offset, filters=filters)

    async def update(self, spec_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        forbidden = _NON_PATCHABLE & patch.keys()
        if forbidden:
            raise HTTPException(
                status_code=400,
                detail=f"Fields are not patchable: {sorted(forbidden)}",
            )
        existing = await self._repo.get_by_id(spec_id)
        if existing is None:
            raise HTTPException(
                status_code=404,
                detail=f"IntentSpecification {spec_id!r} not found",
            )
        now = _now_iso()
        updated = await self._repo.update(spec_id, patch, modified_at=now)
        if updated is None:
            raise HTTPException(
                status_code=404,
                detail=f"IntentSpecification {spec_id!r} not found",
            )
        status_changed = (
            "lifecycleStatus" in patch
            and patch["lifecycleStatus"] != existing.get("lifecycleStatus")
        )
        event = (
            EventType.INTENT_SPEC_STATUS_CHANGE
            if status_changed
            else EventType.INTENT_SPEC_ATTRIBUTE_VALUE_CHANGE
        )
        self._notifications.schedule(event, updated)
        return updated

    async def delete(self, spec_id: str) -> None:
        existed = await self._repo.delete(spec_id)
        if not existed:
            raise HTTPException(
                status_code=404,
                detail=f"IntentSpecification {spec_id!r} not found",
            )
        self._notifications.schedule(
            EventType.INTENT_SPEC_DELETE,
            {"id": spec_id, "@type": "IntentSpecification"},
        )
