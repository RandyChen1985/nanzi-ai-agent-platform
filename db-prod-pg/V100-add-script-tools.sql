-- V100: 新增「脚本工具」配置表（PostgreSQL）
-- 与 sys_api_tools 并列的第二种自定义工具：Python 脚本在用户会话沙箱内执行。
-- builtin 行只存元数据，源码唯一来源是 app/services/ai/tools/script_tool_builtins.py
-- （未覆盖行的源码与参数/依赖/超时都以代码库定义为准；已覆盖行以库里的为准）。
CREATE TABLE IF NOT EXISTS "sys_script_tools" (
  "id" VARCHAR(36) NOT NULL,
  "name" VARCHAR(100) NOT NULL,
  "display_name" VARCHAR(255) NULL,
  "description" TEXT NULL,
  "source" VARCHAR(16) NOT NULL DEFAULT 'custom',
  "language" VARCHAR(16) NOT NULL DEFAULT 'python',
  "code" TEXT NULL,
  "parameter_schema" TEXT NULL,
  "requirements" TEXT NULL,
  "timeout_seconds" INTEGER NULL,
  "group_name" VARCHAR(64) NULL,
  "is_active" BOOLEAN NOT NULL DEFAULT TRUE,
  "created_at" TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updated_at" TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT "pk_sys_script_tools" PRIMARY KEY ("id"),
  CONSTRAINT "uk_sys_script_tools_name" UNIQUE ("name")
);

COMMENT ON TABLE "sys_script_tools" IS 'Python 脚本工具（在会话沙箱内执行）';
COMMENT ON COLUMN "sys_script_tools"."source" IS 'builtin=平台内置，custom=用户自定义';
COMMENT ON COLUMN "sys_script_tools"."code" IS '脚本源码；builtin 为 NULL（源码在代码库）';
COMMENT ON COLUMN "sys_script_tools"."parameter_schema" IS '参数 schema（JSON）';
COMMENT ON COLUMN "sys_script_tools"."requirements" IS '第三方依赖（JSON 数组）';

CREATE INDEX IF NOT EXISTS "idx_sys_script_tools_group" ON "sys_script_tools" ("group_name");
CREATE INDEX IF NOT EXISTS "idx_sys_script_tools_source" ON "sys_script_tools" ("source");

-- 内置脚本工具元数据（源码在 script_tool_builtins.py）。
-- ON CONFLICT DO NOTHING：已存在则不覆盖，避免升级时抹掉管理员改过的 is_active / group_name。
INSERT INTO "sys_script_tools"
  ("id", "name", "display_name", "description", "source", "language", "code", "parameter_schema", "requirements", "timeout_seconds", "group_name", "is_active")
VALUES
(
  'builtin-script-file-to-base64',
  'file_to_base64',
  '文件转 base64',
  '把一个工作区文件读取为 base64 文本，可选输出 data:<mime>;base64, 前缀。适合把图片、PDF 等二进制交给只接受 base64 / data-uri 的下游接口，或内嵌到 HTML、Markdown 中。',
  'builtin', 'python', NULL,
  '{"type":"object","properties":{"path":{"type":"string","description":"工作区内文件路径，如 uploads/a.pdf 或 /workspace/uploads/a.pdf"},"with_data_uri":{"type":"boolean","description":"是否输出 data:<mime>;base64, 前缀","default":false},"max_bytes":{"type":"integer","description":"最大读取字节数，默认和上限均为 49152（48KB；base64 后约 64KB，接近单次输出上限）","default":49152}},"required":["path"]}',
  '[]', NULL, '通用文件', TRUE
),
(
  'builtin-script-base64-to-file',
  'base64_to_file',
  'base64 转文件',
  '把一段 base64 字符串（允许带 data-uri 前缀）还原成文件并写入工作区，同时登记为可下载产物。适合把接口返回的 base64 落成用户能下载的附件。',
  'builtin', 'python', NULL,
  '{"type":"object","properties":{"data":{"type":"string","description":"base64 文本，可带 data:<mime>;base64, 前缀"},"filename":{"type":"string","description":"输出文件名（不含路径），如 report.pdf"},"overwrite":{"type":"boolean","description":"同名文件是否覆盖","default":false}},"required":["data","filename"]}',
  '[]', NULL, '通用文件', TRUE
),
(
  'builtin-script-file-digest',
  'file_digest',
  '计算文件/文本摘要',
  '计算文件或文本的 md5 / sha1 / sha256 摘要，用于校验传输完整性、比对版本或生成对账指纹。',
  'builtin', 'python', NULL,
  '{"type":"object","properties":{"target":{"type":"string","description":"文件路径；当 is_text=true 时按文本内容计算"},"algorithm":{"type":"string","description":"摘要算法","enum":["md5","sha1","sha256"],"default":"sha256"},"is_text":{"type":"boolean","description":"target 是否为文本而非文件路径","default":false}},"required":["target"]}',
  '[]', NULL, '通用文件', TRUE
),
(
  'builtin-script-text-regex-extract',
  'text_regex_extract',
  '正则批量提取',
  '用正则从文本中批量提取匹配内容，可指定捕获组。适合从日志、邮件、报表里抽取订单号、金额、IP 等字段。',
  'builtin', 'python', NULL,
  '{"type":"object","properties":{"text":{"type":"string","description":"待提取的原始文本"},"pattern":{"type":"string","description":"Python 正则表达式"},"group":{"type":"integer","description":"取第几个捕获组，0 表示整体匹配","default":0},"limit":{"type":"integer","description":"最多返回多少条","default":100}},"required":["text","pattern"]}',
  '[]', NULL, '通用文本', TRUE
),
(
  'builtin-script-text-redact',
  'text_redact',
  '敏感信息脱敏',
  '对手机号、身份证号、邮箱、银行卡号做脱敏，输出可以对外发送的文本。适合把工单、日志、报表交给外部前先做一轮清洗。',
  'builtin', 'python', NULL,
  '{"type":"object","properties":{"text":{"type":"string","description":"待脱敏文本"},"targets":{"type":"array","description":"要脱敏的类型，默认全部","items":{"type":"string","enum":["phone","id_card","email","bank_card"]}}},"required":["text"]}',
  '[]', NULL, '通用文本', TRUE
)
ON CONFLICT ("name") DO NOTHING;
