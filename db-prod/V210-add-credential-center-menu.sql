-- 凭据中心：系统管理 → 凭据中心（菜单与功能点）
--
-- 建表部分在 V209；这个迁移只补菜单节点与功能点，方便角色管理里勾选。
-- 后端端点用的是 require_admin，权限码本身不参与准入判定。
--
-- 依赖 V209（凭据中心五张表）已执行。

-- 下面 START/END 之间的内容由 scripts/gen_menu_seed_sql.py 从 menu_seed.py 生成，
-- 不要手改；改了会被契约测试判为与种子不一致。
-- >>> gen_menu_seed_sql:credential-center START（本段由脚本生成，勿手改）>>>
-- ========== 菜单与权限节点 seed（2 条）==========
-- 顺序即同级排序；分组先于其子项，页面先于其功能点，因此逐条插入不需要额外的拓扑排序。

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'group:system'), 'page', 'menu:system:credentials', 'menu:system:credentials', '凭据中心', 'key', '/dashboard/secrets', 'AdminSecretCenter', NULL, NULL, 60, 1, 0, 0, 1, '系统级凭据的创建、授权与停用', NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'menu:system:credentials');

INSERT INTO ai_agent_menus
    (parent_id, node_type, code, permission_code, label, icon, path, active_route_names, url, open_mode, sort_order, enabled, desktop_only, is_landing, builtin, description, created_at, updated_at)
SELECT (SELECT p.id FROM ai_agent_menus p WHERE p.code = 'menu:system:credentials'), 'element', 'element:system:credentials_manage', NULL, '管理系统凭据（授权 / 强制停用）', NULL, NULL, NULL, NULL, NULL, 10, 1, 0, 0, 1, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM ai_agent_menus m WHERE m.code = 'element:system:credentials_manage');
-- <<< gen_menu_seed_sql:credential-center END <<<
