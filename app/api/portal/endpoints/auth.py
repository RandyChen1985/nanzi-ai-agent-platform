from fastapi import APIRouter, Depends, HTTPException, status, Response, Header
from typing import Optional
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.dependencies import require_api_key
from app.core.orm import get_db_session
from app.services.auth_service import AuthService

router = APIRouter()

class LoginRequest(BaseModel):
    api_key: Optional[str] = Field(None, description="API 密钥", json_schema_extra={"example": "S63B_..."})
    username: Optional[str] = Field(None, description="用户名")
    password: Optional[str] = Field(None, description="密码")

class SSOLoginRequest(BaseModel):
    username: str = Field(..., description="SSO 用户名")
    password: str = Field(..., description="SSO 密码")

@router.post("/sso/login", summary="SSO 用户登录")
async def sso_login(
    request: SSOLoginRequest, 
    response: Response,
    db: AsyncSession = Depends(get_db_session)
):
    """
    Yovole SSO 统一认证登录接口
    """
    from app.services.config_service import ConfigService
    if await ConfigService.get("yovole_sso_enabled") != "true":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="SSO 统一认证登录已被禁用"
        )

    result = await AuthService.authenticate_sso_user(request.username, request.password, db=db)
    
    if result["status"] == "success":
        user = result["user"]
        user_id = int(user["user_id"])
        api_key = await AuthService.get_decrypted_api_key(user_id, db=db)
        
        if not api_key:
             raise HTTPException(500, "User has no valid API Key for session")

        response.set_cookie(
            key="admin_token",
            value=api_key,
            httponly=True,
            max_age=86400,
            samesite="lax",
            secure=False
        )

        # 注册在线状态到 Redis
        await AuthService.register_online_state(api_key, user)
        
        # 聚合权限信息返回给前端
        from app.services.permission_service import PermissionService
        perm_service = PermissionService(db)
        perms_response = await perm_service.get_user_permissions(user_id)
        
        return {
            "status": "success",
            "data": {
                **user,
                "api_key": api_key,
                "permissions": perms_response.permissions.model_dump()
            }
        }
    elif result["status"] == "error_not_found":
         raise HTTPException(status_code=401, detail=result["message"])
    elif result["status"] == "error_disabled":
         raise HTTPException(status_code=403, detail=result["message"])
    else:
         raise HTTPException(status_code=401, detail=result["message"])

@router.post("/login", summary="用户登录")
async def login(
    request: LoginRequest, 
    response: Response,
    db: AsyncSession = Depends(get_db_session)
):
    """
    用户登录接口 (支持 API Key 或 账号密码)
    """
    user = None
    
    # 1. API Key Login
    if request.api_key:
        user = await AuthService.verify_api_key(request.api_key, db=db)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="无效的 API Key"
            )
        api_key = request.api_key
        # Set cookie for API Key login
        response.set_cookie(
            key="admin_token",
            value=request.api_key,
            httponly=True,
            max_age=86400,
            samesite="lax",
            secure=False
        )

    # 2. Password Login
    elif request.username and request.password:
        # 检查密码长度，bcrypt 限制密码长度为 72 字节
        password_bytes = request.password.encode('utf-8')
        if len(password_bytes) > 72:
            # 截断密码到 72 字节并解码回字符串
            password = password_bytes[:72].decode('utf-8', errors='ignore')
        else:
            password = request.password
        
        result = await AuthService.verify_user_password(request.username, password, db=db)
        if result["status"] == "success":
            user = result["user"]
            user_id = int(user["user_id"])
            api_key = await AuthService.get_decrypted_api_key(user_id, db=db)
            
            if not api_key:
                 raise HTTPException(500, "User has no valid API Key for session")

            response.set_cookie(
                key="admin_token",
                value=api_key,
                httponly=True,
                max_age=86400,
                samesite="lax",
                secure=False
            )
            # 注册在线状态到 Redis
            await AuthService.register_online_state(api_key, user)
        elif result["status"] == "error_no_password":
             raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, 
                detail=result["message"]
            )
        else:
             raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=result["message"]
            )
    else:
        raise HTTPException(
            status_code=400,
            detail="必须提供 API Key 或 用户名/密码"
        )
    
    # 聚合权限信息返回给前端
    from app.services.permission_service import PermissionService
    user_id_int = int(user["user_id"])
    perm_service = PermissionService(db)
    perms_response = await perm_service.get_user_permissions(user_id_int)
    
    return {
        "status": "success",
        "data": {
            **user,
            "api_key": api_key,  # Include API Key for frontend storage
            "permissions": perms_response.permissions.model_dump()
        }
    }

class PasswordChangeRequest(BaseModel):
    password: str = Field(..., min_length=6, description="新密码")

@router.put("/password", summary="修改密码")
async def change_password(
    request: PasswordChangeRequest,
    user: dict = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session)
):
    """
    修改当前用户密码
    """
    user_id = int(user["user_id"])
    
    # 检查密码长度，bcrypt 限制密码长度为 72 字节
    password_bytes = request.password.encode('utf-8')
    if len(password_bytes) > 72:
        # 截断密码到 72 字节并解码回字符串
        password = password_bytes[:72].decode('utf-8', errors='ignore')
    else:
        password = request.password
    
    success = await AuthService.set_user_password(user_id, password, db=db)
    
    if success:
        return {"status": "success", "message": "密码修改成功"}
    else:
         raise HTTPException(500, "密码修改失败")

@router.post("/logout", summary="退出登录")
async def logout(
    response: Response,
    api_key: Optional[str] = Header(None, alias="X-API-Key")
):
    """
    退出登录并清除 Cookie 和 Redis 缓存
    """
    if api_key:
        await AuthService.expire_api_key(api_key)
        
    response.delete_cookie(key="admin_token")
    return {"status": "success", "message": "Logged out successfully"}



@router.get("/me", summary="获取当前用户信息")
async def get_current_user_info(
    user: dict = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session)
):
    """
    获取当前认证用户的详细信息（包含权限集）
    """
    from app.services.permission_service import PermissionService
    user_id = int(user["user_id"])
    
    # 实时获取最新的权限聚合信息
    perm_service = PermissionService(db)
    perms_response = await perm_service.get_user_permissions(user_id)
    
    from app.services.config_service import ConfigService
    watermark_enabled = await ConfigService.get("embedchat_watermark_enabled") == "true"
    watermark_style = await ConfigService.get("embedchat_watermark_style") or "user_time"
    watermark_text = await ConfigService.get("embedchat_watermark_text") or "云枢系统"

    return {
        "status": "success",
        "data": {
            "id": user.get("user_id"),
            "user_id": user.get("user_id"),
            "user_name": user.get("user_name"),
            "real_name": user.get("real_name") or user.get("user_name"),
            "role": user.get("role"),
            "dept_code": user.get("dept_code"),
            "org_path": user.get("org_path"),
            "extra_data": user.get("extra_data"),
            "created_at": user.get("created_at"),
            "remark": user.get("remark"),
            "status": "active",
            "permissions": perms_response.permissions.model_dump(),
            "watermark": {
                "enabled": watermark_enabled,
                "style": watermark_style,
                "text": watermark_text
            }
        }
    }

@router.get("/permissions", summary="获取当前用户权限列表")
async def get_my_permissions(
    user: dict = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session)
):
    """
    获取当前用户的资源权限列表
    """
    from app.services.permission_service import PermissionService
    user_id = int(user["user_id"])
    service = PermissionService(db)
    return await service.get_user_permissions(user_id)

@router.get("/user_apikey", summary="验证 API Key 有效性")
async def validate_user_apikey(
    user: dict = Depends(require_api_key)
):
    """
    内部接口：
    用于 EmbedChat 或其他组件验证传入的 API Key 是否有效。
    通过 Authorization 头传递 Key。
    如果有效，返回 200 和基础用户信息。
    """
    from app.services.config_service import ConfigService
    watermark_enabled = await ConfigService.get("embedchat_watermark_enabled") == "true"
    watermark_style = await ConfigService.get("embedchat_watermark_style") or "user_time"
    watermark_text = await ConfigService.get("embedchat_watermark_text") or "云枢系统"

    return {
        "status": "success",
        "data": {
            "valid": True,
            "user_id": user.get("user_id"),
            "user_name": user.get("user_name"),
            "real_name": user.get("real_name") or user.get("user_name"),
            "role": user.get("role"),
            "watermark": {
                "enabled": watermark_enabled,
                "style": watermark_style,
                "text": watermark_text
            }
        }
    }


@router.get("/config/public", summary="获取公开配置")
async def get_public_config(
    db: AsyncSession = Depends(get_db_session)
):
    """
    获取不需要登录即可访问的系统配置（如是否启用 SSO）
    """
    from app.services.config_service import ConfigService
    sso_enabled = await ConfigService.get("yovole_sso_enabled") == "true"
    return {
        "status": "success",
        "data": {
            "yovole_sso_enabled": sso_enabled
        }
    }


@router.get("/branding", summary="获取公开品牌配置")
async def get_public_branding():
    """登录页与前端展示用，无需鉴权。"""
    from app.services.branding_settings_service import BrandingSettingsService

    return {
        "status": "success",
        "data": await BrandingSettingsService.get_public_branding(),
    }
