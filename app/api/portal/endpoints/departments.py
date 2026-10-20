"""部门管理接口：结构维护 + 成员归属。

权限口径（与角色成员一致）：

- 读（树 / 成员列表）：`menu:system:departments` 或 `element:dept:edit` 任一即可。
  角色页踩过这个坑——只读管理员进得了页面，点开成员却 403，像是功能坏了。
- 写（建/改/排序/成员增删）：严格要 `element:dept:edit`。
- 删除部门：单独要 `element:dept:delete`，因为影响面比改名大。

成员写入是**增量**语义，刻意不提供整集替换：部门成员没有"一次编排整个部门"的
使用场景，增量接口也不存在两个管理员互相覆盖的窗口。
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import require_any_permission, require_permission
from app.core.orm import get_db_session
from app.models.department import Department, UserDepartment
from app.models.user import User
from app.services.department_service import (
    DepartmentService,
    subtree_like_prefix,
    validate_department_code,
)

router = APIRouter()

# 读权限：菜单码或编辑码任一即可
READ_PERMISSION = require_any_permission(
    ("menu", "menu:system:departments"),
    ("element", "element:dept:edit"),
)


# ---------------------------------------------------------------- 请求模型

class CreateDepartmentRequest(BaseModel):
    name: str
    code: str
    # 上级部门二选一：parent_id（前端手上有 id）或 parent_code（外部系统只有编码）
    parent_id: Optional[int] = None
    parent_code: Optional[str] = None
    description: Optional[str] = None
    sort_order: int = 0
    status: int = 1


class UpdateDepartmentRequest(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None
    description: Optional[str] = None
    sort_order: Optional[int] = None
    status: Optional[int] = None


class ReorderDepartmentsRequest(BaseModel):
    parent_id: Optional[int] = None
    parent_code: Optional[str] = None
    ordered_ids: List[int] = []
    ordered_codes: List[str] = []


class AddMembersRequest(BaseModel):
    user_ids: List[int] = []


class SetPrimaryMemberRequest(BaseModel):
    user_id: int


# ---------------------------------------------------------------- 辅助

async def _require_department(db: AsyncSession, dept_ref: str) -> Department:
    """按「自增 id 或部门编码」取部门。

    外部系统迁移过来只有编码，前端手上有 id；两种标识共用同一套路径参数，
    调用方不必知道我们内部用的是自增主键。
    """
    dept = await DepartmentService(db).get_by_ref(dept_ref)
    if not dept:
        raise HTTPException(status_code=404, detail="部门不存在")
    return dept


def _client_error(exc: ValueError, *, conflict: bool = False) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT if conflict else status.HTTP_400_BAD_REQUEST,
        detail=str(exc),
    )


# ---------------------------------------------------------------- 结构

@router.get("/tree")
async def get_department_tree(
    include_disabled: bool = Query(True, description="是否包含已停用部门"),
    admin: dict = Depends(READ_PERMISSION),
    db: AsyncSession = Depends(get_db_session),
):
    """部门树 + 每节点直属成员数。"""
    service = DepartmentService(db)
    roots = await service.build_tree(include_disabled=include_disabled)

    def count_nodes(nodes: List[dict]) -> int:
        return sum(1 + count_nodes(node["children"]) for node in nodes)

    return {
        "items": roots,
        "total": count_nodes(roots),
        # 去重人数：各节点 member_count 相加是"人次"，多部门用户会重复计数
        "member_total": await service.count_unique_members(),
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_department(
    request: CreateDepartmentRequest,
    admin: dict = Depends(require_permission("element", "element:dept:edit")),
    db: AsyncSession = Depends(get_db_session),
):
    service = DepartmentService(db)
    try:
        dept = await service.create(
            name=request.name,
            code=request.code,
            parent_id=request.parent_id,
            parent_code=request.parent_code,
            description=request.description,
            sort_order=request.sort_order,
            status=request.status,
        )
        await db.commit()
    except ValueError as exc:
        raise _client_error(exc) from exc
    return {"id": int(dept.id), "code": dept.code, "name": dept.name, "path": dept.path}


@router.get("/code-available")
async def check_department_code(
    code: str = Query(..., min_length=1, max_length=64, description="待检查的部门编码"),
    exclude_ref: Optional[str] = Query(None, description="编辑场景排除自身（可传 id 或编码）"),
    admin: dict = Depends(READ_PERMISSION),
    db: AsyncSession = Depends(get_db_session),
):
    """编码可用性预检：让人在输入时就得到反馈，而不是提交后才 400。

    只读接口，不需要 edit 权限（能看部门树的人就该能查重）。
    """
    service = DepartmentService(db)
    try:
        clean_code = validate_department_code(code)
    except ValueError as exc:
        return {"available": False, "code": (code or "").strip(), "reason": str(exc)}

    exclude_id: Optional[int] = None
    if exclude_ref:
        current = await service.get_by_ref(exclude_ref)
        exclude_id = int(current.id) if current else None

    taken = await service.code_taken(clean_code, exclude_id=exclude_id)
    return {
        "available": not taken,
        "code": clean_code,
        "reason": "" if not taken else f"部门编码 {clean_code} 已被使用",
    }


@router.put("/{dept_ref}")
async def update_department(
    dept_ref: str,
    request: UpdateDepartmentRequest,
    admin: dict = Depends(require_permission("element", "element:dept:edit")),
    db: AsyncSession = Depends(get_db_session),
):
    dept = await _require_department(db, dept_ref)
    service = DepartmentService(db)
    try:
        dept = await service.update(
            int(dept.id),
            name=request.name,
            code=request.code,
            description=request.description,
            sort_order=request.sort_order,
            status=request.status,
        )
        await db.commit()
    except ValueError as exc:
        raise _client_error(exc) from exc
    return {"id": int(dept.id), "code": dept.code, "name": dept.name}


@router.delete("/{dept_ref}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_department(
    dept_ref: str,
    admin: dict = Depends(require_permission("element", "element:dept:delete")),
    db: AsyncSession = Depends(get_db_session),
):
    """只允许删除空部门：有子部门或有成员都返回 409，不做级联。"""
    dept = await _require_department(db, dept_ref)
    service = DepartmentService(db)
    try:
        await service.delete(int(dept.id))
        await db.commit()
    except ValueError as exc:
        raise _client_error(exc, conflict=True) from exc
    return None


@router.post("/reorder")
async def reorder_departments(
    request: ReorderDepartmentsRequest,
    admin: dict = Depends(require_permission("element", "element:dept:edit")),
    db: AsyncSession = Depends(get_db_session),
):
    service = DepartmentService(db)
    try:
        await service.reorder(
            request.parent_id,
            request.ordered_ids,
            parent_code=request.parent_code,
            ordered_codes=request.ordered_codes,
        )
        await db.commit()
    except ValueError as exc:
        raise _client_error(exc) from exc
    return {"ok": True, "count": len(request.ordered_ids or request.ordered_codes)}


# ---------------------------------------------------------------- 成员

@router.get("/{dept_ref}/members")
async def list_department_members(
    dept_ref: str,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    search: Optional[str] = None,
    include_sub: bool = Query(False, description="是否包含子部门成员（部门负责人看整个分支）"),
    admin: dict = Depends(READ_PERMISSION),
    db: AsyncSession = Depends(get_db_session),
):
    """部门成员（分页 + 搜索）。部门名一次 IN 查询取回，禁止逐行查。

    `include_sub=true` 时范围扩到整棵子树：一个人可能既在父部门又在子部门，
    所以按 user 去重，否则同一个人会在列表里出现两次。
    """
    dept = await _require_department(db, dept_ref)

    dept_ids = [int(dept.id)]
    if include_sub:
        child_rows = await db.execute(
            select(Department.id).where(
                Department.path.like(subtree_like_prefix(dept.path))
            )
        )
        dept_ids = [int(row[0]) for row in child_rows.all()] or dept_ids

    search_filter = None
    if search:
        like = f"%{search}%"
        search_filter = or_(
            User.user_name.like(like),
            User.real_name.like(like),
            User.email.like(like),
        )

    if include_sub:
        # 子树视图：先是去重后的 user，再分页，避免同一用户的重复归属行占名额
        member_ids = (
            select(UserDepartment.user_id)
            .where(UserDepartment.dept_id.in_(dept_ids))
            .distinct()
        )
        user_stmt = select(User).where(User.id.in_(member_ids))
        if search_filter is not None:
            user_stmt = user_stmt.where(search_filter)
        total = (await db.execute(
            select(func.count()).select_from(user_stmt.subquery())
        )).scalar() or 0
        page_users = (await db.execute(
            user_stmt.order_by(User.user_name.asc())
            .offset((page - 1) * size)
            .limit(size)
        )).scalars().all()
        rows = [(None, user) for user in page_users]
    else:
        stmt = (
            select(UserDepartment, User)
            .join(User, User.id == UserDepartment.user_id)
            .where(UserDepartment.dept_id == int(dept.id))
        )
        if search_filter is not None:
            stmt = stmt.where(search_filter)
        total = (await db.execute(
            select(func.count()).select_from(stmt.subquery())
        )).scalar() or 0
        rows = (await db.execute(
            stmt.order_by(UserDepartment.is_primary.desc(), User.user_name.asc())
            .offset((page - 1) * size)
            .limit(size)
        )).all()

    user_ids = [int(row[1].id) for row in rows]
    dept_names_map: dict[int, List[str]] = {}
    primary_name_map: dict[int, str] = {}
    in_scope_primary: set[int] = set()
    if user_ids:
        rel_rows = await db.execute(
            select(
                UserDepartment.user_id,
                Department.name,
                UserDepartment.dept_id,
                UserDepartment.is_primary,
            )
            .join(Department, Department.id == UserDepartment.dept_id)
            .where(UserDepartment.user_id.in_(user_ids))
            .order_by(Department.path.asc())
        )
        for uid, name, rel_dept_id, is_primary in rel_rows.all():
            uid = int(uid)
            dept_names_map.setdefault(uid, []).append(name)
            if int(is_primary or 0) == 1:
                primary_name_map[uid] = name
                if int(rel_dept_id) in dept_ids:
                    in_scope_primary.add(uid)

    items = []
    for relation, user in rows:
        uid = int(user.id)
        # 直属视图：本部门就是主部门；子树视图：主部门落在子树范围内即算
        is_primary = (
            bool(int(relation.is_primary or 0)) if relation is not None else uid in in_scope_primary
        )
        items.append({
            "id": uid,
            "user_name": user.user_name,
            "real_name": user.real_name,
            "email": user.email or "",
            "role": user.role,
            "status": user.status,
            "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
            "is_primary": is_primary,
            "primary_dept_name": primary_name_map.get(uid, ""),
            "dept_names": dept_names_map.get(uid, []),
        })

    code_paths = await DepartmentService(db).code_path_map([dept.path])
    return {"items": items, "total": int(total), "page": page, "size": size,
            "include_sub": include_sub,
            "dept": {"id": int(dept.id), "name": dept.name, "code": dept.code,
                     "path": dept.path, "code_path": code_paths.get(dept.path, "")}}


@router.post("/{dept_ref}/members")
async def add_department_members(
    dept_ref: str,
    request: AddMembersRequest,
    admin: dict = Depends(require_permission("element", "element:dept:edit")),
    db: AsyncSession = Depends(get_db_session),
):
    """增量添加成员（幂等）：已存在的计入 skipped，不报错。"""
    dept = await _require_department(db, dept_ref)
    service = DepartmentService(db)
    try:
        result = await service.add_members(int(dept.id), request.user_ids)
        await db.commit()
    except ValueError as exc:
        raise _client_error(exc) from exc
    return result


@router.post("/{dept_ref}/members/remove")
async def remove_department_members(
    dept_ref: str,
    request: AddMembersRequest,
    admin: dict = Depends(require_permission("element", "element:dept:edit")),
    db: AsyncSession = Depends(get_db_session),
):
    """只做减法、幂等；移出的若是主部门，会自动从剩余部门里重选。"""
    dept = await _require_department(db, dept_ref)
    service = DepartmentService(db)
    try:
        result = await service.remove_members(int(dept.id), request.user_ids)
        await db.commit()
    except ValueError as exc:
        raise _client_error(exc) from exc
    return result


@router.post("/{dept_ref}/members/primary")
async def set_department_member_primary(
    dept_ref: str,
    request: SetPrimaryMemberRequest,
    admin: dict = Depends(require_permission("element", "element:dept:edit")),
    db: AsyncSession = Depends(get_db_session),
):
    """把本部门设为该成员的主部门（用户必须已是本部门成员）。"""
    dept = await _require_department(db, dept_ref)
    service = DepartmentService(db)
    try:
        await service.set_primary(int(dept.id), request.user_id)
        await db.commit()
    except ValueError as exc:
        raise _client_error(exc) from exc
    return {"ok": True, "user_id": int(request.user_id), "dept_id": int(dept.id)}
