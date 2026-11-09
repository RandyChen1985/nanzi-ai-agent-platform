-- V209: 用户级凭据中心四张表（MySQL 方言）
--
-- ⚠️ 本脚本**不指定库名**（没有 USE，也不带库名前缀），在哪个库执行就建在哪个库。
--    执行前先确认 `SELECT DATABASE();` 返回的是应用配置里 MYSQL_DB 的那个库，
--    否则会出现「表建好了但接口报 Table 'xxx.sys_secrets' doesn't exist」——
--    两个库各自都对，只是不是同一个。
--
-- 背景：平台当前有四套互不相通的「凭据」机制——system_configs.is_secret（明文、
-- 全局唯一、无归属）、{user.xxx} 占位符（只有身份字段，没有密码）、
-- ${CREDENTIAL:header}（只是「未填」标记）、MCP auth_headers（只服务 MCP）。
-- 同一条 token 要在三处各存一份，轮换必漏。
--
-- 本次只**新增**表，不改动任何既有表：存量 system_configs.is_secret 与 MCP
-- auth_headers 保持原样，凭据中心是新路径。代码可以先于本迁移上线——
-- 查表失败时 SecretResolver 按「无凭据」处理，MCP/工具行为退回现状。
--
-- 五条设计约束（改动前请先读 docs/superpowers/specs/2026-11-09-user-credential-center-design.md）：
--   1. 绑定是白名单，默认零授权：建完凭据不等于能用；
--   2. 模型永远不能读值，只能触发已绑定槽位；
--   3. 凭据建完即不可读——包括创建者和管理员，没有任何接口返回明文；
--   4. 脱敏靠 value_fingerprint（sha256 前 16 位），不靠猜键名；
--   5. 域名白名单把「自动注入」关进笼子。

-- ── 凭据本体 ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS `sys_secrets` (
  `id` VARCHAR(36) NOT NULL,
  `owner_scope` VARCHAR(16) NOT NULL DEFAULT 'user'
    COMMENT 'user / system（dept 预留，V1 不开放）',
  -- 空串而非 NULL：MySQL 的 UNIQUE 不约束 NULL，用 NULL 会让系统级凭据
  -- 的 key 唯一性悄悄失效。与 ai_agent_resource_permissions 的处理一致。
  `owner_id` VARCHAR(64) NOT NULL DEFAULT ''
    COMMENT 'user 时=user_id；system 时=空串',
  `secret_key` VARCHAR(128) NOT NULL
    COMMENT '同一 owner 内唯一的引用标识，如 jira.prod.token',
  `name` VARCHAR(128) NOT NULL,
  `secret_type` VARCHAR(16) NOT NULL DEFAULT 'credential'
    COMMENT 'credential=永不可读 / variable=可读可复制',
  `value_encrypted` TEXT NULL COMMENT 'Fernet 密文；两类都加密存',
  `value_fingerprint` CHAR(16) NULL
    COMMENT 'sha256 前 16 位，供落库扫描比对；不可反推明文',
  `value_preview` VARCHAR(24) NULL
    COMMENT '仅 variable 存前 8 字符供列表展示；credential 必须为 NULL',
  `allowed_hosts` JSON NULL
    COMMENT '域名白名单数组；NULL 或 [] = 只允许平台内部',
  `allowed_scenarios` JSON NULL
    COMMENT '["mcp","api_tool","script_env","scheduled_task","agent"] 子集；NULL = 全不勾（fail-closed）',
  `status` VARCHAR(16) NOT NULL DEFAULT 'active'
    COMMENT 'active / disabled / expired',
  `expires_at` DATETIME(6) NULL COMMENT 'NULL=长期有效',
  `previous_value_encrypted` TEXT NULL
    COMMENT '轮换过渡期的旧值，双写 7 天后由后台任务清空',
  `previous_expires_at` DATETIME(6) NULL,
  `category` VARCHAR(64) NULL,
  `remark` VARCHAR(500) NULL,
  `created_by` VARCHAR(64) NULL,
  `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `updated_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  `last_rotated_at` DATETIME(6) NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_secret_owner_key` (`owner_scope`, `owner_id`, `secret_key`),
  KEY `idx_secret_owner` (`owner_scope`, `owner_id`),
  KEY `idx_secret_fingerprint` (`value_fingerprint`),
  KEY `idx_secret_status_expire` (`status`, `expires_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='凭据中心：值永不可读，只在调用时由平台注入';

-- ── 系统级授权给谁 ─────────────────────────────────────────────────────
-- 系统级凭据**不是全平台可用**：未出现在有效 grant 里的用户，连凭据名称都
-- 不返回（凭据名本身也是情报：sap.prod.admin 这种名字一看就知道有什么）。
CREATE TABLE IF NOT EXISTS `sys_secret_grants` (
  `id` VARCHAR(36) NOT NULL,
  `secret_id` VARCHAR(36) NOT NULL,
  `grantee_type` VARCHAR(16) NOT NULL COMMENT 'user / dept（V1 只这两种）',
  `grantee_id` VARCHAR(64) NOT NULL COMMENT '用户 ID 或部门编码',
  `granted_by` VARCHAR(64) NULL,
  `granted_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `expires_at` DATETIME(6) NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_grant_target` (`secret_id`, `grantee_type`, `grantee_id`),
  KEY `idx_grant_grantee` (`grantee_type`, `grantee_id`)
  -- 不设 secret_id 外键：这几张表要能单独导出再导入，外键会强制父表先存在，
  -- 导入顺序一错就整批失败。级联清理由 center.delete_secret 显式做。
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='系统级凭据的可见范围';

-- ── 绑定到消费者 ───────────────────────────────────────────────────────
-- bound_by_user_id 是最容易被漏掉的一列：如果 binding 只记
-- (secret_id, consumer)，A 把系统 token 绑到共享工具上之后，B 调用同一个
-- 工具也会用到它——而 B 可能根本没被授权。解析时必须校验
-- `bound_by_user_id IS NULL OR bound_by_user_id = <执行主体>`，
-- 其中 NULL 表示管理员做的全局绑定。
CREATE TABLE IF NOT EXISTS `sys_secret_bindings` (
  `id` VARCHAR(36) NOT NULL,
  `secret_id` VARCHAR(36) NOT NULL,
  `consumer_type` VARCHAR(24) NOT NULL
    COMMENT 'mcp_server / api_tool / script_tool / agent',
  `consumer_id` VARCHAR(36) NOT NULL,
  `slot` VARCHAR(128) NOT NULL
    COMMENT 'header:X-Api-Key / query:token / body:password / env:DB_PASSWORD',
  `bound_by_user_id` VARCHAR(64) NULL COMMENT 'NULL=管理员全局绑定',
  `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_binding_slot` (`consumer_type`, `consumer_id`, `slot`, `bound_by_user_id`),
  KEY `idx_binding_secret` (`secret_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='凭据能被谁取到：白名单，默认零授权';

-- ── 使用审计 ───────────────────────────────────────────────────────────
-- 刻意**不设** secret_id 外键：删凭据不能连带删使用记录，否则「这个凭据
-- 曾经被谁用过」就永久丢失。secret_key_snapshot 让记录在凭据消失后仍可读。
CREATE TABLE IF NOT EXISTS `sys_secret_usage_logs` (
  `id` VARCHAR(36) NOT NULL,
  `secret_id` VARCHAR(36) NULL,
  `secret_key_snapshot` VARCHAR(128) NULL,
  `owner_scope` VARCHAR(16) NULL,
  `trace_id` VARCHAR(64) NULL,
  `conversation_id` VARCHAR(64) NULL,
  `user_id` VARCHAR(64) NULL,
  `scene` VARCHAR(24) NOT NULL
    COMMENT 'mcp / api_tool / script_env / scheduled_task / agent',
  `consumer_type` VARCHAR(24) NULL,
  `consumer_id` VARCHAR(36) NULL,
  `slot` VARCHAR(128) NULL,
  `target_host` VARCHAR(255) NULL COMMENT '本次注入的目标域名',
  `result` VARCHAR(32) NOT NULL DEFAULT 'success'
    COMMENT 'success / denied_by_binding / denied_by_grant / denied_by_host / denied_by_scenario / expired / disabled',
  `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  KEY `idx_usage_secret_time` (`secret_id`, `created_at`),
  KEY `idx_usage_user_time` (`user_id`, `created_at`),
  KEY `idx_usage_trace` (`trace_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='凭据使用流水；记录不可删除，保留溯源';

-- ── 用户级注入偏好（三层开关的第 ② 层）─────────────────────────────────
-- 与第 ① 层（平台策略，存 system_configs）和第 ③ 层（单条凭据的
-- allowed_scenarios）取交集。只做「收紧」，不提供任何放宽项。
CREATE TABLE IF NOT EXISTS `sys_secret_user_prefs` (
  `user_id` VARCHAR(64) NOT NULL,
  `allow_script_env` TINYINT(1) NOT NULL DEFAULT 0
    COMMENT '允许脚本读取我的凭据；默认 0，脚本能 print env，风险最高',
  `updated_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='用户级凭据注入偏好';

-- ── 平台策略配置项 ─────────────────────────────────────────────────────
-- 冲突时不覆盖已有 value，只同步元数据，避免升级丢失部署方配置。
INSERT INTO `system_configs` (`key`, `value`, `description`, `category`, `is_secret`, `created_at`, `updated_at`)
VALUES
(
  'secret_policy_allow_script_env',
  '0',
  '是否允许脚本工具从环境变量读取凭据（0=禁止，1=允许）。默认禁止：脚本能读 os.environ 就一定能 print 出来，是所有注入场景里风险最高的一个。关闭时是「整类拒绝」，不逐条商量。',
  'security',
  0,
  NOW(6),
  NOW(6)
),
(
  'secret_policy_allow_external_host',
  '1',
  '是否允许凭据发往外部域名（0=只能注入平台内部接口，1=按凭据自身的域名白名单放行）。权威开关仍是每条凭据的 allowed_hosts，这一项是部署级的兜底闸门。',
  'security',
  0,
  NOW(6),
  NOW(6)
),
(
  'secret_policy_max_inject_per_call',
  '3',
  '单次调用最多注入几条凭据（整数）。防止一次请求把全部凭据一起带走。',
  'security',
  0,
  NOW(6),
  NOW(6)
)
ON DUPLICATE KEY UPDATE
  `description` = VALUES(`description`),
  `category` = VALUES(`category`),
  `is_secret` = VALUES(`is_secret`),
  `updated_at` = NOW(6);
