from fastapi import APIRouter, Depends, Query, HTTPException, status, Request
from pydantic import BaseModel
from typing import Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete, desc, case, func, or_
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

# 「持有即等于系统管理员」的 code：`get_user_permissions` 会把业务角色 code 并进
# `roles`，而 `is_admin_user` / `check_permission` 只认 `"admin"`。因此 admin 必须
# 永远禁止通过接口创建，否则「建 code=admin 的角色 + 把自己加进去」就是一键提权。
#
# 其余系统角色（default / system / miniprogram_user）**不附带任何特权**，却都有人工
# 补建需求：default 是「新增用户时默认勾选」的业务角色，缺了它该流程直接退化，
# 所以角色管理页提供了「一键初始化 default 角色」按钮。早前把整个 SYSTEM_ROLE_CODES
# 都当成创建保留字，等于把这个按钮做成必然 400 —— 保留字清单只管提权，删除保护另算。
PRIVILEGED_ROLE_CODES = {"admin"}

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
    # 用户数排序只能在后端做：列表是服务端分页的，前端本地排序只能排当前页，
    # 会把「用户数最多的角色」漏在后面几页。
    sort_by: str = Query("created_at", pattern="^(created_at|user_count)$"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
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
    # 用户数用子查询一次性带出来：既用于排序，又用于返回字段，顺带消掉原先
    # 「每个角色查一次 count」的 N+1。口径必须与成员列表一致——join 用户表，
    # 历史悬挂关系行（指向已删除用户）不计入，否则列表显示「1」、点开却是
    # 「0 位成员」（线上实际出现过）。
    user_count_subq = (
        select(
            UserRoleRelation.role_id.label("role_id"),
            func.count(User.id).label("user_count"),
        )
        .join(User, User.id == UserRoleRelation.user_id)
        .group_by(UserRoleRelation.role_id)
        .subquery()
    )
    # 没有成员行的角色 LEFT JOIN 后是 NULL，按 0 参与排序，避免升序时空角色被当成最小值顶到最前
    user_count_expr = func.coalesce(user_count_subq.c.user_count, 0)

    filters = []
    if search:
        filters.append(Role.name.like(f"%{search}%") | Role.code.like(f"%{search}%"))

    # Count（独立计数，不必为统计套一层 join 子查询）
    count_stmt = select(func.count()).select_from(Role).where(*filters)
    total = (await db.execute(count_stmt)).scalar() or 0

    # Page
    sort_expr = user_count_expr if sort_by == "user_count" else Role.created_at
    primary_order = sort_expr.asc() if sort_order == "asc" else sort_expr.desc()
    # 系统角色固定置顶：default/admin 出错会直接影响登录与开户，不能因为按用户数
    # 排序就被挤到后面几页。判定口径与删除保护一致（去空白 + 大小写归一）。
    system_role_rank = case(
        (func.lower(func.trim(Role.code)).in_(sorted(SYSTEM_ROLE_CODES)), 1),
        else_=0,
    )
    stmt = (
        select(Role, user_count_expr.label("user_count"))
        .outerjoin(user_count_subq, user_count_subq.c.role_id == Role.id)
        .where(*filters)
        # 系统角色组内仍按所选排序；用户数相同（常见：0 人角色）时用创建时间/ID 兜底，
        # 保证翻页不跳行
        .order_by(desc(system_role_rank), primary_order, desc(Role.created_at), desc(Role.id))
        .offset((page - 1) * size)
        .limit(size)
    )
    rows = (await db.execute(stmt)).all()

    items = [
        {
            "id": role.id,
            "code": role.code,
            "name": role.name,
            "description": role.description,
            "created_at": role.created_at.isoformat() if role.created_at else None,
            "user_count": int(user_count or 0),
        }
        for role, user_count in rows
    ]

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

    提权 code（``PRIVILEGED_ROLE_CODES``）一律拒绝：``get_user_permissions`` 会把
    业务角色的 ``code`` 一并并进 ``roles`` 列表，而 ``is_admin_user`` 判的就是
    ``"admin" in roles``。若允许建一个 code=admin 的业务角色，把任何人加进去
    就等于给他系统管理员权限（``check_permission`` 对 admin 直接放行）。

    其余系统角色**不再拦**：``default`` / ``system`` / ``miniprogram_user`` 都不带
    特权，而 ``default`` 需要能被「一键初始化 default 角色」补建出来（见
    ``PRIVILEGED_ROLE_CODES`` 的注释）。它们不会被误删——删除保护看的是
    ``SYSTEM_ROLE_CODES``。
    """
    code = (request.code or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="Role code is required")
    if code.lower() in PRIVILEGED_ROLE_CODES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Role code '{code}' is reserved by the system "
                f"({', '.join(sorted(PRIVILEGED_ROLE_CODES))})"
            ),
        )

    # Check duplicate code（去空白 + 大小写归一）：MySQL 默认排序规则本就忽略大小写，
    # PostgreSQL 不忽略。不归一的话 PG 上能建出 ``Default``，与既有 ``default`` 并存，
    # 而前端「是否已初始化 default」是大小写无关匹配的，会出现两个都算数的异形角色。
    normalized = func.lower(func.trim(Role.code))
    existing = await db.execute(
        select(Role.id).where(normalized == code.lower())
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=400, detail="Role code already exists")
        
    new_role = Role(
        code=code,
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
