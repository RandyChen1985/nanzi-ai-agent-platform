-- V211: 凭据变更审计表（MySQL 方言）
--
-- ⚠️ 本脚本不指定库名，在哪个库执行就建在哪个库。执行前先 `SELECT DATABASE();`
--    确认是应用配置里 MYSQL_DB 的那个库。
--
-- 背景：现有 sys_secret_usage_logs 记的是「谁用了凭据」，不是「谁改了配置」。
-- 出事时最常问的两个问题——「这条 token 谁轮换过」「这个绑定谁加的」——现在
-- 一条都答不上。本表只做这一件事。
--
-- 两条设计约束：
--   1. **不设外键**：删凭据不删审计，否则事后最该看的东西跟着没了；
--      secret_key_snapshot 让记录在凭据消失后仍可读。
--   2. **只增不改**：没有任何 UPDATE / DELETE 路径，代码里也不提供。
--      actor_name 是快照，不 join 用户表——用户改名后审计要保持当时的可读性。
--
-- 代码可以先于本迁移上线：写审计失败只记日志，不阻断主流程。

CREATE TABLE IF NOT EXISTS `sys_secret_audit_logs` (
  `id` VARCHAR(36) NOT NULL,
  `secret_id` VARCHAR(36) NULL COMMENT '凭据删除后置空也不影响，靠 snapshot 溯源',
  `secret_key_snapshot` VARCHAR(128) NULL COMMENT '操作当时的 secret_key',
  `owner_scope` VARCHAR(16) NULL COMMENT 'user / system',
  `actor_id` VARCHAR(64) NULL COMMENT '操作者 user_id；系统自动动作为空',
  `actor_name` VARCHAR(128) NULL COMMENT '操作者当时的显示名快照',
  `action` VARCHAR(24) NOT NULL
    COMMENT 'create / update / rotate / delete / disable / enable / grant / revoke / bind / unbind',
  `detail` TEXT NULL COMMENT '补充信息，JSON 字符串',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  -- 主查询：某条凭据的完整历史，倒序翻页
  KEY `ix_secret_audit_secret_created` (`secret_id`, `created_at`),
  -- 次查询：某人最近动了什么
  KEY `ix_secret_audit_actor_created` (`actor_id`, `created_at`),
  KEY `ix_secret_audit_action` (`action`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='凭据变更审计：谁在什么时候对哪条凭据做了什么';
