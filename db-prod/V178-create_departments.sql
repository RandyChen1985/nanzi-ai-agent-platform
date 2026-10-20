-- V178 部门（组织树）与用户-部门归属
-- Date: 2026-10-20
-- Purpose:
--   新增树形部门管理能力（设计见 docs/superpowers/specs/2026-10-20-department-management-design.md）。
--   - ai_agent_departments：部门树，父指针 + 物化路径（/1/7/23/），同级 sort_order 排序；
--   - ai_agent_user_departments：用户与部门多对多，is_primary 标记主部门。
--
--   用户的 dept_code / org_path 仍保留在 ai_agent_users 上，但语义变为"主部门快照"
--   （由服务层从 is_primary 的那条关系派生），因此 SQL 行级过滤、ChatBI 上下文、
--   报表分享等既有单值消费方无需改动。
--
--   按用户决策，两张表**不建外键约束**，与 ai_agent_user_role_relations 的既有风格一致；
--   悬挂数据由应用层删除路径显式清理，并提供巡检 SQL 兜底。
--
-- 幂等：全部 IF NOT EXISTS，可重复执行。

CREATE TABLE IF NOT EXISTS ai_agent_departments (
    id          BIGINT       NOT NULL AUTO_INCREMENT,
    parent_id   BIGINT       NULL COMMENT '上级部门 ID，根部门为 NULL',
    code        VARCHAR(64)  NOT NULL COMMENT '业务键，与 users.dept_code 对齐',
    name        VARCHAR(100) NOT NULL COMMENT '部门名称',
    path        VARCHAR(512) NOT NULL DEFAULT '/' COMMENT '物化路径，形如 /1/7/23/',
    sort_order  INT          NOT NULL DEFAULT 0 COMMENT '同级排序',
    status      TINYINT      NOT NULL DEFAULT 1 COMMENT '0=停用 1=启用',
    description VARCHAR(255) NULL COMMENT '描述',
    created_at  DATETIME     NULL,
    updated_at  DATETIME     NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_departments_code (code),
    KEY idx_departments_parent (parent_id),
    KEY idx_departments_path (path)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='部门（组织树）';

CREATE TABLE IF NOT EXISTS ai_agent_user_departments (
    id         BIGINT   NOT NULL AUTO_INCREMENT,
    user_id    BIGINT   NOT NULL COMMENT '用户 ID',
    dept_id    BIGINT   NOT NULL COMMENT '部门 ID',
    is_primary TINYINT  NOT NULL DEFAULT 0 COMMENT '1=主部门（每用户最多一个）',
    created_at DATETIME NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_user_departments_user_dept (user_id, dept_id),
    KEY idx_user_departments_user (user_id),
    KEY idx_user_departments_dept (dept_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户-部门归属（多对多）';

-- =====================================================================
-- 部门管理菜单与权限点（前置依赖：V176-create_agent_menus.sql（本文件末尾的菜单 seed 需要 ai_agent_menus 表））
-- 建表与本批菜单种子属于同一个功能，因此写在同一份迁移里（与 V176/V77 同构）。
-- 下面 START/END 之间的内容由 scripts/gen_menu_seed_sql.py 从 menu_seed.py 生成，
-- 请勿手改；要调整菜单请改 menu_seed.py 后重新运行脚本。
-- =====================================================================
-- >>> gen_menu_seed_sql:department-menu START（本段由脚本生成，勿手改）>>>
-- ========== 菜单与权限节点 seed（3 条）==========
-- 顺序即同级排序；分组先于其子项，页面先于其功能点，因此逐条插入不需要额外的拓扑排序。

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'group:system'), 'page', 'menu:system:departments', 'menu:system:departments', '部门管理', 'org', '/dashboard/departments', 'DepartmentManagement', NULL, NULL, 30, 1, 0, 0, 1, '组织架构（树形部门）与部门成员维护', NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'menu:system:departments');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:system:departments'), 'element', 'element:dept:edit', NULL, '维护部门结构与部门成员', NULL, NULL, NULL, NULL, NULL, 10, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:dept:edit');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:system:departments'), 'element', 'element:dept:delete', NULL, '删除部门', NULL, NULL, NULL, NULL, NULL, 20, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:dept:delete');


-- ========== 给新节点腾位置：顺推同级排序（已被手工调整过则跳过）==========
UPDATE ai_agent_menus SET sort_order = 40, updated_at = NOW() WHERE code = 'menu:system:config' AND sort_order = 30;
UPDATE ai_agent_menus SET sort_order = 50, updated_at = NOW() WHERE code = 'menu:system:menus' AND sort_order = 40;
-- <<< gen_menu_seed_sql:department-menu END <<<
