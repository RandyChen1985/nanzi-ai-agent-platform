"""菜单与权限节点表（`ai_agent_menus`）。

对应的迁移是 `db-prod/V176-create_agent_menus.sql` / `db-prod-pg/V77-create_agent_menus.sql`。
这张表同时承担两个职责，因此没有拆成两张表：

1. 侧栏导航：`group` 分组 + `page`/`external` 菜单项；
2. 角色管理的权限树：`page`/`external` 可授权，`element` 是只读的功能点叶子。

刻意没有建 `parent_id` 的自引用外键：`ON DELETE CASCADE` 会连带删掉整棵子树，
而菜单删除必须由应用层严格限制（内置项禁止删除、外链删除要清理授权行）。
"""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Index, Integer, String

from app.core.orm import Base


class AgentMenu(Base):
    __tablename__ = "ai_agent_menus"

    id = Column(BigInteger, primary_key=True, index=True, autoincrement=True)
    parent_id = Column(BigInteger, nullable=True, index=True)
    node_type = Column(String(16), nullable=False)
    # 不可变业务键：seed 幂等、外链路由参数、权限树节点的 key 都用它。
    code = Column(String(64), nullable=False, unique=True)
    # 授权口径。公开菜单（如「我的工作台」）为 NULL。
    permission_code = Column(String(128), nullable=True, unique=True)
    label = Column(String(64), nullable=False)
    icon = Column(String(32), nullable=True)
    path = Column(String(128), nullable=True)
    active_route_names = Column(String(255), nullable=True)
    url = Column(String(512), nullable=True)
    open_mode = Column(String(16), nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)
    enabled = Column(Boolean, nullable=False, default=True)
    desktop_only = Column(Boolean, nullable=False, default=False)
    is_landing = Column(Boolean, nullable=False, default=False)
    builtin = Column(Boolean, nullable=False, default=True)
    description = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    __table_args__ = (
        Index("ix_agent_menus_parent_sort", "parent_id", "sort_order"),
        Index("ix_agent_menus_node_type", "node_type"),
    )
