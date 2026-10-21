-- V79 部门（组织树）与用户-部门归属
-- Date: 2026-10-20
-- Purpose:
--   与 MySQL `db-prod/V178-create_departments.sql` 同义：新增部门树与用户-部门多对多表，
--   `users.dept_code` / `org_path` 变为"主部门快照"（由服务层派生）。
--   按用户决策，两张表不建外键约束，悬挂数据由应用层清理并配合巡检 SQL。
--
-- 幂等：全部 IF NOT EXISTS，可重复执行。

CREATE TABLE IF NOT EXISTS ai_agent_departments (
    id          BIGSERIAL    PRIMARY KEY,
    parent_id   BIGINT       NULL,
    code        VARCHAR(64)  NOT NULL,
    name        VARCHAR(100) NOT NULL,
    path        VARCHAR(512) NOT NULL DEFAULT '/',
    sort_order  INTEGER      NOT NULL DEFAULT 0,
    status      SMALLINT     NOT NULL DEFAULT 1,
    description VARCHAR(255) NULL,
    created_at  TIMESTAMP    NULL,
    updated_at  TIMESTAMP    NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_departments_code ON ai_agent_departments (code);
CREATE INDEX IF NOT EXISTS idx_departments_parent ON ai_agent_departments (parent_id);
CREATE INDEX IF NOT EXISTS idx_departments_path ON ai_agent_departments (path);

COMMENT ON TABLE ai_agent_departments IS '部门（组织树）';
COMMENT ON COLUMN ai_agent_departments.path IS '物化路径，形如 /1/7/23/';
COMMENT ON COLUMN ai_agent_departments.status IS '0=停用 1=启用';

CREATE TABLE IF NOT EXISTS ai_agent_user_departments (
    id         BIGSERIAL PRIMARY KEY,
    user_id    BIGINT    NOT NULL,
    dept_id    BIGINT    NOT NULL,
    is_primary SMALLINT  NOT NULL DEFAULT 0,
    created_at TIMESTAMP NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_user_departments_user_dept
    ON ai_agent_user_departments (user_id, dept_id);
CREATE INDEX IF NOT EXISTS idx_user_departments_user ON ai_agent_user_departments (user_id);
CREATE INDEX IF NOT EXISTS idx_user_departments_dept ON ai_agent_user_departments (dept_id);

COMMENT ON TABLE ai_agent_user_departments IS '用户-部门归属（多对多）';
COMMENT ON COLUMN ai_agent_user_departments.is_primary IS '1=主部门（每用户最多一个）';

-- =====================================================================
-- 部门管理菜单与权限点（前置依赖：V77-create_agent_menus.sql（本文件末尾的菜单 seed 需要 "ai_agent_menus" 表））
-- 建表与本批菜单种子属于同一个功能，因此写在同一份迁移里（与 V176/V77 同构）。
-- 下面 START/END 之间的内容由 scripts/gen_menu_seed_sql.py 从 menu_seed.py 生成，
-- 请勿手改；要调整菜单请改 menu_seed.py 后重新运行脚本。
-- =====================================================================
-- >>> gen_menu_seed_sql:department-menu START（本段由脚本生成，勿手改）>>>
-- ========== 菜单与权限节点 seed（3 条）==========
-- 顺序即同级排序；分组先于其子项，页面先于其功能点，因此逐条插入不需要额外的拓扑排序。

INSERT INTO "ai_agent_menus"
    ("parent_id", "node_type", "code", "permission_code", "label", "icon", "path", "active_route_names", "url", "open_mode", "sort_order", "enabled", "desktop_only", "is_landing", "builtin", "description", "created_at", "updated_at")
SELECT (SELECT p.id FROM "ai_agent_menus" p WHERE p.code = 'group:system'), 'page', 'menu:system:departments', 'menu:system:departments', '部门管理', 'org', '/dashboard/departments', 'DepartmentManagement', NULL, NULL, 30, TRUE, FALSE, FALSE, TRUE, '组织架构（树形部门）与部门成员维护', NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM "ai_agent_menus" m WHERE m.code = 'menu:system:departments');

INSERT INTO "ai_agent_menus"
    ("parent_id", "node_type", "code", "permission_code", "label", "icon", "path", "active_route_names", "url", "open_mode", "sort_order", "enabled", "desktop_only", "is_landing", "builtin", "description", "created_at", "updated_at")
SELECT (SELECT p.id FROM "ai_agent_menus" p WHERE p.code = 'menu:system:departments'), 'element', 'element:dept:edit', NULL, '维护部门结构与部门成员', NULL, NULL, NULL, NULL, NULL, 10, TRUE, FALSE, FALSE, TRUE, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM "ai_agent_menus" m WHERE m.code = 'element:dept:edit');

INSERT INTO "ai_agent_menus"
    ("parent_id", "node_type", "code", "permission_code", "label", "icon", "path", "active_route_names", "url", "open_mode", "sort_order", "enabled", "desktop_only", "is_landing", "builtin", "description", "created_at", "updated_at")
SELECT (SELECT p.id FROM "ai_agent_menus" p WHERE p.code = 'menu:system:departments'), 'element', 'element:dept:delete', NULL, '删除部门', NULL, NULL, NULL, NULL, NULL, 20, TRUE, FALSE, FALSE, TRUE, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM "ai_agent_menus" m WHERE m.code = 'element:dept:delete');


-- ========== 给新节点腾位置：顺推同级排序（已被手工调整过则跳过）==========
UPDATE "ai_agent_menus" SET "sort_order" = 40, "updated_at" = NOW() WHERE "code" = 'menu:system:config' AND "sort_order" = 30;
UPDATE "ai_agent_menus" SET "sort_order" = 50, "updated_at" = NOW() WHERE "code" = 'menu:system:menus' AND "sort_order" = 40;
-- <<< gen_menu_seed_sql:department-menu END <<<
