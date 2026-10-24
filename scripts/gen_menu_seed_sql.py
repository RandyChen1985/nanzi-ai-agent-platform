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
from dataclasses import replace
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.menu_seed import (  # noqa: E402
    BUILTIN_MENU_SEED,
    BuiltinMenuNode,
)

# 基线迁移：建表 + 首批内置节点。**已上线，冻结**——不再重新生成，也不许手工改；
# 后续新增的节点一律追加一个增量迁移，避免每次加菜单都把几百行旧节点重抄一遍。
BASELINE_TARGETS = (
    ("mysql", ROOT / "db-prod" / "V176-create_agent_menus.sql"),
    ("pg", ROOT / "db-prod-pg" / "V77-create_agent_menus.sql"),
)

# 增量迁移：每批新增节点登记一项。校验时会检查
#   1) 登记里的权限码都真实存在（防手滑写错 code）；
#   2) 每个增量文件的内容 == 用登记的节点重新生成的结果（防 SQL 与 seed 漂移）；
#   3) 基线文件 == 用「全部节点 - 已登记增量」重新生成的结果 —— 于是**任何未登记的新节点**
#      都会让基线校验失败并提示「请登记到 INCREMENTAL_MIGRATIONS」，不会悄悄漏进基线。
INCREMENTAL_MIGRATIONS = (
    {
        "key": "department-menu",
        "title": "部门管理菜单",
        "reason": "新增「部门管理」页面节点与两个功能点（维护部门结构 / 删除部门）。",
        "spec": "docs/superpowers/specs/2026-10-20-department-management-design.md",
        # 一个功能一个迁移：建表与它的菜单种子写在同一个文件里（与 V176/V77 一致）。
        # 种子部分被 START/END 标记包围，脚本只改标记块，不碰手写的建表 DDL。
        "targets": {
            "mysql": ROOT / "db-prod" / "V178-create_departments.sql",
            "pg": ROOT / "db-prod-pg" / "V79-create_departments.sql",
        },
        "codes": (
            "menu:system:departments",
            "element:dept:edit",
            "element:dept:delete",
        ),
        # 部门管理插在「角色管理」之后，同级排序按出现顺序自动编号，
        # 于是「系统配置」「菜单管理」被顺推 10。基线（V176）里它们仍是旧值，
        # 由本批 UPDATE 改成新值——不这么写，库里的排序就和代码永久分叉。
        "resort_before": {
            "menu:system:config": 30,
            "menu:system:menus": 40,
        },
    },
    {
        "key": "agent-system-publish",
        "title": "系统级发布审核权限点",
        "reason": "新增「发布审核与系统级上下架」功能点（element:agent:system_publish），"
        "覆盖发布为系统级、下架、审核发布申请三个动作。",
        "spec": "docs/superpowers/specs/2026-10-23-agent-system-publication-design.md",
        # 建表 DDL 与它的权限种子属同一个功能，写在同一个迁移文件里（与 V178/V79 一致）
        "targets": {
            "mysql": ROOT / "db-prod" / "V185-create_agent_system_publication.sql",
            "pg": ROOT / "db-prod-pg" / "V85-create_agent_system_publication.sql",
        },
        "codes": ("element:agent:system_publish",),
    },
)

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


def _split_nodes():
    """按登记表把种子切成「基线节点」与「各增量节点」。

    登记里出现种子里没有的权限码属于笔误，直接失败：否则那个增量迁移会生成空文件，
    而真正的新节点会被当成基线节点、在基线校验里报「过期」，排查起来很绕。
    """
    all_nodes = list(BUILTIN_MENU_SEED)
    all_codes = {node.code for node in all_nodes}
    registered: set = set()
    for item in INCREMENTAL_MIGRATIONS:
        registered.update(item["codes"])
    unknown = registered - all_codes
    if unknown:
        raise SystemExit(f"增量迁移登记了种子里不存在的权限码：{sorted(unknown)}")

    # 基线生成时把「后续迁移要改排序」的节点还原成旧值，否则基线永远与 V176 对不上
    restore = {}
    for item in INCREMENTAL_MIGRATIONS:
        for code, old_sort in (item.get("resort_before") or {}).items():
            restore.setdefault(code, old_sort)

    baseline = [
        replace(node, sort_order=restore[node.code]) if node.code in restore else node
        for node in all_nodes
        if node.code not in registered
    ]
    increments = {
        item["title"]: [node for node in all_nodes if node.code in set(item["codes"])]
        for item in INCREMENTAL_MIGRATIONS
    }
    return baseline, increments


def _body(dialect: str, nodes: List[BuiltinMenuNode], *, with_permission_split: bool) -> str:
    parts: List[str] = []
    parts.append(f"-- ========== 菜单与权限节点 seed（{len(nodes)} 条）==========")
    parts.append(
        "-- 顺序即同级排序；分组先于其子项，页面先于其功能点，因此逐条插入不需要额外的拓扑排序。"
    )
    parts.append("")
    for node in nodes:
        parts.append(_insert_sql(node, dialect))
        parts.append("")
    if with_permission_split:
        parts.append("-- ========== 权限码拆分：复制既有授权 ==========")
        parts.append("")
        parts.append(_split_permission_sql(dialect))
        parts.append("")
        parts.append("-- `menu:system:menus` / `element:menu:edit` 刻意不自动授予任何非 admin 角色：")
        parts.append("-- 菜单管理是新增能力，是否需要授予由管理员在角色管理里决定。")
    return "\n".join(parts)


def _increment_header(item: dict, dialect: str) -> str:
    version = item["pg_version"] if dialect == "pg" else item["version"]
    label = "PostgreSQL" if dialect == "pg" else "MySQL"
    return f"""-- {version}: {item["title"]}（{label} 方言）
-- 变更原因：{item["reason"]}
-- 需求背景：{item["spec"]}
-- 注意事项：本脚本由 scripts/gen_menu_seed_sql.py 从 app/services/menu_seed.py 生成，
--           请勿手工编辑；如需调整菜单请改 menu_seed.py 后重新生成。
--           **只插入本批新增的节点**，表结构与历史节点已由 V176/V77 建立。
--           本脚本仅由 Agent 生成，不得自动执行，请由用户手工导入更新到数据库。
--           apply_sql.py 无 schema_migrations 记录表，幂等必须写在 SQL 自身。
--           幂等写法：INSERT ... SELECT ... WHERE NOT EXISTS（按 code 判重），
--           重复执行只会补齐缺失节点，不会改动已存在的行。
-- =====================================================================
"""


def build_baseline_sql(dialect: str) -> str:
    baseline, _ = _split_nodes()
    ddl = _ddl_pg() if dialect == "pg" else _ddl_mysql()
    return ddl + "\n" + _body(dialect, baseline, with_permission_split=True) + "\n"


def _resort_sql(item: dict, dialect: str) -> str:
    """把给新节点腾位置的旧节点排序往后挪。

    `AND sort_order = <旧值>` 是刻意的：管理员如果手工调过这几项的排序，就不该被迁移覆盖。
    """
    resort = item.get("resort_before") or {}
    if not resort:
        return ""
    table = _quote(TABLE, dialect) if dialect == "pg" else TABLE
    by_code = {node.code: node for node in BUILTIN_MENU_SEED}
    lines = ["-- ========== 给新节点腾位置：顺推同级排序（已被手工调整过则跳过）=========="]
    for code, old_sort in resort.items():
        new_sort = by_code[code].sort_order
        if new_sort == old_sort:
            continue
        lines.append(
            f"UPDATE {table} SET {_quote('sort_order', dialect)} = {new_sort}, "
            f"{_quote('updated_at', dialect)} = {_now(dialect)} "
            f"WHERE {_quote('code', dialect)} = {_lit(code)} "
            f"AND {_quote('sort_order', dialect)} = {old_sort};"
        )
    return "\n".join(lines)


def marker_start(key: str) -> str:
    return f"-- >>> gen_menu_seed_sql:{key} START（本段由脚本生成，勿手改）>>>"


def marker_end(key: str) -> str:
    return f"-- <<< gen_menu_seed_sql:{key} END <<<"


def build_increment_block(item: dict, dialect: str) -> str:
    """标记块**内部**内容：本批新增节点 +（可选）顺推排序。

    不含文件头注释与建表 DDL——它们由人在同一个文件里维护，
    脚本只负责这一段的正确性。
    """
    _, increments = _split_nodes()
    nodes = increments[item["title"]]
    parts = [_body(dialect, nodes, with_permission_split=False)]
    resort = _resort_sql(item, dialect)
    if resort:
        parts.extend(["", resort])
    return "\n".join(parts)


def extract_block(text: str, key: str):
    """取出标记块内部内容；标记缺失或不配对返回 None（区别于「块为空」）。"""
    start, end = marker_start(key), marker_end(key)
    i, j = text.find(start), text.find(end)
    if i < 0 or j < 0 or j < i:
        return None
    return text[i + len(start) : j]


def splice_block(text: str, key: str, body: str) -> str:
    """只替换标记块内部，标记行与块外内容原样保留。"""
    start, end = marker_start(key), marker_end(key)
    i, j = text.find(start), text.find(end)
    if i < 0 or j < 0 or j < i:
        raise ValueError(f"缺少标记块 {key}")
    return text[: i + len(start)] + "\n" + body.rstrip("\n") + "\n" + text[j:]


def _is_current(path: Path, content: str) -> bool:
    return path.exists() and path.read_text(encoding="utf-8") == content


def _baseline_targets() -> list:
    """基线是纯生成文件：整份比对、整份重写（实际从不重写，见 main）。"""
    return [(path, build_baseline_sql(dialect)) for dialect, path in BASELINE_TARGETS]


def _incremental_targets() -> list:
    """增量是标记块：只比对/替换块内部，块外的人工内容不受影响。"""
    targets = []
    for item in INCREMENTAL_MIGRATIONS:
        for dialect in ("mysql", "pg"):
            targets.append((item["targets"][dialect], item["key"], build_increment_block(item, dialect)))
    return targets


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="只比对，不写文件")
    args = parser.parse_args(argv)

    if args.check:
        stale = []
        for path, content in _baseline_targets():
            if not _is_current(path, content):
                stale.append(
                    (
                        path,
                        "基线迁移已冻结：说明有新节点没登记迁移，"
                        "请把它的 code 加进 INCREMENTAL_MIGRATIONS 并重新生成",
                    )
                )
        for path, key, body in _incremental_targets():
            text = path.read_text(encoding="utf-8") if path.exists() else ""
            block = extract_block(text, key)
            if block is None:
                stale.append((path, f"缺少标记块 {key}，请先手工加上 START/END 两行"))
            elif block.strip() != body.strip():
                stale.append((path, "标记块内容与 menu_seed.py 不一致，请重新生成"))
        if stale:
            for path, hint in stale:
                print(f"过期：{path.relative_to(ROOT)}（{hint}）")
            return 1
        print(
            "迁移 SQL 与 menu_seed.py 一致"
            f"（基线 {len(BASELINE_TARGETS)} 份 + 增量 {len(INCREMENTAL_MIGRATIONS)} 批标记块）"
        )
        return 0

    for path, content in _baseline_targets():
        if not _is_current(path, content):
            print(
                f"基线迁移与种子不一致，拒绝覆盖：{path.relative_to(ROOT)}\n"
                "  基线已上线，新增节点请登记到 INCREMENTAL_MIGRATIONS。"
            )
            return 1

    changed = []
    for path, key, body in _incremental_targets():
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        if extract_block(text, key) is None:
            print(
                f"缺少标记块 {key}：{path.relative_to(ROOT)}\n"
                "  请在该文件里手工加上 START/END 两行标记后重新运行。"
            )
            return 1
        updated = splice_block(text, key, body)
        if updated == text:
            continue
        path.write_text(updated, encoding="utf-8")
        changed.append(path)
        print(f"已更新标记块：{path.relative_to(ROOT)}")
    if not changed:
        print("迁移 SQL 已是最新")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
