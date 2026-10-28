-- V98: 用户自定义头像（与 db-prod/V198 同结构，PG 方言）
-- 版本号由来：V97 已被「会话在线分享」占用（db-prod-pg/V97-create-conversation-shares.sql），
--             故本特性顺延取 V98。
-- 语义：存站内短路径 /static/uploads/avatars/<user_id>/avatar_<毫秒时间戳>.<ext>，
--       由 app/core/avatar_assets.py 的 save_user_avatar 生成；物理目录是该用户自己的
--       data/uploads/avatars/<user_id>/，与全局 AI 头像、智能体头像三处目录互不混用。
--       NULL = 未设置自定义头像，前端用姓名首字兜底，存量用户无需回填。
-- 幂等：PG 侧 apply_sql.py **不忽略任何错误**，必须靠原生 IF NOT EXISTS 自查，
--       不能照搬 MySQL「依赖执行器吞 1060」那套机制。

ALTER TABLE "ai_agent_users"
    ADD COLUMN IF NOT EXISTS "avatar_url" VARCHAR(500) NULL;

COMMENT ON COLUMN "ai_agent_users"."avatar_url" IS '自定义头像地址（/static/uploads/avatars/<user_id>/...；NULL=用姓名首字兜底）';
