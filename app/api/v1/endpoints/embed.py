import logging
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.dependencies import require_api_key, get_db_session
from app.services.embed_service import EmbedService
from app.schemas.response import StandardResponse

logger = logging.getLogger(__name__)

# 需要 API Key 鉴权的路由（供宿主系统后端在服务器内网调用）
router = APIRouter()

# 无需提前持有 API Key、凭 Ticket 自行兑换的路由（供前端 iframe 内部调用）
public_router = APIRouter()


class CreateTicketRequest(BaseModel):
    username: Optional[str] = Field(
        None,
        description="目标用户名（代表哪个用户进行交互）。若不传，默认代表当前 API Key 所属用户自己。",
        json_schema_extra={"example": "zhangsan"},
    )
    user_id: Optional[int] = Field(
        None,
        description="目标用户ID。与 username 二选一。",
        json_schema_extra={"example": 123},
    )
    agent_id: Optional[str] = Field(
        None,
        description="指定锁定的智能体 ID。可选。",
        json_schema_extra={"example": "sys-agent-chatbi"},
    )
    allowed_origins: Optional[List[str]] = Field(
        None,
        description="限定允许嵌入的宿主域名列表（防盗链）。如 ['https://crm.example.com']。",
        json_schema_extra={"example": ["https://crm.example.com"]},
    )
    expires_in: Optional[int] = Field(
        300,
        description="Ticket 兑换有效时长（秒），默认 300 秒（5分钟），最大 1800 秒。",
        json_schema_extra={"example": 300},
    )


class TicketResponseData(BaseModel):
    ticket: str = Field(..., description="一次性短时 Ticket 字符串")
    expires_in: int = Field(..., description="Ticket 有效时长（秒）")
    target_user: dict = Field(..., description="目标用户信息摘要")


class ExchangeTicketRequest(BaseModel):
    ticket: str = Field(..., description="一次性短时 Ticket 字符串", json_schema_extra={"example": "emt_..."})


class SessionTokenResponseData(BaseModel):
    session_token: str = Field(..., description="短期受限会话凭证（供后续对话 API 使用）")
    expires_in: int = Field(..., description="Session 有效期（秒），活跃调用会自动滑动延长")
    user_info: dict = Field(..., description="用户基本信息")
    agent_id: Optional[str] = Field(None, description="绑定的智能体 ID")
    session_cookie_issued: Optional[bool] = Field(
        None,
        description="是否已同时下发 embed_session Cookie，供前端确认「刷新可免 URL 凭据」",
    )


@router.post(
    "/tickets",
    response_model=StandardResponse[TicketResponseData],
    summary="签发嵌入式临时票据 (Embed Ticket)",
    description=(
        "供宿主业务系统的后端服务在内网安全调用。通过宿主服务账号代表指定用户签发一次性短时 Ticket。"
        "长期 API Key 留在宿主后端，仅将临时 Ticket 下发给前端浏览器。"
    ),
)
async def create_embed_ticket(
    payload: CreateTicketRequest,
    current_user: dict = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        data = await EmbedService.create_ticket(
            operator_user=current_user,
            target_username=payload.username,
            target_user_id=payload.user_id,
            agent_id=payload.agent_id,
            allowed_origins=payload.allowed_origins,
            expires_in=payload.expires_in or 300,
            db=db,
        )
        return StandardResponse(data=data)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        logger.error("Failed to create embed ticket: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create embed ticket")


@public_router.post(
    "/tickets/exchange",
    response_model=StandardResponse[SessionTokenResponseData],
    summary="兑换嵌入式临时会话 (Exchange Embed Ticket)",
    description=(
        "供前端 iframe 内部在加载时调用。传入一次性 Ticket，原子核销后换取短期 Session Token。"
        "Session Token 在持续对话中会自动滑动续期，闲置超时后自动作废。"
    ),
)
async def exchange_embed_ticket(
    request: Request,
    response: Response,
    payload: ExchangeTicketRequest,
):
    origin = request.headers.get("Origin") or request.headers.get("Referer")
    try:
        data = await EmbedService.exchange_ticket(
            ticket=payload.ticket,
            origin=origin,
        )
        # 与 /api/portal/auth/user_apikey 对称：把兑换来的**会话**写入 HttpOnly Cookie，
        # 让 iframe 内刷新无需再依赖 URL 里的 ticket——ticket 是一次性的（已 GETDEL
        # 核销）且默认仅 5 分钟，放哪都撑不过一次刷新，能撑住刷新的只有 emb_ses_。
        # 延迟导入以避免 api.v1 与 api.portal 之间的循环依赖。
        from app.api.portal.endpoints.auth import _set_embed_session_cookie

        _set_embed_session_cookie(request, response, data["session_token"])
        data["session_cookie_issued"] = True
        return StandardResponse(data=data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        logger.error("Failed to exchange embed ticket: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to exchange embed ticket")
