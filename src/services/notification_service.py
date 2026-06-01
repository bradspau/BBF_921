"""
Async notification fan-out for TMF921 hub subscriptions.

Notifications are fire-and-forget: delivery is attempted after the originating
API write succeeds, and failures are logged without failing the operation.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx

from src.graph.repositories.hub_repository import HubRepository

logger = logging.getLogger(__name__)

# All supported TMF921A event types
class EventType:
    INTENT_CREATE                     = "IntentCreateEvent"
    INTENT_DELETE                     = "IntentDeleteEvent"
    INTENT_STATUS_CHANGE              = "IntentStatusChangeEvent"
    INTENT_ATTRIBUTE_VALUE_CHANGE     = "IntentAttributeValueChangeEvent"
    INTENT_REPORT_CREATE              = "IntentReportCreateEvent"
    INTENT_REPORT_DELETE              = "IntentReportDeleteEvent"
    INTENT_SPEC_CREATE                = "IntentSpecificationCreateEvent"
    INTENT_SPEC_DELETE                = "IntentSpecificationDeleteEvent"
    INTENT_SPEC_ATTRIBUTE_VALUE_CHANGE = "IntentSpecificationAttributeValueChangeEvent"
    INTENT_SPEC_STATUS_CHANGE         = "IntentSpecificationStatusChangeEvent"


def _build_payload(event_type: str, resource: dict[str, Any]) -> dict[str, Any]:
    return {
        "eventId":       str(uuid.uuid4()),
        "correlationId": str(uuid.uuid4()),
        "eventTime":     datetime.now(timezone.utc).isoformat(),
        "eventType":     event_type,
        "@type":         event_type,
        "@baseType":     "Event",
        "event":         resource,
    }


async def _post_one(
    client: httpx.AsyncClient,
    callback: str,
    payload: dict[str, Any],
) -> None:
    try:
        resp = await client.post(callback, json=payload, timeout=10.0)
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Notification delivery failed to %s: %s", callback, exc)


class NotificationService:
    def __init__(self, hub_repo: HubRepository) -> None:
        self._hub_repo = hub_repo

    async def fire(
        self,
        event_type: str,
        resource: dict[str, Any],
    ) -> None:
        """
        Fan-out a notification to all registered hubs.
        Runs as fire-and-forget: schedule with asyncio.create_task from the caller.
        """
        hubs = await self._hub_repo.list_all()
        if not hubs:
            return
        payload = _build_payload(event_type, resource)
        async with httpx.AsyncClient() as client:
            async with asyncio.TaskGroup() as tg:
                for hub in hubs:
                    callback = hub.get("callback")
                    if callback:
                        tg.create_task(_post_one(client, callback, payload))

    def schedule(
        self,
        event_type: str,
        resource: dict[str, Any],
    ) -> asyncio.Task[None]:
        """Schedule fire() as a background task (non-blocking)."""
        return asyncio.create_task(self.fire(event_type, resource))
