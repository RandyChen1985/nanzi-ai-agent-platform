-- V109: 用户级凭据中心四张表（PostgreSQL 方言）
--
-- ⚠️ 本脚本不指定 schema（没有 SET search_path），在哪个 schema 执行就建在哪个 schema。
--    执行前先确认 `SELECT current_schema();` 与连接串里的一致，否则会出现
--    「表建好了但接口报 relation \"sys_secrets\" does not exist」。

--
-- 与 MySQL db-prod/V209-add-credential-center.sql 保持同一口径，字段语义、
-- 约束、默认值一一对应。差异只在方言层：
--   * JSON          → JSONB（可索引、去重键）
--   * DATETIME(6)   → TIMESTAMP(6) / TIMESTAMPTZ
--   * 空串默认      → 同样用 '' 而非 NULL，理由见 MySQL 版本注释
--   * 内联 COMMENT  → COMMENT ON COLUMN
--
-- 本次只新增表，不改动任何既有表。

-- ── 凭据本体 ───────────────────────────────────────────────────────────
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
