-- V175: 修复「菜单 id 被存成 element 类型」的历史脏数据
--
-- 背景：旧版前端权限树把「勾菜单」实现为级联全选子项，级联时把子菜单 id（menu:*）
-- 一并推进了 elements，保存后库里出现 resource_type='element'、resource_id='menu:system:config'
-- 这类行。表现是：用户管理里该页面显示「已勾选」（前端按 id 判授予），但侧栏菜单不出现、
-- 后端 require_permission("menu", ...) 返回 403——因为侧栏与后端都只查 menus。
--
-- 新版前端已把「勾菜单只授予入口」与「全选 / 清空」拆开，写入按 id 前缀分流；
-- 本迁移清理存量。后端读取层（_aggregate_permissions）也做了同样的前缀归位兜底，
-- 所以迁移执行前后表现一致，不必等迁移完成才能用。
--
-- 1) 同一 user/role 已有正确 menu 行时，先删掉重复的 element 行，避免双份授权。
--    只删「enabled 状态一致」的重复行：若一条启用、一条禁用，删掉启用那条会改变
--    有效授权（get_user_permissions 只取 enabled=1）。这种行保留下来、由第 2 步
--    改成 menu，聚合时按 id 去重 + enabled 过滤，有效集合与执行前完全一致。
DELETE dup FROM ai_agent_resource_permissions dup
JOIN ai_agent_resource_permissions keep
  ON keep.resource_type = 'menu'
 AND keep.resource_id = dup.resource_id
 AND keep.user_id <=> dup.user_id
 AND keep.role_id <=> dup.role_id
 AND keep.enabled = dup.enabled
WHERE dup.resource_type = 'element'
  AND dup.resource_id LIKE 'menu:%';

-- 2) 其余 element 类型的菜单 id 归位为 menu（enabled 原样保留）。
UPDATE ai_agent_resource_permissions
SET resource_type = 'menu',
    updated_at = NOW()
WHERE resource_type = 'element'
  AND resource_id LIKE 'menu:%';
