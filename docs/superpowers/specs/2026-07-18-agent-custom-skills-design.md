# 智能体自定义 Skills 设计

**日期:** 2026-07-18  
**状态:** 已批准，实施中

## 目标

在智能体版本配置中支持「是否自定义 Skills」。自定义开启时仅从公共（全局）已启用技能中勾选；运行时加载 = 勾选的全局技能 + 当前用户已启用个人技能。

## 行为

| `skills_custom` | 加载 |
|-----------------|------|
| `false`（默认） | 全部已启用全局 + 当前用户已启用个人 |
| `true` | `skills[]` 白名单全局 + 当前用户已启用个人 |

- 配置端不展示/不保存个人技能
- 自定义开启时至少选 1 个全局 skill id（前后端校验）
- 已禁用或已删除的 id 运行时跳过

## 数据

`ai_agent_versions`:
- `skills_custom` BOOLEAN NOT NULL DEFAULT 0
- `skills` JSON NULL（`string[]`）

同步到 ORM、Pydantic（含 `ChatConfig`）、前端 `AIAgentVersion`。

## 运行时

过滤点统一：
- `discover_platform_skill_paths`（AgentScope workspace）
- `list_skill_metas` / `scan_relevant_skills` / `resolve_skills_from_query`（prompt 注入）

## UI

工具步骤第三 tab「Skills」：开关 + 已启用公共技能卡片多选。
