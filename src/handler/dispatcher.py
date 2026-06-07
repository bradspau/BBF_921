"""
Intent Handler dispatcher.

Bridges the REST API layer and the evaluator: schedules a background
evaluation task for a given Intent and writes the result back as an
IntentReport via the internal IntentReportRepository.
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone

from src.graph.repositories.hub_repository import HubRepository
from src.graph.repositories.intent_repository import IntentRepository
from src.graph.repositories.intent_report_repository import IntentReportRepository
from src.graph.store import FusekiClient
from src.handler.evaluator import evaluate_intent
from src.handler.state_writer import write_handler_state
from src.services.notification_service import EventType, NotificationService

logger = logging.getLogger(__name__)

# Strong references prevent the event loop's weak-ref from GC'ing tasks mid-run.
_background_tasks: set[asyncio.Task] = set()
# Per-intent locks guard the DEGRADED → ACTIVE auto-transition against concurrent evaluations.
_transition_locks: dict[str, asyncio.Lock] = {}

_BASE_HREF = "http://tmforum.org/tmf-api/intentManagement/v5"
_EVAL_TIMEOUT: float = float(os.getenv("EVAL_TIMEOUT_SECONDS", "30"))


async def _try_auto_activate(
    intent_id: str,
    intent_repo: IntentRepository,
    hub_repo: HubRepository,
) -> None:
    """
    Flow 2 — Judge/Preference: when a Fulfilled evaluation finds the intent still
    DEGRADED, automatically transition it to ACTIVE.

    A per-intent lock prevents duplicate transitions when concurrent evaluations
    arrive simultaneously.
    """
    lock = _transition_locks.setdefault(intent_id, asyncio.Lock())
    if lock.locked():
        return
    async with lock:
        intent = await intent_repo.get_by_id(intent_id)
        if intent is None or intent.get("lifecycleStatus") != "DEGRADED":
            return
        now = datetime.now(timezone.utc).isoformat()
        updated = await intent_repo.update(
            intent_id,
            {"lifecycleStatus": "ACTIVE", "statusChangeDate": now},
            modified_at=now,
        )
        if updated is None:
            return
        change_id = str(uuid.uuid4())
        await intent_repo.write_state_change(
            intent_id=intent_id,
            change_id=change_id,
            from_status="DEGRADED",
            to_status="ACTIVE",
            timestamp=now,
        )
        NotificationService(hub_repo).schedule(EventType.INTENT_STATUS_CHANGE, updated)
        logger.info(
            "dispatch_evaluation: auto-activated intent %s (DEGRADED → ACTIVE after Fulfilled eval)",
            intent_id,
        )


async def dispatch_evaluation(
    intent_id: str,
    client: FusekiClient,
    report_repo: IntentReportRepository,
    hub_repo: HubRepository,
    intent_repo: IntentRepository | None = None,
) -> None:
    """
    Evaluate an intent and persist the result as an IntentReport.

    Designed to run as a background asyncio task; errors are logged and
    never propagate to callers.

    When intent_repo is provided, Flow 2 (Judge/Preference) is active:
    a Fulfilled result on a DEGRADED intent auto-transitions it to ACTIVE.
    """
    try:
        result = await asyncio.wait_for(
            evaluate_intent(intent_id, client),
            timeout=_EVAL_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "dispatch_evaluation: evaluation timed out after %ss for intent %s",
            _EVAL_TIMEOUT,
            intent_id,
        )
        result = {"intentHandlingState": "Degraded", "reason": "evaluation timeout"}
    except Exception as exc:
        logger.error(
            "dispatch_evaluation: unhandled error for intent %s: %s",
            intent_id,
            exc,
            exc_info=True,
        )
        return

    try:
        # Write working-memory facts before the IntentReport so the OODA loop
        # always has current state even if the report write subsequently fails.
        await write_handler_state(intent_id, result, client)

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

        NotificationService(hub_repo).schedule(
            EventType.INTENT_REPORT_CREATE,
            {**report_data, "intentId": intent_id},
        )

        if result.get("intentHandlingState") == "Fulfilled" and intent_repo is not None:
            await _try_auto_activate(intent_id, intent_repo, hub_repo)

    except Exception as exc:
        logger.error(
            "dispatch_evaluation: report/notification error for intent %s: %s",
            intent_id,
            exc,
            exc_info=True,
        )


def schedule_evaluation(
    intent_id: str,
    client: FusekiClient,
    report_repo: IntentReportRepository,
    hub_repo: HubRepository,
    intent_repo: IntentRepository | None = None,
) -> asyncio.Task:
    """
    Schedule a background evaluation for the given intent.

    Returns the asyncio.Task so callers can await or cancel if needed.
    The task never raises — all errors are logged internally.

    Pass intent_repo to enable Flow 2 auto-activation (DEGRADED → ACTIVE on Fulfilled).
    """
    task = asyncio.create_task(
        dispatch_evaluation(intent_id, client, report_repo, hub_repo, intent_repo),
        name=f"eval-{intent_id}",
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task
