"""部门（组织树）领域服务。

职责边界：**所有会改变部门归属的写操作都必须经过这里**，因为 `users.dept_code` /
`org_path` 是主部门快照，一旦有接口层绕过服务直接写 `User.dept_code`，快照就会与
`ai_agent_user_departments.is_primary` 不一致，而行级过滤、ChatBI 上下文、报表分享
全都读快照。

事务约定：服务只 `flush()`，由调用方（接口层）`commit()`。这样"改部门 + 同步快照"
天然在同一个事务里，不会出现半成品状态。

纯函数（`compute_department_path` / `resolve_primary_selection` /
`choose_primary_after_removal`）单独抽出，便于穷举边界。
"""

from __future__ import annotations

from typing import Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.department import (
    DEPARTMENT_STATUS_ENABLED,
    Department,
    UserDepartment,
)
from app.models.user import User


# ---------------------------------------------------------------- 纯函数

def compute_department_path(parent_path: Optional[str], dept_id: int) -> str:
    """拼物化路径。末尾一定带 `/`，使 `LIKE '/1/7/%'` 不会误匹配 `/1/70/`。"""
    base = (parent_path or "").strip()
    if not base or base == "/":
        return f"/{dept_id}/"
    if not base.endswith("/"):
        base = f"{base}/"
    return f"{base}{dept_id}/"


MAX_DEPARTMENT_CODE_LENGTH = 50


def validate_department_code(code: str) -> str:
    """部门编码归一化与校验。

    **纯数字一律拒绝**：路径参数 `/{dept_ref}` 靠"是不是纯数字"区分自增 id 与编码，
    允许纯数字编码会让 `GET /departments/42` 产生歧义（到底是 id 42 还是编码 "42"）。
    """
    clean = (code or "").strip()
    if not clean:
        raise ValueError("部门编码不能为空")
    if clean.isdigit():
        raise ValueError("部门编码不能是纯数字（会与部门 id 混淆）")
    if len(clean) > MAX_DEPARTMENT_CODE_LENGTH:
        raise ValueError(f"部门编码不能超过 {MAX_DEPARTMENT_CODE_LENGTH} 个字符")
    return clean


def looks_like_department_id(ref: str) -> bool:
    """判断标识是自增 id 还是部门编码。"""
    text = (ref or "").strip()
    return bool(text) and text.isdigit()


def compose_code_path(path: Optional[str], code_by_id: Dict[int, str]) -> str:
    """把物化路径 `/1/7/` 拼成可读的编码路径 `/ROOT/EAST/`。

    只要链条上有一个 id 查不到编码（部门已被删、或数据不一致），就返回空串——
    宁可展示不出层级，也不要拼出一个缺一截的假路径。
    """
    parts = [part for part in str(path or "").strip("/").split("/") if part.isdigit()]
    if not parts:
        return ""
    codes: List[str] = []
    for part in parts:
        code = code_by_id.get(int(part))
        if code is None:
            return ""
        codes.append(str(code))
    return "/" + "/".join(codes) + "/"


def subtree_like_prefix(path: str) -> str:
    """子树前缀匹配条件（含自身，因为 path 自身也以该前缀开头）。"""
    normalized = path if path.endswith("/") else f"{path}/"
    return f"{normalized}%"


def resolve_primary_selection(
    dept_ids: Sequence[int], requested: Optional[int]
) -> Optional[int]:
    """校验并决议主部门。

    - 空集合：允许（用户不属于任何部门），但此时不允许指定主部门；
    - 未指定：默认取集合第一个（保持前端提交顺序，通常就是管理员先勾的那个）；
    - 指定了但不在集合里：拒绝——否则会写出"主部门存在但用户不是其成员"的非法状态。
    """
    unique_ids = list(dict.fromkeys(int(i) for i in dept_ids))
    if not unique_ids:
        if requested is not None:
            raise ValueError("未选择任何部门时不能指定主部门")
        return None
    if requested is None:
        return unique_ids[0]
    if int(requested) not in unique_ids:
        raise ValueError("主部门必须是该用户已选部门之一")
    return int(requested)


def choose_primary_after_removal(
    remaining: Iterable[Tuple[int, str]]
) -> Optional[int]:
    """移出主部门后重新挑一个：取层级最浅（path 最短）的，同层按 id 升序。

    用 path 而不是"随便选一个"，是为了让结果稳定可预期：管理员看到的主部门
    变成上级部门，而不是某个随机的兄弟部门。
    """
    candidates = [(dept_id, path or "") for dept_id, path in remaining]
    if not candidates:
        return None

    def depth(path: str) -> int:
        """路径层级数。`/1/2/` 是 2 层，`/15/` 是 1 层。"""
        return len([part for part in str(path or "").split("/") if part])

    # 必须按层级排，不能按路径字符串排：字典序下 "/1/2/" < "/15/"，会让二级部门压过
    # 根部门——id 一到两位数就会真的踩到，结果和"往上走一级"的意图正好相反。
    candidates.sort(key=lambda item: (depth(item[1]), item[0]))
    return candidates[0][0]


# ---------------------------------------------------------------- 服务

class DepartmentService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ---------------- 查询 ----------------

    async def get(self, dept_id: int) -> Department:
        dept = await self.db.get(Department, dept_id)
        if not dept:
            raise ValueError("部门不存在")
        return dept

    async def get_by_code(self, code: str) -> Optional[Department]:
        clean = (code or "").strip()
        if not clean:
            return None
        row = await self.db.execute(select(Department).where(Department.code == clean))
        return row.scalar_one_or_none()

    async def get_by_ref(self, ref: str) -> Optional[Department]:
        """按「自增 id 或部门编码」取部门。

        外部系统只有部门编码、前端手里是 id，两者都要能用同一套路径参数寻址；
        存储仍然只有自增 id 一个主键（改编码不必级联重写关联）。
        """
        text = str(ref if ref is not None else "").strip()
        if not text:
            return None
        if looks_like_department_id(text):
            return await self.db.get(Department, int(text))
        return await self.get_by_code(text)

    async def code_taken(self, code: str, exclude_id: Optional[int] = None) -> bool:
        """公开的编码占用查询（供前端实时预检）。"""
        return await self._code_taken(code, exclude_id=exclude_id)

    async def _resolve_parent(
        self,
        parent_id: Optional[int],
        parent_code: Optional[str],
    ) -> Optional[Department]:
        if parent_id is not None and (parent_code or "").strip():
            raise ValueError("上级部门同时给了 id 与编码，只能选一个")
        clean_code = (parent_code or "").strip()
        if clean_code:
            parent = await self.get_by_code(clean_code)
            if not parent:
                raise ValueError(f"上级部门编码 {clean_code} 不存在")
            return parent
        if parent_id is not None:
            return await self.get(int(parent_id))
        return None

    async def code_path_map(self, paths: Sequence[str]) -> Dict[str, str]:
        """把 `/1/7/` 形式的物化路径映射成 `/ROOT/EAST/`。

        落库的 path 用自增 id 拼（改编码时不必级联重写整棵子树），但对外展示与外部
        系统对接需要可读层级，所以在**返回时**派生 code_path，不落库。
        """
        normalized = [str(p) for p in dict.fromkeys(paths or []) if p]
        if not normalized:
            return {}
        ids: set[int] = set()
        for path in normalized:
            for part in path.strip("/").split("/"):
                if part.isdigit():
                    ids.add(int(part))
        if not ids:
            return {}
        rows = await self.db.execute(
            select(Department.id, Department.code).where(Department.id.in_(ids))
        )
        code_by_id = {int(row[0]): str(row[1]) for row in rows.all()}
        return {path: compose_code_path(path, code_by_id) for path in normalized}

    async def _code_taken(self, code: str, exclude_id: Optional[int] = None) -> bool:
        stmt = select(func.count()).select_from(Department).where(Department.code == code)
        if exclude_id is not None:
            stmt = stmt.where(Department.id != exclude_id)
        return bool((await self.db.execute(stmt)).scalar())

    @staticmethod
    async def department_names_for_user(db: AsyncSession, user_id: int) -> List[str]:
        """当前用户的部门名列表，**主部门排第一**。

        给"人看"的位置（个人中心资料、模型上下文）一律用名字：`dept_code` 是英文
        业务键、`org_path` 是 `/main/dev/` 这样的机器路径，都不适合直接展示。
        """
        rows = await db.execute(
            select(Department.name)
            .select_from(UserDepartment)
            .join(Department, Department.id == UserDepartment.dept_id)
            .where(UserDepartment.user_id == int(user_id))
            .order_by(UserDepartment.is_primary.desc(), Department.path.asc())
        )
        return [str(name) for (name,) in rows.all()]

    @staticmethod
    async def department_label_for_user(db: AsyncSession, user_id: int) -> str:
        """当前用户的部门可读标签，如「总部」或「总部（兼：开发部、运维部）」。

        `users.dept_code` / `org_path` 都是机器标识（`main` / `/1/`），模型答
        "我是什么部门的"时只能照搬，因为它手上没有任何中文名。这里给出人话版本。

        兼属多个部门时把其余部门一并列出：只报主部门会让"我还属于哪些部门"这类
        追问落空，而多部门正是本功能的重点。
        """
        names = await DepartmentService.department_names_for_user(db, user_id)
        if not names:
            return ""
        if len(names) == 1:
            return names[0]
        return f"{names[0]}（兼：{'、'.join(names[1:])}）"

    async def count_unique_members(self) -> int:
        """去重后的成员人数。

        各节点的 `member_count` 是**直属**人数，相加得到的是"人次"：一个人同时属于
        「开发部」和「运维部」会被计两次。把它当"人数"展示会虚高，所以单独查一次
        `COUNT(DISTINCT user_id)`。
        """
        value = (await self.db.execute(
            select(func.count(func.distinct(UserDepartment.user_id)))
        )).scalar()
        return int(value or 0)

    async def build_tree(self, include_disabled: bool = True) -> List[dict]:
        """返回嵌套树。member_count 只统计**直属**成员（与成员 tab 的内容一致）。"""
        stmt = select(Department)
        if not include_disabled:
            stmt = stmt.where(Department.status == DEPARTMENT_STATUS_ENABLED)
        rows = list((await self.db.execute(
            stmt.order_by(Department.sort_order, Department.id)
        )).scalars().all())

        code_paths = await self.code_path_map([d.path for d in rows])

        counts: dict[int, int] = {}
        count_rows = await self.db.execute(
            select(UserDepartment.dept_id, func.count())
            .group_by(UserDepartment.dept_id)
        )
        for dept_id, value in count_rows.all():
            counts[int(dept_id)] = int(value)

        nodes = {
            int(d.id): {
                "id": int(d.id),
                "parent_id": int(d.parent_id) if d.parent_id else None,
                "code": d.code,
                "name": d.name,
                "path": d.path,
                "code_path": code_paths.get(d.path, ""),
                "sort_order": d.sort_order,
                "status": d.status,
                "description": d.description,
                "member_count": counts.get(int(d.id), 0),
                "children": [],
            }
            for d in rows
        }

        roots: List[dict] = []
        for node in nodes.values():
            parent = nodes.get(node["parent_id"]) if node["parent_id"] else None
            if parent is None:
                roots.append(node)
            else:
                parent["children"].append(node)
        return roots

    # ---------------- 结构写 ----------------

    async def create(
        self,
        *,
        name: str,
        code: str,
        parent_id: Optional[int] = None,
        parent_code: Optional[str] = None,
        description: Optional[str] = None,
        sort_order: int = 0,
        status: int = DEPARTMENT_STATUS_ENABLED,
    ) -> Department:
        clean_name = (name or "").strip()
        if not clean_name:
            raise ValueError("部门名称不能为空")
        clean_code = validate_department_code(code)
        if await self._code_taken(clean_code):
            raise ValueError(f"部门编码 {clean_code} 已存在")

        parent = await self._resolve_parent(parent_id, parent_code)
        parent_path: Optional[str] = parent.path if parent else None

        dept = Department(
            parent_id=int(parent.id) if parent else None,
            code=clean_code,
            name=clean_name,
            path="/",
            sort_order=int(sort_order or 0),
            status=int(status),
            description=description,
        )
        self.db.add(dept)
        await self.db.flush()
        dept.path = compute_department_path(parent_path, int(dept.id))
        await self.db.flush()
        return dept

    async def update(
        self,
        dept_id: int,
        *,
        name: Optional[str] = None,
        code: Optional[str] = None,
        description: Optional[str] = None,
        sort_order: Optional[int] = None,
        status: Optional[int] = None,
    ) -> Department:
        dept = await self.get(int(dept_id))

        if name is not None:
            clean = name.strip()
            if not clean:
                raise ValueError("部门名称不能为空")
            dept.name = clean

        if code is not None:
            clean_code = validate_department_code(code)
            if clean_code != dept.code:
                if await self._code_taken(clean_code, exclude_id=int(dept.id)):
                    raise ValueError(f"部门编码 {clean_code} 已存在")
                dept.code = clean_code
                # 编码是 users.dept_code 的对齐键：改了就要把成员快照刷一遍，
                # 否则行级过滤会拿旧编码去比，静默查不到数据。
                await self.db.execute(
                    update(User)
                    .where(
                        User.id.in_(
                            select(UserDepartment.user_id).where(
                                UserDepartment.dept_id == int(dept.id),
                                UserDepartment.is_primary == 1,
                            )
                        )
                    )
                    .values(dept_code=clean_code)
                )
                # org_path 是**编码路径**快照（/main/dev/），所以本部门或任一子孙部门
                # 改编码，都会改变树内所有用户的前缀 —— 只刷本部门成员会留下半截旧路径。
                affected = await self.db.execute(
                    select(UserDepartment.user_id)
                    .join(Department, Department.id == UserDepartment.dept_id)
                    .where(
                        UserDepartment.is_primary == 1,
                        Department.path.like(subtree_like_prefix(dept.path)),
                    )
                )
                for (affected_user_id,) in affected.all():
                    await self.sync_primary_snapshot(int(affected_user_id))

        if description is not None:
            dept.description = description
        if sort_order is not None:
            dept.sort_order = int(sort_order)
        if status is not None:
            dept.status = int(status)

        await self.db.flush()
        return dept

    async def delete(self, dept_id: int) -> None:
        """只允许删除空部门：不做级联，避免误删整棵组织树或大批成员归属。"""
        dept = await self.get(int(dept_id))

        child_count = (await self.db.execute(
            select(func.count()).select_from(Department).where(
                Department.parent_id == int(dept.id)
            )
        )).scalar() or 0
        if int(child_count) > 0:
            raise ValueError(f"该部门下还有 {int(child_count)} 个子部门，请先移空")

        member_count = (await self.db.execute(
            select(func.count()).select_from(UserDepartment).where(
                UserDepartment.dept_id == int(dept.id)
            )
        )).scalar() or 0
        if int(member_count) > 0:
            raise ValueError(f"该部门下还有 {int(member_count)} 名成员，请先移出")

        # 显式清一次关系（此时应为 0 行）：无外键，任何残留都会变成悬挂数据
        await self.db.execute(
            delete(UserDepartment).where(UserDepartment.dept_id == int(dept.id))
        )
        await self.db.delete(dept)
        await self.db.flush()

    async def reorder(
        self,
        parent_id: Optional[int] = None,
        ordered_ids: Optional[Sequence[int]] = None,
        *,
        parent_code: Optional[str] = None,
        ordered_codes: Optional[Sequence[str]] = None,
    ) -> None:
        """同级批量排序：按提交顺序写 0,1,2…，保证顺序稳定且可重排。

        支持用部门编码寻址：外部系统没有我们的自增 id，迁移后直接按 code 排序。
        """
        ids = [int(i) for i in (ordered_ids or [])]
        if ordered_codes:
            ids = []
            for code in ordered_codes:
                dept = await self.get_by_code(code)
                if dept is None:
                    raise ValueError(f"部门编码 {code} 不存在")
                ids.append(int(dept.id))
        if parent_code:
            parent = await self.get_by_code(parent_code)
            if parent is None:
                raise ValueError(f"上级部门编码 {parent_code} 不存在")
            parent_id = int(parent.id)
        if not ids:
            return
        rows = list((await self.db.execute(
            select(Department).where(Department.id.in_(ids))
        )).scalars().all())
        by_id = {int(d.id): d for d in rows}
        for index, dept_id in enumerate(ids):
            dept = by_id.get(int(dept_id))
            if dept is None:
                continue
            expected_parent = int(parent_id) if parent_id is not None else None
            if (int(dept.parent_id) if dept.parent_id else None) != expected_parent:
                raise ValueError("只能在同一父节点下排序")
            dept.sort_order = index
        await self.db.flush()

    # ---------------- 成员关系 ----------------

    async def member_rows(self, dept_id: int) -> List[UserDepartment]:
        return list((await self.db.execute(
            select(UserDepartment).where(UserDepartment.dept_id == int(dept_id))
        )).scalars().all())

    async def add_members(self, dept_id: int, user_ids: Sequence[int]) -> dict:
        """增量添加（幂等）。不做整集替换：部门成员没有"一次编排全部门"的场景。"""
        dept = await self.get(int(dept_id))
        unique_ids = [int(i) for i in dict.fromkeys(user_ids or [])]
        if not unique_ids:
            return {"added": 0, "skipped": 0, "user_ids": []}

        existing = set()
        rows = await self.db.execute(
            select(UserDepartment.user_id).where(
                UserDepartment.dept_id == int(dept.id),
                UserDepartment.user_id.in_(unique_ids),
            )
        )
        existing = {int(r[0]) for r in rows.all()}

        to_add = [i for i in unique_ids if i not in existing]
        for user_id in to_add:
            self.db.add(
                UserDepartment(user_id=user_id, dept_id=int(dept.id), is_primary=0)
            )
        await self.db.flush()

        # 没有任何部门的用户，加入的第一个部门即主部门（spec §5）
        for user_id in to_add:
            has_primary = (await self.db.execute(
                select(func.count()).select_from(UserDepartment).where(
                    UserDepartment.user_id == user_id,
                    UserDepartment.is_primary == 1,
                )
            )).scalar() or 0
            if int(has_primary) == 0:
                await self.db.execute(
                    update(UserDepartment)
                    .where(
                        UserDepartment.user_id == user_id,
                        UserDepartment.dept_id == int(dept.id),
                    )
                    .values(is_primary=1)
                )

        for user_id in to_add:
            await self.sync_primary_snapshot(user_id)

        return {
            "added": len(to_add),
            "skipped": len(unique_ids) - len(to_add),
            "user_ids": sorted(to_add),
        }

    async def remove_members(self, dept_id: int, user_ids: Sequence[int]) -> dict:
        """只做减法、幂等；被移出主部门的用户会自动重选主部门。"""
        dept = await self.get(int(dept_id))
        unique_ids = [int(i) for i in dict.fromkeys(user_ids or [])]
        if not unique_ids:
            return {"removed": 0, "skipped": 0, "user_ids": []}

        rows = list((await self.db.execute(
            select(UserDepartment).where(
                UserDepartment.dept_id == int(dept.id),
                UserDepartment.user_id.in_(unique_ids),
            )
        )).scalars().all())
        affected = {int(r.user_id) for r in rows}
        primaries = {int(r.user_id) for r in rows if int(r.is_primary or 0) == 1}

        if affected:
            await self.db.execute(
                delete(UserDepartment).where(
                    UserDepartment.dept_id == int(dept.id),
                    UserDepartment.user_id.in_(list(affected)),
                )
            )
            await self.db.flush()

        # 主部门被移出的用户：从剩余部门里按 path 重选
        for user_id in primaries:
            remaining_rows = await self.db.execute(
                select(UserDepartment.dept_id).where(UserDepartment.user_id == user_id)
            )
            remaining_ids = [int(r[0]) for r in remaining_rows.all()]
            if remaining_ids:
                dept_rows = await self.db.execute(
                    select(Department.id, Department.path).where(
                        Department.id.in_(remaining_ids)
                    )
                )
                next_primary = choose_primary_after_removal(
                    [(int(r[0]), r[1]) for r in dept_rows.all()]
                )
                if next_primary is not None:
                    await self.db.execute(
                        update(UserDepartment)
                        .where(UserDepartment.user_id == user_id)
                        .values(is_primary=0)
                    )
                    await self.db.execute(
                        update(UserDepartment)
                        .where(
                            UserDepartment.user_id == user_id,
                            UserDepartment.dept_id == next_primary,
                        )
                        .values(is_primary=1)
                    )
            await self.sync_primary_snapshot(user_id)

        return {
            "removed": len(affected),
            "skipped": len(unique_ids) - len(affected),
            "user_ids": sorted(affected),
        }

    async def set_primary(self, dept_id: int, user_id: int) -> None:
        """把某部门设为该用户的主部门；要求该用户已是此部门成员。"""
        relation = (await self.db.execute(
            select(UserDepartment).where(
                UserDepartment.dept_id == int(dept_id),
                UserDepartment.user_id == int(user_id),
            )
        )).scalars().first()
        if relation is None:
            raise ValueError("该用户不在此部门中")

        await self.db.execute(
            update(UserDepartment)
            .where(UserDepartment.user_id == int(user_id))
            .values(is_primary=0)
        )
        await self.db.execute(
            update(UserDepartment)
            .where(
                UserDepartment.user_id == int(user_id),
                UserDepartment.dept_id == int(dept_id),
            )
            .values(is_primary=1)
        )
        await self.db.flush()
        await self.sync_primary_snapshot(int(user_id))

    async def set_user_departments(
        self, user_id: int, dept_ids: Sequence[int], primary_dept_id: Optional[int] = None
    ) -> None:
        """用户侧整集保存（用户属性语义）。"""
        target_ids = [int(i) for i in dict.fromkeys(dept_ids or [])]
        primary = resolve_primary_selection(target_ids, primary_dept_id)

        if target_ids:
            found = await self.db.execute(
                select(func.count()).select_from(Department).where(
                    Department.id.in_(target_ids)
                )
            )
            if int(found.scalar() or 0) != len(target_ids):
                raise ValueError("存在无效的部门")

        current_ids = {
            int(r[0])
            for r in (await self.db.execute(
                select(UserDepartment.dept_id).where(
                    UserDepartment.user_id == int(user_id)
                )
            )).all()
        }

        to_remove = current_ids - set(target_ids)
        to_add = set(target_ids) - current_ids

        if to_remove:
            await self.db.execute(
                delete(UserDepartment).where(
                    UserDepartment.user_id == int(user_id),
                    UserDepartment.dept_id.in_(list(to_remove)),
                )
            )
        for dept_id in sorted(to_add):
            self.db.add(
                UserDepartment(user_id=int(user_id), dept_id=int(dept_id), is_primary=0)
            )
        await self.db.flush()

        # 主部门唯一性：先全清再置目标，避免出现两条 is_primary=1
        await self.db.execute(
            update(UserDepartment)
            .where(UserDepartment.user_id == int(user_id))
            .values(is_primary=0)
        )
        if primary is not None:
            await self.db.execute(
                update(UserDepartment)
                .where(
                    UserDepartment.user_id == int(user_id),
                    UserDepartment.dept_id == int(primary),
                )
                .values(is_primary=1)
            )
        await self.db.flush()
        await self.sync_primary_snapshot(int(user_id))

    # ---------------- 快照同步 ----------------

    async def sync_primary_snapshot(self, user_id: int) -> None:
        """把主部门的 code / **编码路径**写回 users，供既有单值消费方读取。

        `org_path` 写的是 `/main/dev/` 而不是 `/1/2/`：id 路径是内部结构标识，
        人读不懂、模型只能照搬、跨系统迁移时 id 还会变；部门 code 是业务键，
        本来就是为对接设计的。树的内部结构仍用 id 路径（见 `path`），两者不要混。
        """
        row = (await self.db.execute(
            select(Department.code, Department.path)
            .select_from(UserDepartment)
            .join(Department, Department.id == UserDepartment.dept_id)
            .where(
                UserDepartment.user_id == int(user_id),
                UserDepartment.is_primary == 1,
            )
            .limit(1)
        )).first()

        if row is None:
            await self.db.execute(
                update(User).where(User.id == int(user_id)).values(
                    dept_code=None, org_path=None
                )
            )
        else:
            # 编码路径在返回时派生（不落库），这里落库的是派生结果，供外部系统与旧字段消费方读
            code_path = (await self.code_path_map([row[1]])).get(row[1], "")
            await self.db.execute(
                update(User).where(User.id == int(user_id)).values(
                    dept_code=row[0], org_path=code_path or None
                )
            )
        await self.db.flush()
