-- =====================================================================
-- 凭据中心：一次建完（PostgreSQL 方言）
-- 创建时间：2026-11-09
-- 注意事项：该脚本仅做生成，不得由 Agent 自动执行，由用户手动导入更新到数据库。
-- =====================================================================
--
-- 与 db-prod/V209 同内容、同顺序，只是方言不同。两套必须一起改，
-- 只改一套的话另一种数据库的用户升级后会遇到「接口报不存在」。
--
-- 整份脚本完全幂等，重复执行无副作用：
--   * CREATE TABLE IF NOT EXISTS
--   * 菜单用 INSERT ... WHERE NOT EXISTS
--   * ADD COLUMN IF NOT EXISTS
--   * UPDATE ... WHERE IS NULL 与 ALTER COLUMN SET NOT NULL 本身幂等
--
-- PostgreSQL 的唯一索引同样把 NULL 视为互不相等，所以第五节的空串改造
-- 在两种数据库上都是必须的。
--
-- 不含本主题的迁移仍独立存在（V110 产物父会话），不要并进来。


-- =======================================================================
-- 一、凭据中心四张表 + 注入策略默认值
-- =======================================================================

CREATE TABLE IF NOT EXISTS "sys_secrets" (
  "id" VARCHAR(36) NOT NULL,
  "owner_scope" VARCHAR(16) NOT NULL DEFAULT 'user',
  "owner_id" VARCHAR(64) NOT NULL DEFAULT '',
  "secret_key" VARCHAR(128) NOT NULL,
  "name" VARCHAR(128) NOT NULL,
  "secret_type" VARCHAR(16) NOT NULL DEFAULT 'credential',
  "value_encrypted" TEXT NULL,
  "value_fingerprint" CHAR(16) NULL,
  "value_preview" VARCHAR(24) NULL,
  "allowed_hosts" JSONB NULL,
  "allowed_scenarios" JSONB NULL,
  "status" VARCHAR(16) NOT NULL DEFAULT 'active',
  "expires_at" TIMESTAMP(6) NULL,
  "previous_value_encrypted" TEXT NULL,
  "previous_expires_at" TIMESTAMP(6) NULL,
  "category" VARCHAR(64) NULL,
  "remark" VARCHAR(500) NULL,
  "created_by" VARCHAR(64) NULL,
  "created_at" TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updated_at" TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "last_rotated_at" TIMESTAMP(6) NULL,
  PRIMARY KEY ("id"),
  CONSTRAINT "uk_secret_owner_key" UNIQUE ("owner_scope", "owner_id", "secret_key")
);

COMMENT ON TABLE "sys_secrets" IS '凭据中心：值永不可读，只在调用时由平台注入';
COMMENT ON COLUMN "sys_secrets"."owner_scope" IS 'user / system（dept 预留，V1 不开放）';
COMMENT ON COLUMN "sys_secrets"."owner_id" IS 'user 时=user_id；system 时=空串（不用 NULL，避免唯一约束失效）';
COMMENT ON COLUMN "sys_secrets"."secret_key" IS '同一 owner 内唯一的引用标识，如 jira.prod.token';
COMMENT ON COLUMN "sys_secrets"."secret_type" IS 'credential=永不可读 / variable=可读可复制';
COMMENT ON COLUMN "sys_secrets"."value_encrypted" IS 'Fernet 密文；两类都加密存';
COMMENT ON COLUMN "sys_secrets"."value_fingerprint" IS 'sha256 前 16 位，供落库扫描比对；不可反推明文';
COMMENT ON COLUMN "sys_secrets"."value_preview" IS '仅 variable 存前 8 字符；credential 必须为 NULL';
COMMENT ON COLUMN "sys_secrets"."allowed_hosts" IS '域名白名单数组；NULL 或 [] = 只允许平台内部';
COMMENT ON COLUMN "sys_secrets"."allowed_scenarios" IS '场景子集；NULL = 全不勾（fail-closed）';
COMMENT ON COLUMN "sys_secrets"."previous_value_encrypted" IS '轮换过渡期的旧值，双写 7 天后清空';

CREATE INDEX IF NOT EXISTS "idx_secret_owner"
  ON "sys_secrets" ("owner_scope", "owner_id");
CREATE INDEX IF NOT EXISTS "idx_secret_fingerprint"
  ON "sys_secrets" ("value_fingerprint");
CREATE INDEX IF NOT EXISTS "idx_secret_status_expire"
  ON "sys_secrets" ("status", "expires_at");

-- ── 系统级授权给谁 ─────────────────────────────────────────────────────
-- 系统级凭据不是全平台可用：未出现在有效 grant 里的用户，连名称都不返回。
CREATE TABLE IF NOT EXISTS "sys_secret_grants" (
  "id" VARCHAR(36) NOT NULL,
  "secret_id" VARCHAR(36) NOT NULL,
  "grantee_type" VARCHAR(16) NOT NULL,
  "grantee_id" VARCHAR(64) NOT NULL,
  "granted_by" VARCHAR(64) NULL,
  "granted_at" TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "expires_at" TIMESTAMP(6) NULL,
  PRIMARY KEY ("id"),
  CONSTRAINT "uk_grant_target" UNIQUE ("secret_id", "grantee_type", "grantee_id")
  -- 不设 secret_id 外键：这几张表要能单独导出再导入，外键会强制父表先存在，
  -- 导入顺序一错就整批失败。级联清理由 center.delete_secret 显式做。
);

COMMENT ON TABLE "sys_secret_grants" IS '系统级凭据的可见范围';
COMMENT ON COLUMN "sys_secret_grants"."grantee_type" IS 'user / dept（V1 只这两种）';

CREATE INDEX IF NOT EXISTS "idx_grant_grantee"
  ON "sys_secret_grants" ("grantee_type", "grantee_id");

-- ── 绑定到消费者 ───────────────────────────────────────────────────────
-- bound_by_user_id：NULL 表示管理员做的全局绑定；否则只对绑定者生效。
-- 漏了这一列，A 绑到共享工具上的系统凭据就会顺带被 B 用到。
CREATE TABLE IF NOT EXISTS "sys_secret_bindings" (
  "id" VARCHAR(36) NOT NULL,
  "secret_id" VARCHAR(36) NOT NULL,
  "consumer_type" VARCHAR(24) NOT NULL,
  "consumer_id" VARCHAR(36) NOT NULL,
  "slot" VARCHAR(128) NOT NULL,
  "bound_by_user_id" VARCHAR(64) NULL,
  "created_at" TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY ("id"),
  CONSTRAINT "uk_binding_slot" UNIQUE ("consumer_type", "consumer_id", "slot", "bound_by_user_id")
);

COMMENT ON TABLE "sys_secret_bindings" IS '凭据能被谁取到：白名单，默认零授权';
COMMENT ON COLUMN "sys_secret_bindings"."slot" IS 'header:X-Api-Key / query:token / body:password / env:DB_PASSWORD';
COMMENT ON COLUMN "sys_secret_bindings"."bound_by_user_id" IS 'NULL=管理员全局绑定';

CREATE INDEX IF NOT EXISTS "idx_binding_secret"
  ON "sys_secret_bindings" ("secret_id");

-- ── 使用审计 ───────────────────────────────────────────────────────────
-- 刻意不设 secret_id 外键：删凭据不能连带删使用记录。
CREATE TABLE IF NOT EXISTS "sys_secret_usage_logs" (
  "id" VARCHAR(36) NOT NULL,
  "secret_id" VARCHAR(36) NULL,
  "secret_key_snapshot" VARCHAR(128) NULL,
  "owner_scope" VARCHAR(16) NULL,
  "trace_id" VARCHAR(64) NULL,
  "conversation_id" VARCHAR(64) NULL,
  "user_id" VARCHAR(64) NULL,
  "scene" VARCHAR(24) NOT NULL,
  "consumer_type" VARCHAR(24) NULL,
  "consumer_id" VARCHAR(36) NULL,
  "slot" VARCHAR(128) NULL,
  "target_host" VARCHAR(255) NULL,
  "result" VARCHAR(32) NOT NULL DEFAULT 'success',
  "created_at" TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY ("id")
);

COMMENT ON TABLE "sys_secret_usage_logs" IS '凭据使用流水；记录不可删除，保留溯源';
COMMENT ON COLUMN "sys_secret_usage_logs"."result" IS 'success / denied_by_binding / denied_by_grant / denied_by_host / denied_by_scenario / expired / disabled';

CREATE INDEX IF NOT EXISTS "idx_usage_secret_time"
  ON "sys_secret_usage_logs" ("secret_id", "created_at");
CREATE INDEX IF NOT EXISTS "idx_usage_user_time"
  ON "sys_secret_usage_logs" ("user_id", "created_at");
CREATE INDEX IF NOT EXISTS "idx_usage_trace"
  ON "sys_secret_usage_logs" ("trace_id");

-- ── 用户级注入偏好（三层开关的第 ② 层）─────────────────────────────────
CREATE TABLE IF NOT EXISTS "sys_secret_user_prefs" (
  "user_id" VARCHAR(64) NOT NULL,
  "allow_script_env" BOOLEAN NOT NULL DEFAULT FALSE,
  "updated_at" TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY ("user_id")
);

COMMENT ON TABLE "sys_secret_user_prefs" IS '用户级凭据注入偏好；只能收紧，不提供放宽项';
COMMENT ON COLUMN "sys_secret_user_prefs"."allow_script_env" IS '允许脚本读取我的凭据；默认 false';

-- ── 平台策略配置项 ─────────────────────────────────────────────────────
-- 冲突时不覆盖 value，避免升级丢失部署方配置。
INSERT INTO "system_configs" ("key", "value", "description", "category", "is_secret") VALUES
(
    'secret_policy_allow_script_env',
    '0',
    '是否允许脚本工具从环境变量读取凭据（0=禁止，1=允许）。默认禁止：脚本能读 os.environ 就一定能 print 出来，是所有注入场景里风险最高的一个。关闭时是「整类拒绝」，不逐条商量。',
    'security',
    FALSE
),
(
    'secret_policy_allow_external_host',
    '1',
    '是否允许凭据发往外部域名（0=只能注入平台内部接口，1=按凭据自身的域名白名单放行）。权威开关仍是每条凭据的 allowed_hosts，这一项是部署级的兜底闸门。',
    'security',
    FALSE
),
(
    'secret_policy_max_inject_per_call',
    '3',
    '单次调用最多注入几条凭据（整数）。防止一次请求把全部凭据一起带走。',
    'security',
    FALSE
)
ON CONFLICT ("key") DO UPDATE
SET
    "description" = EXCLUDED."description",
    "category" = EXCLUDED."category",
    "is_secret" = EXCLUDED."is_secret",
    "updated_at" = CURRENT_TIMESTAMP;


-- =======================================================================
-- 二、菜单与功能点（由 menu_seed.py 生成，勿手改）
-- =======================================================================

-- >>> gen_menu_seed_sql:credential-center START（本段由脚本生成，勿手改）>>>
-- ========== 菜单与权限节点 seed（2 条）==========
-- 顺序即同级排序；分组先于其子项，页面先于其功能点，因此逐条插入不需要额外的拓扑排序。

INSERT INTO "ai_agent_menus"
    ("parent_id", "node_type", "code", "permission_code", "label", "icon", "path", "active_route_names", "url", "open_mode", "sort_order", "enabled", "desktop_only", "is_landing", "builtin", "description", "created_at", "updated_at")
SELECT (SELECT p.id FROM "ai_agent_menus" p WHERE p.code = 'group:system'), 'page', 'menu:system:credentials', 'menu:system:credentials', '凭据中心', 'key', '/dashboard/secrets', 'AdminSecretCenter', NULL, NULL, 60, TRUE, FALSE, FALSE, TRUE, '系统级凭据的创建、授权与停用', NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM "ai_agent_menus" m WHERE m.code = 'menu:system:credentials');

INSERT INTO "ai_agent_menus"
    ("parent_id", "node_type", "code", "permission_code", "label", "icon", "path", "active_route_names", "url", "open_mode", "sort_order", "enabled", "desktop_only", "is_landing", "builtin", "description", "created_at", "updated_at")
SELECT (SELECT p.id FROM "ai_agent_menus" p WHERE p.code = 'menu:system:credentials'), 'element', 'element:system:credentials_manage', NULL, '管理系统凭据（授权 / 强制停用）', NULL, NULL, NULL, NULL, NULL, 10, TRUE, FALSE, FALSE, TRUE, NULL, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM "ai_agent_menus" m WHERE m.code = 'element:system:credentials_manage');
-- <<< gen_menu_seed_sql:credential-center END <<<


-- =======================================================================
-- 三、凭据变更审计表
-- =======================================================================

CREATE TABLE IF NOT EXISTS sys_secret_audit_logs (
  id VARCHAR(36) PRIMARY KEY,
  secret_id VARCHAR(36) NULL,
  secret_key_snapshot VARCHAR(128) NULL,
  owner_scope VARCHAR(16) NULL,
  actor_id VARCHAR(64) NULL,
  actor_name VARCHAR(128) NULL,
  action VARCHAR(24) NOT NULL,
  detail TEXT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_secret_audit_secret_created
  ON sys_secret_audit_logs (secret_id, created_at);
CREATE INDEX IF NOT EXISTS ix_secret_audit_actor_created
  ON sys_secret_audit_logs (actor_id, created_at);
CREATE INDEX IF NOT EXISTS ix_secret_audit_action
  ON sys_secret_audit_logs (action);

COMMENT ON TABLE sys_secret_audit_logs IS '凭据变更审计：谁在什么时候对哪条凭据做了什么';


-- =======================================================================
-- 四、MCP 工具：声明哪些参数的值由凭据中心提供
-- =======================================================================

ALTER TABLE sys_mcp_tool_cache
    ADD COLUMN IF NOT EXISTS credential_params TEXT NULL;

COMMENT ON COLUMN sys_mcp_tool_cache.credential_params
    IS '值由凭据中心提供的参数名，JSON 数组';


-- =======================================================================
-- 五、绑定唯一性：全局绑定改用空串，让唯一索引真正生效
-- =======================================================================

UPDATE sys_secret_bindings SET bound_by_user_id = '' WHERE bound_by_user_id IS NULL;

ALTER TABLE sys_secret_bindings
    ALTER COLUMN bound_by_user_id SET DEFAULT '',
    ALTER COLUMN bound_by_user_id SET NOT NULL;

COMMENT ON COLUMN sys_secret_bindings.bound_by_user_id IS '空串=管理员全局绑定；有值=只对该用户生效';
