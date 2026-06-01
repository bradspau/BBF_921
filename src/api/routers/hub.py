"""
TMF921 Hub (event subscription) router.

Endpoints:
  POST   /hub
  DELETE /hub/{id}
"""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.responses import Response as _Response

from src.api.deps import get_hub_repo
from src.graph.repositories.hub_repository import HubRepository

router = APIRouter(tags=["hub"])

_BASE_HREF = "http://tmforum.org/tmf-api/intentManagement/v5/hub"

_UUIDPath = Annotated[str, Path(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")]


@router.post("/hub", status_code=201)
async def create_hub(
    request: Request,
    hub_repo: HubRepository = Depends(get_hub_repo),
) -> dict:
    body = await request.json()
    if not body.get("callback"):
        raise HTTPException(status_code=400, detail="callback is required")
    hub_id = str(uuid.uuid4())
    data = {
        "id":       hub_id,
        "href":     f"{_BASE_HREF}/{hub_id}",
        "callback": body["callback"],
        "query":    body.get("query"),
    }
    return await hub_repo.create(data)


@router.delete("/hub/{hub_id}", status_code=204, response_class=_Response)
async def delete_hub(
    hub_id: _UUIDPath,
    hub_repo: HubRepository = Depends(get_hub_repo),
) -> _Response:
    existed = await hub_repo.delete(hub_id)
    if not existed:
        raise HTTPException(status_code=404, detail=f"Hub {hub_id!r} not found")
    return _Response(status_code=204)
