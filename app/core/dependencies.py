import logging

from fastapi import Header, HTTPException, status, Request, Response, Depends
from typing import Optional, Dict
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.auth_service import AuthService
from app.services.online_presence_service import OnlinePresenceService
from app.core import redis
from app.core.orm import get_db_session
import datetime

logger = logging.getLogger(__name__)

# 会话 Cookie 的有效期（秒）。服务端会话会随活跃调用滑动续期，而 Cookie 的 max_age
# 自下发起固定计算，二者必须保持同值，并在每个经 Cookie 认证的请求上重新下发。
SESSION_COOKIE_MAX_AGE = 86400


def is_secure_request(request: Request) -> bool:
    """判断当前请求是否应下发 Secure Cookie。

    TLS 可能终止在反向代理（k8s ingress / nginx），此时后端看到的是 http，
    因此优先信任代理写入的 X-Forwarded-Proto。客户端伪造该头只会让自己的
    Cookie 收不到，不构成安全绕过；纯 HTTP 部署下返回 False，保持现有行为可登录。
    """
    forwarded = request.headers.get("x-forwarded-proto")
    if forwarded:
        return forwarded.split(",")[0].strip().lower() == "https"
    return request.url.scheme == "https"


def _renew_session_cookie(
    request: Request, response: Response, name: str, value: str
) -> None:
    """重新下发会话 Cookie，令其 max_age 从当前时刻起算。

    服务端会话在活跃调用时会滑动续期，Cookie 的 max_age 却是固定的，二者必然脱节：
    连续使用满 24 小时后服务端会话仍有效，浏览器却已丢弃 Cookie，刷新会无故要求
    重新登录。每个经 Cookie 认证的请求都重发一次，即可让两者同步顺延。
    """
    response.set_cookie(
        key=name,
        value=value,
        httponly=True,
        max_age=SESSION_COOKIE_MAX_AGE,
        samesite="lax",
        secure=is_secure_request(request),
    )

async def require_api_key(
    request: Request,
    response: Response,
    api_key_header: Optional[str] = Header(default=None, alias="X-API-Key"),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    db: AsyncSession = Depends(get_db_session)
) -> Dict:
    api_key = api_key_header
    
    # Support Bearer Token
    if not api_key and authorization:
        if authorization.startswith("Bearer "):
            api_key = authorization.split(" ")[1]
        else:
            api_key = authorization

    # Support Cookie：admin_token 优先——门户登录态优先于嵌入会话，否则平台内嵌
    # iframe 场景会被降级或串号。
    session_cookie_name: Optional[str] = None
    # 凭据来源。调用方（如 user_apikey 判断是否该下发 embed_session）应按「来源」决策，
    # 而不是拿凭据值互相比较——值相等不代表来源相同（如门户 Cookie 里恰好就是同一个
    # 真实 Key），值比较会在那种情形下漏判。
    credential_source = "header"
    if not api_key:
        api_key = request.cookies.get("admin_token")
        if api_key:
            session_cookie_name = "admin_token"
            credential_source = "cookie:admin_token"

    # 嵌入会话：由 /api/portal/auth/user_apikey 在「显式凭据校验通过」后下发，
    # 使嵌入页刷新时无需再依赖 URL 里的长期 API Key。独立于 admin_token，
    # 因此打开嵌入页不会顶掉用户自己的门户登录态。
    if not api_key:
        api_key = request.cookies.get("embed_session")
        if api_key:
            session_cookie_name = "embed_session"
            credential_source = "cookie:embed_session"

    if not api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing API Key or Token")
    
    user_info = await AuthService.verify_api_key(api_key, db)
    if not user_info:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API Key")
    
    # Keep the raw API Key for downstream（如工具链、上下文管理）；勿回显到不可信客户端。
    try:
        user_info["api_key"] = api_key
    except Exception:
        pass

    request.state.user = user_info
    request.state.credential_source = credential_source

    # 在线状态是展示性数据；Redis 写入失败不能影响正常认证和业务请求。
    try:
        await OnlinePresenceService.touch(user_info)
    except Exception as exc:
        logger.warning("更新在线用户状态失败，不影响本次认证: %s", exc)

    # 会话 Cookie 的 max_age 自下发起固定，而服务端会话会随活跃滑动续期，二者会脱节
    # （浏览器先失效，刷新时无故要求重新登录）。仅在凭据确实来自 Cookie 时重发；
    # header 传来的凭据不得被写进 Cookie，否则等于凭空建立浏览器会话。
    if session_cookie_name:
        _renew_session_cookie(request, response, session_cookie_name, api_key)

    return user_info

async def check_rate_limit(user_id: str):
    """Helper for rate limiting"""
    r = await redis.get_redis()
    if r:
        key = f"rate_limit:{user_id}:{datetime.datetime.now().minute}"
        current = await r.incr(key)
        if current == 1:
            await r.expire(key, 60)
        if current > 1000:
            raise HTTPException(status_code=429, detail="Too Many Requests")

async def require_admin(user: Dict = Depends(require_api_key)) -> Dict:
    """
    Dependency to ensure the current user is an admin.
    Raises 403 if user is not admin.
    """
    if user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )
    return user

def require_permission(resource_type: str, resource_id: str):
    """
    Dependency factory to check for a specific permission.
    Admins bypass this check.
    """
    async def _check_perm(
        user: Dict = Depends(require_api_key),
        db: AsyncSession = Depends(get_db_session)
    ) -> Dict:
        if user.get("role") == "admin":
            return user
        
        from app.services.permission_service import PermissionService
        service = PermissionService(db)
        user_id = int(user["user_id"])
        
        has_perm = await service.check_permission(user_id, resource_type, resource_id)
        if not has_perm:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission required: {resource_id}"
            )
        return user
    return _check_perm

# Alias for consistent naming
get_current_user = require_api_key

async def verify_v1_api_access(
    request: Request,
    user_info: Dict = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Enforce permission check for V1 External APIs.
    Checks if the user has explicit 'api' permission for the current endpoint.
    """
    from app.core.v1_api_access import is_v1_api_whitelisted, resolve_v1_api_resource_id

    if not request.scope.get("route"):
        return user_info

    resource_id, path_template = resolve_v1_api_resource_id(request)

    # Whitelist Core Endpoints (Allow all authenticated users)
    if is_v1_api_whitelisted(path_template) or is_v1_api_whitelisted(request.url.path):
        return user_info

    try:
        user_id = int(user_info["user_id"])
    except (ValueError, TypeError):
        raise HTTPException(status_code=401, detail="Invalid User ID")

    from app.services.permission_service import PermissionService
    service = PermissionService(db)

    has_perm = await service.check_permission(user_id, "api", resource_id)
    if not has_perm:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Permission denied for API: {resource_id} (Path: {path_template})",
        )

    return user_info



