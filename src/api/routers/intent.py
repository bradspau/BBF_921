"""
TMF921 Intent router.

Endpoints:
  GET    /intent
  GET    /intent/{id}
  POST   /intent
  PATCH  /intent/{id}
  DELETE /intent/{id}
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response, HTTPException
from fastapi.responses import Response as _Response

from src.api.deps import get_intent_service
from src.api.middleware.fields_filter import apply_fields
from src.services.intent_service import IntentService

router = APIRouter(tags=["intent"])

_PATCH_TYPES = {"application/json", "application/merge-patch+json"}


@router.get("/intent")
async def list_intents(
    fields: str | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1),
    lifecycleStatus: str | None = Query(default=None),
    name: str | None = Query(default=None),
    response: Response = None,
    service: IntentService = Depends(get_intent_service),
) -> list:
    filters: dict = {}
    if lifecycleStatus:
        filters["lifecycleStatus"] = lifecycleStatus
    if name:
        filters["name"] = name
    items, total = await service.list(limit=limit, offset=offset, filters=filters)
    response.headers["X-Total-Count"] = str(total)
    response.headers["X-Result-Count"] = str(len(items))
    return apply_fields(items, fields)


@router.get("/intent/{intent_id}")
async def get_intent(
    intent_id: str,
    fields: str | None = Query(default=None),
    service: IntentService = Depends(get_intent_service),
) -> dict:
    intent = await service.get_by_id(intent_id)
    return apply_fields(intent, fields)


@router.post("/intent", status_code=201)
async def create_intent(
    request: Request,
    service: IntentService = Depends(get_intent_service),
) -> dict:
    body = await request.json()
    return await service.create(body)


@router.patch("/intent/{intent_id}")
async def patch_intent(
    intent_id: str,
    request: Request,
    service: IntentService = Depends(get_intent_service),
) -> dict:
    ct = request.headers.get("content-type", "").split(";")[0].strip()
    if ct not in _PATCH_TYPES:
        raise HTTPException(status_code=415, detail="Unsupported Media Type")
    body = await request.json()
    return await service.update(intent_id, body)


@router.delete("/intent/{intent_id}", status_code=204, response_class=_Response)
async def delete_intent(
    intent_id: str,
    service: IntentService = Depends(get_intent_service),
) -> _Response:
    await service.delete(intent_id)
    return _Response(status_code=204)
