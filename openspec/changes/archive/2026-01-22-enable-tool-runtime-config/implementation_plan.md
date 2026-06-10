# 实施计划：工具级运行时配置 (Tool-Level Runtime Configuration)

## 1. 为什么这么做 (Why)

目前智能体是一个“单大脑”结构，所有的思考、工具调用和总结都强制使用同一个 LLM 模型。但在实际场景中：
- **SQL 生成** 需要极高逻辑性的 Code 模型（如 DeepSeek-Coder）。
- **知识库检索** 的后续总结可能只需要轻量级模型。
- **复杂推理工具** 内部可能需要比主模型更强大的模型。

通过支持工具级模型覆盖，我们可以实现“混合模型智能体 (Hybrid-Model Agent)”，在保证智能度的同时优化成本和响应速度。

## 2. 准备怎么搞 (How)

### 后端部分
1.  **数据模型适配**：
    - 在 `app/schemas/agent.py` 中引入 `ToolConfigItem` 类。
    - 调整 `ChatConfig` 以容纳这些结构化数据。
2.  **工具初始化流程重构**：
    - 修改 `app/services/ai/tools/registry.py`。
    - 在 `get_tools` 时，根据 `ToolConfigItem` 里的配置，如果发现有 `model_name` 或 `temperature` 覆盖，则为该工具创建一个专用的 LLM 实例或配置。
    - 对于 `GenericApiTool`（通过 HTTP 调用的工具），可能需要支持在 Header 中透传模型选择（如果后端 API 支持）。
3.  **Executor 适配**：
    - 确保 `AssistantExecutor` 在调用工具时，传递的是已经配置好的工具实例。

### 前端部分
1.  **UI 升级**：
    - 修改工具列表的配置模态框。
    - 添加一个新的 Tab “运行时配置”。
    - 提供模型下拉菜单（数据源自现有的模型管理接口）。

## 3. 为什么这么修改 (Rationale)

- **使用 `Union[str, ToolConfigItem]`**：这是为了向下兼容。现有的智能体配置中 `tools` 只是字符串列表，这样修改可以确保老数据不挂，同时新数据可以逐步迁移。
- **在 `Registry` 层拦截**：这是影响最小的地方。Executor 不需要知道工具内部是怎么实现的，只要 `Registry` 返回的工具对象是“配置好”的即可。
- **合并 UI 弹窗**：避免在界面上增加过多的图标和按钮，保持 Agent Studio 的简洁性。

## 4. 预期效果
用户可以在配置智能体时，点击某个工具（如 SQL 执行），在弹窗中选择“DeepSeek-Coder”，保存后。该智能体在执行 SQL 相关任务时会自动切换大脑，而聊天依然使用原来的模型。
