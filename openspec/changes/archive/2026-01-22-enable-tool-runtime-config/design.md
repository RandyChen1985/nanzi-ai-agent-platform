# 设计：工具级运行时配置

## 1. Schema 变更

### `app/schemas/agent.py`

我们需要将 `tools` 从 `List[str]` 转换为 `List[Union[str, ToolConfigItem]]`。

```python
class ToolConfigItem(BaseModel):
    """特定工具实例使用的配置。"""
    name: str
    enabled: bool = True
    model_name: Optional[str] = None  # 覆盖智能体的模型
    temperature: Optional[float] = None # 覆盖智能体的温度
    description_override: Optional[str] = None # 可选：允许提示词微调
    engine_config_override: Optional[Dict[str, Any]] = None # 针对特定工具的业务配置（如 RAG 的 TopK）

class ChatConfig(BaseModel):
    # ... 现有字段 ...
    tools: List[Union[str, ToolConfigItem]]
```

> **兼容性说明**：通过在列表中允许 `str` 来保持向后兼容性。后端会将 `str` 归一化为 `ToolConfigItem(name=s)`。

## 2. 工具注册表与工厂 (Tool Registry & Factory)

### `app/services/ai/tools/registry.py`

更新 `get_tools` 以接受混合列表。

```python
    @classmethod
    async def get_tools(cls, tool_configs: List[Union[str, ToolConfigItem]]) -> list[Any]:
        tools = []
        for item in tool_configs:
            name = item if isinstance(item, str) else item.name
            
            # 获取基础工具实例
            tool = await cls.get_tool(name)
            if not tool:
                continue
                
            # 如果有覆盖配置，则应用运行时配置
            if isinstance(item, ToolConfigItem):
                # 如果工具支持配置注入（例如实现了某个接口或包装器）
                tool = await cls._apply_tool_overrides(tool, item)
            
            tools.append(tool)
        return tools
```

## 3. 执行流程 (Execution Flow)

1.  **请求**：收到 `ChatCompletionRequest`。
2.  **上下文**：`AgentContextManager` 解析 `ChatConfig`。它现在从数据库加载丰富的工具列表（数据库存储也将适配 JSON 格式）。
3.  **执行器**：`AssistantExecutor` 调用 `ToolRegistry.get_tools(self.config.tools)`。
4.  **运行时**：返回的工具已预绑定了正确的 LLM 客户端（如果适用）。当调用 `ainvoke` 时，它们使用指定的模型。

## 4. 数据库存储考虑

`AIAgentVersion` 模型中的 `tools` 列已经是 `JSON` 类型，因此可以直接存储对象列表而无需进行数据库迁移 (Schema Migration)。

## 5. UI 变更

*   **组件**：工具设置模态框 (Tool Settings Modal)。
*   **动作**：在现有配置弹窗中添加“运行时 (Runtime)” 标签页或高级选项区域。
*   **字段**：模型下拉框（获取激活的模型列表）、温度滑块。