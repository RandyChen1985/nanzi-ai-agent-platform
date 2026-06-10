# 提案：支持工具级运行时配置 (Tool-Level Runtime Configuration)

## 目标
允许为智能体中的单个工具配置细粒度的执行参数（特别是 LLM 模型和温度）。这实现了“工具级隔离”，使得专门的工具（例如用于 SQL 生成的 DeepSeek-Coder）可以使用与主智能体（例如用于对话的 DeepSeek-V3）不同的模型。

## 背景
目前，智能体在 `ChatConfig` 中定义的单一全局配置（`model_name`, `temperature`）下运行。智能体调用的所有工具通常在此上下文中运行，或者是静态函数。目前没有机制可以指定“工具 A”无论主智能体的模型是什么，都应始终使用“模型 X”。

## 拟议解决方案
1.  **Schema 更新**：修改 `ChatConfig.tools` 和 `AIAgentVersion.tools`，以支持结构化配置对象 (`ToolConfigItem`) 而不仅仅是简单的字符串名称。
2.  **UI/UX**：更新“工具设置”模态框，允许用户为每个工具选择“执行模型”和“温度”。
3.  **执行器逻辑**：更新 `AssistantExecutor` 和 `ToolRegistry` 以遵循这些覆盖配置。在为会话初始化/获取工具时，如果存在配置覆盖，则该工具将配置有特定的 LLM。