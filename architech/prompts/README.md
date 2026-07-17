# 南孜智能体平台 · 提示词草稿库

本目录存放**运营/架构迭代用的提示词 Markdown 草稿**，便于版本对比与导入智能体管理后台。  
**运行时真相源**以代码与数据库为准：

| 类型 | 运行时位置 |
|------|------------|
| 平台全局守则 | `app/services/ai/agent_prompts.py` → `PLATFORM_GLOBAL_SYSTEM_PROMPT` |
| 路由 / 意图 | `router_service.py`、`intent_service.py`（内置，不可 DB 配置） |
| 编排注入（技能/LTM/记忆） | `agent_prompts.py` + `agent_service.py` |
| ChatBI / 通用对话护栏 | `app/services/ai/executors/prompts.py` |
| 智能体主提示 | DB `ai_agent_versions.system_prompt` |

**流程文档**：[../design/chat/README.md](../design/chat/README.md)

---

## 当前推荐草稿（`system_agents/`）

| 智能体 | 文件 | 说明 |
|--------|------|------|
| ChatBI | [system_agents/chatbi/V8_chatbi_runner_aligned.md](system_agents/chatbi/V8_chatbi_runner_aligned.md) | 面向 DataAgentRunner 门控分工优化的 ChatBI 草稿 |
| DevOps | [system_agents/devops/system_prompt_V5.md](system_agents/devops/system_prompt_V5.md) | 当前保留的最新运维助手草稿 |
| 通用对话 | [system_agents/generl/general_chat_v2.md](system_agents/generl/general_chat_v2.md) | 通用对话 v2（目录名 `generl` 为历史拼写） |
| 知识库 | [system_agents/knowledge/knowledge_base.md](system_agents/knowledge/knowledge_base.md) | 知识库助手 |
| 元数据 | [system_agents/metadata/metadata_specialist.md](system_agents/metadata/metadata_specialist.md) | 元数据专家 |
| 元数据生成 | [meta/metadata_generator.md](meta/metadata_generator.md) | DDL/Markdown 解析 |

## 已归档（勿再作为线上依据）

旧版 ChatBI（V2–V6）、DevOps（V2–V4）已移至 [archive/](archive/README.md)，仅作历史对照。

---

## 调优说明

1. **ChatBI**：线上 `system_prompt` 须保留 `{dataset_menu}` 占位，由 `DataQueryExecutor` 按权限注入。
2. **路由**：见 `RouterService.DEFAULT_SYSTEM_PROMPT`；产品说明见 `design/AGENT_ROUTING_DESIGN.md`。
3. **全局安全/工具/记忆对照**：优先改代码中的 `PLATFORM_GLOBAL_SYSTEM_PROMPT`（含 memory_search、知识库、仅调用已绑定工具等），避免在每个智能体草稿里重复冗长安全段。
