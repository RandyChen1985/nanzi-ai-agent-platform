-- V163: 用户资料增加邮箱字段
-- 用途：注册/管理员增改/个人中心自助编辑，并作为审核结果通知的投递地址
-- 说明：MySQL 8.0 不支持 ADD COLUMN IF NOT EXISTS（那是 MariaDB 扩展）；重复执行时
--       apply_sql.py 会吞掉 ERROR 1060(列已存在)/1061(索引已存在)，因此本文件可安全重跑。
--       email 允许 NULL：唯一索引在多 NULL 下不冲突，所以未填写邮箱的用户可以很多。

ALTER TABLE `ai_agent_users`
  ADD COLUMN `email` VARCHAR(254) NULL COMMENT '邮箱（小写归一化存储，全局唯一；NULL=未填写）' AFTER `real_name`;

CREATE UNIQUE INDEX `uk_ai_agent_users_email` ON `ai_agent_users` (`email`);
