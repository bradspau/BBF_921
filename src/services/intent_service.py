"""
Orchestration service for TMF921 Intent resources.

Responsibilities:
- Generates id, href, creationDate, lastUpdate, and initial lifecycleStatus=ACKNOWLEDGED
- Enforces non-patchable field rejection
- Delegates persistence to IntentRepository
- Enforces lifecycle FSM via state_machine.validate_transition()
- Writes immutable StateChange audit record on each valid lifecycle transition
- Schedules async notification fan-out after every mutating operation
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from src.graph.repositories.intent_repository import IntentRepository
from src.graph.repositories.intent_report_repository import IntentReportRepository
from src.graph.repositories.hub_repository import HubRepository
from src.services.state_machine import validate_transition
from src.services.notification_service import EventType, NotificationService
from src.handler.dispatcher import schedule_evaluation

_BASE_HREF = "http://tmforum.org/tmf-api/intentManagement/v5/intent"

_NON_PATCHABLE = frozenset({
    "id", "href", "creationDate", "lastUpdate",
    "statusChangeDate", "version",
    "@type", "@baseType", "@schemaLocation",
})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class IntentService:
    def __init__(
        self,
        intent_repo: IntentRepository,
        hub_repo: HubRepository,
        report_repo: IntentReportRepository | None = None,
    ) -> None:
        self._repo = intent_repo
        self._hub_repo = hub_repo
        self._report_repo = report_repo
        self._notifications = NotificationService(hub_repo)

    # ── Create ────────────────────────────────────────────────────────────────

    async def create(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        Persist a new Intent.  Server-side fields are set here; caller must NOT
        supply id, href, creationDate, lastUpdate, or lifecycleStatus.
        """
        intent_id = str(uuid.uuid4())
        now = _now_iso()
        intent_type = data.get("@type", "Intent")

        payload: dict[str, Any] = {
            **data,
            "id":             intent_id,
            "href":           f"{_BASE_HREF}/{intent_id}",
            "creationDate":   now,
            "lastUpdate":     now,
            "lifecycleStatus": "ACKNOWLEDGED",
            "@type":          intent_type,
        }

        result = await self._repo.create(payload)
        self._notifications.schedule(EventType.INTENT_CREATE, result)
        if self._report_repo is not None:
            schedule_evaluation(intent_id, self._repo._client, self._report_repo, self._hub_repo, self._repo)
        return result

    # ── Read ──────────────────────────────────────────────────────────────────

    async def get_by_id(self, intent_id: str) -> dict[str, Any]:
        intent = await self._repo.get_by_id(intent_id)
        if intent is None:
            raise HTTPException(status_code=404, detail=f"Intent {intent_id!r} not found")
        return intent

    # ── List ──────────────────────────────────────────────────────────────────

    async def list(
        self,
        limit: int = 20,
        offset: int = 0,
        filters: dict | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        return await self._repo.list(limit=limit, offset=offset, filters=filters)

    # ── Update (PATCH / RFC 7386 merge-patch) ────────────────────────────────

    async def update(
        self,
        intent_id: str,
        patch: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Apply a partial update.  Non-patchable fields are rejected with 422.
        Lifecycle transitions are validated; a valid transition triggers a
        StateChange audit write and IntentStatusChangeEvent notification.
        """
        forbidden = _NON_PATCHABLE & patch.keys()
        if forbidden:
            raise HTTPException(
                status_code=400,
                detail=f"Fields are not patchable: {sorted(forbidden)}",
            )

        existing = await self._repo.get_by_id(intent_id)
        if existing is None:
            raise HTTPException(status_code=404, detail=f"Intent {intent_id!r} not found")

        status_changed = False
        if "lifecycleStatus" in patch:
            from_status = existing["lifecycleStatus"]
            to_status   = patch["lifecycleStatus"]
            if from_status != to_status:
                validate_transition(from_status, to_status)
                status_changed = True

        now = _now_iso()
        updated = await self._repo.update(intent_id, patch, modified_at=now)
        if updated is None:
            raise HTTPException(status_code=404, detail=f"Intent {intent_id!r} not found")

        if status_changed:
            change_id = str(uuid.uuid4())
            await self._repo.write_state_change(
                intent_id=intent_id,
                change_id=change_id,
                from_status=existing["lifecycleStatus"],
                to_status=patch["lifecycleStatus"],
                timestamp=now,
            )
            self._notifications.schedule(EventType.INTENT_STATUS_CHANGE, updated)
        else:
            self._notifications.schedule(EventType.INTENT_ATTRIBUTE_VALUE_CHANGE, updated)

        if self._report_repo is not None:
            schedule_evaluation(intent_id, self._repo._client, self._report_repo, self._hub_repo, self._repo)

        return updated

    # ── Delete ────────────────────────────────────────────────────────────────

    async def delete(self, intent_id: str) -> None:
        existed = await self._repo.delete(intent_id)
        if not existed:
            raise HTTPException(status_code=404, detail=f"Intent {intent_id!r} not found")
        self._notifications.schedule(
            EventType.INTENT_DELETE,
            {"id": intent_id, "@type": "Intent"},
        )
