import logging
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.dependencies import require_api_key, get_db_session, require_permission
from app.services.embed_service import EmbedService, TICKET_TTL_SECONDS
from app.services.wechat_miniprogram_service import (
    MiniprogramIdentityError,
    derive_instance_id,
    resolve_or_create_user,
    warn_if_new_account_burst,
)
from app.schemas.response import StandardResponse
from app.core.rate_limit import hit_rate_limit

logger = logging.getLogger(__name__)

# 需要 API Key 鉴权的路由（供宿主系统后端在服务器内网调用）
router = APIRouter()

# 无需提前持有 API Key、凭 Ticket 自行兑换的路由（供前端 iframe 内部调用）
public_router = APIRouter()

# 频率上限。嵌入入口对第三方站点开放，且小程序侧会按 openid **自动开户**，
# 没有上限时一次被刷就是一批垃圾账号 + Redis 灌满。
# 阈值刻意留了余量：宿主后端可能在一屏里批量签发 ticket，小程序也有早高峰集中冷启动；
# 这里挡的是脚本级刷量（千次/分钟），不是正常业务峰值。
EMBED_TICKET_RATE_LIMIT_PER_MINUTE = 300
MINIPROGRAM_OPENID_RATE_LIMIT_PER_MINUTE = 30
MINIPROGRAM_APPID_RATE_LIMIT_PER_MINUTE = 600


async def _enforce_rate_limit(key: str, limit: int) -> None:
    """超限直接 429。调用点必须在业务 try 之外——HTTPException 也是 Exception。"""
    if await hit_rate_limit(key, limit):
        raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")


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
    allowed_origins: Optional[List[str]] = Field(
        None,
        description=(
            "该票据限定的宿主来源列表，已归一化为 scheme://host[:port]。"
            "空列表表示未限制来源；iframe 据此校验宿主 postMessage 的来源，"
            "而不是无差别接受任意 origin。"
        ),
    )
    session_cookie_issued: Optional[bool] = Field(
        None,
        description="是否已同时下发 embed_session Cookie，供前端确认「刷新可免 URL 凭据」",
    )


class MiniprogramSessionRequest(BaseModel):
    openid: str = Field(
        ...,
        description=(
            "微信用户在该小程序下的 openid。由小程序云函数用 cloud.getWXContext() 取得；"
            "平台不接收浏览器直接传入的值（该接口仅对持 API Key 的云函数开放）。"
        ),
        json_schema_extra={"example": "oABC1234567890123456789012"},
    )
    appid: str = Field(
        ...,
        description="小程序 AppID",
        json_schema_extra={"example": "wx1ca87185956d0071"},
    )
    unionid: Optional[str] = Field(None, description="微信开放平台 unionid，可空")
    agent_id: Optional[str] = Field(None, description="可选：锁定智能体 ID")


class MiniprogramIdentitySummary(BaseModel):
    user_id: int = Field(..., description="平台账号 ID")
    user_name: str = Field(..., description="平台账号名（自 openid 派生）")
    is_new: bool = Field(..., description="本次调用是否新建了账号")


class MiniprogramSessionResponseData(BaseModel):
    ticket: str = Field(..., description="一次性短时 Ticket")
    expires_in: int = Field(..., description="Ticket 有效时长（秒）")
    instance_id: str = Field(
        ..., description="前端会话实例标识（逐 openid 派生，用于会话分桶）"
    )
    user: MiniprogramIdentitySummary = Field(
        ..., description="身份摘要（不含 openid / unionid / API Key）"
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
    # 限流放在 try 之外：下面的 except Exception 会把 HTTPException 一并吞掉转成 500。
    await _enforce_rate_limit(
        f"embed:ticket:{current_user.get('user_id')}",
        EMBED_TICKET_RATE_LIMIT_PER_MINUTE,
    )
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


@router.post(
    "/miniprogram/session",
    response_model=StandardResponse[MiniprogramSessionResponseData],
    summary="小程序微端：按 openid 自动开户并签发嵌入凭证",
    description=(
        "供微信小程序云函数调用（云函数用 cloud.getWXContext() 取得 openid 后转发）。"
        "平台按 (appid, openid) 解析账号；未命中且带了 unionid 则回退 unionid；仍未命中则自动开户、"
        "挂「小程序用户」角色，最后签发一次性 Ticket。"
        "权限复用 POST:/api/v1/embed/tickets —— /embed 路径在 V1 白名单内、自动校验不生效，"
        "故此处必须显式校验，且必须前置于建号，否则会成为刷号入口。"
    ),
    dependencies=[Depends(require_permission("api", "POST:/api/v1/embed/tickets"))],
)
async def create_miniprogram_session(
    payload: MiniprogramSessionRequest,
    current_user: dict = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    from app.services.config_service import ConfigService

    if await ConfigService.get("miniprogram_enabled") != "true":
        raise HTTPException(status_code=403, detail="微信小程序微端未开启")

    # 自动开户是这里最敏感的副作用：同一 openid 每分钟 20 次、同一 appid 每分钟 200 次，
    # 足以覆盖正常冷启动重试，又能挡住脚本批量刷号。
    await _enforce_rate_limit(
        f"embed:mp:openid:{payload.appid}:{payload.openid}",
        MINIPROGRAM_OPENID_RATE_LIMIT_PER_MINUTE,
    )
    await _enforce_rate_limit(
        f"embed:mp:appid:{payload.appid}",
        MINIPROGRAM_APPID_RATE_LIMIT_PER_MINUTE,
    )

    try:
        identity = await resolve_or_create_user(
            openid=payload.openid,
            appid=payload.appid,
            unionid=payload.unionid,
            db=db,
        )
    except MiniprogramIdentityError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    if identity["is_new"]:
        # 防雪崩：只告警、不阻断（见 service 中的 warn_if_new_account_burst）
        await warn_if_new_account_burst()

    try:
        ticket_data = await EmbedService.create_ticket(
            operator_user=current_user,
            target_user_id=identity["user_id"],
            agent_id=payload.agent_id,
            expires_in=TICKET_TTL_SECONDS,
            db=db,
        )
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("Failed to create miniprogram session ticket: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create miniprogram session")

    return StandardResponse(
        data=MiniprogramSessionResponseData(
            ticket=ticket_data["ticket"],
            expires_in=ticket_data["expires_in"],
            instance_id=derive_instance_id(payload.openid),
            user=MiniprogramIdentitySummary(**identity),
        )
    )


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
