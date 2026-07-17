-- V59: 记忆管理模块（配置表 + 菜单/元素权限）
-- 配置与 system_configs 分离，仅在「记忆管理中心」维护

-- 1. 记忆服务专用配置表
CREATE TABLE IF NOT EXISTS `memory_service_configs` (
    `key` VARCHAR(255) NOT NULL COMMENT '配置键名',
    `value` TEXT NULL COMMENT '配置值',
    `description` VARCHAR(255) NULL COMMENT '配置描述',
    `is_secret` BOOLEAN NOT NULL DEFAULT 0 COMMENT '是否敏感（前端脱敏）',
    `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    `updated_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='记忆管理中心服务配置';

INSERT IGNORE INTO `memory_service_configs` (`key`, `value`, `description`, `is_secret`) VALUES
('memory_service_enabled', 'true', '记忆服务总开关（摘要写入与 memory_search 工具）', 0),
('memory_summary_enabled', 'true', '是否写入会话摘要与向量索引', 0),
('memory_embedding_base_url', '', 'Embedding API Base URL（OpenAI 兼容）；空则回退 llm_base_url', 0),
('memory_embedding_api_key', '', 'Embedding API Key；空则回退 llm_api_key', 1),
('memory_embedding_model', 'text-embedding-3-small', 'Embedding 模型名称', 0),
('memory_embedding_dimensions', '1536', '向量维度（变更后需重建 RediSearch 索引；索引名固定 nanzi:idx:memory:session_summary）', 0),
('memory_summary_max_sessions', '50', '每用户最多保留的会话摘要条数', 0),
('memory_summary_ttl_days', '30', '会话摘要 Redis 文档 TTL（天）', 0),
('memory_history_ttl_days', '7', '会话 history LIST TTL（天）', 0),
('memory_summarize_debounce_turns', '3', '累计多少轮对话后触发摘要合并', 0),
('memory_summarize_debounce_seconds', '300', '同会话两次摘要最短间隔（秒）', 0),
('memory_search_knn_top_k', '5', 'memory_search / 检索测试默认 Top K', 0),
('memory_summarize_min_assistant_chars', '30', 'assistant 回复低于该长度时跳过摘要', 0);

-- 2. 记忆管理中心菜单与元素权限
INSERT IGNORE INTO ai_agent_resource_permissions (resource_type, resource_id, enabled, created_at, updated_at) VALUES
('menu', 'menu:memory_management', 1, NOW(), NOW()),
('element', 'element:memory:config_save', 1, NOW(), NOW()),
('element', 'element:memory:config_index', 1, NOW(), NOW()),
('element', 'element:memory:view_data', 1, NOW(), NOW()),
('element', 'element:memory:delete', 1, NOW(), NOW()),
('element', 'element:memory:view_all_users', 1, NOW(), NOW()),
('element', 'element:memory:test_search', 1, NOW(), NOW());
