from fastapi import APIRouter, Body, Depends, Query, HTTPException, status, Request
from pydantic import BaseModel, Field, field_validator
from typing import Any, Dict, Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete, func, desc, or_
from sqlalchemy.orm import selectinload
from app.core.dependencies import require_admin, require_api_key, require_permission, require_permission
from app.core.orm import get_db_session
from app.services.auth_service import AuthService
from app.models.user import (
    USER_STATUS_DISABLED,
    USER_STATUS_ENABLED,
    USER_STATUS_PENDING_REVIEW,
    User,
)
from app.schemas.permission import UserPermissionsResponse, PermissionUpdate
from app.services.permission_service import PermissionService
from app.models.agent import AIAgent
from app.models.permission import ResourcePermission, Role, UserRoleRelation
from app.models.wechat_binding import WechatBinding
from app.services.sso_user import LaplacePortalApiClient
from app.services.db_connection_service import DbConnectionService
import json
import logging

logger = logging.getLogger(__name__)

# Admin-only router
router = APIRouter()

# Request/Response Models
class AgentRestrictionUpdate(BaseModel):
    """智能体级「功能限定」的写入体。

    刻意用 schema 而不是裸 ``Dict``：这两个字段**逐项决定落库行**，而服务层是按
    ``for value in values`` 迭代的——直接传字符串会被按**字符**拆开，``"exec_command"``
    变成 e/x/c/_/o/m/a/n/d 九条单字符记录，HTTP 还返回 200，而原有禁用项已被 DELETE
    清空。把"必须是字符串数组"挡在入口，比事后靠日志发现垃圾数据便宜得多。
    """

    forbidden_tools: Optional[List[str]] = None
    forbidden_skills: Optional[List[str]] = None

    @field_validator("forbidden_tools", "forbidden_skills", mode="before")
    @classmethod
    def _must_be_list_or_none(cls, value: Any) -> Any:
        if value is None or isinstance(value, list):
            return value
        raise ValueError("必须是字符串数组或 null")

class CreateUserRequest(BaseModel):
    user_name: str
    real_name: Optional[str] = None
    role: str = "user"  # "admin" or "user"
    dept_code: Optional[str] = None
    org_path: Optional[str] = None
    extra_data: Optional[str] = None
    allowed_resources: Optional[list] = []
    role_ids: Optional[List[int]] = [] # Business Roles
    remark: Optional[str] = None
    email: Optional[str] = None

class SsoSyncRequest(BaseModel):
    usernames: List[str]
    role: str = "user"
    role_ids: Optional[List[int]] = []

class SetPasswordRequest(BaseModel):
    password: str = Field(..., min_length=8, max_length=32, description="新密码（须符合等保复杂度要求）")

# ... existing code ...

@router.get("/sso-users")
async def get_sso_users(
    admin: dict = Depends(require_permission("element", "element:user:edit")),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Fetch users from SSO and mark those already in local DB.
    """
    from app.services.config_service import ConfigService
    if await ConfigService.get("yovole_sso_enabled") != "true":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="SSO 统一认证登录已被禁用"
        )
    try:
        # 1. Get all users from SSO
        sso_users = LaplacePortalApiClient.get_all_users()
        
        # 2. Get all existing usernames from local DB
        stmt = select(User.user_name)
        result = await db.execute(stmt)
        existing_usernames = set(result.scalars().all())
        
        # 3. Mark synced status
        items = []
        for user in sso_users:
            items.append({
                **user,
                "is_synced": user["code"] in existing_usernames
            })
            
        return {"items": items}
    except Exception as e:
        logger.error(f"Failed to fetch SSO users: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/sso-sync")
async def sync_sso_users(
    request: SsoSyncRequest,
    admin: dict = Depends(require_permission("element", "element:user:edit")),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Bulk sync selected users from SSO to local DB with role assignment.
    """
    from app.services.config_service import ConfigService
    if await ConfigService.get("yovole_sso_enabled") != "true":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="SSO 统一认证登录已被禁用"
        )
    if not request.usernames:
        return {"message": "No users selected"}
        
    try:
        # 1. Get SSO data to fetch real names/emails
        sso_users = LaplacePortalApiClient.get_all_users()
        sso_map = {u["code"]: u for u in sso_users}
        
        # 2. Filter out already existing users
        stmt = select(User.user_name).where(User.user_name.in_(request.usernames))
        existing = set((await db.execute(stmt)).scalars().all())
        
        to_sync = [u for u in request.usernames if u not in existing]
        
        count = 0
        service = PermissionService(db)
        
        for username in to_sync:
            sso_data = sso_map.get(username)
            if not sso_data:
                continue
                
            # Create user and generate API Key
            await AuthService.generate_api_key(
                user_name=username,
                real_name=sso_data.get("name"),
                role=request.role,
                remark=f"SSO Sync: {sso_data.get('department')} / {sso_data.get('position')}",
                db=db
            )
            
            # Find newly created user
            res = await db.execute(select(User).where(User.user_name == username))
            new_user = res.scalar_one()
            
            # Assign Business Roles
            if request.role_ids:
                await service.update_user_roles(new_user.id, request.role_ids)
                
            count += 1
            
        return {"message": f"Successfully synced {count} users", "count": count}
    except Exception as e:
        logger.error(f"SSO Sync Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# --- Third-party user sync ---

from app.schemas.user_sync import (
    ThirdPartyUserSyncConfig,
    ThirdPartyUserSyncConfigUpdate,
    ThirdPartyUserSyncRunRequest,
    ThirdPartyUserSyncPreviewRequest,
)
from app.services.user_sync_service import UserSyncService


@router.get("/third-party-sync/config")
async def get_third_party_sync_config(
    admin: dict = Depends(require_permission("element", "element:user:edit")),
):
    config = await UserSyncService.get_config()
    return {"data": config.model_dump()}


@router.put("/third-party-sync/config")
async def update_third_party_sync_config(
    body: ThirdPartyUserSyncConfigUpdate,
    admin: dict = Depends(require_permission("element", "element:user:edit")),
):
    try:
        saved = await UserSyncService.save_config(
            body,
            changed_by=admin.get("user_name", "admin"),
        )
        return {"message": "配置已保存", "data": saved.model_dump()}
    except Exception as e:
        logger.error(f"Save third-party sync config failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/third-party-sync/datasources")
async def list_third_party_sync_datasources(
    admin: dict = Depends(require_permission("element", "element:user:edit")),
    db: AsyncSession = Depends(get_db_session),
):
    configs = await DbConnectionService.list_configs(db)
    return {
        "items": [
            {
                "id": c.id,
                "name": c.name,
                "db_type": c.db_type,
                "database_name": c.database_name,
            }
            for c in configs
        ]
    }


@router.get("/third-party-sync/tables")
async def list_third_party_sync_tables(
    connection_config_id: int = Query(..., description="数据源配置 ID"),
    admin: dict = Depends(require_permission("element", "element:user:edit")),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        tables = await UserSyncService.list_tables(db, connection_config_id)
        return {"items": tables}
    except Exception as e:
        logger.error(f"List third-party sync tables failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/third-party-sync/columns")
async def list_third_party_sync_columns(
    connection_config_id: int = Query(..., description="数据源配置 ID"),
    table_name: str = Query(..., description="表名"),
    admin: dict = Depends(require_permission("element", "element:user:edit")),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        columns = await UserSyncService.list_columns(db, connection_config_id, table_name)
        return {"items": columns}
    except Exception as e:
        logger.error(f"List third-party sync columns failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/third-party-sync/preview")
async def preview_third_party_sync_users(
    request: ThirdPartyUserSyncPreviewRequest,
    admin: dict = Depends(require_permission("element", "element:user:edit")),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        cfg = request.config or await UserSyncService.get_config()
        items = await UserSyncService.preview_users(db, config=cfg)
        return {"items": items}
    except Exception as e:
        logger.error(f"Preview third-party sync users failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/third-party-sync/preview")
async def preview_third_party_sync_users_saved(
    admin: dict = Depends(require_permission("element", "element:user:edit")),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        items = await UserSyncService.preview_users(db)
        return {"items": items}
    except Exception as e:
        logger.error(f"Preview third-party sync users failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/third-party-sync/run")
async def run_third_party_sync(
    request: ThirdPartyUserSyncRunRequest,
    admin: dict = Depends(require_permission("element", "element:user:edit")),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        cfg = request.config or await UserSyncService.get_config()
        result = await UserSyncService.run_sync(db, user_names=request.user_names, config=cfg)
        return {
            "message": (
                f"同步完成：新增 {result['created']} 人，更新 {result['updated']} 人，"
                f"失败 {result['failed']} 人"
            ),
            **result,
        }
    except Exception as e:
        logger.error(f"Third-party sync run failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))

class UpdateUserRequest(BaseModel):
    real_name: Optional[str] = None
    role: Optional[str] = None
    dept_code: Optional[str] = None
    org_path: Optional[str] = None
    extra_data: Optional[str] = None
    allowed_resources: Optional[list] = None
    role_ids: Optional[List[int]] = None # Business Roles
    remark: Optional[str] = None
    email: Optional[str] = None

class UpdateStatusRequest(BaseModel):
    status: int  # 0=disabled, 1=enabled, 2=pending_review
    # 审核通过时顺带分配业务角色；None = 不触碰角色，[] = 显式清空
    role_ids: Optional[List[int]] = None

class BatchUpdateStatusRequest(BaseModel):
    user_ids: List[int] = Field(..., description="目标用户 ID 列表")
    status: int = Field(..., description="状态 (1=启用, 0=禁用)")

# 单次批量操作的用户数上限，防止误操作与超长事务
BATCH_STATUS_MAX_USERS = 200


@router.get("/users/pending-count")
async def get_pending_user_count(
    admin: dict = Depends(require_permission("menu", "menu:system:users")),
    db: AsyncSession = Depends(get_db_session)
):
    """待审核账号数量，供用户管理页的「待审核」页签徽章展示。

    只读计数，权限与用户列表一致（menu:system:users）；审核动作本身仍受 element:user:edit 保护。
    与注册接口的「积压上限」兜底共用同一计数口径，避免徽章和门禁各算各的。
    """
    return {"count": await AuthService.count_pending_registrations(db=db)}


@router.get("/users/{user_id}/permissions", response_model=UserPermissionsResponse)
async def get_user_permissions(
    user_id: int,
    request: Request,
    admin: dict = Depends(require_permission("element", "element:user:edit")),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get user permissions. Admin only.
    """
    from app.core.v1_api_access import normalize_api_permission_ids

    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    service = PermissionService(db)
    response = await service.get_user_permissions(user_id)
    response.permissions.apis = normalize_api_permission_ids(
        request.app,
        response.permissions.apis or [],
    )
    # 禁用资源页要的是「用户直配」而不是这个并集：拿并集整份写回会把角色级禁用固化成
    # 用户级记录（角色侧取消后用户仍被禁）。apis 同样归一，保持两份结构可比。
    response.direct_permissions = await service.get_user_direct_permissions(user_id)
    response.direct_permissions.apis = normalize_api_permission_ids(
        request.app,
        response.direct_permissions.apis or [],
    )
    return response

@router.put("/users/{user_id}/permissions")
async def update_user_permissions(
    user_id: int,
    permissions: PermissionUpdate,
    request: Request,
    admin: dict = Depends(require_permission("element", "element:user:edit")),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Update user permissions. Admin only.
    """
    from app.core.v1_api_access import normalize_api_permission_ids

    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    permissions.apis = normalize_api_permission_ids(request.app, permissions.apis or [])

    service = PermissionService(db)
    await service.update_user_permissions(user_id, permissions)
    return {"message": "Permissions updated successfully"}

@router.get("/users")
async def list_users(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=1000),
    search: Optional[str] = None,
    role: Optional[str] = None,
    status_filter: Optional[int] = Query(None, alias="status"),
    exclude_status: Optional[int] = Query(
        None,
        ge=0,
        description="排除某个状态的用户。用户列表用它排掉待审核（2），"
                    "使待审核账号只出现在「待审核」页签里",
    ),
    exclude_role_id: Optional[int] = Query(
        None,
        ge=1,
        description="排除已属于该角色的用户，用于角色「分配用户」弹窗的候选列表",
    ),
    admin: dict = Depends(require_permission("menu", "menu:system:users")),
    db: AsyncSession = Depends(get_db_session)
):
    """
    List all users with pagination and filters.
    Admin only.
    """
    stmt = select(User).order_by(desc(User.created_at))
    
    if search:
        stmt = stmt.where(
            (User.user_name.like(f"%{search}%"))
            | (User.real_name.like(f"%{search}%"))
            | (User.email.like(f"%{search}%"))
        )
    if role and role in ["admin", "user"]:
        stmt = stmt.where(User.role == role)
    if status_filter is not None:
        stmt = stmt.where(User.status == status_filter)
    if exclude_status is not None:
        # 显式带上 NULL：status 列可空，而 SQL 里 `status != 2` 对 NULL 求值为 NULL
        # （不成立），只写 `!=` 会让历史 NULL 行从「全部」里凭空消失。
        stmt = stmt.where(
            or_(User.status != exclude_status, User.status.is_(None))
        )
    if exclude_role_id is not None:
        member_ids = select(UserRoleRelation.user_id).where(
            UserRoleRelation.role_id == exclude_role_id
        )
        stmt = stmt.where(~User.id.in_(member_ids))
        
    # Count
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar()
    
    # Page
    stmt = stmt.offset((page - 1) * size).limit(size)
    rows = (await db.execute(stmt)).scalars().all()

    # 本页用户的微信绑定情况：一次 IN 查询，不要逐行查
    page_user_ids = [row.id for row in rows]
    bound_user_ids: set = set()
    if page_user_ids:
        bound_rows = await db.execute(
            select(WechatBinding.user_id)
            .where(WechatBinding.user_id.in_(page_user_ids))
            .distinct()
        )
        bound_user_ids = {r[0] for r in bound_rows.all()}

    items = []
    for row in rows:
        role_ids = [r.id for r in row.roles] if row.roles else []
        role_names = [r.name for r in row.roles] if row.roles else []
        
        items.append({
            "id": row.id,
            "user_name": row.user_name,
            "real_name": row.real_name,
            "email": row.email or "",
            "role": row.role,
            "dept_code": row.dept_code,
            "org_path": row.org_path,
            "extra_data": row.extra_data,
            "role_ids": role_ids,
            "role_names": role_names,
            "remark": row.remark,
            "status": row.status,
            "two_factor_enabled": bool(row.two_factor_enabled),
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            "last_login_at": row.last_login_at.strftime("%Y-%m-%d %H:%M:%S") if row.last_login_at else None,
            "wechat_bound": row.id in bound_user_ids,
            "allowed_resources": []
        })

    return {
        "total": total,
        "page": page,
        "size": size,
        "items": items
    }

# ---------------------------------------------------------------------------
# 微信绑定（小程序微端，见 spec §7.6）
# ---------------------------------------------------------------------------

def mask_openid(value: Optional[str]) -> Optional[str]:
    """openid/unionid 脱敏：保留首 6 尾 4，中间以 **** 代替。

    它不是凭证（拿它无法登录），但能关联到具体微信用户，列表页没必要裸奔；
    需要明文排查时走 /wechat-bindings/{id}/identity。
    """
    if not value:
        return value
    if len(value) <= 12:
        return value[:3] + "****"
    return f"{value[:6]}****{value[-4:]}"


@router.get("/wechat-bindings")
async def list_wechat_bindings(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    search: Optional[str] = None,
    appid: Optional[str] = None,
    admin: dict = Depends(require_permission("menu", "menu:system:users")),
    db: AsyncSession = Depends(get_db_session),
):
    """微信身份绑定记录列表。

    一行 = 一条绑定记录，而不是一行一个用户：同一账号将来可能挂多个微信身份
    （多小程序 / 网站应用），按绑定行展示才不会丢信息。
    """
    stmt = (
        select(WechatBinding, User)
        .join(User, User.id == WechatBinding.user_id)
        .order_by(desc(WechatBinding.last_login_at), desc(WechatBinding.id))
    )

    if appid:
        stmt = stmt.where(WechatBinding.appid == appid)
    if search:
        stmt = stmt.where(
            (User.user_name.like(f"%{search}%")) | (User.real_name.like(f"%{search}%"))
        )

    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar()

    rows = (await db.execute(stmt.offset((page - 1) * size).limit(size))).all()

    items = []
    for binding, user in rows:
        items.append({
            "id": binding.id,
            "user_id": user.id,
            "user_name": user.user_name,
            "real_name": user.real_name,
            "appid": binding.appid,
            "openid_masked": mask_openid(binding.openid),
            "unionid_masked": mask_openid(binding.unionid),
            "created_at": binding.created_at.strftime("%Y-%m-%d %H:%M:%S") if binding.created_at else None,
            "last_login_at": binding.last_login_at.strftime("%Y-%m-%d %H:%M:%S") if binding.last_login_at else None,
        })

    return {"total": total, "page": page, "size": size, "items": items}


@router.get("/wechat-bindings/{binding_id}/identity")
async def get_wechat_binding_identity(
    binding_id: int,
    admin: dict = Depends(require_permission("element", "element:user:edit")),
    db: AsyncSession = Depends(get_db_session),
):
    """取单条绑定的完整 openid/unionid，供对照云函数日志排查。

    单独成接口、且权限升到编辑级，而不是给列表加 include_raw 参数——
    后者一次请求就把整页明文全下发，脱敏等于白做。
    """
    binding = await db.get(WechatBinding, binding_id)
    if binding is None:
        raise HTTPException(status_code=404, detail="绑定记录不存在")

    logger.info(
        "管理员读取微信绑定明文身份: binding_id=%s user_id=%s operator=%s",
        binding_id, binding.user_id, admin.get("user_name"),
    )
    return {"openid": binding.openid, "unionid": binding.unionid}

@router.post("/users")
async def create_user(
    request: CreateUserRequest,
    admin: dict = Depends(require_permission("element", "element:user:edit")),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Create a new user with API key.
    Admin only.
    """
    # Validate role
    if request.role not in ["admin", "user"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Role must be 'admin' or 'user'"
        )
    
    # Check duplicate
    existing = await db.execute(select(User).where(User.user_name == request.user_name))
    if existing.scalar_one_or_none():
         raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already exists"
        )

    raw_email = (getattr(request, "email", None) or "").strip()
    try:
        email = AuthService.normalize_email(raw_email, strict=True) if raw_email else None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if email and await AuthService.is_user_email_taken(email, db=db):
        raise HTTPException(status_code=400, detail="该邮箱已被其他账号使用")

    try:
        # Generate API key (Delegates to AuthService which uses ORM now)
        api_key = await AuthService.generate_api_key(
            request.user_name,
            real_name=request.real_name,
            role=request.role,
            remark=request.remark,
            dept_code=request.dept_code,
            org_path=request.org_path,
            extra_data=request.extra_data,
            email=email,
            db=db # Pass session!
        )
        
        # Fetch back user to return consistent structure
        result = await db.execute(select(User).options(selectinload(User.roles)).where(User.user_name == request.user_name))
        user = result.scalar_one()
        
        # Assign Business Roles
        if request.role_ids:
            service = PermissionService(db)
            await service.update_user_roles(user.id, request.role_ids)
            # Refresh to get roles
            await db.refresh(user)

        # Re-fetch for roles if refreshed (or construct response manually)
        role_ids = request.role_ids or []
        
        return {
            "id": user.id,
            "user_name": user.user_name,
            "real_name": user.real_name,
            "role": user.role,
            "dept_code": user.dept_code,
            "org_path": user.org_path,
            "extra_data": user.extra_data,
            "role_ids": role_ids,
            "remark": user.remark,
            "status": user.status,
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "api_key": api_key, # Explicit return
            "allowed_resources": request.allowed_resources or []
        }

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.put("/users/{user_id}")
async def update_user(
    user_id: int,
    request: UpdateUserRequest,
    admin: dict = Depends(require_permission("element", "element:user:edit")),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Update user role and permissions.
    """
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    if request.role is not None:
        if request.role not in ["admin", "user"]:
             raise HTTPException(status_code=400, detail="Invalid role")
        user.role = request.role
    
    if request.real_name is not None:
        user.real_name = request.real_name
        
    if request.dept_code is not None:
        user.dept_code = request.dept_code
    
    if request.org_path is not None:
        user.org_path = request.org_path
        
    if request.extra_data is not None:
        user.extra_data = request.extra_data
        
    if request.remark is not None:
        user.remark = request.remark

    # 邮箱：用 email_provided 而不是 `if email:` 判定，否则管理员把邮箱清空
    # （传空串）时会被静默忽略，永远清不掉。语义：null / 字段缺省 = 不修改，
    # 空串或纯空白 = 清空。
    email_provided = getattr(request, "email", None) is not None
    if email_provided:
        raw_email = (request.email or "").strip()
        try:
            email = AuthService.normalize_email(raw_email, strict=True) if raw_email else None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        # 排除自己：否则「不改邮箱直接保存」会被判成与本人重复
        if email and await AuthService.is_user_email_taken(
            email, exclude_user_id=user_id, db=db
        ):
            raise HTTPException(status_code=400, detail="该邮箱已被其他账号使用")
        user.email = email

    # Update Business Roles
    if request.role_ids is not None:
        service = PermissionService(db)
        await service.update_user_roles(user_id, request.role_ids)
        
    try:
        await db.commit()
        # Reload user with roles
        result = await db.execute(
            select(User).options(selectinload(User.roles)).where(User.id == user_id)
        )
        user = result.scalar_one()
    except Exception as e:
        await db.rollback()
        logger.error(f"Failed to commit user update: {e}")
        raise HTTPException(status_code=500, detail=f"Database update failed: {str(e)}")

    # Cache Invalidation：系统角色 / 业务角色 / 权限变更后同时清权限缓存与 auth:api_key 缓存
    try:
        service = PermissionService(db)
        await service.invalidate_cached_permissions_for_users([user_id])
    except Exception as e:
        logger.error(f"Failed to clear permission/auth cache for user {user_id}: {e}")
    
    role_ids = [r.id for r in user.roles]
    role_names = [r.name for r in user.roles]

    return {
        "id": user.id,
        "user_name": user.user_name,
        "real_name": user.real_name,
        "role": user.role,
        "dept_code": user.dept_code,
        "org_path": user.org_path,
        "extra_data": user.extra_data,
        "role_ids": role_ids,
        "role_names": role_names,
        "remark": user.remark,
        "status": user.status,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "updated_at": user.updated_at.isoformat() if user.updated_at else None,
        "allowed_resources": []
    }


async def _send_review_result_mail(user: User, approved: bool):
    """审核结果通知，返回 (sent, error)。

    强制走**全局** SMTP（已决策）：这是系统发给用户的通知，若走用户个人配置，
    用户把个人 SMTP 关掉或填错就永远收不到审核结果——而这恰恰是他能否登录的前提。
    """
    from starlette.concurrency import run_in_threadpool

    from app.services.email_delivery_service import (
        EmailDeliveryService, build_review_mail,
    )

    if not (user.email or "").strip():
        return False, "该用户未填写邮箱"

    settings = await EmailDeliveryService.load_global_smtp_settings()
    if settings is None:
        return False, "平台邮件服务未启用或配置不完整"

    subject, body = build_review_mail(user.user_name, approved, settings.sender_name)
    ok, message = await run_in_threadpool(
        EmailDeliveryService.send_mail, settings, [user.email], subject, body
    )
    if not ok:
        logger.warning("审核结果邮件发送失败: user=%s reason=%s", user.user_name, message)
    return ok, (None if ok else message)


@router.patch("/users/{user_id}/status")
async def update_user_status(
    user_id: int,
    request: UpdateStatusRequest,
    admin: dict = Depends(require_permission("element", "element:user:edit")),
    db: AsyncSession = Depends(get_db_session)
):
    """
    更新用户状态（启用 / 禁用 / 待审核），并可在审核通过时顺带分配业务角色。
    """
    if request.status not in (0, 1, 2):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="status must be 0 (disabled), 1 (enabled) or 2 (pending_review)",
        )

    current_user_id = admin.get("user_id")
    try:
        current_user_id = int(current_user_id) if current_user_id else None
    except (ValueError, TypeError):
        logger.warning(f"Failed to convert current_user_id to int: {current_user_id}")
    
    if user_id == current_user_id and request.status == 0:
        raise HTTPException(status_code=403, detail="Cannot disable yourself")

    # 角色存在性必须在改状态之前校验：否则状态已提交、角色写入却因外键失败，
    # 请求以 500 收场，管理员会看到「审核失败」但实际上账号已经启用。
    role_ids = request.role_ids
    if role_ids is not None:
        from app.models.permission import Role

        requested = list(dict.fromkeys(int(rid) for rid in role_ids))
        if requested:
            found = (
                await db.execute(select(Role.id).where(Role.id.in_(requested)))
            ).scalars().all()
            if set(found) != set(requested):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="包含不存在的业务角色，请刷新后重试",
                )
        role_ids = requested

    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # 记录审核前的状态：只有「待审核 → 启用/禁用」才是审核动作。
    # 用它区分审核与日常启停，避免管理员事后启停账号时给用户发骚扰邮件，
    # 而且不需要额外字段或动作类型参数。
    was_pending = user.status == USER_STATUS_PENDING_REVIEW

    user.status = request.status
    await db.commit()

    # 审核通过时顺带分配业务角色：role_ids=None 表示不触碰，[] 表示清空
    if request.status == 1 and role_ids is not None:
        try:
            await PermissionService(db).update_user_roles(user_id, role_ids)
        except Exception as e:
            logger.error(f"Failed to update roles for user {user_id}: {e}")
            raise HTTPException(status_code=500, detail="业务角色分配失败，请重试")
    
    # Security: Clear Redis cache to force re-authentication
    try:
        await AuthService.invalidate_user_auth_cache(
            user_id, db=db, api_key_hash=user.api_key_hash, revoke_sessions=True
        )
        await PermissionService(db).invalidate_cached_permissions_for_users([user_id])
    except Exception as e:
        logger.error(f"Failed to clear user cache: {e}")

    # 审核结果通知：尽力而为，绝不影响审核结果本身。
    # 放在所有可能抛错的动作之后，避免角色分配失败（500）时用户却已收到「已通过」邮件。
    email_sent = False
    email_error: Optional[str] = None
    if was_pending and user.status in (USER_STATUS_ENABLED, USER_STATUS_DISABLED):
        email_sent, email_error = await _send_review_result_mail(
            user, approved=(user.status == USER_STATUS_ENABLED)
        )

    return {
        "message": "User status updated successfully",
        "email_sent": email_sent,
        "email_error": email_error,
    }


class NotifyReviewMailRequest(BaseModel):
    # 显式传事件而不是从当前状态推断：重发往往发生在用户状态已被改动之后，
    # 从状态推断会发出与实际审核决定不符的文案。
    approved: bool


@router.post(
    "/users/{user_id}/notify-review-mail",
    dependencies=[Depends(require_permission("element", "element:user:edit"))],
)
async def notify_review_mail(
    user_id: int,
    request: NotifyReviewMailRequest,
    db: AsyncSession = Depends(get_db_session),
):
    """重发审核结果邮件。"""
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if target.status == USER_STATUS_PENDING_REVIEW:
        raise HTTPException(status_code=400, detail="该用户仍在待审核状态，无需重发通知")

    sent, error = await _send_review_result_mail(target, approved=request.approved)
    return {"ok": sent, "message": "已发送" if sent else (error or "发送失败")}


@router.patch("/users/batch-status")
async def batch_update_user_status(
    request: BatchUpdateStatusRequest,
    admin: dict = Depends(require_permission("element", "element:user:edit")),
    db: AsyncSession = Depends(get_db_session)
):
    """
    批量启用 / 禁用用户。

    约定：
    - 批量禁用时自动跳过当前登录账号，计入 skipped_self（其余目标照常执行）；
    - 不存在的用户 ID 计入 not_found；
    - 状态落库后清除认证与权限缓存，禁用立即生效（已登录会话被踢下线）。
    """
    if request.status not in (0, 1):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="status must be 0 (disabled) or 1 (enabled)"
        )

    # 去重并保持顺序，避免重复 ID 造成计数错乱
    target_ids = list(dict.fromkeys(request.user_ids or []))
    if not target_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="user_ids cannot be empty"
        )
    if len(target_ids) > BATCH_STATUS_MAX_USERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Too many users in one request (max {BATCH_STATUS_MAX_USERS})"
        )

    current_user_id = admin.get("user_id")
    try:
        current_user_id = int(current_user_id) if current_user_id else None
    except (ValueError, TypeError):
        logger.warning(f"Failed to convert current_user_id to int: {current_user_id}")
        current_user_id = None

    # 安全规则：禁止批量禁用自己，但不因此中断整批操作
    skipped_self = 0
    if request.status == 0 and current_user_id is not None and current_user_id in target_ids:
        target_ids = [uid for uid in target_ids if uid != current_user_id]
        skipped_self = 1

    if not target_ids:
        return {"updated": 0, "skipped_self": skipped_self, "not_found": 0, "updated_ids": []}

    result = await db.execute(select(User).where(User.id.in_(target_ids)))
    users = result.scalars().all()
    found_ids = {u.id for u in users}
    not_found = sum(1 for uid in target_ids if uid not in found_ids)

    if not users:
        return {
            "updated": 0,
            "skipped_self": skipped_self,
            "not_found": not_found,
            "updated_ids": []
        }

    # 提交前快照 id / api_key_hash，清缓存阶段不再依赖 ORM 提交后状态
    targets = [(user.id, user.api_key_hash) for user in users]

    for user in users:
        user.status = request.status
    await db.commit()

    updated_ids = [uid for uid, _ in targets]

    # Security: Clear Redis cache to force re-authentication
    try:
        from app.services.auth_service import AuthService
        from app.services.permission_service import PermissionService

        for uid, api_key_hash in targets:
            await AuthService.invalidate_user_auth_cache(
                uid, db=db, api_key_hash=api_key_hash, revoke_sessions=True
            )
        await PermissionService(db).invalidate_cached_permissions_for_users(updated_ids)
    except Exception as e:
        logger.error(f"Failed to clear user cache in batch: {e}")

    logger.info(
        "Batch user status updated to %s by %s: updated=%s, skipped_self=%s, not_found=%s",
        request.status,
        admin.get("user_name"),
        len(updated_ids),
        skipped_self,
        not_found,
    )

    return {
        "updated": len(updated_ids),
        "skipped_self": skipped_self,
        "not_found": not_found,
        "updated_ids": updated_ids
    }

@router.get("/api-key/{user_id}")
async def get_user_api_key(
    user_id: int,
    current_user: dict = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get decrypted API Key.
    """
    current_user_id = current_user.get("user_id")
    current_role = current_user.get("role")
    
    try:
        current_user_id = int(current_user_id) if current_user_id else None
    except (ValueError, TypeError):
        logger.warning(f"Failed to convert current_user_id to int: {current_user_id}")
    
    if current_role != "admin" and user_id != current_user_id:
        service = PermissionService(db)
        can_view = await service.check_permission(
            int(current_user_id), "element", "element:user:view_key"
        )
        if not can_view:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Missing permission element:user:view_key",
            )
        
    api_key = await AuthService.get_decrypted_api_key(user_id, db=db)
    
    if not api_key:
        raise HTTPException(status_code=404, detail="API Key not found or decryption failed")
        
    return {
        "user_id": user_id,
        "api_key": api_key
    }

@router.delete("/users/{user_id}")
async def delete_user(
    user_id: int,
    admin: dict = Depends(require_permission("element", "element:user:delete")),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Delete a user.
    """
    current_user_id = admin.get("user_id")
    try:
        current_user_id = int(current_user_id) if current_user_id else None
    except (ValueError, TypeError):
        logger.warning(f"Failed to convert current_user_id to int: {current_user_id}")
    
    if user_id == current_user_id:
        raise HTTPException(status_code=403, detail="Cannot delete yourself")
        
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    if user.role == "admin" and user.user_name == "admin":
         raise HTTPException(status_code=403, detail="Cannot delete system admin")
         
    # Store hash for cache clearing
    api_key_hash = user.api_key_hash
         
    # 1. Clean up non-relationship data (permissions and scheduled tasks)
    from app.models.task import AgentScheduledTask
    
    # 先记下任务 ID，删完库里的行后同步摘除调度器 Job，避免留下到点空跑的僵尸调度
    task_ids = list(
        (await db.execute(
            select(AgentScheduledTask.id).where(AgentScheduledTask.user_id == user_id)
        )).scalars().all()
    )

    await db.execute(delete(ResourcePermission).where(ResourcePermission.user_id == user_id))
    await db.execute(delete(AgentScheduledTask).where(AgentScheduledTask.user_id == user_id))
    
    # 2. Use ORM relationship to clean up UserRoleRelation
    # This avoids StaleDataError because SQLAlchemy will track the deletion
    user.roles = []
    await db.flush()

    # 兜底再按 user_id 显式删一次：上面依赖 roles 集合已被完整加载，而 MySQL 侧这张表
    # 没有外键约束（见 db-prod/V27），漏删就会留下指向不存在用户的悬挂行——
    # 线上出现过「角色列表显示 1 人、点开抽屉 0 位成员」正是这类数据。
    await db.execute(delete(UserRoleRelation).where(UserRoleRelation.user_id == user_id))
    
    # 3. Finally delete the user
    await db.delete(user)
    await db.commit()
    
    # Remove scheduler jobs for the deleted tasks
    if task_ids:
        try:
            from app.services.ai.scheduler_service import scheduler_service
            for task_id in task_ids:
                await scheduler_service.remove_task(task_id)
        except Exception as e:
            logger.error(f"Failed to remove scheduler jobs for deleted user {user_id}: {e}")
    
    # Security: Clear Redis cache
    try:
        from app.services.auth_service import AuthService
        from app.services.permission_service import PermissionService
        await AuthService.invalidate_user_auth_cache(
            user_id, db=db, api_key_hash=api_key_hash, revoke_sessions=True
        )
        await PermissionService(db).invalidate_cached_permissions_for_users([user_id])
    except Exception as e:
        logger.error(f"Failed to clear user cache: {e}")
    
    return {"message": "User deleted successfully"}

@router.post("/users/{user_id}/reset-key")
async def reset_user_api_key(
    user_id: int,
    admin: dict = Depends(require_permission("element", "element:user:reset_key")),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Reset user API Key.
    """
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    new_api_key = await AuthService.reset_api_key(user_id, db=db)
    
    if not new_api_key:
        raise HTTPException(status_code=500, detail="Failed to reset API Key")

    # 换掉凭据不等于吊销会话：会话令牌是独立于 api_key_hash 的 Redis 键，不按 user_id
    # 成批吊销的话，「管理员重置 Key」之后对方手里的会话仍然有效，重置等于没重置。
    # （用户自助重置是唯一例外：它刻意不退出现有会话，见 auth.py 对应端点的说明。）
    try:
        await AuthService.invalidate_user_auth_cache(user_id, revoke_sessions=True)
    except Exception as e:
        logger.error(f"Failed to revoke sessions after API Key reset for user {user_id}: {e}")
        
    return {
        "message": "API Key reset successfully",
        "user_id": user_id,
        "api_key": new_api_key
    }

@router.post("/users/{user_id}/set-password")
async def set_user_password(
    user_id: int,
    request: SetPasswordRequest,
    admin: dict = Depends(require_permission("element", "element:user:edit")),
    db: AsyncSession = Depends(get_db_session)
):
    """
    管理员为指定用户设置/重置登录密码。
    """
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # 校验密码复杂度（等保要求）
    valid, msg = AuthService.validate_password_complexity(request.password, username=user.user_name)
    if not valid:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg)

    # bcrypt 限制密码长度为 72 字节
    password_bytes = request.password.encode('utf-8')
    if len(password_bytes) > 72:
        password = password_bytes[:72].decode('utf-8', errors='ignore')
    else:
        password = request.password

    success = await AuthService.set_user_password(user_id, password, db=db)
    if not success:
        raise HTTPException(status_code=500, detail="密码设置失败")

    # 代设/重置密码后必须吊销该用户全部会话：与用户自助改密同口径（见 auth.py 的
    # change_password）。否则「管理员发现账号被盗 → 改密码」之后，对方手里的会话
    # 依然有效，改密等于没改。长时效 API Key 不在会话索引里，不受影响。
    try:
        await AuthService.revoke_sessions_for_user(user_id)
    except Exception as exc:
        logger.warning("代设密码后吊销会话失败 user_id=%s: %s", user_id, exc)

    return {"status": "success", "message": "密码设置成功", "user_id": user_id}

@router.post("/users/{user_id}/disable-2fa")
async def disable_user_two_factor(
    user_id: int,
    admin: dict = Depends(require_permission("element", "element:user:edit")),
    db: AsyncSession = Depends(get_db_session)
):
    """
    管理员强制关闭指定用户的两步验证 (2FA / TOTP)。
    """
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.two_factor_enabled = 0
    user.two_factor_secret = None
    await db.commit()

    logger.info(f"Admin {admin.get('user_name')} disabled 2FA for user {user.user_name} (ID: {user_id})")
    return {"status": "success", "message": f"用户 {user.user_name} 的两步验证已成功关闭", "user_id": user_id}

@router.get("/resources/available")
async def get_available_resources(
    admin: dict = Depends(require_permission("element", "element:user:edit")),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get all available resources for permission assignment.
    """
    from app.core.v1_api_access import get_assignable_v1_api_resources
    from app.services.metadata_service import MetadataService
    from app.models.agent import AIAgent
    
    # 1. Agents
    agent_stmt = select(AIAgent.id, AIAgent.name, AIAgent.display_name).where(
        AIAgent.is_enabled == True,
        AIAgent.is_system == True
    )
    agent_rows = (await db.execute(agent_stmt)).all()
    
    agents = [{"id": r.id, "name": r.display_name or r.name, "key": r.id} for r in agent_rows]
    
    # 2. Metadata Datasets
    meta_datasets = await MetadataService.get_datasets(db)
    datasets = [{"id": str(d.id), "name": d.display_name or d.name, "key": str(d.id)} for d in meta_datasets]
    
    # 3. APIs
    apis = get_assignable_v1_api_resources()

    return {
        "agents": agents,
        "metadata": datasets,
        "apis": apis
    }

# ---------------------------------------------------------------------------
# 智能体级资源限制（用户/角色 × 单个智能体的排除机制）
#
# 与全局禁用资源的关系：有效禁用 = 全局禁用 ∪ 该智能体的智能体级禁用。这里只读写
# 「智能体级」那一层（agent_id 等于目标值的行），绝不触碰全局行，也不动其它智能体的行。
# 详见 docs/superpowers/specs/2026-10-13-agent-scoped-resource-restrictions-design.md
# ---------------------------------------------------------------------------


@router.get("/agents/{agent_id}/restrictable-resources")
async def get_agent_restrictable_resources(
    agent_id: str,
    user_id: Optional[int] = Query(None, description="按用户视角回填 forbidden_globally"),
    role_id: Optional[int] = Query(None, description="按角色视角回填 forbidden_globally"),
    admin: dict = Depends(require_permission("element", "element:user:edit")),
    db: AsyncSession = Depends(get_db_session),
):
    """该智能体**实际绑定**的可限制资源（内置工具 / MCP 分组 / 公共技能 / 个人技能）。

    候选不是"平台全部资源"而是这个智能体的绑定集合，管理员才能在其上做排除。
    每项带 ``forbidden_globally``：全局已禁的项在弹框里置灰只读（排除机制只增不减）。

    ``user_id`` / ``role_id`` 是**视角**参数，弹框要靠它算出"这个主体已经全局禁了哪些"。
    漏传时所有项都会被算成"未全局禁用"、全部可勾选，管理员会误以为能在智能体层面
    **放开**全局禁用（实际不会）——这是设计文档明确警告过的误用。所以这里不再静默接受：
    缺参数时记一条 warning，让这类调用在日志里可见，而不是安静地给出误导性结果。
    """
    if user_id is None and role_id is None:
        logger.warning(
            "[agent-scope] restrictable-resources called without user_id/role_id; "
            "forbidden_globally will be all-false and every item will look selectable"
        )
    from app.services.agent_scoped_restrictions import (
        build_restrictable_resources,
        list_personal_skill_metas,
    )

    agent = await db.get(AIAgent, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    globally_tools: set[str] = set()
    globally_skills: set[str] = set()
    person: Optional[dict] = None
    if user_id is not None:
        person = (await db.execute(select(User).where(User.id == int(user_id)))).scalar_one_or_none()
    if person is not None:
        perms = await PermissionService(db).get_user_permissions(int(user_id))
        globally_tools = set(perms.permissions.forbidden_tools or [])
        globally_skills = set(perms.permissions.forbidden_skills or [])
    elif role_id is not None:
        role_perms = await PermissionService(db).get_role_permissions(int(role_id))
        globally_tools = set(role_perms.permissions.forbidden_tools or [])
        globally_skills = set(role_perms.permissions.forbidden_skills or [])

    # 个人技能必须按**目标**视角取：
    # - 配用户时看该用户自己的个人技能。传当前登录管理员会让管理员看到自己的私有技能，
    #   而目标用户真正拥有的技能一个都不显示；
    # - 配角色时角色没有个人技能（个人技能属于账号），必须传空——否则会把管理员的
    #   私有技能塞进角色的可限制清单，等于凭空给角色造出条目。
    personal_skill_owner: Optional[dict] = None
    if person is not None:
        personal_skill_owner = {"id": person.id, "user_name": person.user_name}

    try:
        return await build_restrictable_resources(
            db,
            agent_id=agent_id,
            globally_forbidden_tools=globally_tools,
            globally_forbidden_skills=globally_skills,
            personal_skills=list_personal_skill_metas(personal_skill_owner),
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except RuntimeError as exc:
        # 未迁移时 _supports_agent_scope 会抛 RuntimeError：给出可操作的提示而不是 500
        raise HTTPException(status_code=409, detail=str(exc))


@router.get("/users/{user_id}/agent-restrictions/{agent_id}")
async def get_user_agent_restrictions(
    user_id: int,
    agent_id: str,
    admin: dict = Depends(require_permission("element", "element:user:edit")),
    db: AsyncSession = Depends(get_db_session),
):
    """该用户在该智能体上的智能体级限制（不含全局项）。"""
    try:
        data = await PermissionService(db).get_agent_scoped_forbidden_ids(
            user_id=user_id, agent_id=agent_id
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"agent_id": agent_id, **data}


@router.put("/users/{user_id}/agent-restrictions/{agent_id}")
async def update_user_agent_restrictions(
    user_id: int,
    agent_id: str,
    body: AgentRestrictionUpdate,
    admin: dict = Depends(require_permission("element", "element:user:edit")),
    db: AsyncSession = Depends(get_db_session),
):
    """覆盖式写入该用户的智能体级限制（只动该 agent 的行）。"""
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        data = await PermissionService(db).update_agent_scoped_forbidden_ids(
            user_id=user_id,
            agent_id=agent_id,
            forbidden_tools=body.forbidden_tools,
            forbidden_skills=body.forbidden_skills,
        )
        # 服务层自己 commit（与其它 update_* 一致）——这里不再重复提交，
        # 否则会出现"提交两次"，且缓存失效发生在第一次提交之前。
    except RuntimeError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        logger.exception("Failed to update agent restrictions for user %s", user_id)
        raise HTTPException(status_code=400, detail=f"Failed to save agent restrictions: {exc}")
    return {"agent_id": agent_id, **data}


@router.get("/roles/{role_id}/agent-restrictions/{agent_id}")
async def get_role_agent_restrictions(
    role_id: int,
    agent_id: str,
    admin: dict = Depends(require_permission("element", "element:user:edit")),
    db: AsyncSession = Depends(get_db_session),
):
    """该角色在该智能体上的智能体级限制（不含全局项）。"""
    try:
        data = await PermissionService(db).get_agent_scoped_forbidden_ids(
            role_id=role_id, agent_id=agent_id
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"agent_id": agent_id, **data}


@router.put("/roles/{role_id}/agent-restrictions/{agent_id}")
async def update_role_agent_restrictions(
    role_id: int,
    agent_id: str,
    body: AgentRestrictionUpdate,
    admin: dict = Depends(require_permission("element", "element:user:edit")),
    db: AsyncSession = Depends(get_db_session),
):
    """覆盖式写入该角色的智能体级限制（只动该 agent 的行）。"""
    role = (await db.execute(select(Role).where(Role.id == role_id))).scalar_one_or_none()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    try:
        data = await PermissionService(db).update_agent_scoped_forbidden_ids(
            role_id=role_id,
            agent_id=agent_id,
            forbidden_tools=body.forbidden_tools,
            forbidden_skills=body.forbidden_skills,
        )
        # 同上：提交由服务层负责，避免二次提交与失效早于提交。
    except RuntimeError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        logger.exception("Failed to update agent restrictions for role %s", role_id)
        raise HTTPException(status_code=400, detail=f"Failed to save agent restrictions: {exc}")
    return {"agent_id": agent_id, **data}


@router.get("/agent-restrictions/counts")
async def get_agent_restriction_counts(
    user_id: Optional[int] = Query(None),
    role_id: Optional[int] = Query(None),
    agent_ids: str = Query("", description="逗号分隔的智能体 id 列表"),
    admin: dict = Depends(require_permission("element", "element:user:edit")),
    db: AsyncSession = Depends(get_db_session),
):
    """**一次**返回多个智能体的智能体级限制清单，供权限页卡片的角标取齐。

    为什么不返回「已禁数目」：角标口径必须与弹框完全一致——弹框会把**全局已禁用**的项
    排除在计数外（界面上它们置灰不可选），如果这里图省事返回一个 COUNT，两个数字就会
    对不上，管理员会看到"角标 3、打开只有 1 项"。所以这里返回与 GET 单智能体完全相同的
    清单结构，由前端复用同一个计数函数——顺便让弹框打开时直接命中缓存。

    逐个 GET 会让打开一次权限页发出 N 个请求（N = 已授权智能体数），因此用一次
    ``agent_id IN (...)`` 查询聚合。
    """
    ids = [item.strip() for item in (agent_ids or "").split(",") if item.strip()]
    if not ids:
        return {"restrictions": {}}
    if user_id is None and role_id is None:
        raise HTTPException(status_code=400, detail="user_id 或 role_id 必须提供一个")

    try:
        # 未迁移时没有作用域列：返回空清单（而不是报错让整个权限页打不开）
        if not await PermissionService(db)._supports_agent_scope():
            return {"restrictions": {agent_id: {"forbidden_tools": [], "forbidden_skills": []} for agent_id in ids}}
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    from sqlalchemy import text

    subject_column = "user_id" if user_id is not None else "role_id"
    subject_value = int(user_id if user_id is not None else role_id)
    placeholders = ", ".join(f":aid_{index}" for index in range(len(ids)))
    params = {f"aid_{index}": value for index, value in enumerate(ids)}
    params["subject"] = subject_value

    rows = (
        await db.execute(
            text(
                f"SELECT agent_id, resource_type, resource_id "
                f"FROM ai_agent_resource_permissions "
                f"WHERE {subject_column} = :subject AND agent_id IN ({placeholders}) "
                # TRUE 而非整型：PG 的 enabled 是 BOOLEAN，不接受整型字面量（MySQL 视 TRUE 为 1）
                f"AND enabled = TRUE "
                f"AND resource_type IN ('forbidden_tool', 'forbidden_skill')"
            ).bindparams(**params)
        )
    ).all()

    restrictions: Dict[str, Dict[str, List[str]]] = {
        agent_id: {"forbidden_tools": [], "forbidden_skills": []} for agent_id in ids
    }
    seen: Dict[str, Dict[str, set]] = {
        agent_id: {"forbidden_tools": set(), "forbidden_skills": set()} for agent_id in ids
    }
    for agent_id, resource_type, resource_id in rows:
        agent_key = str(agent_id)
        if agent_key not in restrictions:
            continue
        bucket = "forbidden_skills" if resource_type == "forbidden_skill" else "forbidden_tools"
        value = str(resource_id)
        if value in seen[agent_key][bucket]:
            continue
        seen[agent_key][bucket].add(value)
        restrictions[agent_key][bucket].append(value)
    return {"restrictions": restrictions}
