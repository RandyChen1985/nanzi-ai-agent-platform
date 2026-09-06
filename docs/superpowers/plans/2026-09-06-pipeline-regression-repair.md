# 模块化流水线回归修复实施计划

> **For agentic workers:** 在当前会话内逐任务执行，严格遵循测试驱动开发；本任务不使用子代理，不自动提交。

**Goal:** 保留六阶段模块化流水线，同时恢复旧主流程的权限、状态、事件、持久化和可观测性契约。

**Architecture:** 以 `PipelineContext` 为跨步骤关键状态来源，保留 `shared_state` 作为执行器兼容桥。每个步骤只负责自己的边界；所有终态统一由 Finalize 收拢，未处理异常统一安全清洗。

**Tech Stack:** Python 3.11、FastAPI、AgentScope 2.x、pytest、asyncio。

---

### Task 1: 恢复权限与上下文 fail-closed 边界

**Files:**
- Modify: `tests/ai/pipeline/test_pipeline_steps.py`
- Modify: `app/services/ai/pipeline/steps/route_step.py`
- Modify: `app/services/ai/pipeline/steps/assemble_step.py`

- [ ] 增加路由权限拒绝测试，断言错误事件、终止状态以及模型解析未被调用。
- [ ] 运行新测试并确认当前实现失败。
- [ ] 在 RouteStep 检查 `err_msg`，记录准备失败并立即返回。
- [ ] 增加 `setup_context` 异常测试，断言提示词组装和执行准备均未继续。
- [ ] 运行新测试并确认当前实现失败。
- [ ] 移除 AssembleStep 的吞异常逻辑，让 PipelineRunner 统一处理。
- [ ] 运行 Task 1 测试并确认通过。

### Task 2: 修复多智能体异步流和内容协议

**Files:**
- Modify: `tests/ai/test_multi_agent_orchestrator.py`
- Modify: `app/services/ai/multi_agent_orchestrator.py`
- Modify: `app/services/ai/agent_service.py`

- [ ] 使用现有失败用例确认 supervisor/hierarchical 包装器返回 coroutine 而非异步迭代器。
- [ ] 将包装方法改为直接返回异步生成器的普通方法，保持调用方 `async for` 接口不变。
- [ ] 用共享 `_accumulate_stream_content` 处理 `answer_delta`、`process_narration_promote` 和 `retraction`。
- [ ] 运行多智能体测试并确认所有既有用例通过。

### Task 3: 恢复结果复用、知识增强和 LTM 传递

**Files:**
- Modify: `tests/ai/pipeline/test_pipeline_steps.py`
- Modify: `app/services/ai/pipeline/context.py`
- Modify: `app/services/ai/pipeline/steps/context_step.py`
- Modify: `app/services/ai/pipeline/steps/route_step.py`
- Modify: `app/services/ai/pipeline/steps/assemble_step.py`

- [ ] 增加测试，断言复用事件与 `TurnDecision.reusable_result_*` 完全一致并遵守轮次类型。
- [ ] 运行测试并确认当前决策字段丢失。
- [ ] 在路由类型确定后解析/约束复用结果，并把字段写入 TurnDecision。
- [ ] 增加知识轮次测试，断言 `enrich_for_knowledge_turn`、显式数据集 setup 和 LTM 提示词参数。
- [ ] 运行测试并确认当前实现失败。
- [ ] 从 PipelineContext 传递 LTM，并恢复知识轮次专用上下文调用。
- [ ] 对齐 `context.user_query` 和 `lane_user_id`。
- [ ] 运行 Task 3 测试并确认通过。

### Task 4: 恢复流式状态、时间线和取消语义

**Files:**
- Modify: `tests/ai/pipeline/test_pipeline_steps.py`
- Modify: `tests/services/ai/test_agent_service_stream_content.py`
- Modify: `app/services/ai/pipeline/steps/execution_step.py`
- Modify: `app/services/ai/pipeline/steps/finalize_step.py`
- Modify: `app/services/ai/pipeline/runner.py`

- [ ] 增加流式测试，断言每个 chunk 后正文、推理、执行状态和时间线已写回 PipelineContext。
- [ ] 增加取消测试，断言部分输出不会丢失且 Finalize 仍执行必要持久化。
- [ ] 运行测试并确认当前实现失败。
- [ ] ExecutionStep 使用 context 绑定的累加状态逐 chunk 写回，并跟踪过程事件。
- [ ] Finalize 恢复成功 Todo 收尾和完整时间线。
- [ ] 使用现有 detached/uncancelling 辅助函数保护取消后的必要持久化。
- [ ] 运行 Task 4 测试并确认通过。

### Task 5: 恢复终态事件、安全错误和审计契约

**Files:**
- Modify: `tests/ai/pipeline/test_pipeline_steps.py`
- Modify: `tests/services/ai/test_agent_service_stream_content.py`
- Modify: `app/services/ai/pipeline/runner.py`
- Modify: `app/services/ai/pipeline/steps/finalize_step.py`

- [ ] 增加异常泄漏测试，输入包含密码样式文本和内部路径的异常，断言客户端不可见。
- [ ] 增加非法下载链接测试，断言客户端收到 retraction 且保存内容已过滤。
- [ ] 增加 `awaiting_user` 审计测试，断言不会记作完成。
- [ ] 运行测试并确认当前实现失败。
- [ ] Runner 通过 `_enrich_terminal_error_chunk` 生成安全错误事件。
- [ ] Finalize 在需要时先发送 retraction，再发送终态，并统一使用完整等待状态集合。
- [ ] 运行 Task 5 测试并确认通过。

### Task 6: 覆盖生产入口和可观测性

**Files:**
- Modify: `tests/ai/pipeline/test_pipeline_steps.py`
- Modify: `tests/services/ai/test_execution_observability.py`
- Modify: `app/services/ai/agent_service.py`
- Modify: `app/services/ai/pipeline/steps/assemble_step.py`

- [ ] 增加从 `chat_completion_stream()` 进入默认六步流水线的测试，断言步骤顺序和 Finalize 执行。
- [ ] 增加性能追踪器、技能日志和 `return_raw_prompt` 调试事件测试。
- [ ] 运行测试并确认生产入口当前缺失这些数据。
- [ ] 在生产入口创建并传递 `ExecutionPerformanceTracker`。
- [ ] AssembleStep 发送既有技能日志和原始提示词事件。
- [ ] 运行 Task 6 测试并确认通过。

### Task 7: 回归验证和清单校正

**Files:**
- Modify: `tests/CHECKLIST.md`
- Modify: `app/services/ai/agent_service.py`

- [ ] 运行流水线、多智能体、技能、上下文、恢复、错误、时间线和可观测性定向测试。
- [ ] 运行受影响的前端事件契约测试。
- [ ] 将 CHECKLIST 改为只陈述有测试证据的事实。
- [ ] 修复文件末尾空行并运行 `git diff --check`。
- [ ] 检查 `git status --short`，确认没有暂存、提交或无关文件变化。
- [ ] 汇总通过、失败和未执行的真实环境验收边界。
