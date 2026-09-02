# 实体关系智能发现策略 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为实体关系智能发现增加严格/智能推断策略，并在无主外键的数据库中安全扩大结构化元数据候选召回。

**Architecture:** API 请求携带可选策略，生成器将策略传给候选服务。严格模式沿用现有候选规则；智能推断模式在类型兼容和结构化语义证据基础上生成有上限的低分候选。两种模式共用外键元数据探测、AI 分组和确定性校验，前端默认智能推断但后端默认严格以保持兼容。

**Tech Stack:** FastAPI、Pydantic 2、Python 3.11、Vue 3、TypeScript、pytest、vue-tsc。

---

### Task 1: 扩展请求和生成器策略契约

**Files:**
- Modify: `app/schemas/metadata.py:58-65`
- Modify: `app/api/portal/endpoints/metadata.py:897-1075`
- Modify: `app/services/metadata_generator.py:697-910`
- Test: `tests/api/test_metadata_relationships_recommend.py`
- Test: `tests/services/test_smart_relationship_recommend.py`

- [ ] **Step 1: Write the failing tests**

增加请求模型允许 `strategy` 为 `strict`/`smart`，拒绝未知值；验证普通和 SSE worker 将 `strategy` 传给 `MetadataGeneratorService.recommend_relationships`，以及生成结果 `_debug.strategy` 保留策略。

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. .venv/bin/pytest tests/api/test_metadata_relationships_recommend.py tests/services/test_smart_relationship_recommend.py -q`

Expected: 新增断言失败，因为请求模型和生成器当前没有策略字段。

- [ ] **Step 3: Write minimal implementation**

在 `RelationshipRecommendRequest` 中增加默认值为 `strict` 的 `Literal["strict", "smart"]` 字段；普通/SSE 路由读取 `req.strategy` 并传入生成器；生成器增加 `strategy="strict"` 参数，将规范化策略写入 trace/debug，并保持没有传参时的现有行为。

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. .venv/bin/pytest tests/api/test_metadata_relationships_recommend.py tests/services/test_smart_relationship_recommend.py -q`

Expected: PASS。

### Task 2: 实现智能推断候选扩展和上限

**Files:**
- Modify: `app/services/metadata_relationship_candidate_service.py:303-470`
- Modify: `app/services/metadata_generator.py:790-850`
- Test: `tests/services/test_metadata_relationship_candidate_service.py`
- Test: `tests/services/test_smart_relationship_recommend.py`

- [ ] **Step 1: Write the failing tests**

增加以下行为测试：没有主外键但存在兼容类型、表名/字段中文术语或描述语义线索时，`smart` 生成候选；`strict` 不生成该低置信度候选；智能候选超过上限时按分数降序和稳定表顺序截断，并返回被截断计数。

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. .venv/bin/pytest tests/services/test_metadata_relationship_candidate_service.py tests/services/test_smart_relationship_recommend.py -q`

Expected: 新增智能模式测试失败，因为候选服务当前没有策略参数和智能候选扩展。

- [ ] **Step 3: Write minimal implementation**

给候选构建增加策略参数和明确的智能候选上限。严格模式调用现有评分；智能模式只在字段类型兼容且存在可解释结构线索时增加低分候选，线索包括表名 token 与字段 token/中文 term/description 的交集、索引或唯一字段角色。排除通用 `id` 对、纯业务相近但没有字段线索的表对。候选排序后截断，并把原始候选数、智能候选数、截断数返回给生成器诊断。不要改变 `validate_relationship` 的确定性校验。

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. .venv/bin/pytest tests/services/test_metadata_relationship_candidate_service.py tests/services/test_smart_relationship_recommend.py -q`

Expected: PASS。

### Task 3: 增加前端策略选择和诊断展示

**Files:**
- Modify: `frontend/src/api/metadata.ts:120-155,390-420`
- Modify: `frontend/src/components/metadata/SmartRelationshipModal.vue:35-330,800-940`
- Test: `tests/frontend/test_smart_relationship_modal_contract.py`

- [ ] **Step 1: Write the failing test**

断言弹窗存在 `relationshipStrategy`，默认值为 `smart`，包含“智能推断”和“严格模式”文案，SSE 请求发送 `strategy`，运行时禁用切换，并展示候选诊断字段。

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_smart_relationship_modal_contract.py -q`

Expected: FAIL，因为当前弹窗没有策略状态和请求字段。

- [ ] **Step 3: Write minimal implementation**

在配置区增加分段选择器和策略说明；`relationshipStrategy` 默认 `smart`，分析开始后禁用，重置配置时保持当前选择。API 类型增加 `strategy` 和诊断字段，SSE 请求透传策略。结果为空时显示本次策略、候选表对、AI 分析组和去重/截断信息；不改变已有结果选择和保存流程。

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_smart_relationship_modal_contract.py -q`

Expected: PASS。

### Task 4: 全量定向验证和工作区检查

**Files:**
- Test only: `tests/api/test_metadata_relationships_recommend.py`
- Test only: `tests/services/test_metadata_relationship_candidate_service.py`
- Test only: `tests/services/test_smart_relationship_recommend.py`
- Test only: `tests/frontend/test_smart_relationship_modal_contract.py`

- [ ] **Step 1: Run backend and frontend tests**

Run: `PYTHONPATH=. .venv/bin/pytest tests/api/test_metadata_relationships_recommend.py tests/services/test_metadata_relationship_candidate_service.py tests/services/test_smart_relationship_recommend.py -q` and `pytest --confcutdir=tests/frontend tests/frontend/test_smart_relationship_modal_contract.py -q`。

Expected: 全部 PASS。

- [ ] **Step 2: Run type/compile checks**

Run: `./frontend/node_modules/.bin/vue-tsc --noEmit` and `python -m py_compile app/schemas/metadata.py app/api/portal/endpoints/metadata.py app/services/metadata_relationship_candidate_service.py app/services/metadata_generator.py`。

Expected: 无输出且退出码为 0。

- [ ] **Step 3: Check diff hygiene**

Run: `git diff --check`。

Expected: 无空白错误。保留工作区中与本需求无关的用户改动，不自动暂存、提交或启动服务。
