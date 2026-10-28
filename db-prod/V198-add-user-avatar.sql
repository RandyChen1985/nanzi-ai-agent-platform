-- V198: 用户自定义头像（个人中心上传；删除后回落「姓名首字」兜底）
-- 版本号由来：V197 已被「会话在线分享」占用（db-prod/V197-create-conversation-shares.sql），
--             故本特性顺延取 V198。
-- 语义：存站内短路径 /static/uploads/avatars/<user_id>/avatar_<毫秒时间戳>.<ext>，
--       由 app/core/avatar_assets.py 的 save_user_avatar 生成；物理目录是该用户自己的
--       data/uploads/avatars/<user_id>/，与全局 AI 头像、智能体头像三处目录互不混用。
--       NULL = 未设置自定义头像，前端用姓名首字兜底，存量用户无需回填。
-- 幂等：MySQL 8.0 不支持 ADD COLUMN IF NOT EXISTS（那是 MariaDB 扩展）；重复执行时
--       db-prod/apply_sql.py 会吞掉 ERROR 1060（列已存在），因此本文件可安全重跑。

ALTER TABLE `ai_agent_users`
  ADD COLUMN `avatar_url` VARCHAR(500) NULL COMMENT '自定义头像地址（/static/uploads/avatars/<user_id>/...；NULL=用姓名首字兜底）';
