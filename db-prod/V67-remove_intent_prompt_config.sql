-- V67: 移除数据库中的意图识别提示词配置 (intent_recognition_prompt)
-- Date: 2026-05-30
-- Purpose:
-- 意图识别提示词已内置到代码 IntentService.DEFAULT_SYSTEM_PROMPT，运行时不再从数据库读取，
-- 也不再在"提示词管理"中暴露。此处清理历史遗留的 system_configs 配置及其变更历史，
-- 避免运营在配置页误改导致第二层意图识别失准。
-- 影响: 删除后意图识别完全使用代码内置提示词；V7 等历史迁移保留不动（本迁移负责收尾清理）。

DELETE FROM `system_configs` WHERE `key` = 'intent_recognition_prompt';
DELETE FROM `system_config_history` WHERE `config_key` = 'intent_recognition_prompt';
