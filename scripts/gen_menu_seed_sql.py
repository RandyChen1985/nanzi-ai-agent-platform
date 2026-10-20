#!/usr/bin/env python3
"""从 `app/services/menu_seed.py` 生成菜单表的两份迁移 SQL。

为什么用脚本生成而不是手写 SQL：菜单定义同时要供后端兜底（表未建立时返回内置导航）
和迁移 seed 使用。手写两份必然漂移，而这正是本次改造要消灭的问题（原先菜单散落在
前端 `menuGroups`、`MENU_TREE`、路由表、后端显示名字典四处）。

用法：
    python3 scripts/gen_menu_seed_sql.py            # 写文件
    python3 scripts/gen_menu_seed_sql.py --check    # 只比对，不写（供测试使用）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.menu_seed import (  # noqa: E402
    BUILTIN_MENU_SEED,
    BuiltinMenuNode,
)

MYSQL_PATH = ROOT / "db-prod" / "V176-create_agent_menus.sql"
PG_PATH = ROOT / "db-prod-pg" / "V77-create_agent_menus.sql"

TABLE = "ai_agent_menus"

# 需要拆分权限码的两个菜单：新码 ← 旧码。迁移里把旧码已生效的授权复制到新码，
# 保证升级前后可见性完全一致，之后管理员可以单独收紧。
SPLIT_PERMISSION_PAIRS = (
    ("menu:token_stats", "menu:system:audit"),
    ("menu:knowledge_metrics", "menu:knowledge_management"),
)

INSERT_COLUMNS = (
    "parent_id",
    "node_type",
    "code",
    "permission_code",
    "label",
    "icon",
    "path",
    "active_route_names",
    "url",
    "open_mode",
    "sort_order",
    "enabled",
    "desktop_only",
    "is_landing",
    "builtin",
    "description",
    "created_at",
    "updated_at",
)


def _lit(value) -> str:
    """把 Python 值渲染成 SQL 字面量。"""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    text = str(value).replace("'", "''")
    return f"'{text}'"


def _bool_literal(value: bool, dialect: str) -> str:
    if dialect == "pg":
        return "TRUE" if value else "FALSE"
    return "1" if value else "0"


def _now(dialect: str) -> str:
    # 两库都支持 NOW()；PG 里它等价于 CURRENT_TIMESTAMP。
    return "NOW()"


def _quote(identifier: str, dialect: str) -> str:
    return f'"{identifier}"' if dialect == "pg" else identifier


def _ddl_mysql() -> str:
    return f"""-- V176: 平台菜单与权限节点表（MySQL 方言）
-- 变更原因：左侧菜单原先写死在 frontend/src/views/Dashboard.vue 的 menuGroups，
--           权限树写死在 frontend/src/constants/permissions.ts 的 MENU_TREE，
--           同一份菜单存在四份清单、靠人工同步，无法在后台改名/排序/加外链。
-- 需求背景：docs/superpowers/specs/2026-10-20-menu-management-design.md
-- 创建时间：2026-10-20
-- 注意事项：本脚本由 scripts/gen_menu_seed_sql.py 从 app/services/menu_seed.py 生成，
--           请勿手工编辑；如需调整菜单请改 menu_seed.py 后重新生成。
--           本脚本仅由 Agent 生成，不得自动执行，请由用户手工导入更新到数据库。
--           apply_sql.py 无 schema_migrations 记录表，幂等必须写在 SQL 自身。

-- 与 V26 等既有迁移保持一致：显式声明连接字符集，否则命令行客户端可能按 latin1 导入，
-- 中文菜单名会被截断或写成问号（表建好了但侧栏是乱码，比报错更难排查）。
SET NAMES utf8mb4;

CREATE TABLE IF NOT EXISTS {TABLE} (
    id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
    parent_id BIGINT NULL COMMENT '父节点ID：分组为空；菜单项指向分组；功能点指向所属页面',
    node_type VARCHAR(16) NOT NULL COMMENT '节点类型：group/page/external/element',
    code VARCHAR(64) NOT NULL COMMENT '不可变业务键（seed 幂等与外链定位）',
    permission_code VARCHAR(128) NULL COMMENT '授权码（menu:* / element:*），公开菜单为空',
    label VARCHAR(64) NOT NULL COMMENT '显示名',
    icon VARCHAR(32) NULL COMMENT '内置图标 key',
    path VARCHAR(128) NULL COMMENT '内置页面路由路径',
    active_route_names VARCHAR(255) NULL COMMENT '用于高亮的额外路由 name，逗号分隔',
    url VARCHAR(512) NULL COMMENT '外链地址（external 专用）',
    open_mode VARCHAR(16) NULL COMMENT '外链打开方式：new_tab/embed',
    sort_order INT NOT NULL DEFAULT 0 COMMENT '同级排序',
    enabled TINYINT(1) NOT NULL DEFAULT 1 COMMENT '是否启用',
    desktop_only TINYINT(1) NOT NULL DEFAULT 0 COMMENT '仅桌面端显示',
    is_landing TINYINT(1) NOT NULL DEFAULT 0 COMMENT '默认落地页（全局至多一条）',
    builtin TINYINT(1) NOT NULL DEFAULT 1 COMMENT '是否内置项（内置项不可删除）',
    description VARCHAR(255) NULL COMMENT '备注',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    UNIQUE KEY ux_agent_menus_code (code),
    UNIQUE KEY ux_agent_menus_permission_code (permission_code),
    KEY ix_agent_menus_parent_sort (parent_id, sort_order),
    KEY ix_agent_menus_node_type (node_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='平台菜单与权限节点表';

-- 注意：parent_id 刻意不建自引用外键。ON DELETE CASCADE 会连带删掉整棵子树，
--       而菜单删除必须由应用层严格限制（内置项禁止删除，外链删除要清理授权行）。
"""


def _ddl_pg() -> str:
    return f"""-- =====================================================================
-- 变更原因：左侧菜单原先写死在 frontend/src/views/Dashboard.vue 的 menuGroups，
--           权限树写死在 frontend/src/constants/permissions.ts 的 MENU_TREE，
--           同一份菜单存在四份清单、靠人工同步，无法在后台改名/排序/加外链。
-- 需求背景：动态菜单与菜单管理（PostgreSQL 方言）
--           见 docs/superpowers/specs/2026-10-20-menu-management-design.md
-- 创建时间：2026-10-20
-- 注意事项：本脚本由 scripts/gen_menu_seed_sql.py 从 app/services/menu_seed.py 生成，
--           请勿手工编辑；如需调整菜单请改 menu_seed.py 后重新生成。
--           该脚本仅做生成，不得由 Agent 自动执行，由用户手动导入更新到数据库。
--           apply_sql.py 无 schema_migrations 记录表，幂等必须写在 SQL 自身。
-- =====================================================================

CREATE TABLE IF NOT EXISTS "{TABLE}" (
    "id" BIGSERIAL PRIMARY KEY,
    -- 父节点：分组为空；菜单项指向分组；功能点指向所属页面
    "parent_id" BIGINT,
    -- 节点类型：group/page/external/element
    "node_type" VARCHAR(16) NOT NULL,
    -- 不可变业务键（seed 幂等与外链定位）
    "code" VARCHAR(64) NOT NULL,
    -- 授权码（menu:* / element:*），公开菜单为空
    "permission_code" VARCHAR(128),
    -- 显示名
    "label" VARCHAR(64) NOT NULL,
    -- 内置图标 key
    "icon" VARCHAR(32),
    -- 内置页面路由路径
    "path" VARCHAR(128),
    -- 用于高亮的额外路由 name，逗号分隔
    "active_route_names" VARCHAR(255),
    -- 外链地址（external 专用）
    "url" VARCHAR(512),
    -- 外链打开方式：new_tab/embed
    "open_mode" VARCHAR(16),
    -- 同级排序
    "sort_order" INTEGER NOT NULL DEFAULT 0,
    -- 是否启用
    "enabled" BOOLEAN NOT NULL DEFAULT TRUE,
    -- 仅桌面端显示
    "desktop_only" BOOLEAN NOT NULL DEFAULT FALSE,
    -- 默认落地页（全局至多一条）
    "is_landing" BOOLEAN NOT NULL DEFAULT FALSE,
    -- 是否内置项（内置项不可删除）
    "builtin" BOOLEAN NOT NULL DEFAULT TRUE,
    -- 备注
    "description" VARCHAR(255),
    -- 创建时间
    "created_at" TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    -- 更新时间
    "updated_at" TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "ux_agent_menus_code" UNIQUE ("code"),
    CONSTRAINT "ux_agent_menus_permission_code" UNIQUE ("permission_code")
);

-- 注意：parent_id 刻意不建自引用外键。ON DELETE CASCADE 会连带删掉整棵子树，
--       而菜单删除必须由应用层严格限制（内置项禁止删除，外链删除要清理授权行）。

CREATE INDEX IF NOT EXISTS "ix_agent_menus_parent_sort"
    ON "{TABLE}" ("parent_id", "sort_order");

CREATE INDEX IF NOT EXISTS "ix_agent_menus_node_type"
    ON "{TABLE}" ("node_type");
"""


def _insert_sql(node: BuiltinMenuNode, dialect: str) -> str:
    """生成一条幂等 INSERT。

    父节点用子查询按 `code` 解析，这样脚本不依赖自增 id，重复执行也不会错位。
    """
    table = _quote(TABLE, dialect) if dialect == "pg" else TABLE
    if node.parent_code:
        parent_expr = f"(SELECT p.id FROM {table} p WHERE p.code = {_lit(node.parent_code)})"
    else:
        parent_expr = "NULL"

    values = [
        parent_expr,
        _lit(node.node_type),
        _lit(node.code),
        _lit(node.permission_code),
        _lit(node.label),
        _lit(node.icon),
        _lit(node.path),
        _lit(node.active_route_names),
        "NULL",  # url：内置节点没有外链
        "NULL",  # open_mode
        str(node.sort_order),
        _bool_literal(True, dialect),
        _bool_literal(node.desktop_only, dialect),
        _bool_literal(node.is_landing, dialect),
        _bool_literal(True, dialect),  # builtin
        _lit(node.description),
        _now(dialect),
        _now(dialect),
    ]

    columns = ", ".join(_quote(col, dialect) for col in INSERT_COLUMNS) if dialect == "pg" else ", ".join(INSERT_COLUMNS)
    return (
        f"INSERT INTO {table}\n    ({columns})\n"
        f"SELECT {', '.join(values)}\n"
        f"WHERE NOT EXISTS (SELECT 1 FROM {table} m WHERE m.code = {_lit(node.code)});"
    )


def _split_permission_sql(dialect: str) -> str:
    """把两处复用权限码的既有授权复制到新拆出的独立码。"""
    table = "ai_agent_resource_permissions"
    if dialect == "pg":
        not_distinct = "IS NOT DISTINCT FROM"
        literals = {"enabled_true": "TRUE"}
    else:
        not_distinct = "<=>"
        literals = {"enabled_true": "1"}

    blocks = []
    for new_code, old_code in SPLIT_PERMISSION_PAIRS:
        blocks.append(
            f"""-- {new_code}：从 {old_code} 复制既有授权，升级前后可见性一致
INSERT INTO {table}
    (user_id, role_id, resource_type, resource_id, enabled, created_at, updated_at)
SELECT DISTINCT src.user_id, src.role_id, 'menu', {_lit(new_code)}, {literals['enabled_true']}, {_now(dialect)}, {_now(dialect)}
FROM {table} src
WHERE src.resource_type = 'menu'
  AND src.resource_id = {_lit(old_code)}
  AND src.enabled = {literals['enabled_true']}
  AND (src.user_id IS NOT NULL OR src.role_id IS NOT NULL)
  AND NOT EXISTS (
      SELECT 1 FROM {table} dst
      WHERE dst.resource_type = 'menu'
        AND dst.resource_id = {_lit(new_code)}
        AND dst.user_id {not_distinct} src.user_id
        AND dst.role_id {not_distinct} src.role_id
  );"""
        )
    return "\n\n".join(blocks)


def _body(dialect: str) -> str:
    parts: List[str] = []
    parts.append(f"-- ========== 菜单与权限节点 seed（{len(BUILTIN_MENU_SEED)} 条）==========")
    parts.append(
        "-- 顺序即同级排序；分组先于其子项，页面先于其功能点，因此逐条插入不需要额外的拓扑排序。"
    )
    parts.append("")
    for node in BUILTIN_MENU_SEED:
        parts.append(_insert_sql(node, dialect))
        parts.append("")
    parts.append("-- ========== 权限码拆分：复制既有授权 ==========")
    parts.append("")
    parts.append(_split_permission_sql(dialect))
    parts.append("")
    parts.append("-- `menu:system:menus` / `element:menu:edit` 刻意不自动授予任何非 admin 角色：")
    parts.append("-- 菜单管理是新增能力，是否需要授予由管理员在角色管理里决定。")
    return "\n".join(parts)


def build_mysql_sql() -> str:
    return _ddl_mysql() + "\n" + _body("mysql") + "\n"


def build_pg_sql() -> str:
    return _ddl_pg() + "\n" + _body("pg") + "\n"


def _is_current(path: Path, content: str) -> bool:
    return path.exists() and path.read_text(encoding="utf-8") == content


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="只比对，不写文件")
    args = parser.parse_args(argv)

    targets = (
        (MYSQL_PATH, build_mysql_sql()),
        (PG_PATH, build_pg_sql()),
    )

    if args.check:
        stale = [path for path, content in targets if not _is_current(path, content)]
        if stale:
            for path in stale:
                print(f"过期：{path.relative_to(ROOT)}（请重新运行 scripts/gen_menu_seed_sql.py）")
            return 1
        print("两套迁移 SQL 与 menu_seed.py 一致")
        return 0

    changed = [path for path, content in targets if not _is_current(path, content)]
    for path, content in targets:
        if _is_current(path, content):
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        print(f"已更新：{path.relative_to(ROOT)}")
    if not changed:
        print("两套迁移 SQL 已是最新")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
