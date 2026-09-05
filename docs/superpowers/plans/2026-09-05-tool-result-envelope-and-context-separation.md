# 工具结果凭证与过程内容分离实施计划

## 目标

落地两个 P0：所有取证型工具在统一边界生成 `ToolResultEnvelope`；过程日志、思考卡片和运行总结不进入 `EvidenceLedger`，跨轮上下文只保留已完成且成功的工具结果。

## 约束

- 不新增数据库表、字段或 DDL。
- 不改变工具返回给模型的业务 payload 格式；凭证是运行时内部对象。
- 失败、拒绝、超时、未知状态和仅有过程文本的结果不可进入证据账本。
- 保留 ChatBI 的最终结构化结果和可信的跨轮缓存复用能力。

## 实施步骤

1. 在 grounding 模型中增加不可变 `ToolResultEnvelope`，统一保存状态、调用标识、生产者、来源引用、观测时间、数据截至时间、截断标记和证据准入标记。
2. 在 `EvidenceLedger` 增加 envelope 入口，由 envelope 的 `evidence_eligible` 决定是否生成 receipt；现有 `record_success` 保留兼容，并让新的运行时工具路径不再直接调用它。
3. 在 AgentScope RuntimeTool、原生工具和流式原生工具的最终收口处生成 envelope；流式过程中间 chunk、错误 chunk 和展示审计事件不记账。
4. 将外部执行恢复、ChatBI 最终结果和跨轮缓存结果改用同一 envelope 入口，保证 MCP、Generic API、知识库和查数路径一致。
5. 过滤 `tool_result_states` 非成功项，只生成“已完成成功结果”上下文；将上下文块改名为 `backend_tool_result_context`，前端同时继续清理旧标签和新标签。
6. 先运行新增失败测试，再运行 grounding、runtime、runner、context compaction 和前端内部上下文契约测试，并执行 `git diff --check`。

## 验收标准

- 成功非空结果能生成完整 envelope 并进入账本，来源和时间字段可追溯。
- 错误 payload、错误 chunk、思考/日志/总结文本不会生成有效 receipt。
- 重试失败后成功只保留成功调用的 receipt；恢复路径不因中间状态重复记账。
- 后续模型仍可获得最终成功工具结果，不会获得过程日志、思考卡片或失败文本。
