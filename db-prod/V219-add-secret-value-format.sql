-- V219: 凭据值支持结构化 JSON（录入形态 + 列表摘要）。（MySQL）
--
-- 背景：凭据值原来只有一个自由文本框，一个字符串存到底。接入邮箱 / FTP / 数据库
-- 这类多字段凭据时，用户只能手写 JSON，格式全靠自己记；列表里也看不出这条到底是
-- 单个 token 还是一坨结构化配置。
--
-- 三个新列都只是**描述**，不改变存储契约：明文仍然是 `value_encrypted` 里那一段
-- （结构化时是 JSON 序列化后的字符串），加密链路、掩码、审计、轮换一律不动。
--
--   value_format   text | json。免费文本与结构化共存，存量行默认 text，无需回填。
--   value_template 模板标识（如 smtp / sftp），仅 json 写入；模板定义在前端常量里，
--                  后端不校验 schema——模板升级因此不会让老记录读不出来。
--   value_summary  列表展示用的摘要，形如 `smtp.exmail.qq.com:465 · alert-bot`。
--                  只由**非敏感字段**拼成，且由后端按前端声明的敏感键名从 JSON 里挑，
--                  前端无法把任意字符串塞进来（明细见 center.build_value_summary）。
--
-- 幂等：MySQL 8.0 没有 `ADD COLUMN IF NOT EXISTS`，而 `apply_sql.py` 整文件重放、只按
-- 语句粒度忽略错误码（1060 列已存在）。三列放在同一条 ALTER 里，重复执行时只会命中
-- 一次 1060 并被忽略，不会出现「加了一半」的中间态。

ALTER TABLE `sys_secrets`
  ADD COLUMN `value_format` VARCHAR(16) NOT NULL DEFAULT 'text'
    COMMENT '值形态：text=单值（免费文本/PEM）/ json=结构化对象',
  ADD COLUMN `value_template` VARCHAR(64) NULL
    COMMENT '结构化模板标识（smtp/sftp/mysql…），仅 value_format=json 时写入；模板定义在前端常量',
  ADD COLUMN `value_summary` VARCHAR(160) NULL
    COMMENT '列表摘要：只拼非敏感字段（host/port/用户名等），不含 secret 字段的值';
