-- V177 清理指向已删除用户的角色关系（悬挂行）
-- Date: 2026-10-20
-- Purpose:
--   `ai_agent_user_role_relations` 在 MySQL 侧**没有外键约束**（见 V27），删除用户的代码路径
--   一旦漏删关系（或历史上被直接 SQL 删过用户），就会留下 user_id 指向不存在用户的悬挂行。
--
--   这类行的直接后果是管理端自相矛盾：`GET /roles` 的 user_count 按关系行计数，而
--   `GET /roles/{id}/users` 的成员列表要 join `ai_agent_users`，于是角色列表显示「1」、
--   点开成员却显示「0 位成员」（线上实测 role_id=563 / user_id=24554 即为此类）。
--
--   应用侧已改为同口径（两处都 join 用户表），本迁移负责把历史脏数据一并清掉，
--   让两张表的语义重新一致。
--
-- 安全性：只删除「用户已不存在」的关系行；正常成员一律不动。幂等，可重复执行。

DELETE r
FROM ai_agent_user_role_relations r
LEFT JOIN ai_agent_users u ON u.id = r.user_id
WHERE u.id IS NULL;
