# 智能体工具库架构文档

本文档详细描述了南孜智能体平台现有的工具（Tools）、工作流程逻辑，以及如何开发并注册新工具的完整指南。

## 1. 现有工具清单 (Current Tools)

目前平台内置了以下核心工具，主要用于 ChatBI（数据查询）和上下文联动场景。

### 1.1 `get_dataset_schema` (数据模式检索)
- **标识符**: `get_dataset_schema`
- **文件路径**: `app/services/ai/tools/data_api.py`
- **功能**: 根据用户的自然语言描述（Query），检索与之最相关的元数据定义（Dataset YAML）。
- **核心逻辑**:
    1. 接收用户的模糊查询（如“PUE”、“机房能耗”）。
    2. 检查是否启用了 RAGFlow（向量检索）；若启用则调用向量库。
    3. 若未启用 RAG，则进行本地数据库模糊搜索 (`MetadataService.search_datasets`)。
    4. 将命中的数据集结构（Table definitions）导出为 YAML 格式返回给 LLM。
- **自愈反馈**: 若搜索无结果，会提示模型“尝试更通用的关键词”或“列出所有资产”。

### 1.2 `execute_sql_query` (SQL 执行器)
- **标识符**: `execute_sql_query`
- **文件路径**: `app/services/ai/tools/data_api.py`
- **功能**: 执行由 LLM 生成的 SQL 语句（仅限 SELECT），并返回 JSON 格式的数据结果。
- **核心逻辑**:
    1. **安全校验**:
        - 必须以 `SELECT` 开头。
        - 禁止多语句（`;`）。
        - 检查黑名单关键字（`DROP`, `DELETE`, `GRANT`, `SYSTEM` 等）。
        - 使用 `sqlglot` 进行 ClickHouse 语法预验证。
    2. **调试模式**: 若开启 `dry_run`，直接返回 SQL 文本而不执行。
    3. **自愈反馈**:
        - 若出现 `Unknown column`，提示模型“检查列名”。
        - 若出现 `Syntax error`，提示模型注意 ClickHouse 特殊语法。
    4. **执行**: 调用外部 SQL 执行 API（HTTP），获取数据。

### 1.3 `update_dashboard_context` (大屏上下文联动)
- **标识符**: `update_dashboard_context`
- **文件路径**: `app/services/ai/tools/dashboard_tools.py`
- **功能**: 允许智能体控制前端大屏的状态，实现“多模态”交互。
- **核心逻辑**:
    1. 这是一个“占位”工具，本身只返回成功消息。
    2. **拦截机制**: `DataQueryExecutor` 会监听该工具的调用。
    3. **事件分发**: 当工具调用成功时，后端通过 SSE (Server-Sent Events) 向前端发送类型为 `context_update` 的事件。
    4. **前端响应**: 前端接收到事件后，自动切换机房视角或时间范围。

---

## 2. 工具开发指南 (Development Guide)

本节介绍如何从零开发一个新工具并将其集成到平台中。

### 2.1 开发步骤

#### 第一步：编写工具函数
在 `app/services/ai/tools/` 目录下创建新文件（或使用现有文件），使用平台兼容的 `@tool` 装饰器定义函数（`app/services/ai/tools/tool_compat.py`，非 LangChain）。

**规范要求**:
- 必须有清晰的 Python 类型提示（Type Hints）。
- 必须包含 Google 风格或详细的 Docstring，因为这部分内容**直接作为 Prompt** 发送给 LLM。

**示例**:
```python
# app/services/ai/tools/server_tools.py
from app.services.ai.tools.tool_compat import tool

@tool
def reboot_server(server_id: str, reason: str = "maintenance"):
    """
    重启指定的服务器实例。仅在用户明确要求时调用。
    
    Args:
        server_id: 服务器的唯一标识符 (UUID)
        reason: 重启原因，默认是维护。
    """
    # 实际业务逻辑
    print(f"Rebooting {server_id} for {reason}...")
    return f"Server {server_id} reboot command issued."
```

#### 第二步：注册工具
在 `app/services/ai/tools/registry.py` 中注册你的新工具。

1. 导入你的工具函数。
2. 将其添加到 `_registry` 字典中。

```python
# app/services/ai/tools/registry.py
from app.services.ai.tools.server_tools import reboot_server  # <--- 1. 导入

class ToolRegistry:
    _registry: Dict[str, Any] = {
        # ... 现有工具 ...
        "reboot_server": reboot_server,  # <--- 2. 注册
    }
```

#### 第三步：配置智能体
不需要修改核心代码。只需在数据库或管理界面中，将新工具的标识符（Key）添加到目标智能体的配置中。

- **数据库表**: `ai_agent_versions`
- **字段**: `tools` (JSON Array)
- **值**: `["get_dataset_schema", "execute_sql_query", "reboot_server"]`

### 2.2 最佳实践

1. **原子性**: 一个工具只做一件事。
2. **容错性**: 工具内部应捕获具体的异常（如 `httpx.HTTPError`），并返回易于 LLM 理解的字符串错误信息（如 `[Tool Error] Connection failed`），而不是抛出堆栈信息。
3. **参数校验**: 对于敏感操作，务必在工具内部再次校验参数合法性（即使 LLM 已经生成了参数）。
4. **自愈友好**: 如果工具失败，返回的错误信息应包含“如何修复”的提示，帮助 Agent 自我修正。

---

## 3. 工作流程图 (Tool Execution Flow)

```mermaid
graph TD
    A[User Request] --> B[Agent Service]
    B --> C{Intent Recognition}
    
    C -- Data Query --> D[DataQueryExecutor]
    C -- Knowledge --> KE[KnowledgeExecutor]
    C -- Assistant --> E[AssistantExecutor]
    
    D --> DR[DataAgentRunner]
    KE --> KR[KnowledgeAgentRunner]
    E --> AR[AssistantAgentRunner]
    
    subgraph AgentScope [AgentScope ReAct]
        DR --> AS[Agent.reply_stream]
        GR --> AS
        AS --> F[LLM (Think)]
        F --> G{Call Tool?}
        G -- Yes --> I[ToolRegistry → RuntimeToolSpec]
        I --> J[AgentScopeRuntimeTool.invoke]
        J --> K[Execution & Validation]
        K --> L[Result → AgentState]
        L -- Feedback --> F
        G -- No (Final Answer) --> M[SSE Response Stream]
    end
```

运行时事件映射与权限挂起见 [../design/AGENTSCOPE_RUNTIME.md](../design/AGENTSCOPE_RUNTIME.md)。
