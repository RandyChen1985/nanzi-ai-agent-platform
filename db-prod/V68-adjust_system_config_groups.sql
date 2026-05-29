-- V68: 调整系统配置的分组分类
-- Date: 2026-05-30
-- Purpose:
-- 1. 将 ChatBI 经验库的配置项 (chatbi_sample_knowledge_base, chatbi_sample_similarity_threshold, chatbi_sample_vector_similarity_weight) 划分到 "智能报表 (ChatBI)" 的组（分类字段为 'data_api'）
-- 2. 将全局模型的配置项 (llm_model_name, llm_temperature) 划分到 "智能体设置 (AI Agent)" 的组（分类字段为 'agent'）
-- 3. 废弃并自动移除 "大语言模型 (Large Language Model)" 这一独立的分组 (分类为 'llm')

-- 1. 移动 ChatBI 配置项至 data_api 组
UPDATE `system_configs` 
SET `category` = 'data_api' 
WHERE `key` IN ('chatbi_sample_knowledge_base', 'chatbi_sample_similarity_threshold', 'chatbi_sample_vector_similarity_weight');

-- 2. 移动 LLM 模型配置项至 agent 组
UPDATE `system_configs` 
SET `category` = 'agent' 
WHERE `key` IN ('llm_model_name', 'llm_temperature');
