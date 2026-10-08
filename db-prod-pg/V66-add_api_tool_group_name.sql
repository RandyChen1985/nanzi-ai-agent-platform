-- 原因：为 API 工具（sys_api_tools）增加业务分组字段（PostgreSQL 版本）
-- 需求背景：同 MySQL V165——自定义 API 工具在智能体配置里全落进「其他扩展工具」兜底组，
--           无法按业务域查找与管理。
-- 变更范围：仅新增一个可空列；使用 IF NOT EXISTS 保证可重复执行；**不改动任何既有数据**。
-- 创建人：Antigravity
-- 创建时间：2026-10-08

ALTER TABLE "sys_api_tools"
  ADD COLUMN IF NOT EXISTS "group_name" VARCHAR(64) NULL;

COMMENT ON COLUMN "sys_api_tools"."group_name" IS '业务分组名（智能体配置里按它归组，NULL 表示未分组）';
