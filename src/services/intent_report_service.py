"""
Service for IntentReport sub-resources.
IntentReport is read-only from the API perspective (GET + DELETE only).
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from src.graph.repositories.intent_report_repository import IntentReportRepository
from src.graph.repositories.hub_repository import HubRepository
from src.services.notification_service import EventType, NotificationService


class IntentReportService:
    def __init__(
        self,
        report_repo: IntentReportRepository,
        hub_repo: HubRepository,
    ) -> None:
        self._repo = report_repo
        self._notifications = NotificationService(hub_repo)

    async def get_by_id(self, intent_id: str, report_id: str) -> dict[str, Any]:
        report = await self._repo.get_by_id(intent_id, report_id)
        if report is None:
            raise HTTPException(
                status_code=404,
                detail=f"IntentReport {report_id!r} not found for Intent {intent_id!r}",
            )
        return report

    async def list(
        self,
        intent_id: str,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        return await self._repo.list(intent_id, limit=limit, offset=offset)

    async def delete(self, intent_id: str, report_id: str) -> None:
        existed = await self._repo.delete(intent_id, report_id)
        if not existed:
            raise HTTPException(
                status_code=404,
                detail=f"IntentReport {report_id!r} not found for Intent {intent_id!r}",
            )
        self._notifications.schedule(
            EventType.INTENT_REPORT_DELETE,
            {"id": report_id, "@type": "IntentReport", "intentId": intent_id},
        )
