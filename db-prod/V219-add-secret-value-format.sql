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
-- 同一功能的第二半在文件末尾：绑定表加 `sys_secret_bindings.value_path`（使用侧
-- 「只取某个字段」）。录入结构化、使用侧选字段是同一条链路上的两半，所以放一个文件。
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

-- ── 使用侧：绑定只取某个字段 ─────────────────────────────────────────
--
-- 结构化凭据入库是一段 JSON，而绑定原来只能整段给：把 SMTP 凭据绑到邮件的
-- `smtp_password` 参数上，注进去的是 `{"host":…,"password":…}`，调用方必然失败。
--
--   value_path  绑定要取用的字段路径，点分隔，如 `password` / `auth.token`。
--               空串 = 整段给（单值凭据的唯一选择，也是全部存量行的默认）。
--               只对 fixed 绑定有意义：规则绑定每次挑中的凭据结构可能不同，
--               钉死一个字段路径只会在别的凭据上失败（resolver 里显式忽略）。
--
-- **单独一条 ALTER，不并进上面那条。** 上面那条在已经执行过的库上会命中 1060
-- 被整条忽略（语句粒度），把这一列塞进去就永远补不上了；分开写才能做到
-- 「新库一次到位、老库只补缺的那一列」。

ALTER TABLE `sys_secret_bindings`
  ADD COLUMN `value_path` VARCHAR(255) NOT NULL DEFAULT ''
    COMMENT '要取用的字段路径（点分隔，如 password / auth.token）；空串=整段。仅 fixed 绑定生效';
