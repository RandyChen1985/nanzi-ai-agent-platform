-- V217: 通知渠道支持「一个用户同一渠道配置多个群」（钉钉 / 飞书 / 企业微信）
--
-- 背景：user_notification_configs 上的 ux_user_notification_channel (user_id, channel_type)
-- 唯一键是「一个用户每个渠道只能配一个群」的根源，导致所有通知都只能进同一个群。
--
-- 本次改动：
--   1) 去掉该唯一键，允许同一 (user_id, channel_type) 存在多条记录；
--   2) 新增 name（群名称，用于按名称指定发送目标）与 is_default（该渠道的默认群）；
--   3) 存量记录按渠道类型回填名称，并标记为默认群 —— 保证升级后行为与升级前完全一致：
--      读配置时取「该渠道的默认群」，等价于原来「唯一的那一条」。
--
-- 注意：群 ID 直接复用主键 id，不新增列。id 全表唯一且不复用，
-- 因此「钉钉 13 号群」与「飞书 13 号群」不可能混淆，跨渠道校验也能靠它完成。
--
-- ⚠ 执行顺序不能颠倒（MySQL Error 1553）：
--   外键 fk_user_notification_config_user 建在 user_id 上，而 InnoDB 要求外键列必须有索引。
--   历史上这个职责是由 ux_user_notification_channel 的最左前缀 (user_id) 顺带承担的，
--   直接 DROP 会报 "Cannot drop index ... needed in a foreign key constraint"。
--   所以必须先建好同样以 user_id 开头的新索引，再删旧唯一键。

-- 幂等性（两档，别混）：
--   • 通过 db-prod/apply_sql.py 执行：DDL 重复会返回 1060/1061/1091，执行器把它们
--     统一当作「对象已存在/已变更」跳过，因此整个文件可以重复跑。
--   • 用手工客户端逐条执行：不会跳过，会直接报错中断。重跑前先用文件末尾的
--     「执行前状态自查」语句确认到哪一步了。
--   ④⑤ 两条 UPDATE 本身是幂等的（见各自注释），不受执行方式影响。

-- ① 先加列（拆分为单列 DDL 以兼容分片/只允许单语句单列的 MySQL 环境；下面建索引要用到 is_default）
ALTER TABLE user_notification_configs
    ADD COLUMN name VARCHAR(64) NOT NULL DEFAULT '' COMMENT '群名称（按名称指定发送目标时使用）' AFTER channel_type;

ALTER TABLE user_notification_configs
    ADD COLUMN is_default TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否为该渠道的默认群：1=是，0=否' AFTER config_json;

-- ② 建新索引：最左前缀是 user_id，接替旧唯一键支撑外键，
--    同时供「按 (user_id, channel_type) 取默认群」的查询使用
ALTER TABLE user_notification_configs
    ADD INDEX idx_user_notification_channel_default (user_id, channel_type, is_default);

-- ③ 现在才能安全删掉旧唯一键
ALTER TABLE user_notification_configs
    DROP INDEX ux_user_notification_channel;

-- ④ 存量记录回填群名称：按渠道类型给一个能一眼看懂的名字
UPDATE user_notification_configs
SET name = CASE channel_type
        WHEN 'dingtalk' THEN '默认钉钉群'
        WHEN 'feishu' THEN '默认飞书群'
        WHEN 'wechat_work' THEN '默认企微群'
        WHEN 'email' THEN '默认邮件配置'
        ELSE '默认群'
    END
WHERE name = '';

-- ⑤ 补齐默认群：只在「该渠道一个默认群都没有」时，取 id 最小的一条补上。
--
-- 不能写成「把该渠道所有记录的 is_default 都置 1」：升级当时每个渠道确实只有一条，
-- 但多群之后同一渠道会有多条，那样**重跑一次就会造出「一个渠道多个默认群」**，
-- 把用户手动选的默认群覆盖掉——而且它不会报错，属于最难发现的那类副作用。
-- 用 HAVING 挡住已有默认群的情况，重复执行即无匹配行、无任何写入。
UPDATE user_notification_configs AS t
JOIN (
    SELECT user_id, channel_type, MIN(id) AS keep_id
    FROM user_notification_configs
    WHERE channel_type IN ('dingtalk', 'feishu', 'wechat_work', 'email')
    GROUP BY user_id, channel_type
    HAVING SUM(is_default = 1) = 0
) AS need ON t.id = need.keep_id
SET t.is_default = 1;

-- 执行前状态自查（手工执行时用，避免重复跑到已完成的步骤）：
--   SHOW COLUMNS FROM user_notification_configs LIKE 'name';
--   SHOW INDEX FROM user_notification_configs
--     WHERE Key_name IN ('ux_user_notification_channel', 'idx_user_notification_channel_default');
--   查不到 name 列 → 从 ① 开始；name 已存在 → 从 ② 开始；两个索引都在 → 从 ③ 开始。
