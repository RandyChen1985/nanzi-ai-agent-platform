from fastapi import APIRouter, Depends, Query, HTTPException, status, Request
from pydantic import BaseModel
from typing import Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete, desc, func, or_
from app.core.dependencies import require_admin, require_any_permission, require_permission
from app.core.orm import get_db_session
from app.models.user import User
from app.models.permission import Role, ResourcePermission, UserRoleRelation
from app.schemas.permission import PermissionUpdate
from app.services.permission_service import PermissionService

router = APIRouter()

# 系统角色：一律拒绝删除。与前端 frontend/src/views/Roles.vue 的 isSystemRole() 同口径。
# miniprogram_user 是微信小程序静默登录自动开户时按 code 分配的角色
# （wechat_miniprogram_service.MINIPROGRAM_ROLE_CODE），删掉会让新用户开户直接失败，
# 所以和 default/admin/system 一样按系统角色保护。
SYSTEM_ROLE_CODES = {"default", "admin", "system", "miniprogram_user"}

# --- Request Models ---

class CreateRoleRequest(BaseModel):
    code: str
    name: str
    description: Optional[str] = None

class UpdateRoleRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None

class RoleResponse(BaseModel):
    id: int
    code: str
    name: str
    description: Optional[str]
    created_at: Optional[str]
    user_count: int = 0

class RoleListResponse(BaseModel):
    total: int
    page: int
    size: int
    items: List[RoleResponse]

class BulkAssignUsersRequest(BaseModel):
    user_ids: List[int]

class RemoveRoleUsersRequest(BaseModel):
    # 允许空数组：抽屉里"批量移出"可能在选中被清空后到达，返回 0 比 400 更省一次前端判断
    user_ids: List[int] = []

# --- Endpoints ---

@router.get("", response_model=RoleListResponse)
async def list_roles(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=1000),
    search: Optional[str] = None,
    # 读列表放开给「用户新增/编辑」：分配业务角色是编辑用户的一部分，不该再要求
    # 角色管理的查看权限。写操作仍各自绑定 element:role:edit / element:role:delete。
    admin: dict = Depends(require_any_permission(
        ("menu", "menu:system:roles"),
        ("element", "element:user:edit"),
    )),
    db: AsyncSession = Depends(get_db_session)
):
    """
    List all business roles.
    """
    stmt = select(Role).order_by(desc(Role.created_at))
    
    if search:
        stmt = stmt.where(Role.name.like(f"%{search}%") | Role.code.like(f"%{search}%"))
        
    # Count
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar()
    
    # Page
    stmt = stmt.offset((page - 1) * size).limit(size)
    result = await db.execute(stmt)
    roles = result.scalars().all()
    
    items = []
    for role in roles:
        # 用户数必须与成员列表同口径（join 用户表）。
        # 关系表在 MySQL 侧没有外键，历史脏数据里存在指向已删除用户的悬挂行——
        # 只 count 关系行会让列表显示「1」、点开却是「0 位成员」（线上实际出现过）。
        user_count_stmt = (
            select(func.count())
            .select_from(UserRoleRelation)
            .join(User, User.id == UserRoleRelation.user_id)
            .where(UserRoleRelation.role_id == role.id)
        )
        user_count = (await db.execute(user_count_stmt)).scalar()
        
        items.append({
            "id": role.id,
            "code": role.code,
            "name": role.name,
            "description": role.description,
            "created_at": role.created_at.isoformat() if role.created_at else None,
            "user_count": user_count
        })

    return {
        "total": total,
        "page": page,
        "size": size,
        "items": items
    }

@router.post("")
async def create_role(
    request: CreateRoleRequest,
    admin: dict = Depends(require_permission("element", "element:role:edit")),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Create a new role.
    """
    # Check duplicate code
    existing = await db.execute(select(Role).where(Role.code == request.code))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Role code already exists")
        
    new_role = Role(
        code=request.code,
        name=request.name,
        description=request.description
    )
    db.add(new_role)
    await db.commit()
    await db.refresh(new_role)
    
    return {
        "id": new_role.id,
        "code": new_role.code,
        "name": new_role.name,
        "description": new_role.description,
        "created_at": new_role.created_at.isoformat()
    }

@router.put("/{role_id}")
async def update_role(
    role_id: int,
    request: UpdateRoleRequest,
    admin: dict = Depends(require_permission("element", "element:role:edit")),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Update role details.
    """
    role = await db.get(Role, role_id)
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
        
    if request.name is not None:
        role.name = request.name
    if request.description is not None:
        role.description = request.description
        
    await db.commit()
    await db.refresh(role)
    return {
        "id": role.id,
        "code": role.code,
        "name": role.name,
        "description": role.description,
        "created_at": role.created_at.isoformat()
    }

@router.delete("/{role_id}")
async def delete_role(
    role_id: int,
    admin: dict = Depends(require_permission("element", "element:role:delete")),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Delete a role.

    权限码用 element:role:delete（「删除角色」）而不是 element:role:edit：
    权限树里这两个码是分开的，删除能力不该顺带绑死在编辑权限上。
    """
    role = await db.get(Role, role_id)
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")

    # 系统角色保护：必须在清理权限/成员关系之前拒绝，避免「关系删了、角色还在」。
    if (role.code or "").strip().lower() in SYSTEM_ROLE_CODES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"系统角色「{role.code}」不可删除",
        )

    users_stmt = select(UserRoleRelation.user_id).where(UserRoleRelation.role_id == role_id)
    users_result = await db.execute(users_stmt)
    affected_user_ids = list(users_result.scalars().all())

    # Clean up permissions
    await db.execute(delete(ResourcePermission).where(ResourcePermission.role_id == role_id))

    # Clean up user relations
    await db.execute(delete(UserRoleRelation).where(UserRoleRelation.role_id == role_id))
    
    await db.delete(role)
    await db.commit()

    if affected_user_ids:
        perm_service = PermissionService(db)
        await perm_service.invalidate_cached_permissions_for_users(affected_user_ids)

    return {"message": "Role deleted successfully"}

# --- Role User Management ---

@router.get("/{role_id}/users")
async def get_role_users(
    role_id: int,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=1000),
    search: Optional[str] = None,
    # 读成员与读角色列表同口径：只读管理员（仅有菜单权限）也要能看，否则会
    # 「进得了角色页、点开用户数 403」。写操作仍各自绑定 element:role:edit。
    admin: dict = Depends(require_any_permission(
        ("menu", "menu:system:roles"),
        ("element", "element:role:edit"),
    )),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get users assigned to this role.

    注意两点，改接口前务必先读：

    1. `user_ids` 是该角色的**全量**成员 ID，**不受 search / page 影响**。
       前端保存走的是“整集替换”语义（提交完整 ID 列表），一旦这里返回过滤或分页后的
       子集，未出现在响应里的成员会在保存时被静默移除。
    2. `items` 是给弹窗展示用的成员详情，支持搜索与分页，与 `user_ids` 范围可以不同。
    """
    role = await db.get(Role, role_id)
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")

    # 同样要与 items 同口径：悬挂 id 不能进入 user_ids，否则「分配用户」弹窗的整集替换
    # 会把它当成现有成员写回，脏数据永远清不掉。
    id_stmt = (
        select(UserRoleRelation.user_id)
        .join(User, User.id == UserRoleRelation.user_id)
        .where(UserRoleRelation.role_id == role_id)
    )
    user_ids = list((await db.execute(id_stmt)).scalars().all())

    stmt = (
        select(User)
        .join(UserRoleRelation, UserRoleRelation.user_id == User.id)
        .where(UserRoleRelation.role_id == role_id)
    )
    if search:
        like = f"%{search}%"
        stmt = stmt.where(
            or_(User.user_name.like(like), User.real_name.like(like))
        )

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar()

    stmt = stmt.order_by(desc(User.created_at)).offset((page - 1) * size).limit(size)
    rows = (await db.execute(stmt)).scalars().all()

    # 当前页成员的业务角色名：一次 IN 查询，禁止在下面逐行查（N+1）。
    # 抽屉靠这一列告诉管理员「这个人还属于哪些角色」——多角色权限是并集，
    # 不显示的话容易被误判成"此人的全部权限就是当前角色给的"。
    page_user_ids = [row.id for row in rows]
    role_names_map: dict[int, list[str]] = {int(uid): [] for uid in page_user_ids}
    if page_user_ids:
        names_stmt = (
            select(UserRoleRelation.user_id, Role.name)
            .join(Role, Role.id == UserRoleRelation.role_id)
            .where(UserRoleRelation.user_id.in_(page_user_ids))
            .order_by(Role.name)
        )
        for member_id, role_name in (await db.execute(names_stmt)).all():
            role_names_map.setdefault(int(member_id), []).append(role_name)

    items = [
        {
            "id": row.id,
            "user_name": row.user_name,
            "real_name": row.real_name,
            "email": row.email,
            "status": row.status,
            "role": row.role,
            "last_login_at": row.last_login_at.isoformat() if row.last_login_at else None,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "role_names": role_names_map.get(int(row.id), []),
        }
        for row in rows
    ]

    return {
        "user_ids": user_ids,
        "total": total,
        "page": page,
        "size": size,
        "items": items,
    }

@router.post("/{role_id}/users")
async def bulk_assign_role_users(
    role_id: int,
    request: BulkAssignUsersRequest,
    admin: dict = Depends(require_permission("element", "element:role:edit")),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Bulk assign users to this role.
    This replaces all user assignments FOR THIS ROLE ONLY.
    It does not affect other roles assigned to the users.
    """
    role = await db.get(Role, role_id)
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    
    target_user_ids = set(request.user_ids)
    
    # 1. Get current user assignments for this role
    stmt = select(UserRoleRelation.user_id).where(UserRoleRelation.role_id == role_id)
    result = await db.execute(stmt)
    current_user_ids = set(result.scalars().all())
    
    # 2. Identify users to add and remove
    ids_to_add = target_user_ids - current_user_ids
    ids_to_remove = current_user_ids - target_user_ids
    
    # 3. Perform removals
    if ids_to_remove:
        delete_stmt = delete(UserRoleRelation).where(
            UserRoleRelation.role_id == role_id,
            UserRoleRelation.user_id.in_(ids_to_remove)
        )
        await db.execute(delete_stmt)
        
    # 4. Perform additions
    for uid in ids_to_add:
        relation = UserRoleRelation(user_id=uid, role_id=role_id)
        db.add(relation)
        
    await db.commit()

    affected_user_ids = ids_to_add | ids_to_remove
    if affected_user_ids:
        perm_service = PermissionService(db)
        await perm_service.invalidate_cached_permissions_for_users(affected_user_ids)

    return {"message": f"Successfully updated role users: added {len(ids_to_add)}, removed {len(ids_to_remove)}"}

@router.post("/{role_id}/users/remove")
async def remove_role_users(
    role_id: int,
    request: RemoveRoleUsersRequest,
    admin: dict = Depends(require_permission("element", "element:role:edit")),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Remove users from this role. **Subtractive only** —— 只删不增。

    为什么需要这个端点而不是复用上面的整集替换：整集替换要求前端先取回全量成员 ID
    再提交差值，两个管理员并发操作时会互相覆盖（A 的成员快照会把 B 刚做的移出撤回）。
    这里只做减法，没有覆盖窗口。

    幂等：重复提交同一批 user_ids 不报错，`removed` 只统计真正删除的行数，
    `skipped` 报告传入但本来就不属于该角色的数量，供前端提示"部分成员已不在该角色中"。

    作用域：`WHERE role_id = X AND user_id IN (...)` —— 只动这一个角色的关系，
    这些用户在其他角色里的关系原样保留。
    """
    role = await db.get(Role, role_id)
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")

    # 去重但保序，避免同一 id 重复出现把 skipped 算错
    requested = list(dict.fromkeys(int(uid) for uid in (request.user_ids or [])))
    if not requested:
        return {"removed": 0, "skipped": 0, "user_ids": []}

    existing_stmt = select(UserRoleRelation.user_id).where(
        UserRoleRelation.role_id == role_id,
        UserRoleRelation.user_id.in_(requested),
    )
    existing_ids = set((await db.execute(existing_stmt)).scalars().all())
    skipped = len([uid for uid in requested if uid not in existing_ids])

    if not existing_ids:
        return {"removed": 0, "skipped": skipped, "user_ids": []}

    # 不存在"部分成功"：单条 DELETE + 一次 commit，要么全删要么都不删
    await db.execute(
        delete(UserRoleRelation).where(
            UserRoleRelation.role_id == role_id,
            UserRoleRelation.user_id.in_(existing_ids),
        )
    )
    await db.commit()

    # 漏掉这一步会让被移出者在权限缓存 TTL（1 小时）内继续持有该角色的权限
    perm_service = PermissionService(db)
    await perm_service.invalidate_cached_permissions_for_users(existing_ids)

    return {
        "removed": len(existing_ids),
        "skipped": skipped,
        "user_ids": sorted(int(uid) for uid in existing_ids),
    }

# --- Role Resource Permissions ---

@router.get("/{role_id}/permissions")
async def get_role_resources(
    role_id: int,
    request: Request,
    admin: dict = Depends(require_permission("element", "element:role:edit")),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get resources assigned to this role.
    """
    from app.core.v1_api_access import normalize_api_permission_ids

    role = await db.get(Role, role_id)
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
        
    service = PermissionService(db)
    response = await service.get_role_permissions(role_id)
    response.permissions.apis = normalize_api_permission_ids(
        request.app,
        response.permissions.apis or [],
    )
    return response

@router.put("/{role_id}/permissions")
async def update_role_resources(
    role_id: int,
    permissions: PermissionUpdate,
    request: Request,
    admin: dict = Depends(require_permission("element", "element:role:edit")),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Assign resources to this role.
    """
    from app.core.v1_api_access import normalize_api_permission_ids

    role = await db.get(Role, role_id)
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")

    permissions.apis = normalize_api_permission_ids(request.app, permissions.apis or [])
        
    service = PermissionService(db)
    await service.update_role_permissions(role_id, permissions)
    return {"message": "Role permissions updated successfully"}
