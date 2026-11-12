from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.core.dependencies import require_api_key
from app.core.orm import get_db_session
from app.services.notification_service import (
    MULTI_GROUP_CHANNELS,
    NotificationService,
)

router = APIRouter()

class SaveConfigReq(BaseModel):
    channel_type: str = Field(..., description="通道类型, 如 dingtalk, wechat_work, email")
    config_data: Dict[str, Any] = Field(..., description="配置细节")

class TestConfigReq(BaseModel):
    channel_type: str = Field(..., description="通道类型, 如 dingtalk, wechat_work, email")
    config_data: Dict[str, Any] = Field(..., description="配置细节")
    channel_id: Optional[int] = Field(
        default=None,
        description="可选。测试某个具体群时传它的群 ID，脱敏密钥按该群反解",
    )

class ChannelItemReq(BaseModel):
    id: Optional[int] = Field(default=None, description="群 ID。为空表示新建")
    name: str = Field(..., description="群名称，同一渠道下必须唯一")
    is_enabled: bool = Field(default=True, description="是否启用")
    is_default: bool = Field(default=False, description="是否为该渠道的默认群")
    webhook_url: str = Field(default="", description="群机器人 Webhook 地址")
    secret: Optional[str] = Field(default=None, description="加签密钥，脱敏值 ****** 会原样保留")

class SaveChannelsReq(BaseModel):
    channel_type: str = Field(..., description="通道类型：dingtalk / feishu / wechat_work")
    channels: List[ChannelItemReq] = Field(
        default_factory=list,
        description="该渠道的完整群列表：带 id 的是更新，不带 id 的是新建，未提交的视为删除",
    )

@router.get("/config", summary="获取当前登录用户的全部通知配置")
async def get_notifications_config(
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session)
):
    user_id = int(user_info["user_id"])
    try:
        return await NotificationService.get_user_configs(db, user_id)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取消息配置失败: {str(e)}"
        )

@router.get("/readiness", summary="各通知渠道当前是否可投递（前端勾选门禁判据）")
async def get_notifications_readiness(
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session)
):
    """返回每个渠道 {ready, reason}。

    判定放在后端而不是各前端组件里：渠道可用性取决于「全局邮件服务是否启用」
    这类前端根本看不到的事实，前端各自推断必然与投递路径漂移（曾把可用渠道
    锁死）。这里与发信同源，前端只负责展示。
    """
    user_id = int(user_info["user_id"])
    try:
        channels = await NotificationService.get_channel_readiness(db, user_id)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取通知渠道状态失败: {str(e)}"
        )
    return {"channels": channels}

@router.put("/config", summary="保存当前登录用户的特定通道配置")
async def save_notifications_config(
    req: SaveConfigReq,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session)
):
    user_id = int(user_info["user_id"])
    try:
        await NotificationService.save_user_config(db, user_id, req.channel_type, req.config_data)
        return {"status": "success", "message": "配置保存成功"}
    except ValueError as ve:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(ve)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"保存消息配置失败: {str(e)}"
        )

@router.post("/test", summary="测试消息通道连通性")
async def test_notifications_config(
    req: TestConfigReq,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session)
):
    user_id = int(user_info["user_id"])
    try:
        # 将脱敏字符 "******" 反解回 DB 中真实的值。指定了具体群时按该群反解，
        # 否则多群场景下会拿「默认群」的密钥去测另一个群，结论没有意义。
        resolved = await NotificationService.resolve_masked_config(
            db, user_id, req.channel_type, req.config_data, channel_id=req.channel_id
        )
        # 仅透传展示所需的身份字段，避免把 user_info 中的 api_key 带进消息链路
        actor = {
            "user_name": user_info.get("user_name", ""),
            "real_name": user_info.get("real_name", ""),
        }
        success, err_msg = await NotificationService.test_connection(
            # user_id / db 供邮件渠道解析 smtp_source 分层并推导收件人
            req.channel_type, resolved, actor, user_id=user_id, db=db
        )
        if success:
            return {"status": "success", "message": "测试连通成功"}
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"连通测试失败: {err_msg}"
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"连通测试执行异常: {str(e)}"
        )

@router.get("/channels", summary="获取当前登录用户三个群渠道的全部群配置")
async def get_notification_channels(
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    """返回钉钉 / 飞书 / 企业微信三个渠道下的全部群（含群 ID 与默认标记）。

    与 ``GET /config`` 的区别：那个接口按渠道返回「当前生效的那一条」，
    供任务中心等只需判断「能不能发」的地方使用；这里返回完整列表，
    供个人中心做多群管理。两者并存，避免改动既有调用方。
    """
    user_id = int(user_info["user_id"])
    try:
        return await NotificationService.list_channels(
            db, user_id, include_credentials=True
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取群列表失败: {str(e)}",
        )


@router.put("/channels", summary="整体保存某个渠道的群列表（新增/修改/删除）")
async def save_notification_channels(
    req: SaveChannelsReq,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    """整份提交该渠道的群列表：带 id 的更新、不带 id 的新建、未提交的删除。

    一次事务完成，不会留下「两个默认群」或「默认群被删了没人接班」的中间态。
    """
    user_id = int(user_info["user_id"])
    if req.channel_type not in MULTI_GROUP_CHANNELS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{req.channel_type} 渠道不支持配置多个群",
        )
    try:
        saved = await NotificationService.save_channels(
            db,
            user_id,
            req.channel_type,
            [item.model_dump() for item in req.channels],
        )
        return {"status": "success", "message": "配置保存成功", "channels": saved}
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"保存群配置失败: {str(e)}",
        )
