"""
TMF921 IntentSpecification router.

Endpoints:
  GET    /intentSpecification
  GET    /intentSpecification/{id}
  POST   /intentSpecification
  PATCH  /intentSpecification/{id}
  DELETE /intentSpecification/{id}
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import Response as _Response

from src.api.deps import get_intent_spec_service
from src.api.middleware.fields_filter import apply_fields
from src.services.intent_spec_service import IntentSpecService

router = APIRouter(tags=["intentSpecification"])

_PATCH_TYPES = {"application/json", "application/merge-patch+json"}


@router.get("/intentSpecification")
async def list_intent_specs(
    fields: str | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1),
    lifecycleStatus: str | None = Query(default=None),
    name: str | None = Query(default=None),
    response: Response = None,
    service: IntentSpecService = Depends(get_intent_spec_service),
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


@router.get("/intentSpecification/{spec_id}")
async def get_intent_spec(
    spec_id: str,
    fields: str | None = Query(default=None),
    service: IntentSpecService = Depends(get_intent_spec_service),
) -> dict:
    spec = await service.get_by_id(spec_id)
    return apply_fields(spec, fields)


@router.post("/intentSpecification", status_code=201)
async def create_intent_spec(
    request: Request,
    service: IntentSpecService = Depends(get_intent_spec_service),
) -> dict:
    body = await request.json()
    return await service.create(body)


@router.patch("/intentSpecification/{spec_id}")
async def patch_intent_spec(
    spec_id: str,
    request: Request,
    service: IntentSpecService = Depends(get_intent_spec_service),
) -> dict:
    ct = request.headers.get("content-type", "").split(";")[0].strip()
    if ct not in _PATCH_TYPES:
        raise HTTPException(status_code=415, detail="Unsupported Media Type")
    body = await request.json()
    return await service.update(spec_id, body)


@router.delete(
    "/intentSpecification/{spec_id}",
    status_code=204,
    response_class=_Response,
)
async def delete_intent_spec(
    spec_id: str,
    service: IntentSpecService = Depends(get_intent_spec_service),
) -> _Response:
    await service.delete(spec_id)
    return _Response(status_code=204)
