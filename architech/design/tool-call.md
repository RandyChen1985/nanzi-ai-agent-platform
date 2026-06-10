# 云枢智能体平台工具调用规范与格式文档

本文档描述平台在与大语言模型交互时，工具定义、AgentScope ReAct 执行与 SSE 对外协议。自 AgentScope 迁移后，**运行时不再使用 LangChain `bind_tools`**。

## 1. 整体架构概览

本地智能体（General / ChatBI）基于 **AgentScope Agent + Toolkit**，对外仍兼容 **OpenAI Function Calling** 形态的 JSON Schema。执行遵循 **ReAct** 循环：

1. **推理**：模型在 `MODEL_CALL` 中决定是否调用工具。
2. **行动**：输出 `TOOL_CALL_*` 事件，平台执行 `RuntimeToolSpec.invoke()`。
3. **观察**：`TOOL_RESULT_*` 回注 Agent 上下文。
4. **收尾**：`TEXT_BLOCK_DELTA` 流式输出最终回答；或触发 `REQUIRE_USER_CONFIRM` 挂起。

事件消费与 SSE 映射见 [AGENTSCOPE_RUNTIME.md](./AGENTSCOPE_RUNTIME.md)。

---

## 2. 工具定义格式 (Registration Format)

工具元数据由 `RuntimeToolSpec.parameters_schema` 提供，经 AgentScope Toolkit 发给 LLM（OpenAI tools 兼容结构）。

### 2.1 标准 JSON 结构

```json
{
  "type": "function",
  "function": {
    "name": "execute_sql_query",
    "description": "针对指定数据源执行只读 SQL SELECT，并在数据集权限范围内校验。",
    "parameters": {
      "type": "object",
      "properties": {
        "sql": { "type": "string", "description": "只读 SELECT 语句" },
        "data_source": { "type": "string", "description": "数据源标识，如 mysql_oa" },
        "dataset_name": { "type": "string", "description": "数据集名，用于权限校验" }
      },
      "required": ["sql", "data_source", "dataset_name"]
    }
  }
}
```

### 2.2 工具来源

| 类型 | 注册方式 |
|------|----------|
| 静态 Python 工具 | `ToolRegistry._registry` + `get_runtime_tool()` |
| ChatBI 核心工具 | `_create_chatbi_runtime_tool_spec()` |
| 通用 API 工具 | DB `sys_api_tools` → `GenericApiToolFactory` → `runtime_tool_spec_from_legacy_tool()` |
| MCP 工具 | `McpToolFactory` |
| AgentScope 内置 | `exec_command`→`Bash`、`read_file`→`Read` 等别名 |

---

## 3. 运行时消息与事件（AgentScope）

Agent 内部维护 `AgentState` 与消息块（`TextBlock`、`ToolCallBlock`、`ToolResultBlock`）。平台通过 `reply_stream` 消费事件，**不**再手工拼装 LangChain 的 `assistant.tool_calls` / `tool` 消息对。

对外 SSE 仍包含：

| SSE 类型 | 含义 |
|----------|------|
| `type: log` | 工具开始/完成、步骤日志 |
| `content` | 流式正文 |
| `permission_required` | ASK 工具需用户确认 |
| `external_execution_required` | 需外部执行后回填 |
| `tool_result_data` | 工具返回的二进制/结构化块 |

权限恢复：`UserConfirmResultEvent` + `POST /permissions/{id}/confirm`。

---

## 4. 权限范围 (permission_scope)

`RuntimeToolSpec.permission_scope` 决定 AgentScope 工具执行前行为：

| scope | 行为 |
|-------|------|
| `read` | 自动执行（如 `get_dataset_schema`、只读 Bash） |
| `ask` | 发出 `REQUIRE_USER_CONFIRM`，等待用户确认 |
| `dangerous` | 拒绝自动执行 |

实现：`app/services/ai/runtime/agentscope/tools.py` → `AgentScopeRuntimeTool.check_permissions()`。

---

## 5. 核心实现参考

| 能力 | 路径 |
|------|------|
| 工具注册与 RuntimeSpec | `app/services/ai/tools/registry.py` |
| Toolkit 构建 | `app/services/ai/runtime/agentscope/tools.py` |
| 事件 → SSE | `app/services/ai/runtime/agentscope/event_stream.py` |
| General 执行 | `app/services/ai/runners/assistant_agent_runner.py` |
| Knowledge 执行 | `app/services/ai/runners/knowledge_agent_runner.py` |
| ChatBI 执行 + 守卫 | `app/services/ai/runners/data_agent_runner.py` |
| 权限挂起恢复 | `app/services/ai/runtime/agentscope/confirmations.py` |
| 工具开发指南 | [../tools-schemal/README.md](../tools-schemal/README.md) |

---

## 6. 安全与权限

- **SQL 安全**：`sqlglot` 校验 AST，仅允许只读语句；`execute_sql_query` 走权限服务校验 `dataset_name`。
- **物理隔离**：元数据检索按用户授权数据集过滤。
- **SSRF 防御**：通用 HTTP 工具对 `url_template` 做白名单与内网 IP 过滤。

---

## 7. 历史说明

- LangChain `bind_tools`、手工 `ToolMessage` 链、`data_executor.bind_tools` 等描述仅适用于迁移前版本。
- `parse_xml_tool_calls()`（`executors/common.py`）为遗留兼容代码，**General AgentScope 路径不再依赖 XML 工具块解析**；模型应通过标准 function calling / AgentScope 工具协议调用。

*文档版本：2026-06-09*
