-- 场景模板交付实例表
-- 用途：记录某个场景模板最终交付出来的业务实例，关联到实际创建或复用的智能体。
-- 说明：这里保留 agent_id 作为业务关联，不创建外键，避免不同历史库字符集/排序规则导致迁移失败。
CREATE TABLE IF NOT EXISTS `ai_agent_scenario_instances` (
  `id` varchar(36) NOT NULL COMMENT '实例ID，UUID',
  `template_id` varchar(100) NOT NULL COMMENT '场景模板ID，例如 chatbi-business-analysis',
  `template_name` varchar(100) NOT NULL COMMENT '场景模板名称快照',
  `agent_id` varchar(36) NOT NULL COMMENT '交付后关联的智能体ID',
  `agent_name` varchar(100) NOT NULL COMMENT '交付后关联的智能体标识快照',
  `display_name` varchar(100) NOT NULL COMMENT '交付实例显示名称',
  `owner` varchar(100) DEFAULT NULL COMMENT '业务负责人或交付负责人',
  `status` varchar(32) NOT NULL DEFAULT 'installed' COMMENT '交付状态：installed 已安装，needs_resources 待补齐资源',
  `resource_bindings` json DEFAULT NULL COMMENT '资源绑定快照，例如数据集、知识库、MCP/API工具、通知渠道',
  `missing_resources` json DEFAULT NULL COMMENT '安装时仍缺失的资源清单',
  `deliverables` json DEFAULT NULL COMMENT '本次交付物清单快照',
  `acceptance_criteria` json DEFAULT NULL COMMENT '验收标准快照',
  `next_steps` json DEFAULT NULL COMMENT '安装完成后的下一步动作',
  `created_by` varchar(64) DEFAULT NULL COMMENT '创建人',
  `updated_by` varchar(64) DEFAULT NULL COMMENT '最后更新人',
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uix_scenario_template_agent` (`template_id`, `agent_id`) COMMENT '同一模板同一智能体只保留一个交付实例',
  KEY `idx_scenario_template_instances_template_id` (`template_id`) COMMENT '按模板查询交付实例',
  KEY `idx_scenario_template_instances_agent_id` (`agent_id`) COMMENT '按智能体查询交付实例'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='场景模板交付实例表';

-- 场景模板安装运行记录表
-- 用途：记录每次预检安装尝试的结果，便于排查为什么安装成功、复用或被阻断。
-- 说明：失败预检也会写入 blocked 记录，方便后续审计和交付复盘。
CREATE TABLE IF NOT EXISTS `ai_agent_scenario_install_runs` (
  `id` varchar(36) NOT NULL COMMENT '安装运行记录ID，UUID',
  `instance_id` varchar(36) DEFAULT NULL COMMENT '关联的交付实例ID，预检失败时可能为空',
  `template_id` varchar(100) NOT NULL COMMENT '场景模板ID',
  `agent_id` varchar(36) DEFAULT NULL COMMENT '关联的智能体ID，预检失败时可能为空',
  `status` varchar(32) NOT NULL COMMENT '运行状态：success 成功，blocked 预检阻断，failed 安装失败',
  `precheck_result` json DEFAULT NULL COMMENT '预检结果快照',
  `install_result` json DEFAULT NULL COMMENT '安装结果快照',
  `error_message` text DEFAULT NULL COMMENT '失败或阻断原因',
  `created_by` varchar(64) DEFAULT NULL COMMENT '执行人',
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`id`),
  KEY `idx_scenario_install_runs_instance_id` (`instance_id`) COMMENT '按交付实例查询安装记录',
  KEY `idx_scenario_install_runs_template_id` (`template_id`) COMMENT '按模板查询安装记录',
  KEY `idx_scenario_install_runs_agent_id` (`agent_id`) COMMENT '按智能体查询安装记录'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='场景模板安装运行记录表';
