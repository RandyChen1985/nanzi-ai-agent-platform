from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import require_api_key, require_permission
from app.core.orm import get_db_session
from app.models.permission import Role
from app.models.user import User
from app.schemas.quota import QuotaPolicyResponse, QuotaPolicyUpdate, QuotaStatusResponse
from app.services.quota_service import QuotaService

# 额度口径：系统默认额度与用户专属额度都是**编辑**动作，统一校验
# ``element:user:edit``，与用户管理页入口的判定（前端 canEditUser）一致。
# 早先系统默认额度只校验 ``menu:system:users``（能进菜单就能改），于是前端按编辑
# 权限隐藏入口、后端却仍然放行——留下「没有编辑权限也能调接口改全站额度」的口子。
# 角色额度模板仍归角色管理菜单（``menu:system:roles``），不在用户管理页。
router = APIRouter()


@router.get("/me", response_model=QuotaStatusResponse)
async def get_my_quota(
    user: dict = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    service = QuotaService(db)
    return await service.get_user_quota_status(
        int(user["user_id"]),
        user["user_name"],
    )


@router.get("/system", response_model=QuotaPolicyResponse)
async def get_system_quota(
    editor: dict = Depends(require_permission("element", "element:user:edit")),
    db: AsyncSession = Depends(get_db_session),
):
    service = QuotaService(db)
    return await service.get_scope_policy("system", None)


@router.put("/system", response_model=QuotaPolicyResponse)
async def update_system_quota(
    body: QuotaPolicyUpdate,
    editor: dict = Depends(require_permission("element", "element:user:edit")),
    db: AsyncSession = Depends(get_db_session),
):
    service = QuotaService(db)
    return await service.upsert_scope_policy(
        "system",
        None,
        enabled=body.enabled,
        limit_tokens=body.limit_tokens,
    )


@router.delete("/system")
async def delete_system_quota(
    editor: dict = Depends(require_permission("element", "element:user:edit")),
    db: AsyncSession = Depends(get_db_session),
):
    service = QuotaService(db)
    await service.delete_scope_policy("system", None)
    return {"message": "已恢复为无系统默认额度"}


@router.get("/users/{user_id}", response_model=QuotaPolicyResponse)
async def get_user_quota_policy(
    user_id: int,
    editor: dict = Depends(require_permission("element", "element:user:edit")),
    db: AsyncSession = Depends(get_db_session),
):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    service = QuotaService(db)
    return await service.get_scope_policy(
        "user",
        user_id,
        preview_user_id=user_id,
        preview_username=user.user_name,
    )


@router.put("/users/{user_id}", response_model=QuotaPolicyResponse)
async def update_user_quota_policy(
    user_id: int,
    body: QuotaPolicyUpdate,
    editor: dict = Depends(require_permission("element", "element:user:edit")),
    db: AsyncSession = Depends(get_db_session),
):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    service = QuotaService(db)
    result = await service.upsert_scope_policy(
        "user",
        user_id,
        enabled=body.enabled,
        limit_tokens=body.limit_tokens,
    )
    result.effective = await service.get_user_quota_status(user_id, user.user_name)
    return result


@router.delete("/users/{user_id}")
async def delete_user_quota_policy(
    user_id: int,
    editor: dict = Depends(require_permission("element", "element:user:edit")),
    db: AsyncSession = Depends(get_db_session),
):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    service = QuotaService(db)
    await service.delete_scope_policy("user", user_id)
    return {"message": "已清除用户专属额度，将继承角色/系统策略"}


@router.get("/roles/{role_id}", response_model=QuotaPolicyResponse)
async def get_role_quota_policy(
    role_id: int,
    admin: dict = Depends(require_permission("menu", "menu:system:roles")),
    db: AsyncSession = Depends(get_db_session),
):
    role = await db.get(Role, role_id)
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")
    service = QuotaService(db)
    return await service.get_scope_policy("role", role_id)


@router.put("/roles/{role_id}", response_model=QuotaPolicyResponse)
async def update_role_quota_policy(
    role_id: int,
    body: QuotaPolicyUpdate,
    admin: dict = Depends(require_permission("menu", "menu:system:roles")),
    db: AsyncSession = Depends(get_db_session),
):
    role = await db.get(Role, role_id)
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")
    service = QuotaService(db)
    return await service.upsert_scope_policy(
        "role",
        role_id,
        enabled=body.enabled,
        limit_tokens=body.limit_tokens,
    )


@router.delete("/roles/{role_id}")
async def delete_role_quota_policy(
    role_id: int,
    admin: dict = Depends(require_permission("menu", "menu:system:roles")),
    db: AsyncSession = Depends(get_db_session),
):
    role = await db.get(Role, role_id)
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")
    service = QuotaService(db)
    await service.delete_scope_policy("role", role_id)
    return {"message": "已清除角色额度模板"}
