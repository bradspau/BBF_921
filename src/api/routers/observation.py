"""
Metric observation endpoint.

POST /intent/{id}/observation — submit a metric reading for an intent.

Writes a met:Observation to the intent's observation graph, then triggers
re-evaluation so the intent handler picks up the new value immediately and
produces a fresh IntentReport.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel

from src.api.deps import get_fuseki_client, get_hub_repo, get_intent_report_repo
from src.graph.repositories.hub_repository import HubRepository
from src.graph.repositories.intent_repository import IntentRepository
from src.graph.repositories.intent_report_repository import IntentReportRepository
from src.graph.store import FusekiClient
from src.handler.dispatcher import schedule_evaluation
from src.handler.observation_store import write_observation

router = APIRouter(tags=["observation"])

_UUIDPath = Annotated[
    str,
    Path(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"),
]


class ObservationCreate(BaseModel):
    metricUri: str
    value: float
    obtainedAt: str | None = None


@router.post("/intent/{intent_id}/observation", status_code=201)
async def create_observation(
    intent_id: _UUIDPath,
    body: ObservationCreate,
    client: FusekiClient = Depends(get_fuseki_client),
    report_repo: IntentReportRepository = Depends(get_intent_report_repo),
    hub_repo: HubRepository = Depends(get_hub_repo),
) -> dict:
    obs_id = await write_observation(
        intent_id, body.metricUri, body.value, client, body.obtainedAt
    )
    schedule_evaluation(intent_id, client, report_repo, hub_repo, IntentRepository(client))
    return {
        "observationId": obs_id,
        "intentId": intent_id,
        "metricUri": body.metricUri,
        "value": body.value,
    }
