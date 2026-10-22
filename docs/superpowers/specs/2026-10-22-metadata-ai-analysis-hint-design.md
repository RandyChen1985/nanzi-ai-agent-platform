# 元数据智能导入「智能分析执行方」提示设计

- 日期：2026-10-22
- 状态：已实现
- 相关代码：`app/services/metadata_generator.py`、`app/api/portal/endpoints/metadata.py`、`frontend/src/api/metadata.ts`、`frontend/src/components/metadata/SmartImportWizard.vue`

## 1. 背景

「智能元数据导入向导」的「开始智能识别」直接调用 `MetadataGeneratorService.generate_from_ddl()`。该服务会尝试读取 `metadata-specialist`（元数据专家）智能体的配置：

- 智能体启用且配置了系统提示词 → 使用智能体的模型与提示词；
- 智能体禁用或配置缺失 → 回退到系统默认模型 + 代码常量 `DEFAULT_METADATA_SYSTEM_PROMPT`。

但界面上没有任何说明，用户不知道这次分析用的是哪个模型、为什么效果和别处不同、想换模型该去哪里改。发行种子中 `metadata-specialist` 默认禁用（`db-prod/V20-add_agent_enabled_status.sql`、`db-prod-pg/V0-baseline.sql`），因此绝大多数用户看到的是「系统默认模型 + 内置模板」，却无从得知。

## 2. 目标

1. 向导打开时，明确展示**实际生效**的智能体启用状态与模型名，而不是写死文案。
2. 提供「去配置」入口，跳转智能体中心；无 `menu:agent_management` 权限时不显示按钮，但保留说明文案。
3. 提示条读取失败或加载中不渲染，绝不阻塞、不改变导入主流程。
4. 提示条口径与 `generate_from_ddl()` 的实际执行口径同源，避免「界面说一套、实际跑一套」。

## 3. 非目标

- 不改变智能分析的执行行为（仍为单轮结构化 LLM 调用，不引入 AgentScope 运行时、工具或子智能体）。
- 不在界面展开「启用后种子提示词会覆盖内置模板」的技术细节；该问题作为独立议题处理。

## 4. 方案

### 4.1 后端只读探测

`MetadataGeneratorService.describe_ai_analysis_config()`：

1. 查 `AIAgent(name='metadata-specialist')` 得到 `enabled` 与 `display_name`；
2. **仅当启用**才调用 `AgentManagerService.get_active_agent_config()`——该方法对禁用态会打 warning，而提示条属于高频只读查询；
3. 用 `resolve_runtime_model_info()` 解析实际生效模型与来源（`agent_config` / `system_default`）；
4. `prompt_source` 复用与 `generate_from_ddl()` 完全一致的判断：`agent_config and agent_config.system_prompt`。

新增 `GET /api/portal/metadata/ai-analysis-config`，权限复用 `element:metadata:import`（能打开导入向导即可读取），只读、不触发模型调用。

响应示例：

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "agent_name": "metadata-specialist",
    "display_name": "元数据专家",
    "enabled": false,
    "model_name": "deepseek-chat",
    "model_source": "system_default",
    "prompt_source": "builtin"
  }
}
```

### 4.2 前端提示条

`SmartImportWizard.vue` 在步骤一输入区顶部（标题与 DDL 输入框之间）渲染浅蓝信息条，按真实执行状态分三种表述：

- 智能体驱动（已启用且读到系统提示词）：「本次智能分析由「元数据专家」智能体驱动，模型：`xxx`」，副文案说明可在智能体中心调整、停用会回退；
- 已启用但提示词缺失：「「元数据专家」已启用，但未读到该智能体的系统提示词，本次仍使用平台内置解析模板（模型：`xxx`）」，引导检查已发布版本；
- 未启用：「「元数据专家」当前未启用，本次使用系统默认模型 `xxx` 与平台内置解析模板」，副文案引导去启用配置；
- 「去配置」按钮新标签页打开 `AgentManagement`，避免跳转丢失已粘贴的 DDL；
- 配置在弹窗打开时拉取（`watch(props.show)`），失败静默隐藏。

### 4.3 测试

- `tests/frontend/test_metadata_ai_analysis_hint_contract.py`：前端 API、提示条文案、权限显隐、失败降级、后端路由与口径一致性；
- `tests/api/test_metadata_ai_analysis_config.py`：禁用 / 启用有提示词 / 启用无配置 / 智能体缺失四种分支。

## 5. 风险与后续

- **双源漂移**：`prompt_source` 的判断与 `generate_from_ddl()` 的提示词分支必须同步，两处已互相加注释引用；若后续改为「内置模板始终为基底」，需同步修改探测方法。
- **启用即降级的坑**：MySQL 种子版提示词缺少 `partition_fields` / `index_fields`，PG baseline 只有一句话。直接启用 `metadata-specialist` 会让 DDL 解析能力退化。建议后续先补迁移对齐提示词，再考虑启用，或把提示词权威来源统一收敛。
