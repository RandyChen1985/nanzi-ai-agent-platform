"""部门（组织树）与用户-部门归属。

设计要点（详见 docs/superpowers/specs/2026-10-20-department-management-design.md）：

1. **树用物化路径** `path`（如 `/1/7/23/`）而不是递归 CTE：部门数量是人力规模量级，
   前缀匹配足够，且 MySQL / PostgreSQL 行为一致，前端拿到 path 也能直接判断祖先关系。
2. **两张表都不建外键** —— 用户明确要求，与 `ai_agent_user_role_relations`（V27 起无 FK）
   保持一致。代价是悬挂数据只能靠代码保证，因此：
   - 删除用户时显式删除 `ai_agent_user_departments`（见 `management.delete_user`）；
   - 删除部门只允许"无子部门且无成员"，从源头避免指向已删部门的关系。
3. `users.dept_code` / `org_path` 是**派生快照**（主部门的 code / path），唯一真源是
   `ai_agent_user_departments.is_primary`。快照由 `DepartmentService.sync_primary_snapshot`
   统一维护，接口层不得直接写这两个列——行级过滤、ChatBI 上下文、报表分享都读它们。
"""

from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, Integer, String, UniqueConstraint

from app.core.orm import Base

DEPARTMENT_STATUS_DISABLED = 0
DEPARTMENT_STATUS_ENABLED = 1


class Department(Base):
    __tablename__ = "ai_agent_departments"

    id = Column(BigInteger, primary_key=True, index=True, autoincrement=True)
    # 根部门为 NULL；单层挂载，不做多父节点（组织结构是树不是图）
    parent_id = Column(BigInteger, nullable=True, index=True)
    code = Column(String(64), unique=True, nullable=False, comment="业务键，与 users.dept_code 对齐")
    name = Column(String(100), nullable=False)
    # 物化路径，形如 /1/7/23/；末尾带斜杠，使 LIKE '/1/7/%' 不会误匹配 /1/70/
    path = Column(String(512), nullable=False, default="/", index=True)
    sort_order = Column(Integer, nullable=False, default=0)
    status = Column(
        Integer,
        nullable=False,
        default=DEPARTMENT_STATUS_ENABLED,
        comment="0=停用 1=启用",
    )
    description = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class UserDepartment(Base):
    __tablename__ = "ai_agent_user_departments"
    __table_args__ = (
        UniqueConstraint("user_id", "dept_id", name="uk_user_departments_user_dept"),
    )

    id = Column(BigInteger, primary_key=True, index=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    dept_id = Column(BigInteger, nullable=False, index=True)
    # 一个用户可属于多个部门，但最多一个主部门；主部门的 code/path 同步到 users 表
    is_primary = Column(Integer, nullable=False, default=0, comment="1=主部门")
    created_at = Column(DateTime, default=datetime.now)
