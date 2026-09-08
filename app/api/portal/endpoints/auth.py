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
        # 记录用户登录时间
        await AuthService.record_user_login(user_id, db=db)
        
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
        await AuthService.record_user_login(int(user["user_id"]), db=db)

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

            # 2FA 检查：若开启了 Google 二次验证，不下发正式 Session Cookie，返回临时票据
            if user.get("two_factor_enabled"):
                two_factor_token = await AuthService.create_2fa_pending_token(user_id)
                return {
                    "status": "two_factor_required",
                    "data": {
                        "two_factor_required": True,
                        "two_factor_token": two_factor_token,
                        "user_name": user["user_name"],
                        "real_name": user.get("real_name") or user["user_name"]
                    }
                }

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
            # 记录用户登录时间
            await AuthService.record_user_login(user_id, db=db)
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

class TwoFactorLoginRequest(BaseModel):
    two_factor_token: str = Field(..., description="两步验证临时凭证")
    code: str = Field(..., description="Google 身份验证器 6 位动态验证码")

@router.post("/login/2fa", summary="两步验证二次登录")
async def two_factor_login(
    request: TwoFactorLoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_db_session)
):
    """
    两步验证登录接口 (使用 6 位 TOTP 动态码验证)
    """
    user = await AuthService.verify_and_consume_2fa_pending_token(
        request.two_factor_token,
        request.code,
        db=db
    )
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="动态验证码错误或已失效，请重新输入"
        )
    
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
    # 记录用户登录时间
    await AuthService.record_user_login(user_id, db=db)

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

class PasswordChangeRequest(BaseModel):
    password: str = Field(..., min_length=8, max_length=32, description="新密码（须符合等保复杂度要求）")

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
    username = user.get("user_name")
    
    # 校验密码复杂度（等保要求）
    valid, msg = AuthService.validate_password_complexity(request.password, username=username)
    if not valid:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg)
    
    # 检查密码长度，bcrypt 限制密码长度为 72 字节
    password_bytes = request.password.encode('utf-8')
    if len(password_bytes) > 72:
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
    watermark_text = await ConfigService.get("embedchat_watermark_text") or "南孜系统"

    two_factor_enabled = await AuthService.get_user_2fa_status(user_id, db=db)

    from app.models.user import User
    from datetime import datetime
    user_obj = await db.get(User, user_id)
    
    # 获取密码修改间隔配置天数（默认 30 天）
    password_expire_days_val = await ConfigService.get("password_expire_days") or "30"
    try:
        password_expire_days = max(1, int(password_expire_days_val))
    except (ValueError, TypeError):
        password_expire_days = 30

    has_password = bool(user_obj and user_obj.password_hash)
    password_updated_at = None
    days_since_last_change = None
    days_until_next_change = None
    is_expired = False

    if has_password and user_obj:
        dt = user_obj.password_updated_at or user_obj.updated_at or user_obj.created_at
        if dt:
            password_updated_at = dt.strftime("%Y-%m-%d %H:%M:%S")
            now = datetime.now()
            delta = now - dt
            days_since_last_change = max(0, delta.days)
            days_until_next_change = password_expire_days - days_since_last_change
            is_expired = days_until_next_change <= 0

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
            "last_login_at": user_obj.last_login_at.strftime("%Y-%m-%d %H:%M:%S") if (user_obj and user_obj.last_login_at) else None,
            "remark": user.get("remark"),
            "status": "active",
            "two_factor_enabled": two_factor_enabled,
            "password_info": {
                "has_password": has_password,
                "password_updated_at": password_updated_at,
                "password_expire_days": password_expire_days,
                "days_since_last_change": days_since_last_change,
                "days_until_next_change": days_until_next_change,
                "is_expired": is_expired,
            },
            "permissions": perms_response.permissions.model_dump(),
            "watermark": {
                "enabled": watermark_enabled,
                "style": watermark_style,
                "text": watermark_text
            }
        }
    }

class EnableTwoFactorRequest(BaseModel):
    code: str = Field(..., min_length=6, max_length=6, description="Google 身份验证器 6 位动态验证码")

class DisableTwoFactorRequest(BaseModel):
    code: Optional[str] = Field(None, description="Google 身份验证器 6 位动态验证码")
    password: Optional[str] = Field(None, description="用户当前登录密码")

@router.get("/2fa/status", summary="获取两步验证状态")
async def get_two_factor_status(
    user: dict = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session)
):
    """
    获取当前登录用户的两步验证 (2FA) 状态
    """
    user_id = int(user["user_id"])
    enabled = await AuthService.get_user_2fa_status(user_id, db=db)
    return {
        "status": "success",
        "data": {
            "enabled": enabled
        }
    }

@router.post("/2fa/setup", summary="发起两步验证绑定")
async def setup_two_factor(
    user: dict = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session)
):
    """
    发起两步验证绑定，生成 Base32 临时 Secret 及 otpauth 链接
    """
    from app.services.totp_service import TotpService
    user_id = int(user["user_id"])
    secret = TotpService.generate_secret()
    await AuthService.create_2fa_setup_cache(user_id, secret, ttl=600)
    
    from app.services.branding_settings_service import BrandingSettingsService
    branding = await BrandingSettingsService.get_public_branding()
    issuer = (branding.get("product_name") or "NanZi Platform").strip()
    
    otpauth_url = TotpService.generate_otpauth_url(
        user_name=user.get("user_name", "user"),
        secret=secret,
        issuer=issuer
    )
    return {
        "status": "success",
        "data": {
            "secret": secret,
            "otpauth_url": otpauth_url
        }
    }

@router.post("/2fa/enable", summary="确认开启两步验证")
async def enable_two_factor(
    request: EnableTwoFactorRequest,
    user: dict = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session)
):
    """
    提交手机 Google 身份验证器生成的 6 位动态码，验证成功后正式启用 2FA
    """
    from app.services.totp_service import TotpService
    user_id = int(user["user_id"])
    secret = await AuthService.get_2fa_setup_cache(user_id)
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="两步验证设置会话已过期或未发起，请重新点击开启"
        )
    
    if not TotpService.verify_code(secret, request.code):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="动态验证码错误，请确保手机时间同步后重新输入"
        )
    
    await AuthService.enable_user_2fa(user_id, secret, db=db)
    await AuthService.clear_2fa_setup_cache(user_id)
    return {
        "status": "success",
        "message": "两步验证开启成功"
    }

@router.post("/2fa/disable", summary="关闭两步验证")
async def disable_two_factor(
    request: DisableTwoFactorRequest,
    user: dict = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session)
):
    """
    关闭两步验证 (需校验动态码或当前登录密码)
    """
    from app.services.totp_service import TotpService
    from app.models.user import User
    user_id = int(user["user_id"])
    
    user_obj = await db.get(User, user_id)
    if not user_obj:
        raise HTTPException(status_code=404, detail="用户不存在")
    
    if not user_obj.two_factor_enabled:
        return {"status": "success", "message": "两步验证尚未开启"}

    verified = False
    if request.code:
        secret = await AuthService.get_user_2fa_secret(user_id, db=db)
        if secret and TotpService.verify_code(secret, request.code):
            verified = True
            
    if not verified and request.password and user_obj.password_hash:
        if AuthService.verify_password_hash(request.password, user_obj.password_hash):
            verified = True
            
    if not verified:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="验证失败，请输入正确的 6 位 Google 动态验证码或当前登录密码"
        )

    await AuthService.disable_user_2fa(user_id, db=db)
    return {
        "status": "success",
        "message": "两步验证已关闭"
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
    watermark_text = await ConfigService.get("embedchat_watermark_text") or "南孜系统"

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


class ResetMyApiKeyRequest(BaseModel):
    password: Optional[str] = Field(None, description="登录密码")
    code: Optional[str] = Field(None, description="Google 身份验证器 6 位动态验证码")

@router.post("/api-key/reset", summary="重置当前用户 API Key")
async def reset_my_api_key(
    request: ResetMyApiKeyRequest,
    response: Response,
    user: dict = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session)
):
    """
    当前用户重置个人 API Key：
    - 若未开启两步验证，必须校验当前登录密码；
    - 若已开启两步验证，可通过当前登录密码或 Google 动态验证码（二选一）校验。
    """
    from app.services.totp_service import TotpService
    from app.models.user import User

    user_id = int(user["user_id"])
    user_obj = await db.get(User, user_id)
    if not user_obj:
        raise HTTPException(status_code=404, detail="用户不存在")

    is_2fa = bool(user_obj.two_factor_enabled)
    verified = False

    # 1. 动态码验证（仅当开启 2FA 且提供了验证码时有效）
    if is_2fa and request.code and request.code.strip():
        secret = await AuthService.get_user_2fa_secret(user_id, db=db)
        if secret and TotpService.verify_code(secret, request.code.strip()):
            verified = True
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="动态验证码错误或已失效"
            )

    # 2. 密码验证
    if not verified and request.password:
        if not user_obj.password_hash:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="当前账户尚未设置登录密码，请先设置密码或使用动态验证码"
            )
        if AuthService.verify_password_hash(request.password, user_obj.password_hash):
            verified = True
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="登录密码错误"
            )

    if not verified:
        if is_2fa:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="请提供当前登录密码或 6 位 Google 动态验证码进行身份验证"
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="请提供当前登录密码进行身份验证"
            )

    new_api_key = await AuthService.reset_api_key(user_id, db=db)
    if not new_api_key:
        raise HTTPException(status_code=500, detail="重置 API Key 失败")

    # 同步更新当前会话 Cookie 与在线状态
    response.set_cookie(
        key="admin_token",
        value=new_api_key,
        httponly=True,
        max_age=86400,
        samesite="lax",
        secure=False
    )
    await AuthService.register_online_state(new_api_key, user)

    return {
        "status": "success",
        "message": "API Key 重置成功",
        "api_key": new_api_key
    }


@router.get("/config/public", summary="获取公开配置")
async def get_public_config(
    db: AsyncSession = Depends(get_db_session)
):
    """
    获取不需要登录即可访问的系统配置（如是否启用 SSO）
    """
    from app.services.config_service import ConfigService
    from app.services.platform_timezone import (
        DEFAULT_PLATFORM_TIMEZONE,
        PLATFORM_TIMEZONE_CONFIG_KEY,
        get_platform_timezone,
    )

    sso_enabled = await ConfigService.get("yovole_sso_enabled") == "true"
    hide_login_apikey = await ConfigService.get("hide_login_apikey") == "true"
    platform_timezone = await get_platform_timezone()
    return {
        "status": "success",
        "data": {
            "yovole_sso_enabled": sso_enabled,
            "hide_login_apikey": hide_login_apikey,
            "platform_timezone": platform_timezone or DEFAULT_PLATFORM_TIMEZONE,
            PLATFORM_TIMEZONE_CONFIG_KEY: platform_timezone or DEFAULT_PLATFORM_TIMEZONE,
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
