"""管理员站内信群发接口（仅 admin 角色）。

为什么挂在独立 router 而不是塞进 ``inbox``：``/inbox`` 是「我自己的收件箱」，
里面每个端点都只操作 ``user_info["user_id"]`` 这一个信箱，只有 ``require_api_key``。
群发是管理动作、写入的是别人的信箱，权限口径完全不同，混在一个 router 里迟早
有人照着邻居复制出越权端点。
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import require_admin
from app.core.orm import get_db_session
from app.schemas.portal_broadcast import (
    BroadcastPreviewData,
    BroadcastPreviewRequest,
    BroadcastRecordDetail,
    BroadcastRecordListData,
    BroadcastSendData,
    BroadcastSendRequest,
)
from app.services.portal_broadcast_service import (
    BroadcastAudienceChanged,
    PortalBroadcastService,
)

router = APIRouter()


@router.post("/preview", dependencies=[Depends(require_admin)])
async def preview_broadcast(
    payload: BroadcastPreviewRequest,
    db: AsyncSession = Depends(get_db_session),
):
    """只解析命中人数与抽样名单，不落库。"""
    if payload.audience.is_empty():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="请至少选择一种收件人范围（指定用户 / 部门 / 角色）",
        )
    data = await PortalBroadcastService.preview(db, payload.audience, limit=payload.limit)
    return {"data": BroadcastPreviewData(**data).model_dump()}


@router.post("/send")
async def send_broadcast(
    payload: BroadcastSendRequest,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    """把站内信写入命中用户的消息中心，返回实际写入条数。"""
    if payload.audience.is_empty():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="请至少选择一种收件人范围（指定用户 / 部门 / 角色）",
        )

    try:
        sent = await PortalBroadcastService.send(
            db,
            operator=admin,
            audience=payload.audience,
            title=payload.title,
            content=payload.content,
            level=payload.level,
            confirm_total=payload.confirm_total,
        )
    except BroadcastAudienceChanged as exc:
        # 409 而不是 400：请求本身没问题，是「预览之后数据变了」，前端应重新预览
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    if sent == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="命中 0 个启用账号，未发送",
        )
    return {"data": BroadcastSendData(sent=sent).model_dump()}


@router.get("/records", dependencies=[Depends(require_admin)])
async def list_broadcast_records(
    page: int = Query(1, ge=1),
    size: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db_session),
):
    """历史群发记录（时间倒序）。正文只给截断预览，全文走详情接口。"""
    items, total = await PortalBroadcastService.list_records(
        db, limit=size, offset=(page - 1) * size
    )
    return {"data": BroadcastRecordListData(total=total, items=items).model_dump()}


@router.get("/records/{record_id}", dependencies=[Depends(require_admin)])
async def get_broadcast_record(
    record_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    """单条群发记录详情（含正文原文，供前端回填重发）。"""
    record = await PortalBroadcastService.get_record(db, record_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="群发记录不存在")
    return {"data": BroadcastRecordDetail(**record).model_dump()}
