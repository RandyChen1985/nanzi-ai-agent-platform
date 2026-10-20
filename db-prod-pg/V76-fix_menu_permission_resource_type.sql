-- V76: 修复「菜单 id 被存成 element 类型」的历史脏数据（PostgreSQL）
--
-- 与 db-prod/V175 同一件事，只换 PG 语法。根因见 MySQL 版注释：旧前端「勾菜单 = 级联
-- 全选子项」把 menu:* 写进了 elements，于是库里有 resource_type='element' 的菜单行，
-- 前端权限树显示已勾选、侧栏却不显示，后端菜单鉴权 403。
--
-- 1) 同一 user/role 已有正确 menu 行时，先删掉重复的 element 行。
--    只删 enabled 状态一致的重复行：一启用一禁用时保留两者，第 2 步改成 menu 后
--    聚合按 id 去重 + enabled 过滤，有效授权与执行前完全一致。
DELETE FROM ai_agent_resource_permissions dup
USING ai_agent_resource_permissions keep
WHERE dup.resource_type = 'element'
  AND dup.resource_id LIKE 'menu:%'
  AND keep.resource_type = 'menu'
  AND keep.resource_id = dup.resource_id
  AND keep.user_id IS NOT DISTINCT FROM dup.user_id
  AND keep.role_id IS NOT DISTINCT FROM dup.role_id
  AND keep.enabled = dup.enabled;

-- 2) 其余 element 类型的菜单 id 归位为 menu（enabled 原样保留）。
UPDATE ai_agent_resource_permissions
SET resource_type = 'menu',
    updated_at = NOW()
WHERE resource_type = 'element'
  AND resource_id LIKE 'menu:%';
