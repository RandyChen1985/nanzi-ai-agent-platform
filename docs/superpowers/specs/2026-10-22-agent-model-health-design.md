# 智能体模型健康检查与顶部告警设计

日期：2026-10-22
状态：已确认，待实现

## 背景

一次现场故障：定时任务连续失败 7 次，报错为

```
[系统错误] 执行过程中发生异常: LLM API Key is missing for model 'DeepSeek-V3.2'
```

根因不是密钥问题，而是系统未配置默认模型（`llm_model_name` 为空），旧代码在
三处兜底里写死了 `or "DeepSeek-V3.2"`，把目标环境并不存在的模型名写进新建智能体
的版本配置。该提交已修复兜底逻辑（见 `app/services/ai/default_model.py`），但
**已经落库的错误模型名不会自动纠正**，而且当前界面上完全看不出哪个智能体的模型
不可用——只能等运行时失败。

现有的 `readiness_ready` 只检查发布版本、主能力、知识库绑定与工具，不含模型维度。

## 目标

在「智能体中心」页面顶部提示：**哪些智能体绑定的 LLM 模型在当前环境不可用**，
并能一键跳到对应智能体修复。

## 非目标

- 不改变 `readiness_ready` 的语义，不阻断发布流程
- 不对模型发真实探活请求（避免计费与延迟）
- 不校验非 LOCAL 引擎（OPENCLAW 的 `model` 是机器人 ID，RAGFLOW 不调用平台 LLM）
- 不做定时后台巡检，只在打开页面时计算

## 判定规则

输入：已启用（`is_enabled=True`）且 `engine_type` 为 LOCAL 的智能体，取其最新
已发布版本的 `model_name`。

| 情况 | `code` | 是否告警 |
|---|---|---|
| 模型名为空 | `missing` | 是 |
| 精确匹配 `ai_models.model_id` 且 `is_active=true` | — | 否 |
| 精确匹配但 `is_active=false` | `disabled` | 是 |
| 无精确匹配，唯一匹配 `ai_models.name` 且 `is_active=true` | — | 否 |
| 唯一匹配 `ai_models.name` 但 `is_active=false` | `disabled` | 是 |
| 匹配到、启用，但 `api_key` 为空且全局 `llm_api_key` 也为空 | `missing_api_key` | 是 |
| 两种匹配都失败 | `unregistered` | 是 |

「唯一匹配 name」沿用 `app/services/ai/model_registry.resolve_model_record` 的语义：
显示名命中多条视为歧义，按未注册处理（提示改用 model_id）。

## 数据与接口

### 系统默认模型（`llm_model_name`）

除了单个智能体，系统默认模型本身也可能「未配置 / 未注册 / 已禁用 / 缺 Key」。
它影响所有没有显式绑定模型的链路（意图路由、通用对话兜底等），界面同样看不出异常。

新增轻量只读接口 `GET /api/portal/agents/model-health`，返回：

```json
{ "default_model_issue": { "code": "...", "message": "...", "model_name": "..." } }
```

正常时为 `null`。权限与列表接口一致（`menu:agent_management`），判定复用同一套
`evaluate_model_issue` 与模型表快照规则，不复制逻辑。

### 智能体列表

**零新增接口**。管理页列表（`AgentManagerService.list_agents`）本来就逐条计算
readiness，且已批量加载各智能体的最新发布版本
（`_latest_published_versions_by_agent`）。在这个循环里：

- 一次性载入模型表（`select(AIModel)`，通常在几十条量级）
- 一次性读全局 `llm_api_key`
- 对每个 LOCAL 智能体计算 `model_issue`

结果作为可选字段 `model_issue` 挂到响应：`{code, message, model_name}` 或 `null`。

**影响**：单次列表最多新增 2 条查询；不改变已有字段语义，老前端不受影响。

## 前端交互

`frontend/src/views/AgentManagement.vue`，位置在页面标题行之下、筛选区之上。

- `modelIssues = computed(() => agents.value.filter(a => a.model_issue))`
- `modelHealthItems` 合并两个来源：系统默认模型（首条）+ 各智能体
- 为空时**不渲染任何元素**（不占空间）
- 非空时渲染琥珀色横幅：`⚠ N 处模型配置异常` + 「查看」展开
- 展开后逐条展示：标题 · 原因 · **操作路径提示**
  - 系统默认模型条目带「去设置」按钮 → 跳 `/dashboard/system?tab=models`（入口唯一且可靠）
  - 智能体条目**不提供跳转**：模型存在「版本配置」里，且用户可能一个版本都没建过，
    复用 `openAgentModal`（基础信息弹窗）改不了模型。改为按 `code` 展示明确的操作路径：
    - `missing` / `unregistered`：请在该智能体的「版本配置 → 模型」中选择模型并发布
    - `disabled`：请在「模型管理」中启用该模型
    - `missing_api_key`：请在「模型管理」中为该模型填写 API Key
- 右下角「知道了」收起本条提示（仅当前会话，不持久化）
- 问题清空后自动重置「知道了」，避免修好一个之后再出现新问题却不再提示

## 测试

- `tests/services/ai/test_agent_model_health.py`：六种判定分支 + 默认模型三种情况
- `tests/frontend/test_agent_model_issue_banner_contract.py`：横幅存在性、两个来源、
  跳转目标、「知道了」重置逻辑、就绪度弹窗不再引用幽灵字段 `model_missing`
- 真实数据校验：本地 6 个智能体仅命中 1 个真实缺模型项，无误报

## 风险与降级

| 风险 | 处理 |
|---|---|
| 模型表很大 | 单次全表读，量级小；后续如需可加进程内缓存 |
| 全局 `llm_api_key` 与模型 key 都为空但 provider 本就不需要 key（本地 Ollama） | 只在两者同时为空时报 `missing_api_key`，已是事实判断；仍可能对免鉴权网关误报，文案写成「未配置 API Key」而非「Key 错误」 |
| 智能体无已发布版本 | 不告警（无版本本身由 readiness 的 `published_version` 覆盖） |
| 权限 | 沿用列表接口可见性，用户只看到自己能看到的智能体的异常 |
