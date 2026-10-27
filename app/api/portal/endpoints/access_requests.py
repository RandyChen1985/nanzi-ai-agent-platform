"""资源访问申请与统一审批（知识库 + 数据集）。

审批只对管理员开放：知识库原本允许「库归属人」审批，但归属人未必有
``menu:system:users`` 菜单权限，把入口放在用户管理页会把人挡在路由守卫外面，
而且两类资源各有一个审批人集合会让「统一审核」名不副实。因此收口为管理员。
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import require_admin, require_api_key
from app.core.orm import get_db_session
from app.schemas.response import StandardResponse
from app.services.resource_access_request_service import (
    AccessRequestError,
    ResourceAccessRequestService,
)

router = APIRouter()


class CreateAccessRequestBody(BaseModel):
    resource_type: str = Field(..., description="dataset=知识库 / metadata=数据集")
    resource_id: str = Field(..., description="知识库=RAGFlow dataset id / 数据集=meta_datasets.id")
    reason: Optional[str] = None


class DecideBody(BaseModel):
    comment: Optional[str] = None


def _service(db: AsyncSession) -> ResourceAccessRequestService:
    return ResourceAccessRequestService(db)


@router.post("", response_model=StandardResponse[Dict[str, Any]], summary="提交资源访问申请")
async def create_access_request(
    payload: CreateAccessRequestBody,
    user: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        # 名称由服务端按资源回填：前端只传 ID，审批人需要看到可读的资源名
        row = await _service(db).create(
            resource_type=payload.resource_type,
            resource_id=payload.resource_id,
            resource_name=None,
            user=user,
            reason=payload.reason,
        )
    except AccessRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return StandardResponse(data={"id": row.id, "status": row.status})


@router.get("/mine", response_model=StandardResponse[Dict[str, Any]], summary="我的资源申请")
async def list_my_access_requests(
    resource_type: Optional[str] = Query(default=None),
    user: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    items = await _service(db).list_mine(int(user["user_id"]), resource_type=resource_type)
    return StandardResponse(data={"items": items, "total": len(items)})


@router.get("/pending", response_model=StandardResponse[Dict[str, Any]], summary="待审批申请（管理员）")
async def list_pending_access_requests(
    resource_type: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=100, ge=1, le=500),
    _admin: Dict[str, Any] = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    items, total = await _service(db).list_pending(
        resource_type=resource_type, page=page, size=size
    )
    return StandardResponse(
        data={"items": items, "total": total, "page": page, "size": size}
    )


@router.get("/reviews", response_model=StandardResponse[Dict[str, Any]], summary="已处理申请（管理员）")
async def list_reviewed_access_requests(
    resource_type: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=100, ge=1, le=500),
    _admin: Dict[str, Any] = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    items, total = await _service(db).list_reviews(
        resource_type=resource_type, page=page, size=size
    )
    return StandardResponse(
        data={"items": items, "total": total, "page": page, "size": size}
    )


@router.post("/{request_id}/approve", response_model=StandardResponse[Dict[str, Any]], summary="通过申请")
async def approve_access_request(
    request_id: int,
    payload: DecideBody,
    admin: Dict[str, Any] = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        row = await _service(db).decide(
            request_id=request_id, reviewer=admin, approve=True, comment=payload.comment
        )
    except AccessRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return StandardResponse(data={"id": row.id, "status": row.status})


@router.post("/{request_id}/reject", response_model=StandardResponse[Dict[str, Any]], summary="驳回申请")
async def reject_access_request(
    request_id: int,
    payload: DecideBody,
    admin: Dict[str, Any] = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        row = await _service(db).decide(
            request_id=request_id, reviewer=admin, approve=False, comment=payload.comment
        )
    except AccessRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return StandardResponse(data={"id": row.id, "status": row.status})


@router.post("/{request_id}/cancel", response_model=StandardResponse[Dict[str, Any]], summary="撤回申请")
async def cancel_access_request(
    request_id: int,
    user: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        row = await _service(db).cancel(request_id, user)
    except AccessRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return StandardResponse(data={"id": row.id, "status": row.status})
