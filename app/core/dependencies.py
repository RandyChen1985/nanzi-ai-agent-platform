from fastapi import Header, HTTPException, status, Request, Depends
from typing import Optional, Dict
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.auth_service import AuthService
from app.core import redis
from app.core.orm import get_db_session
import datetime

async def require_api_key(
    request: Request,
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

    # Support Cookie (admin_token)
    if not api_key:
        api_key = request.cookies.get("admin_token")

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
    # 1. Identify Resource
    route = request.scope.get("route")
    if not route:
        return user_info
        
    path_template = route.path
    method = request.method
    resource_id = f"{method}:{path_template}"
    
    # Whitelist Core Endpoints (Allow all authenticated users)
    # 1. All Chat related endpoints
    if "/chat" in path_template and not path_template.startswith("/api/v1/chatbi"):
        return user_info
    
    # 2. All Task related endpoints (User-owned resources)
    if "/tasks" in path_template:
        return user_info
    
    try:
        user_id = int(user_info["user_id"])
    except (ValueError, TypeError):
        # Should be handled by require_api_key but safe check
        raise HTTPException(status_code=401, detail="Invalid User ID")
    
    # 2. Check Permission
    from app.services.permission_service import PermissionService
    service = PermissionService(db)
    
    has_perm = await service.check_permission(user_id, "api", resource_id)
    if not has_perm:
         raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Permission denied for API: {resource_id} (Path: {path_template})"
        )
        
    return user_info




