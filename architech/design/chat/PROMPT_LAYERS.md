# 提示词分层与注入说明

> 关联：[CHAT_FLOW.md](./CHAT_FLOW.md)（业务流）、代码 `agent_service.py` / `agent_prompts.py` / `executors/prompts.py`  
> 版本：2026-05-31

---

## 1. 三类 LLM 调用（不要混在同一条 messages 里）

| 层级 | 何时触发 | 提示词位置 | 进入主对话？ |
|------|----------|------------|--------------|
| **路由** | 未传 `agent_id` | `RouterService.DEFAULT_SYSTEM_PROMPT` | 否 |
| **轮次/意图** | `resolve_turn_for_session` | `IntentService.DEFAULT_SYSTEM_PROMPT` 或启发式 | 否 |
| **主对话** | 执行器 `execute()` | 见下文 | 是 |
| **会话摘要** | 流结束后异步 | `ConversationSummarizer` | 否 |

路由与意图提示**写死在代码**（`PromptService.SYSTEM_PROMPT_REGISTRY` 为空，避免配置页误改）。

---

## 2. `system_prompt` 如何拼出来

### 2.1 起点

| 来源 | 说明 |
|------|------|
| `ai_agent_versions.system_prompt` | 智能体 **PUBLISHED** 版本（运营在管理后台配置） |
| 兜底 | `ContextManagerPrompts.GENERAL_CHAT_FALLBACK_SYSTEM_PROMPT` |
| RAGFLOW / OPENCLAW | 占位 `[Managed by …]`，真实提示在外部引擎 |

加载：`AgentManagerService.get_active_agent_config` / `get_version_config`。

### 2.2 编排层 prepend（`AgentService`）

**代码执行顺序**（每次 `f"{新块}\n\n{旧内容}"`）；**模型阅读顺序**与执行顺序相反，**最顶**由最后一步统一加上平台全局守则。

| 顺序（代码先后） | 块 | 条件 |
|------------------|-----|------|
| 1 | `[Active Skills Loaded]` + 技能摘要（**非** SKILL.md 全文） | 挂载或口头解析；全文须 `read_skill_instruction` |
| 2 | `[Skill Discovery Hint]` | 未加载任何技能 |
| 3 | — | `resolve_turn_for_session`（不直接改 prompt） |
| 4 | `[Memory Profile]` LTM JSON | `should_inject_ltm` |
| 5 | `[跨会话记忆检索]` | `should_inject_memory_recall_hint` + 记忆服务开启 |
| 6 | `[System Preloaded Memories]` | 回忆意图 / 日期命中 |
| 7 | **`[云枢智能体平台 · 全局守则]`** | `engine_type == LOCAL` |
| 8 | **整段替换** | `debug_options.system_prompt_override` |

**发给模型时，从上到下：**

```
[云枢智能体平台 · 全局守则]     ← PLATFORM_GLOBAL_SYSTEM_PROMPT（常量）
[System Preloaded Memories]    ← 可选
[跨会话记忆检索]               ← 可选
[Memory Profile]               ← 可选
[Skill Discovery Hint]         ← 可选
[Active Skills Loaded]（摘要）  ← 可选；全文走 read_skill_instruction
────────────────────────────
智能体 DB system_prompt        ← 领域专规（栈底）
```

修改全局守则：仅改 `app/services/ai/agent_prompts.py` 中 `PLATFORM_GLOBAL_SYSTEM_PROMPT`（含记忆工具对照、仅调用已绑定工具；进程/读写仅在有对应工具时按 tool description 使用）。

### 2.3 不进 `system_prompt` 的 system 消息

插入 `messages` 列表（`convert_history_to_messages` 会转为 `SystemMessage`）：

| 内容 | 条件 |
|------|------|
| `# Active User Profile`（称呼礼仪） | `should_inject_user_context`；安全规则已迁至全局块 |
| `# Session Runtime Context` + 移动/桌面 UI | `debug_options.injected_context` |

### 2.4 按轮次裁剪（`turn_classifier`）

| 轮次 | 常省略 |
|------|--------|
| K1/K2 查数、技能执行 | LTM、跨会话 hint、预加载、**用户画像 system** |
| 知识库 KNOWLEDGE | 跨会话 hint |

**平台全局守则不省略**（LOCAL 每轮都有）。

---

## 3. 主对话 messages 最终形态

```
SystemMessage #1   ← 完整 agent_config.system_prompt（§2.2 栈）
SystemMessage #2   ← 用户画像（可选）
SystemMessage #3   ← Session Runtime Context（可选）
HumanMessage / AIMessage … 历史
HumanMessage       ← 本轮用户（见 §4）
[+ DataQuery 额外 SystemMessage：SQL 计划、追问约束等]
[+ ToolMessage / 纠正语等]
```

执行器入口：

- **GeneralChat**：`SystemMessage(system_prompt)` + 历史；知识库轮局部加 `KNOWLEDGE_TURN_SYSTEM_HINT`
- **DataQuery**：Few-Shot prepend、`{dataset_menu}` 替换、SQL 护栏等（`executors/prompts.py`）
- **RAG / OpenClaw**：不走 LOCAL 全局 prepend 栈，自有逻辑

**工具 `description`**：来自 `ToolRegistry`，不算 chat 里的 system 文本。

---

## 4. `role: user` 侧提示（非 system_prompt）

| 部分 | 说明 |
|------|------|
| `---` 之前 | 用户可见原话 |
| `---` 之后 | 前端 `appendAttachmentContext`：知识库必须检索、文件路径、技能路径等 |
| 执行器追加 | `SharedPrompts.NON_IMAGE_ATTACHMENT_*`（当前轮非图片附件） |

历史轮次 API 只发 `---` 前纯文字（`buildOutboundMessages` / `_plain_user_text`）。

---

## 5. 什么适合放在「全局」vs 条件块 vs DB

| 放全局常量 | 放条件 prepend | 放智能体 DB | 放执行器 |
|------------|----------------|-------------|----------|
| 安全、保密、反幻觉 | LTM、跨会话、预加载记忆 | 领域角色、输出格式、ChatBI 口径 | SQL 计划、dataset_menu、知识库模式 |
| 优先级、默认中文 | 技能全文 / 发现 hint | `{dataset_menu}` 占位 | Few-Shot 案例块 |
| 工具通则、记忆对照表、仅调用已绑定工具 | — | 运维「必须推荐问题」等 | — |
| — | — | — | 移动/桌面 UI → `injected_context` |

---

## 6. 文件索引

| 用途 | 路径 |
|------|------|
| 平台全局 + 编排文案 | `app/services/ai/agent_prompts.py` |
| 编排注入顺序 | `app/services/ai/agent_service.py` |
| ChatBI / GeneralChat 执行器 | `app/services/ai/executors/prompts.py` |
| 跨会话 hint | `app/services/ai/memory_recall_policy.py` |
| 轮次裁剪 | `app/services/ai/turn_classifier.py` |
| 运营草稿（ChatBI V7 等） | `architech/prompts/system_agents/` |
