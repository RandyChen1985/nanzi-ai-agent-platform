# OpenAI-compatible 提示词缓存布局与观测设计

## 背景与目标

平台现有的 `PromptAssembler` 已有“稳定前缀/动态后缀”概念，但默认不开启，且主助手、Knowledge、ChatBI 三个 runner 会在组装后继续前置路由提示、时间锚点、检索状态或数据集菜单。这使最终发送给 OpenAI-compatible 模型的 token 前缀经常在每轮变化，无法可靠复用供应商的 prompt cache。

本设计目标是在不改变权限、工具门禁、检索/SQL 流程、当前轮任务边界或用户隔离的前提下：

1. 统一最终系统提示词的稳定层与动态层；
2. 使稳定内容始终位于最终模型请求的最前方；
3. 记录供应商实际返回的缓存输入 token、命中率和首 token 延迟；
4. 支持先观测、再灰度、可立即回滚的上线方式；
5. 覆盖 OpenAI-compatible 接口，不引入 Anthropic 专有 `cache_control` 协议。

## 非目标

- 不改变模型供应商协议、模型价格或供应商的缓存 TTL。
- 不将用户画像、权限、资源目录、记忆、RAG/SQL 结果或本轮工具放入共享稳定层。
- 第一阶段不重排消息列表为“历史消息后再插入 system/developer 消息”；该布局对兼容服务的语义支持不一致，留作独立的后续实验。
- 不展示或持久化完整系统提示词、用户画像、检索结果和提示词哈希明文。

## 最终提示词布局契约

每次模型调用由统一渲染器生成单个 system 内容，顺序固定如下：

```text
[stable]
1. 平台固定安全、权限、工具总原则
2. 固定会话历史边界规则
3. 已发布智能体版本的 system_prompt
4. 当前执行器的固定规则

[dynamic]
5. 本轮实际可用工具与工具专属规则
6. 当前用户画像、授权资源目录
7. 路由与复用结果决策（只出现一次）
8. 记忆、技能与子智能体目录
9. 时间锚点、会话工作区与本轮预检提示
10. ChatBI 数据集菜单、查询状态、Schema/Few-shot/结果上下文，或
    Knowledge 检索状态、检索结果与引用上下文

[messages]
11. 已完成历史消息
12. 当前用户消息
```

动态层仍是服务端 system 指令，具有与现有注入相同的权限和约束效果；“动态”仅表示不应作为跨请求共享的缓存前缀。运行时不得将动态段再次前置到稳定段之前。

## 组件设计

### 1. 统一提示词计划

扩展现有 `PromptAssemblyInput` 与 `AssembledSystemPrompt`，新增 `PromptPlan` 保存有序 section：

- `stable_sections`：平台固定规则、历史边界、智能体版本专规、runner 固定规则；
- `dynamic_sections`：全部请求级、用户级、权限级、时间级、工具级与检索级上下文；
- `render()`：只在 runner 创建最终 `SystemMessage` 前渲染；
- `metadata`：稳定/动态字符数、估算 token 数、section 名称、布局版本和是否启用；不包含正文。

现有 `NANZI_CACHE_BOUNDARY` HTML 注释不再发往模型。必要时仅在调试输出中标识 section 边界。

### 2. 平台提示词拆分

`AgentServicePrompts` 拆为固定规则构造器与本轮能力构造器：

- 固定规则构造器不读取工具集、用户、会话、时间或路由信息；
- 本轮能力构造器仅使用最终 runtime tool set，生成可用工具目录、敏感工具规范、确认规则和技能状态；
- 当前工具名称与 runtime 注册仍从同一门控结果派生，动态工具缺失或权限变化时必须 fail-closed。

### 3. Runner 适配

- **AssistantAgentRunner**：路由提示、时间锚点、工作区、复用结果和工具预检改为追加动态 section；移除与 PromptAssembler 重复的路由决策提示。
- **KnowledgeAgentRunner**：固定检索/引用规则进入稳定 section；目录查询状态、预检索输出、引用、跟进提示、时间与工作区均追加动态 section。
- **DataAgentRunner / ChatBI**：固定 SQL 防护、分页语法和表达规则进入稳定 section；时间、数据集菜单、查询状态、上下文结果、Schema、few-shot 和继承分析上下文均追加动态 section。禁止秒级时间锚点出现在稳定段前。

任何 runner 需要新增系统内容时只能调用 `PromptPlan.append_dynamic()`；禁止字符串前置拼接。

## 系统配置页

在 `frontend/src/views/SystemConfig.vue` 的“智能体”分类排序中，两个新配置必须位于 `agent_max_iterations` 之前：

1. `agent_prompt_layout_mode`
   - 控件：下拉列表。
   - `legacy`（默认）：完全使用现有提示词布局，不改变运行行为。
   - `observe`：继续发送旧布局，同时构建新布局元数据并记录潜在可缓存 token；不改变模型输入。
   - `enabled`：按灰度比例发送新布局；灰度外请求仍使用旧布局。
   - 页面说明：该配置控制提示词布局与上线阶段；开启不会扩大工具、数据或用户权限。

2. `agent_prompt_cache_rollout_percent`
   - 控件：0–100 的滑动滑块，展示当前百分比与数字输入/无障碍文本。
   - 仅 `enabled` 可编辑；`legacy` 和 `observe` 时禁用，并清晰说明此时不会改变模型请求。
   - 取值为 0 时等价于不发送新布局，100 时所有符合条件的请求使用新布局。
   - 页面说明：灰度按稳定的会话/请求分桶，确保同一会话不会因每轮切换布局造成行为抖动；建议从 10% 开始，观察缓存命中、首 token 延迟和功能指标后再逐步放量。

后端读取默认值为 `legacy` 与 `0`。若系统配置存储要求预置记录，则同步新增 MySQL 与 PostgreSQL 迁移；不直接修改任何数据库。

## 命中观测

每个模型调用统一归一化并记录：

```text
provider, model, input_tokens, output_tokens, cache_input_tokens,
cache_hit, cache_hit_ratio, stable_prompt_tokens, dynamic_prompt_tokens,
prompt_layout_mode, usage_source, ttft_ms, total_model_ms
```

优先使用 AgentScope 已归一化的 `cache_input_tokens`；对于非原生流式/综合调用，兼容 OpenAI 风格 usage 的缓存 token 字段。供应商未返回该字段时标记 `usage_source=unavailable`，不得把“未知”记为未命中。

以现有模型调用统计为主承载，按供应商、模型、执行器和布局版本聚合以下指标：请求命中率、Token 命中率、未缓存输入 token、TTFT P50/P95、总模型耗时 P50/P95。指标不记录提示词正文。

## 上线与回滚

1. 发布后保持 `legacy`；
2. 全量 `observe` 运行 3–7 天，建立旧布局基线；
3. `enabled` 且 10% 灰度主助手普通聊天；
4. 依次扩大主助手带工具/时间表达、Knowledge、ChatBI；
5. 每阶段比较缓存指标与工具成功率、权限拒绝率、知识库引用成功率、SQL 成功率；
6. 出现行为、权限、工具或模型兼容性异常时，将模式切回 `legacy`，即时停止新布局。

## 验收与测试

- 提示词层：相同智能体但不同用户、资源、工具、时间、记忆时，稳定 section 完全一致，所有敏感上下文均在动态 section。
- Runner 层：主助手、Knowledge、ChatBI 最终 `SystemMessage` 均无动态前置；路由提示只注入一次。
- 功能层：工具门控、用户资源隔离、时间理解、记忆、技能、Knowledge 检索/引用、ChatBI Schema/SQL/复用结果保持原行为。
- UI 层：两个配置排序在 `agent_max_iterations` 前；下拉选项、说明、滑块范围、禁用逻辑和保存 payload 均有前端契约测试。
- Usage 层：覆盖 AgentScope usage、OpenAI-compatible usage 变体、流式最终 chunk、字段缺失；未知不伪造为零命中。
- 真实验收：在支持 prompt cache 的已配置 OpenAI-compatible 模型上，以相同智能体和连续受控会话验证第二次及后续请求返回实际缓存输入 token，并对比 TTFT；该项不能用单元测试替代。

## 文件范围（预期）

- `app/services/ai/prompt_assembler.py`
- `app/services/ai/agent_prompts.py`
- `app/services/ai/pipeline/steps/assemble_step.py`
- `app/services/ai/runners/assistant_agent_runner.py`
- `app/services/ai/runners/knowledge_agent_runner.py`
- `app/services/ai/runners/chatbi/system_prompt.py` 及必要的 ChatBI 后续注入模块
- OpenAI-compatible / AgentScope token usage 归一化与现有模型调用统计链路
- `frontend/src/views/SystemConfig.vue`
- 对应后端、runner、前端契约测试，以及 `tests/CHECKLIST.md`
- 若需预置系统配置：对应 MySQL 与 PostgreSQL 迁移文件
