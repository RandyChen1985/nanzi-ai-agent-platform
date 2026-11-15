-- V119: 凭据值支持结构化 JSON（录入形态 + 列表摘要）。（PostgreSQL）
-- 与 MySQL 侧 db-prod/V219-add-secret-value-format.sql 保持同一结构：
--   value_format   text | json（存量行默认 text，无需回填）
--   value_template 模板标识（smtp/sftp/mysql…），仅 json 写入；模板定义在前端常量
--   value_summary  列表摘要，只拼非敏感字段，由后端按前端声明的敏感键名挑值
--
-- 同一功能的第二半在文件末尾：绑定表加 `sys_secret_bindings.value_path`（使用侧
-- 「只取某个字段」）。录入结构化、使用侧选字段是同一条链路上的两半。
--
-- 三列都只是描述，不改变存储契约：明文仍在 value_encrypted 里（结构化时是 JSON 字符串），
-- 加密、掩码、审计、轮换链路完全不动。
--
-- 幂等：PG 执行器不吞任何错误、整文件一个事务，所以语句必须自己幂等 ——
-- `ADD COLUMN IF NOT EXISTS` + 可重复设置的 `COMMENT ON COLUMN`。

ALTER TABLE "sys_secrets"
    ADD COLUMN IF NOT EXISTS "value_format" VARCHAR(16) NOT NULL DEFAULT 'text';

ALTER TABLE "sys_secrets"
    ADD COLUMN IF NOT EXISTS "value_template" VARCHAR(64) NULL;

ALTER TABLE "sys_secrets"
    ADD COLUMN IF NOT EXISTS "value_summary" VARCHAR(160) NULL;

COMMENT ON COLUMN "sys_secrets"."value_format"
    IS '值形态：text=单值（免费文本/PEM）/ json=结构化对象';
COMMENT ON COLUMN "sys_secrets"."value_template"
    IS '结构化模板标识（smtp/sftp/mysql…），仅 value_format=json 时写入；模板定义在前端常量';
COMMENT ON COLUMN "sys_secrets"."value_summary"
    IS '列表摘要：只拼非敏感字段（host/port/用户名等），不含 secret 字段的值';

-- ── 使用侧：绑定只取某个字段 ─────────────────────────────────────────
-- 与 MySQL 侧 db-prod/V219-add-secret-value-format.sql 末段同一语义：
--   value_path  点分隔字段路径（password / auth.token）；空串 = 整段给，
--               是全部存量行的默认，无需回填；只对 fixed 绑定生效。

ALTER TABLE "sys_secret_bindings"
    ADD COLUMN IF NOT EXISTS "value_path" VARCHAR(255) NOT NULL DEFAULT '';

COMMENT ON COLUMN "sys_secret_bindings"."value_path"
    IS '要取用的字段路径（点分隔，如 password / auth.token）；空串=整段。仅 fixed 绑定生效';
