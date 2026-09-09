-- V151: 增加提示词缓存布局与灰度比例系统配置
INSERT IGNORE INTO `system_configs` (`key`, `value`, `description`, `category`, `is_secret`) VALUES
(
  'agent_prompt_layout_mode',
  'legacy',
  '提示词缓存布局策略：legacy（传统布局：动态内容前置，按原逻辑拼装）；observe（仅观测：保持传统布局发送，仅统计供应商缓存命中指标）；enabled（启用优化：按灰度比例启用稳定层前置缓存布局）。',
  'agent',
  0
),
(
  'agent_prompt_cache_rollout_percent',
  '0',
  '提示词缓存灰度分桶百分比（0-100）。仅当布局策略为 enabled 时生效，按会话 ID 哈希值在该百分比内的会话生效新布局。0 表示全走传统布局，100 表示全量生效。',
  'agent',
  0
);
