# OpenAI-compatible 提示词缓存布局与观测 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改变权限、工具和执行语义的前提下，将最终系统提示词稳定层前置，提供布局灰度配置与真实缓存命中观测。

**Architecture:** `PromptPlan` 统一存放稳定和动态 section，三个 runner 只能追加动态 section。系统配置决定旧布局、只观测或按会话灰度启用新布局；模型调用统计记录供应商实际返回的缓存输入 token 与布局元数据。

**Tech Stack:** Python 3.11、FastAPI、SQLAlchemy 异步服务、AgentScope、Vue 3、TypeScript、pytest。

---

### Task 1: 提示词布局配置与确定性灰度选择

**Files:**
- Modify: `app/services/ai/prompt_assembler.py`
- Test: `tests/ai/test_prompt_assembler.py`

- [ ] **Step 1: 写入 RED 测试**

```python
def test_resolve_prompt_layout_config_clamps_rollout_and_selects_session_bucket(monkeypatch):
    assert should_use_prompt_cache_layout("enabled", "100", "conversation-a") is True
    assert should_use_prompt_cache_layout("enabled", "0", "conversation-a") is False
    assert should_use_prompt_cache_layout("observe", "100", "conversation-a") is False
```

- [ ] **Step 2: 运行 RED 测试**

Run: `PYTHONPATH=. .venv/bin/pytest -q tests/ai/test_prompt_assembler.py -k prompt_layout`

Expected: 因缺少布局解析和分桶函数而失败。

- [ ] **Step 3: 实现最小配置接口**

在 `prompt_assembler.py` 增加 `PromptLayoutConfig`、`resolve_prompt_layout_config()` 和 `should_use_prompt_cache_layout()`；模式只允许 `legacy`、`observe`、`enabled`，比例归一化到 0–100，并以 SHA-256 的会话 ID 前缀稳定分桶。保留旧布尔配置作为兼容回退，不允许其覆盖显式 `legacy`。

- [ ] **Step 4: 运行 GREEN 测试**

Run: `PYTHONPATH=. .venv/bin/pytest -q tests/ai/test_prompt_assembler.py -k prompt_layout`

Expected: 所有新增布局配置测试通过。

### Task 2: PromptPlan 与稳定/动态渲染契约

**Files:**
- Modify: `app/services/ai/prompt_assembler.py`
- Modify: `app/services/ai/agent_prompts.py`
- Test: `tests/ai/test_prompt_assembler.py`

- [ ] **Step 1: 写入 RED 测试**

```python
def test_enabled_layout_keeps_user_and_turn_data_after_stable_sections():
    assembled = assemble_system_prompt(_params(
        prompt_layout_mode="enabled", user_profile="用户甲", turn_decision=_decision(),
    ))
    assert assembled.full_text.index("Agent DB prompt") < assembled.full_text.index("用户甲")
    assert assembled.full_text.index("用户甲") < assembled.full_text.index("本轮执行上下文")
    assert "NANZI_CACHE_BOUNDARY" not in assembled.full_text
```

- [ ] **Step 2: 运行 RED 测试**

Run: `PYTHONPATH=. .venv/bin/pytest -q tests/ai/test_prompt_assembler.py -k stable_sections`

Expected: 旧组装器将用户画像放在稳定段或仍输出 HTML 边界标记，测试失败。

- [ ] **Step 3: 实现 PromptPlan**

新增 `PromptPlan`，明确 `stable_sections` 和 `dynamic_sections` 并在唯一 `render()` 中合并。拆分平台固定规则与依赖 runtime tool set 的能力规则；用户画像、资源目录、路由决策、技能、记忆和子智能体目录只进入动态 section。删除发给模型的 HTML 缓存边界标记。

- [ ] **Step 4: 运行 GREEN 测试**

Run: `PYTHONPATH=. .venv/bin/pytest -q tests/ai/test_prompt_assembler.py`

Expected: 组装器既有契约和新增分层契约通过。

### Task 3: Pipeline 将布局与调试元数据传递给运行时

**Files:**
- Modify: `app/services/ai/pipeline/steps/assemble_step.py`
- Test: `tests/ai/pipeline/test_pipeline_steps.py`

- [ ] **Step 1: 写入 RED 测试**

```python
async def test_assemble_step_uses_enabled_layout_only_for_selected_conversation(...):
    # 断言传入的 PromptAssemblyInput 带 mode，且元数据含 layout_mode。
```

- [ ] **Step 2: 运行 RED 测试**

Run: `PYTHONPATH=. .venv/bin/pytest -q tests/ai/pipeline/test_pipeline_steps.py -k layout_mode`

Expected: 因 Pipeline 未传递模式与 rollout 选择结果而失败。

- [ ] **Step 3: 实现最小传递逻辑**

在 AssembleStep 读取新配置，按 `conversation_id` 选择布局；`observe` 仍发送 legacy 文本但把候选稳定/动态 token 元数据记录在 debug/trace 元数据中。`enabled` 只对选中会话渲染新布局。

- [ ] **Step 4: 运行 GREEN 测试**

Run: `PYTHONPATH=. .venv/bin/pytest -q tests/ai/pipeline/test_pipeline_steps.py -k layout_mode`

Expected: 新增 pipeline 契约通过。

### Task 4: 三个 runner 禁止动态内容前置

**Files:**
- Modify: `app/services/ai/runners/assistant_agent_runner.py`
- Modify: `app/services/ai/runners/knowledge_agent_runner.py`
- Modify: `app/services/ai/runners/chatbi/system_prompt.py`
- Test: `tests/ai/runners/test_general_agent_multiturn.py`
- Test: `tests/ai/runners/test_knowledge_agent_tools.py`
- Test: `tests/ai/runners/test_data_agent_runner.py`

- [ ] **Step 1: 写入 RED 测试**

每个 runner 的测试捕获最终 `SystemMessage`，断言稳定的智能体版本提示词先于路由/时间/检索或数据集动态信息，且路由快照仅出现一次。

- [ ] **Step 2: 运行 RED 测试**

Run: `PYTHONPATH=. .venv/bin/pytest -q tests/ai/runners/test_general_agent_multiturn.py tests/ai/runners/test_knowledge_agent_tools.py tests/ai/runners/test_data_agent_runner.py -k prompt_layout`

Expected: 当前前置拼接导致至少一个断言失败。

- [ ] **Step 3: 实现追加式动态注入**

将 Assistant 的重复路由提示、Knowledge 的路由/检索前置提示、ChatBI 的时间/状态前置拼接改为追加到 `PromptPlan` 动态段。固定 SQL 与知识库规则通过稳定 section 注册；时间、数据集菜单、RAG/SQL 结果和工作区保持动态。

- [ ] **Step 4: 运行 GREEN 测试**

Run: `PYTHONPATH=. .venv/bin/pytest -q tests/ai/runners/test_general_agent_multiturn.py tests/ai/runners/test_knowledge_agent_tools.py tests/ai/runners/test_data_agent_runner.py -k prompt_layout`

Expected: 三类最终系统消息顺序契约通过。

### Task 5: OpenAI-compatible usage 归一化与指标扩展

**Files:**
- Modify: `app/services/ai/executors/common.py`
- Modify: `app/services/ai/runtime/agentscope/middleware.py`
- Modify: `app/api/v1/endpoints/chat.py`
- Test: `tests/services/ai/test_base_executor_token_usage.py`
- Test: `tests/ai/runtime/test_agentscope_runtime_foundation.py`
- Test: `tests/ai/test_model_call_context_breakdown.py`

- [ ] **Step 1: 写入 RED 测试**

```python
def test_extract_tokens_reads_openai_cached_prompt_tokens():
    message = _message_with_token_usage({
        "prompt_tokens": 100, "completion_tokens": 5,
        "prompt_tokens_details": {"cached_tokens": 80},
    })
    assert extract_tokens_from_message(message)["cache_input_tokens"] == 80
```

- [ ] **Step 2: 运行 RED 测试**

Run: `PYTHONPATH=. .venv/bin/pytest -q tests/services/ai/test_base_executor_token_usage.py -k cached_prompt`

Expected: 现有 token 提取结果没有 `cache_input_tokens`，测试失败。

- [ ] **Step 3: 实现最小 usage 归一化**

`extract_tokens_from_message()` 返回 `cache_input_tokens` 与 `usage_source`，支持 AgentScope、OpenAI `prompt_tokens_details.cached_tokens` 和常见 `cache_*` 变体；字段缺失时标记 unavailable。模型调用统计追加 layout mode、稳定/动态 token 和 usage source；不记录提示词正文。

- [ ] **Step 4: 运行 GREEN 测试**

Run: `PYTHONPATH=. .venv/bin/pytest -q tests/services/ai/test_base_executor_token_usage.py tests/ai/runtime/test_agentscope_runtime_foundation.py tests/ai/test_model_call_context_breakdown.py`

Expected: 真实缓存 token 被保留，字段缺失不伪造命中。

### Task 6: 系统配置页下拉、滑块、说明与排序

**Files:**
- Modify: `frontend/src/views/SystemConfig.vue`
- Test: `tests/frontend/test_prompt_cache_system_config_contract.py`

- [ ] **Step 1: 写入 RED 契约测试**

```python
def test_prompt_cache_settings_precede_agent_iteration_limit():
    source = SYSTEM_CONFIG.read_text()
    order = source[source.index("const order = [", source.index("category === 'agent'")):]
    assert order.index("agent_prompt_layout_mode") < order.index("agent_max_iterations")
    assert order.index("agent_prompt_cache_rollout_percent") < order.index("agent_max_iterations")
```

- [ ] **Step 2: 运行 RED 测试**

Run: `PYTHONPATH=. .venv/bin/pytest --confcutdir=tests/frontend -q tests/frontend/test_prompt_cache_system_config_contract.py`

Expected: 配置不存在且排序断言失败。

- [ ] **Step 3: 实现 UI**

为模式配置渲染原生 select（`legacy`、`observe`、`enabled`）和中文效果说明；为比例配置渲染 0–100 range 滑块、数字值和无障碍标签。非 `enabled` 状态禁用滑块，并说明不会改变模型请求。把两个 key 放到 `agent_max_iterations` 前，并确保保存沿用 ConfigItem payload。

- [ ] **Step 4: 运行 GREEN 测试和类型检查**

Run: `PYTHONPATH=. .venv/bin/pytest --confcutdir=tests/frontend -q tests/frontend/test_prompt_cache_system_config_contract.py`

Run: `./node_modules/.bin/vue-tsc --noEmit`（目录：`frontend`）

Expected: 前端契约和类型检查通过。

### Task 7: 配置预置、回归清单与全量定向验证

**Files:**
- Modify: `db-prod/V*-add-agent-prompt-cache-layout-config.sql`（若系统配置表需显式预置）
- Modify: `db-prod-pg/V*-add-agent-prompt-cache-layout-config.sql`（同上）
- Modify: `tests/CHECKLIST.md`

- [ ] **Step 1: 检查系统配置预置机制并写入 RED 测试或迁移断言**

验证新键在新安装环境中可读取 `legacy` 与 `0`；若现有机制允许代码默认值而无需预置记录，不创建迁移。

- [ ] **Step 2: 仅在需要时新增双数据库迁移**

新增同版本 MySQL/PostgreSQL SQL，插入 `agent_prompt_layout_mode=legacy` 与 `agent_prompt_cache_rollout_percent=0`，不修改已有迁移或数据库。

- [ ] **Step 3: 更新测试清单**

在 `tests/CHECKLIST.md` 新增提示词缓存布局、配置 UI、usage 归一化及真实供应商验收边界。

- [ ] **Step 4: 完整定向验证**

Run: `PYTHONPATH=. .venv/bin/pytest -q tests/ai/test_prompt_assembler.py tests/ai/pipeline/test_pipeline_steps.py tests/ai/runners/test_general_agent_multiturn.py tests/ai/runners/test_knowledge_agent_tools.py tests/ai/runners/test_data_agent_runner.py tests/services/ai/test_base_executor_token_usage.py tests/ai/runtime/test_agentscope_runtime_foundation.py tests/ai/test_model_call_context_breakdown.py`

Run: `PYTHONPATH=. .venv/bin/pytest --confcutdir=tests/frontend -q tests/frontend/test_prompt_cache_system_config_contract.py`

Run: `./node_modules/.bin/vue-tsc --noEmit`（目录：`frontend`）

Run: `git diff --check`

Expected: 定向测试、类型检查与 diff 检查通过；真实 OpenAI-compatible 缓存命中与 TTFT 由已配置供应商环境手工验收。

> 本计划遵循用户当前要求：不自动暂存、提交或启动服务。
