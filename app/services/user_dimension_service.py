"""SQL 行级过滤用的用户维度：主部门 + 多部门集合 + 含子孙部门的集合。

原先 ChatBI、固化报表、明细导出各自复制了一份 `_chatbi_user_dimensions()`，只塞
`dept_code`（主部门快照，单值）。用户属于 A、B 两个部门时，规则
`dept_code = {user.dept_code}` 只能命中主部门，B 的数据永远查不到——是漏权而非越权，
但业务上就是"我在 B 部门却看不到 B 的数据"。

现在统一从这里构造，一次给三个键：

- ``dept_code``：主部门（``users.dept_code`` 快照），模板 ``dept_code = {user.dept_code}``，
  旧规则零改动；
- ``dept_codes``：本人所属全部启用部门，模板 ``dept_code IN ({user.dept_codes})``；
- ``dept_codes_subtree``：本人所属部门 + 它们的全部子孙，
  模板 ``dept_code IN ({user.dept_codes_subtree})``。

``dept_codes_subtree`` 对应"部门负责人看整个分支"的常见诉求，用物化路径前缀一次算好，
不让规则模板去拼递归。

停用部门一律排除：停用的语义是"该部门不再使用"，若仍参与数据权限，会出现
"部门已停用但数据还能被看到"的怪状态。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.department import DEPARTMENT_STATUS_ENABLED, Department, UserDepartment
from app.services.department_service import subtree_like_prefix

logger = logging.getLogger(__name__)


def build_base_dimensions(user_info: Dict[str, Any], user_id: int) -> Dict[str, Any]:
    """不查库的基础维度。降级路径与无部门用户都用它，保证键齐全。"""
    return {
        "id": user_id,
        "user_name": user_info.get("user_name"),
        "real_name": user_info.get("real_name"),
        "role": user_info.get("role"),
        # 主部门：旧规则继续可用，不受多部门能力影响
        "dept_code": user_info.get("dept_code"),
        "dept_codes": [],
        "dept_codes_subtree": [],
        "org_path": user_info.get("org_path"),
        "extra_data": user_info.get("extra_data"),
    }


async def build_sql_user_dimensions(
    session: AsyncSession,
    user_info: Dict[str, Any],
    user_id: int,
) -> Dict[str, Any]:
    """构造 SQL 行级过滤的用户维度。

    部门查询失败时降级为"仅主部门"而不是抛错：代码可能先于迁移上线，
    此时库里也不存在部门数据，让 ChatBI 整页 500 是不可接受的。
    """
    dims = build_base_dimensions(user_info, user_id)
    try:
        own, subtree = await department_code_sets(session, int(user_id))
    except Exception as exc:  # noqa: BLE001 - 任何失败都降级，绝不打断查询主链路
        logger.warning("[UserDimension] 部门维度不可用，降级为仅主部门: %s", exc)
        # 失败查询会让事务进入失效状态，不回滚的话同一 session 后续查询全部报错
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return dims
    dims["dept_codes"] = own
    dims["dept_codes_subtree"] = subtree
    return dims


async def department_code_sets(session: AsyncSession, user_id: int) -> Tuple[List[str], List[str]]:
    """返回 (本人所属部门 code, 本人所属部门及其全部子孙的 code)。

    两条查询封顶：本人部门一条、子孙一条；没有部门时只发第一条。
    """
    rows = (
        await session.execute(
            select(Department.code, Department.path)
            .join(UserDepartment, UserDepartment.dept_id == Department.id)
            .where(
                UserDepartment.user_id == user_id,
                Department.status == DEPARTMENT_STATUS_ENABLED,
            )
        )
    ).all()

    own = sorted({str(code).strip() for code, _path in rows if code and str(code).strip()})
    paths = [str(path) for _code, path in rows if path]
    if not paths:
        return own, own

    child_rows = (
        await session.execute(
            select(Department.code).where(
                Department.status == DEPARTMENT_STATUS_ENABLED,
                or_(*[Department.path.like(subtree_like_prefix(path)) for path in paths]),
            )
        )
    ).all()
    subtree = sorted(
        set(own) | {str(code).strip() for (code,) in child_rows if code and str(code).strip()}
    )
    return own, subtree
