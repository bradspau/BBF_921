"""
Intent Handler dispatcher.

Bridges the REST API layer and the evaluator: schedules a background
evaluation task for a given Intent and writes the result back as an
IntentReport via the internal IntentReportRepository.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from src.graph.repositories.hub_repository import HubRepository
from src.graph.repositories.intent_report_repository import IntentReportRepository
from src.graph.store import FusekiClient
from src.handler.evaluator import evaluate_intent
from src.services.notification_service import EventType, NotificationService

logger = logging.getLogger(__name__)

_BASE_HREF = "http://tmforum.org/tmf-api/intentManagement/v5"


async def dispatch_evaluation(
    intent_id: str,
    client: FusekiClient,
    report_repo: IntentReportRepository,
    hub_repo: HubRepository,
) -> None:
    """
    Evaluate an intent and persist the result as an IntentReport.

    Designed to run as a background asyncio task; errors are logged and
    never propagate to callers.
    """
    try:
        result = await evaluate_intent(intent_id, client)

        report_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        report_href = f"{_BASE_HREF}/intent/{intent_id}/intentReport/{report_id}"

        report_data = {
            "id": report_id,
            "href": report_href,
            "@type": "IntentReport",
            "name": f"Evaluation for intent {intent_id}",
            "creationDate": now,
            "expression": None,
            "intentHandlingState": result.get("intentHandlingState", "Degraded"),
            "intentHandlingReason": result.get("reason"),
        }

        await report_repo.create(intent_id, report_data)
        logger.info(
            "dispatch_evaluation: created report %s for intent %s (state=%s)",
            report_id,
            intent_id,
            result.get("intentHandlingState"),
        )

        notifications = NotificationService(hub_repo)
        notifications.schedule(
            EventType.INTENT_REPORT_CREATE,
            {**report_data, "intentId": intent_id},
        )

    except Exception as exc:
        logger.error(
            "dispatch_evaluation: unhandled error for intent %s: %s",
            intent_id,
            exc,
            exc_info=True,
        )


def schedule_evaluation(
    intent_id: str,
    client: FusekiClient,
    report_repo: IntentReportRepository,
    hub_repo: HubRepository,
) -> asyncio.Task:
    """
    Schedule a background evaluation for the given intent.

    Returns the asyncio.Task so callers can await or cancel if needed.
    The task never raises — all errors are logged internally.
    """
    return asyncio.create_task(
        dispatch_evaluation(intent_id, client, report_repo, hub_repo),
        name=f"eval-{intent_id}",
    )
