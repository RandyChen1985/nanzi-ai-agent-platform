from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Dict, Any
from pydantic import BaseModel, Field

from app.core.dependencies import require_api_key
from app.core.orm import get_db_session
from app.services.notification_service import NotificationService

router = APIRouter()

class SaveConfigReq(BaseModel):
    channel_type: str = Field(..., description="通道类型, 如 dingtalk, wechat_work, email")
    config_data: Dict[str, Any] = Field(..., description="配置细节")

class TestConfigReq(BaseModel):
    channel_type: str = Field(..., description="通道类型, 如 dingtalk, wechat_work, email")
    config_data: Dict[str, Any] = Field(..., description="配置细节")

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
        # 将脱敏字符 "******" 反解回 DB 中真实的值
        resolved = await NotificationService.resolve_masked_config(
            db, user_id, req.channel_type, req.config_data
        )
        # 仅透传展示所需的身份字段，避免把 user_info 中的 api_key 带进消息链路
        actor = {
            "user_name": user_info.get("user_name", ""),
            "real_name": user_info.get("real_name", ""),
        }
        success, err_msg = await NotificationService.test_connection(
            req.channel_type, resolved, actor
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
