-- V117: 通知渠道支持「一个用户同一渠道配置多个群」（钉钉 / 飞书 / 企业微信）
--
-- 背景：user_notification_configs 上的 ux_user_notification_channel (user_id, channel_type)
-- 唯一约束是「一个用户每个渠道只能配一个群」的根源，导致所有通知都只能进同一个群。
--
-- 本次改动：
--   1) 去掉该唯一约束，允许同一 (user_id, channel_type) 存在多条记录；
--   2) 新增 name（群名称，用于按名称指定发送目标）与 is_default（该渠道的默认群）；
--   3) 存量记录按渠道类型回填名称，并标记为默认群 —— 保证升级后行为与升级前完全一致：
--      读配置时取「该渠道的默认群」，等价于原来「唯一的那一条」。
--
-- 注意：群 ID 直接复用主键 id，不新增列。id 全表唯一且不复用，
-- 因此「钉钉 13 号群」与「飞书 13 号群」不可能混淆，跨渠道校验也能靠它完成。
--
-- 顺序说明：PostgreSQL 不会因为外键而拒绝删除唯一约束（外键列索引不是强制的），
-- 但仍沿用与 MySQL 版一致的顺序 —— 先建新索引再删旧约束，
-- 让两套迁移的差异只剩语法，排查时不必再分辨「哪边先哪边后」。

-- 幂等性：三条 DDL 都带 IF NOT EXISTS / IF EXISTS，④⑤ 两条 UPDATE 各自幂等，
-- 因此整个文件可以重复执行（与 MySQL 版不同，那边靠 apply_sql.py 的错误码白名单兜底）。

-- ① 先加列（下面建索引要用到 is_default）
ALTER TABLE "user_notification_configs"
    ADD COLUMN IF NOT EXISTS "name" VARCHAR(64) NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS "is_default" BOOLEAN NOT NULL DEFAULT FALSE;

-- ② 建新索引：最左前缀是 user_id，接替旧唯一约束承担外键列索引的职责，
--    同时供「按 (user_id, channel_type) 取默认群」的查询使用
CREATE INDEX IF NOT EXISTS "idx_user_notification_channel_default"
    ON "user_notification_configs" ("user_id", "channel_type", "is_default");

-- ③ 删掉旧唯一约束
ALTER TABLE "user_notification_configs"
    DROP CONSTRAINT IF EXISTS "ux_user_notification_channel";

-- ④ 存量记录回填群名称
UPDATE "user_notification_configs"
SET "name" = CASE "channel_type"
        WHEN 'dingtalk' THEN '默认钉钉群'
        WHEN 'feishu' THEN '默认飞书群'
        WHEN 'wechat_work' THEN '默认企微群'
        WHEN 'email' THEN '默认邮件配置'
        ELSE '默认群'
    END
WHERE "name" = '';

-- ⑤ 补齐默认群：只在「该渠道一个默认群都没有」时，取 id 最小的一条补上。
--
-- 不能写成「把该渠道所有记录的 is_default 都置 TRUE」：升级当时每个渠道确实只有一条，
-- 但多群之后同一渠道会有多条，那样**重跑一次就会造出「一个渠道多个默认群」**，
-- 把用户手动选的默认群覆盖掉——而且它不会报错，属于最难发现的那类副作用。
-- 用 HAVING 挡住已有默认群的情况，重复执行即无匹配行、无任何写入。
UPDATE "user_notification_configs" AS t
SET "is_default" = TRUE
FROM (
    SELECT "user_id", "channel_type", MIN("id") AS "keep_id"
    FROM "user_notification_configs"
    WHERE "channel_type" IN ('dingtalk', 'feishu', 'wechat_work', 'email')
    GROUP BY "user_id", "channel_type"
    HAVING COUNT(*) FILTER (WHERE "is_default") = 0
) AS need
WHERE t."id" = need."keep_id";

COMMENT ON COLUMN "user_notification_configs"."name" IS '群名称（按名称指定发送目标时使用）';
COMMENT ON COLUMN "user_notification_configs"."is_default" IS '是否为该渠道的默认群';
