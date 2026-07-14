# 会话级反幻觉开关设计

## 目标

在聊天“界面设置”和 AgentDebug 右侧“调试配置”中移除重复的模型选择控件，并在原位置放置“反幻觉校验”开关。开关默认关闭，按当前浏览器页面中的会话生效；刷新页面或开启新会话后恢复关闭。EmbedChat 与 AgentDebug 输入框中已有的模型选择保留。

## 行为边界

- 前端每次聊天请求都通过 `debug_options.grounding_enabled` 显式传递布尔值。
- 字段缺失、`false` 或非布尔真值均视为关闭，确保旧客户端默认不启用。
- 关闭时，Main、ChatBI、KnowledgeAgent 跳过 Grounding 审核、风险提示和 Knowledge 反思重试。
- 关闭不影响模型生成、工具调用、知识库检索、ChatBI SQL 执行、权限门禁或流式协议。
- 开启时完整复用当前意图优先 Grounding 策略。
- `hallucination_check` 继续作为 KnowledgeAgent 的深度模型评估选项；只有总开关开启时才可能生效。

## 实现结构

`BaseExecutor` 提供严格布尔判断 `_grounding_enabled()`，各 Runner 统一使用。Main 在关闭时不建立审核缓冲；ChatBI 的统一审计返回 PASS；KnowledgeAgent 在首次生成后直接输出，不调用评估器或规则重试。

前端 `ChatSettings.vue` 将模型选择区域替换为二段式开关。`EmbedChat.vue` 初始化 `enableGrounding: false`，发送请求时始终传递该值，并在开启新会话时重置为 `false`。AgentDebug 右侧 `DebugConfigPanel.vue` 同样用开关替换模型覆盖下拉框，由 `AgentDebug.vue` 的 `debugConfig.enableGrounding` 透传并在新建调试会话时复位；输入框模型选择继续维护 `debugConfig.model`。

## 帮助提示

两个前端入口统一复用 `GroundingHelpPopover.vue`，在“反幻觉校验”标题旁显示圆形 `?`。桌面端支持悬停和点击，触屏端支持点击；点击组件外部或按 `Esc` 关闭，不改变开关状态，也不发起后端请求。

弹层使用“查询当前销售额”作为同一问题示例，说明三种可感知结果：

- 关闭：模型直接回答，不进行事实来源校验。
- 开启且证据匹配：工具或知识库结果能够支持回答，正常展示结果。
- 开启但证据不足：保留回答，同时在消息末尾追加风险提示，不出现阻断卡片。

帮助文案只解释产品行为，不承诺开启后所有事实必然正确；两处入口共享同一组件，避免规则演进后出现不同口径。

## 切换反馈

EmbedChat 和 AgentDebug 复用平台现有全局 `useToast()`，只在用户实际改变反幻觉开关状态时提示：开启使用成功提示“反幻觉校验已开启”，关闭使用信息提示“反幻觉校验已关闭”。重复点击当前状态不提示；新会话自动重置为关闭、打开或关闭帮助弹层也不提示。

Toast 仅用于即时交互反馈，不参与配置保存、请求参数或 Grounding 审核流程。EmbedChat 在更新状态后显示 Toast 并沿用既有保存/关闭设置面板行为；AgentDebug 在复选框 `change` 后读取最新状态并显示对应 Toast。

## 验证

- Main：默认关闭无风险提示；显式开启仍产生既有提示。
- ChatBI：关闭时不追加风险提示；开启时保持原行为。
- KnowledgeAgent：关闭时只生成一次且不调用幻觉评估器；开启时保留反思循环。
- 前端：静态交互测试确认模型选择已移除、开关默认关闭、请求显式透传以及新会话重置。
- 帮助提示：静态交互测试确认两个入口复用同一组件，包含三种示例结果，并支持点击、悬停、外部点击和 `Esc` 关闭。
- 切换反馈：静态交互测试确认两个入口均复用 `useToast()`，开启与关闭文案和类型正确，且 EmbedChat 重复点击当前状态不会提示。
- 执行相关 pytest、前端脚本测试、Vue SFC 静态解析、`py_compile` 和 `git diff --check`；服务构建、启停与 `./dev.sh` 由用户手动执行。
