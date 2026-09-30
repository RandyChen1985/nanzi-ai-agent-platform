-- =====================================================================
-- 变更原因：新增 Langfuse LLM 链路追踪的独立配置表，并注册独立保存权限。
-- 需求背景：LLM 可观测性接入（排障与性能链路追踪），配置与 system_configs 解耦，
--           使用单行强类型配置表，密钥加密存储。
-- 创建时间：2026-09-30
-- 创建人：Antigravity Agent
-- 注意事项：该脚本仅做生成，不得由 Agent 自动执行，由用户手动导入更新到数据库。
-- 说明：字段与取值校验由应用层 app/services/ai/observability/config_store.py 负责；
--       本脚本只建表、灌入默认行（开关默认关闭）并注册权限元素。
-- =====================================================================

CREATE TABLE IF NOT EXISTS `langfuse_config` (
  `id` INT NOT NULL DEFAULT 1 COMMENT '固定为 1 的单行配置主键',
  `enabled` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '总开关：关闭时连 span 都不创建',
  `host` VARCHAR(512) NULL COMMENT 'Langfuse 地址，如 http://langfuse:3000',
  `public_key` VARCHAR(255) NULL COMMENT 'Langfuse public key（非机密，明文存储）',
  `secret_key` TEXT NULL COMMENT 'Langfuse secret key（Fernet 密文，前缀 langfusekey:v1:）',
  `sample_rate` DECIMAL(4,3) NOT NULL DEFAULT 1.000 COMMENT 'trace 粒度采样率 0.000~1.000',
  `capture_content` TINYINT(1) NOT NULL DEFAULT 1 COMMENT '是否上报提示词/补全内容',
  `environment` VARCHAR(64) NULL COMMENT 'Langfuse environment，留空不写入该属性',
  `release` VARCHAR(64) NULL COMMENT '发布版本，便于按版本对比',
  `timeout_seconds` INT NOT NULL DEFAULT 5 COMMENT '导出/探测超时秒数',
  `trace_url_template` VARCHAR(512) NULL COMMENT '深链模板，含 {trace_id} 占位',
  `updated_by` VARCHAR(64) NULL COMMENT '最后修改人',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Langfuse LLM 链路追踪配置（单行）';

-- 默认行：开关关闭、内容采集开启、采样 100%、超时 5s
INSERT IGNORE INTO `langfuse_config`
  (`id`, `enabled`, `sample_rate`, `capture_content`, `timeout_seconds`)
VALUES (1, 0, 1.000, 1, 5);

-- 独立功能权限：Langfuse 配置的保存与连通性探测
--
-- 注意：这里刻意不用仓库里其它权限迁移的 `INSERT IGNORE` 写法。`ai_agent_resource_permissions`
-- 上**没有** (resource_type, resource_id) 唯一索引（只有非唯一索引 idx_resource），
-- 因此 `INSERT IGNORE` 实际拦不住重复插入——重复执行本迁移会一次次追加同一行
-- （已知后果：`menu:dashboard` 7 行、`menu:ai_chat` 6 行等历史脏数据）。
-- 权限判定走 EXISTS/IN，重复行不影响鉴权结果，但会污染角色权限树，故这里用
-- `WHERE NOT EXISTS` 写成真正幂等（PG 侧 V62 同样是该写法）。
INSERT INTO `ai_agent_resource_permissions`
  (`resource_type`, `resource_id`, `enabled`, `created_at`, `updated_at`)
SELECT 'element', 'element:system:langfuse_save', 1, NOW(), NOW()
FROM DUAL
WHERE NOT EXISTS (
  SELECT 1 FROM `ai_agent_resource_permissions`
  WHERE `resource_type` = 'element'
    AND `resource_id` = 'element:system:langfuse_save'
);
