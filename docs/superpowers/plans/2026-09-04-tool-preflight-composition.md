# 工具预检精度、流式撤回与多工具证据合同 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 本仓库约定本次不自动提交代码，所有步骤在当前工作树完成并保留用户已有未提交改动。

**Goal:** 为通用 AI 工具预检补齐元问题过滤、可靠 fallback、Generic API POST 只读声明、受限 provenance overlap、可选流式撤回和多工具独立证据合同，同时保持默认主流程兼容。

**Architecture:** 在 `tool_nudge_policy` 中先完成元问题和多工具计划判定，再由 runner 把单工具或合同计划编码到 `tool_preflight` 日志。`EvidenceLedger` 提供按 producer 的当前轮收据和受限关联判断，runner 根据 `agent_grounding_block_mode` 选择严格缓冲或流式后撤回。Generic API 表内接口统一按只读查询处理，GET/POST 都进入证据链路，不修改数据库表结构。

**Tech Stack:** Python 3.11、FastAPI、SQLAlchemy 2.x、Pydantic 2、AgentScope 2.x、pytest、Vue 3/TypeScript/Vite、MySQL/PostgreSQL migration SQL。

**Implementation status:** 已完成六项功能与定向回归（2026-09-04）。默认仍为 `strict_buffer`；本次不涉及数据库迁移，服务启动、MCP 和浏览器验收留给部署/测试环境执行。

---

### Task 1: 元问题分类和普通预检保护

**Files:**
- Modify: `app/services/ai/tool_nudge_policy.py`
- Modify: `app/services/ai/runners/assistant_agent_runner.py`
- Test: `tests/ai/test_tool_nudge_policy.py`
- Test: `tests/ai/runners/test_assistant_agent_grounding_gate.py`

- [ ] **Step 1: Write the failing tests**

在 `tests/ai/test_tool_nudge_policy.py` 增加以下行为测试：

```python
def test_capability_only_query_does_not_force_an_evidence_tool():
    tool = SimpleNamespace(
        name="weather_lookup",
        description="查询指定城市的实时天气和未来天气",
        permission_scope="read",
        source_type="generic_api",
        evidence_types={EvidenceType.EXTERNAL_TOOL},
    )

    nudge = resolve_tool_nudge("你们支持查天气吗？", [tool])

    assert nudge is None or nudge.force_first_call is False


def test_capability_question_with_real_target_still_uses_tool():
    tool = SimpleNamespace(
        name="weather_lookup",
        description="查询指定城市的实时天气和未来天气",
        permission_scope="read",
        source_type="generic_api",
        evidence_types={EvidenceType.EXTERNAL_TOOL},
    )

    nudge = resolve_tool_nudge("查一下上海明天的天气", [tool])

    assert nudge is not None
    assert nudge.tool_name == "weather_lookup"
    assert nudge.force_first_call is True
```

在 runner 测试中构造 `resolve_tool_nudge` 返回证据型 nudge 的预检场景，断言“你们支持哪些工具”不产生 `current_turn_evidence_required`；真实查询仍产生该字段。

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
PYTHONPATH=. .venv/bin/pytest -q tests/ai/test_tool_nudge_policy.py -k 'capability_only or real_target'
```

Expected: 能力咨询当前仍可能返回 `force_first_call=True`，或者新增测试因分类函数不存在而失败。

- [ ] **Step 3: Write minimal implementation**

在 `tool_nudge_policy.py` 增加 `_looks_like_tool_meta_query(query)`：先识别能力/参数/用法词，再排除日期、地点、数量、具体对象和真实动作；元问题只返回分类结果，不改权限。把它放在通用 nudge 和 evidence fallback 之前，但保留显式工具调用、Office、子代理、资源目录等已有显式分支优先级。

在 `assistant_agent_runner.py` 的预检入口使用同一分类结果：元问题不设置 `preflight_tool_choice`，不把 grounding requirement 提升为 `grounding_requires_tool`，但保留可供模型阅读的工具能力摘要；普通查询沿用当前证据型 nudge。

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
PYTHONPATH=. .venv/bin/pytest -q tests/ai/test_tool_nudge_policy.py tests/ai/runners/test_assistant_agent_grounding_gate.py
```

Expected: 元问题不强制调用，带真实目标的查询继续强制取证，现有 Office、子代理、todo 和 memory 分支不回归。

### Task 2: Fallback 阈值、候选 gap 和通用词过滤

**Files:**
- Modify: `app/services/ai/tool_nudge_policy.py`
- Test: `tests/ai/test_tool_nudge_policy.py`

- [ ] **Step 1: Write the failing tests**

增加 fallback 行为测试：

```python
def test_evidence_fallback_requires_the_higher_minimum_score():
    tools = [
        SimpleNamespace(
            name="weather_lookup",
            description="查询城市天气",
            permission_scope="read",
            evidence_types={EvidenceType.EXTERNAL_TOOL},
            source_type="generic_api",
        )
    ]

    assert resolve_evidence_tool_fallback_nudge("天气情况", tools) is None


def test_evidence_fallback_rejects_ambiguous_best_candidate():
    tools = [
        SimpleNamespace(name="weather_a", description="查询上海天气温度", permission_scope="read", evidence_types={EvidenceType.EXTERNAL_TOOL}, source_type="mcp"),
        SimpleNamespace(name="weather_b", description="查询上海天气降雨", permission_scope="read", evidence_types={EvidenceType.EXTERNAL_TOOL}, source_type="mcp"),
    ]

    assert resolve_evidence_tool_fallback_nudge("查询上海天气", tools) is None


def test_evidence_fallback_skips_capability_questions():
    tool = SimpleNamespace(name="weather_lookup", description="查询天气", permission_scope="read", evidence_types={EvidenceType.EXTERNAL_TOOL}, source_type="mcp")

    assert resolve_evidence_tool_fallback_nudge("有没有天气查询工具", [tool]) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
PYTHONPATH=. .venv/bin/pytest -q tests/ai/test_tool_nudge_policy.py -k 'fallback'
```

Expected: 旧默认 `0.25` 和无 gap 校验会使一个或多个新增断言失败。

- [ ] **Step 3: Write minimal implementation**

把 fallback 默认 `min_score` 调整为 `0.35`，在候选循环中维护 `(score, tool, metadata)` 排序结果；过滤 `_looks_like_tool_meta_query`、少于两个有效 signal 和没有区分度信号的候选。只有第一名分数达到阈值，且无第二名或第一名减第二名至少 `0.10` 时才返回具体 `ToolNudge`。保留异常 metadata resolver 的最小安全 fallback。

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
PYTHONPATH=. .venv/bin/pytest -q tests/ai/test_tool_nudge_policy.py
```

Expected: fallback 新增测试和已有 nudge 测试全部通过；常规预检阈值不被改变。

### Task 3: Generic API GET/POST 统一只读配置

**Files:**
- Modify: `app/services/ai/tools/generic_api.py`
- Test: `tests/ai/tools/test_generic_api.py`

- [ ] **Step 1: Write the failing tests**

在 Generic API 测试中增加 POST 只读测试：

```python
def test_generic_api_post_query_is_read_only_and_evidence_tool():
    config = SysApiTool(
        name="report_query",
        description="使用 POST 查询报表",
        method="POST",
        url_template="http://api.example.com/report",
        parameter_schema={"properties": {"date": {"type": "string"}}},
    )

    tool = GenericApiToolFactory.create_tool(config)

    assert tool.is_read_only is True
    assert tool.permission_scope == "read"
    assert EvidenceType.EXTERNAL_TOOL in tool.evidence_types
```

Generic API 工厂直接将所有表内接口注册为只读，并默认加入 `EXTERNAL_TOOL` 证据类型；无需 ORM、schema、CRUD、前端字段或数据库迁移。

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
PYTHONPATH=. .venv/bin/pytest -q tests/ai/tools/test_generic_api.py -k 'post_query or create_tool_schema'
```

Expected: POST 查询当前不会被识别为只读证据工具，新增断言失败。

- [ ] **Step 3: Write minimal implementation**

Generic API 工厂将表内 GET/POST 接口统一标记为 `is_read_only=True`、`permission_scope="read"`，并在未显式声明证据类型时加入 `EXTERNAL_TOOL`。既有权限确认仍由运行时权限链路负责。

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
PYTHONPATH=. .venv/bin/pytest -q tests/ai/tools/test_generic_api.py
./node_modules/.bin/vue-tsc --noEmit
```

Expected: Generic API 的 GET/POST 只读元数据和证据声明通过；不涉及表结构变更。

### Task 4: 受限 provenance overlap 和 producer 级账本能力

**Files:**
- Modify: `app/services/ai/grounding/ledger.py`
- Modify: `app/services/ai/grounding/policy.py`
- Modify: `app/services/ai/runners/assistant_agent_runner.py`
- Test: `tests/ai/grounding/test_ledger.py`（若目录没有该文件则创建）
- Test: `tests/ai/grounding/test_policy.py`（若目录没有该文件则创建）
- Test: `tests/ai/runners/test_assistant_agent_grounding_gate.py`

- [ ] **Step 1: Write the failing tests**

增加三组审计断言：

```python
def test_success_receipt_allows_non_concrete_execution_summary_without_marker_overlap():
    ledger = EvidenceLedger(user_id="1", conversation_id="c1")
    ledger.record_success(
        call_id="call-1", producer="report_query", evidence_types={EvidenceType.EXTERNAL_TOOL},
        result={"status": "ok", "data": {"state": "healthy"}}, policy="allow_empty_success",
    )

    decision = GroundingService.audit(
        requirement=FactRequirement(
            required=True, accepted_types=frozenset({EvidenceType.EXTERNAL_TOOL}),
            freshness=FactFreshness.DYNAMIC, block_unsupported_facts=True,
        ),
        candidate_text="查询已完成，数据已同步。",
        ledger=ledger,
    ).decision

    assert decision.action is GroundingAction.PASS


def test_success_receipt_does_not_allow_unrelated_concrete_fact():
    ledger = EvidenceLedger(user_id="1", conversation_id="c1")
    ledger.record_success(
        call_id="call-1", producer="weather_lookup", evidence_types={EvidenceType.EXTERNAL_TOOL},
        result={"city": "上海", "temperature": 26},
    )

    decision = GroundingService.audit(
        requirement=FactRequirement(
            required=True, accepted_types=frozenset({EvidenceType.EXTERNAL_TOOL}),
            freshness=FactFreshness.DYNAMIC, block_unsupported_facts=True,
        ),
        candidate_text="北京今天有 38 度。",
        ledger=ledger,
    ).decision

    assert decision.action is not GroundingAction.PASS


def test_ledger_can_require_fresh_receipt_from_specific_producer():
    ledger = EvidenceLedger(user_id="1", conversation_id="c1")
    ledger.record_success(
        call_id="call-1", producer="weather_lookup", evidence_types={EvidenceType.EXTERNAL_TOOL},
        result={"city": "上海", "temperature": 26},
    )

    assert ledger.has_fresh_evidence_from_producer(
        "weather_lookup", {EvidenceType.EXTERNAL_TOOL}, freshness=FactFreshness.DYNAMIC,
    )
    assert not ledger.has_fresh_evidence_from_producer(
        "finance_lookup", {EvidenceType.EXTERNAL_TOOL}, freshness=FactFreshness.DYNAMIC,
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
PYTHONPATH=. .venv/bin/pytest -q tests/ai/grounding tests/ai/runners/test_assistant_agent_grounding_gate.py -k 'provenance or producer or concrete'
```

Expected: ledger 缺少 producer 查询，具体实现会把执行总结按普通事实审计，新增行为无法稳定通过。

- [ ] **Step 3: Write minimal implementation**

在 `EvidenceLedger` 增加按 producer 和 freshness 过滤的成功收据查询，以及对应的 candidate overlap 查询；producer 匹配只接受完整名称和稳定的规范化别名，不使用模糊任意包含。把 marker 规范化提取为小型内部 helper，继续存 digest，不把原始工具结果放进日志。

在 grounding policy 中增加“非具体执行状态总结”判定：没有数字/日期/编号/地点/表格/业务对象具体值且只包含完成、同步、已处理等状态表达时，只有 fresh exact evidence receipt 存在才放行。具体事实仍执行现有 overlap 和冲突检查。

在 runner 单工具严格审计中使用该受限规则；不要把成功 receipt 转换成全局 bypass。新增的 warning/block reason 要保留 required/available evidence metadata，便于思考卡片解释。

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
PYTHONPATH=. .venv/bin/pytest -q tests/ai/grounding tests/ai/runners/test_assistant_agent_grounding_gate.py
```

Expected: 非具体总结放行、无关具体事实仍被 warning/block、producer 过滤生效，原有历史复用排除测试通过。

### Task 5: Streaming + Retraction 可选模式

**Files:**
- Modify: `app/services/ai/runners/assistant_agent_runner.py`
- Modify: `app/services/ai/runtime/agentscope/process_narration.py`
- Modify: `app/services/ai/agent_service.py`（仅在现有消费路径缺少 grounding retraction 持久化一致性时修改）
- Modify: `frontend/src/utils/agentscopeSseHandlers.ts`（仅在现有类型/消费契约不足时修改）
- Test: `tests/ai/runners/test_assistant_agent_grounding_gate.py`
- Test: `tests/ai/executors/test_chat_executor.py`
- Test: `tests/frontend/test_retraction_contract.py`

- [ ] **Step 1: Write the failing tests**

增加两种模式的事件序列测试：

```python
async def test_strict_buffer_does_not_emit_unverified_answer_before_audit():
    chunks = [chunk async for chunk in runner_with_preflight(mode="strict_buffer")]
    assert not any("北京今天有" in str(chunk.get("content")) for chunk in chunks)
    assert any(chunk.get("grounding_blocked") for chunk in chunks)


async def test_stream_with_retraction_emits_speculative_answer_then_retraction():
    chunks = [chunk async for chunk in runner_with_preflight(mode="stream_with_retraction")]
    answer_index = next(i for i, chunk in enumerate(chunks) if "北京今天有" in str(chunk.get("content")))
    retraction_index = next(i for i, chunk in enumerate(chunks) if chunk.get("type") == "retraction")
    assert answer_index < retraction_index
    assert "北京今天有" not in str(chunks[retraction_index].get("content"))
```

同时增加配置值非法时回退 `strict_buffer`、普通 grounding warning 不生成 retraction，以及前端 `retraction` 会替换累积正文的契约测试。

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
PYTHONPATH=. .venv/bin/pytest -q tests/ai/runners/test_assistant_agent_grounding_gate.py -k 'buffer or retraction'
```

Expected: 当前严格路径始终缓冲，`stream_with_retraction` 不会先发事实再发撤回。

- [ ] **Step 3: Write minimal implementation**

在 runner 添加只读配置解析 helper，允许 `debug_options` 覆盖测试值，生产默认从 `ConfigService.get("agent_grounding_block_mode", "strict_buffer")` 读取；只接受两个白名单值。严格预检事件到达后，根据模式决定是否把严格 answer delta 放入 `chunks_buffer`。

流式模式下继续累计 `full_text`，审计失败时先发 `grounding` blocked log，再发：

```python
{
    "type": "retraction",
    "content": "本轮未获得与当前问题匹配的实时工具结果，暂时无法可靠提供具体结论。请稍后重试。",
    "grounding_blocked": True,
}
```

确保 retraction 不重复发送 buffered chunks，`process_narration.accumulate_visible_answer` 和 AgentService 的 `_accumulate_stream_content` 仍以替换正文为准；不改变下载 URL 已有 retraction。

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
PYTHONPATH=. .venv/bin/pytest -q tests/ai/runners/test_assistant_agent_grounding_gate.py tests/ai/executors/test_chat_executor.py tests/frontend/test_retraction_contract.py
```

Expected: 默认严格缓冲行为不变，可选模式先流后撤回，最终可见正文和持久化正文都为安全内容。

### Task 6: 多工具任务拆分和独立证据合同

**Files:**
- Modify: `app/services/ai/tool_nudge_policy.py`
- Modify: `app/services/ai/runners/assistant_agent_runner.py`
- Modify: `app/services/ai/grounding/ledger.py`
- Modify: `app/services/ai/grounding/policy.py`
- Modify: `app/services/ai/runtime/agentscope/process_narration.py`（仅补充计划摘要显示时）
- Test: `tests/ai/test_tool_nudge_policy.py`
- Test: `tests/ai/runners/test_assistant_agent_grounding_gate.py`
- Test: `tests/ai/grounding/test_ledger.py`

- [ ] **Step 1: Write the failing tests**

增加计划识别和合同审计测试：

```python
def test_multi_tool_plan_requires_two_distinct_high_confidence_read_tools():
    tools = [
        SimpleNamespace(name="weather_lookup", description="查询天气", permission_scope="read", evidence_types={EvidenceType.EXTERNAL_TOOL}, source_type="mcp"),
        SimpleNamespace(name="train_lookup", description="查询高铁车票", permission_scope="read", evidence_types={EvidenceType.EXTERNAL_TOOL}, source_type="mcp"),
    ]

    plan = resolve_tool_nudge_plan("查上海明天天气，并查北京到上海的高铁", tools)

    assert plan is not None
    assert [contract.tool_name for contract in plan.evidence_contracts] == ["weather_lookup", "train_lookup"]
    assert plan.primary.tool_name == "weather_lookup"


def test_ambiguous_multi_tool_query_falls_back_to_single_or_none():
    tools = [SimpleNamespace(name="weather_lookup", description="查询天气", permission_scope="read", evidence_types={EvidenceType.EXTERNAL_TOOL}, source_type="mcp")]

    assert resolve_tool_nudge_plan("查天气并顺便看看情况", tools) is None
```

增加 runner 测试：一个合同有 producer 收据、另一个合同缺收据时，最终必须 `grounding_blocked`；两个 producer 都有收据且各自 marker 命中时放行。历史 `reuse_previous` 收据不能满足任一合同。

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
PYTHONPATH=. .venv/bin/pytest -q tests/ai/test_tool_nudge_policy.py tests/ai/runners/test_assistant_agent_grounding_gate.py -k 'multi_tool or contract'
```

Expected: `resolve_tool_nudge_plan` 不存在，或者当前 runner 只审计 evidence types 并集，缺少一个 producer 时仍可能放行。

- [ ] **Step 3: Write minimal implementation**

在 `tool_nudge_policy.py` 增加不可变 `EvidenceContract` 和 `ToolNudgePlan`，以及 `resolve_tool_nudge_plan()`：按连接词切分子句、为每个子句计算只读 evidence tool 的最佳候选，要求至少两个不同工具且每个分数达到 `0.35`、各子句内部 gap 满足 `0.10`。只在计划成功时由 runner 优先调用；失败时继续当前单 nudge。

runner 的 `tool_preflight` 日志增加 `evidence_contracts`，结构固定为：

```json
[
  {
    "tool_name": "weather_lookup",
    "required_evidence_types": ["external_tool"],
    "freshness": "current_turn"
  }
]
```

保留 union 形式的 `required_evidence_types` 供旧消费者使用；严格状态额外保存 contract 列表，结束时逐项调用 ledger 的 producer/freshness/overlap 查询。只要任一合同缺失或具体回答没有对应关联，就走已有安全 block/retraction 分支。系统提示只注入执行顺序，不在服务端隐式发起后续工具调用。

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
PYTHONPATH=. .venv/bin/pytest -q tests/ai/test_tool_nudge_policy.py tests/ai/grounding tests/ai/runners/test_assistant_agent_grounding_gate.py
```

Expected: 多工具计划只在明确高置信时启用，合同按 producer 独立审计，单工具和不明确复合问题不回归。

### Task 7: 全量定向回归、契约清单和人工验收边界

**Files:**
- Modify: `tests/CHECKLIST.md`

- [ ] **Step 1: Run backend focused regression**

Run:

```bash
PYTHONPATH=. .venv/bin/pytest -q \
  tests/ai/test_tool_policy.py \
  tests/ai/test_tool_nudge_policy.py \
  tests/ai/tools/test_generic_api.py \
  tests/ai/grounding \
  tests/ai/runners/test_assistant_agent_grounding_gate.py \
  tests/ai/runtime/test_event_stream_observability.py \
  tests/ai/runtime/test_tool_result_observability.py
```

Expected: 本次新增行为和已有执行链路测试通过；环境型 Redis/数据库/外部服务失败单独列出，不混入修复结论。

- [ ] **Step 2: Run frontend contracts and type check**

Run:

```bash
pytest --confcutdir=tests/frontend -q tests/frontend/test_internal_context_display_sanitization.py tests/frontend/test_retraction_contract.py
(cd frontend && ../node_modules/.bin/vue-tsc --noEmit)
```

Expected: 内部 backend 摘要仍不可见，retraction 替换正文契约通过，类型检查通过。

- [ ] **Step 3: Check SQL and working-tree scope**

Run:

```bash
git diff --check
git status --short
```

确认只新增两套 migration，没有执行 DDL；保留用户已有未提交文件，不执行 `./dev.sh`，不执行服务、浏览器、真实 MCP 或数据库实例验收。

- [ ] **Step 4: Update checklist and report acceptance boundary**

在 `tests/CHECKLIST.md` 补充：元问题不强制取证、fallback 使用 `0.35 + 0.10 gap`、Generic API 的 GET/POST 统一只读、provenance 仅覆盖非具体总结、流式撤回配置默认关闭、多工具合同按 producer 独立审计。

最终报告中明确区分：自动化测试证明、未运行的真实服务/数据库/MCP/浏览器验收，以及用户需要手动验证的三条场景：能力咨询不出工具调用卡、POST 查询出工具卡且最终事实有证据、复合问题两个工具分别出卡并缺一证据时安全收口。
