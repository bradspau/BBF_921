"""
TMF921 IntentReport router (nested under intent).

Endpoints:
  GET    /intent/{intentId}/intentReport
  GET    /intent/{intentId}/intentReport/{id}
  DELETE /intent/{intentId}/intentReport/{id}
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import Response as _Response

from src.api.deps import get_intent_report_service
from src.api.middleware.fields_filter import apply_fields
from src.services.intent_report_service import IntentReportService

router = APIRouter(tags=["intentReport"])


@router.get("/intent/{intent_id}/intentReport")
async def list_intent_reports(
    intent_id: str,
    fields: str | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1),
    response: Response = None,
    service: IntentReportService = Depends(get_intent_report_service),
) -> list:
    items, total = await service.list(intent_id, limit=limit, offset=offset)
    response.headers["X-Total-Count"] = str(total)
    response.headers["X-Result-Count"] = str(len(items))
    return apply_fields(items, fields)


@router.get("/intent/{intent_id}/intentReport/{report_id}")
async def get_intent_report(
    intent_id: str,
    report_id: str,
    fields: str | None = Query(default=None),
    service: IntentReportService = Depends(get_intent_report_service),
) -> dict:
    report = await service.get_by_id(intent_id, report_id)
    return apply_fields(report, fields)


@router.delete(
    "/intent/{intent_id}/intentReport/{report_id}",
    status_code=204,
    response_class=_Response,
)
async def delete_intent_report(
    intent_id: str,
    report_id: str,
    service: IntentReportService = Depends(get_intent_report_service),
) -> _Response:
    await service.delete(intent_id, report_id)
    return _Response(status_code=204)
