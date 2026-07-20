# Agent Primary Type and Delegation Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace free-form orchestration setup with three fixed agent types and ensure Main only delegates to published, resource-ready agents.

**Architecture:** Persist `agent_type` on `ai_agents` while retaining `capabilities` as the runtime compatibility contract. Centralize type/tag normalization and readiness evaluation in focused backend modules, then reuse the same candidate resolver for Main roster, capability targeting, and `sub_agent_call`. The existing Vue modal becomes a type-card UI with a collapsed advanced capability section.

**Tech Stack:** FastAPI, Pydantic v2, SQLAlchemy async, MySQL migrations, pytest, Vue 3 Composition API, TypeScript, Tailwind CSS.

---

## File map

- Create `app/services/ai/agent_types.py`: type enum, locked capability mapping, capability normalization.
- Create `app/services/ai/agent_readiness.py`: structured readiness result and tool/resource validation.
- Create `db-prod/V103-add-agent-primary-type.sql`: schema change, backfill, locked-label repair.
- Modify `app/models/agent.py`: persisted `agent_type` column.
- Modify `app/schemas/agent.py`: request/response type contract and readiness fields.
- Modify `app/services/ai/agent_manager.py`: normalize writes, expose readiness summaries, validate publishing.
- Modify `app/services/ai/tools/agent_delegate_tool.py`: reuse runnable-candidate resolver and precise errors.
- Modify `app/services/ai/agent_roster.py`: build Main roster from runnable candidates only.
- Modify `app/services/ai/runners/assistant_agent_runner.py`: stable capability target selection from runnable candidates.
- Modify `frontend/src/api/agent.ts`: type and readiness contracts.
- Modify `frontend/src/views/AgentManagement.vue`: three type cards, locked tags, advanced capabilities, readiness feedback.
- Create `tests/ai/test_agent_types.py`: normalization contract.
- Create `tests/ai/test_agent_readiness.py`: readiness rules.
- Modify `tests/services/ai/test_agent_manager.py`: create/update/publish behavior.
- Modify `tests/ai/test_sub_agent_delegation.py`: candidate filtering and precise error behavior.
- Modify `tests/ai/test_agent_roster.py`: unavailable agents omitted.
- Create `tests/frontend/test_agent_type_form_contract.py`: stable frontend regression contract.
- Modify `tests/CHECKLIST.md`: record focused validation.

### Task 1: Persist and normalize the three primary types

**Files:**
- Create: `app/services/ai/agent_types.py`
- Create: `db-prod/V103-add-agent-primary-type.sql`
- Modify: `app/models/agent.py`
- Modify: `app/schemas/agent.py`
- Modify: `app/services/ai/agent_manager.py`
- Test: `tests/ai/test_agent_types.py`
- Test: `tests/services/ai/test_agent_manager.py`

- [ ] **Step 1: Write failing normalization tests**

```python
from app.services.ai.agent_types import AgentType, normalize_agent_capabilities


def test_chatbi_adds_locked_data_query_and_preserves_extensions():
    assert normalize_agent_capabilities(AgentType.CHATBI, [" reporting ", "data_query"]) == [
        "data_query",
        "reporting",
    ]


def test_switching_to_knowledge_removes_other_primary_tags():
    assert normalize_agent_capabilities(
        AgentType.KNOWLEDGE_BASE,
        ["data_query", "general_chat", "qa"],
    ) == ["knowledge_base", "qa"]
```

- [ ] **Step 2: Run tests and verify RED**

Run: `venv/bin/python -m pytest tests/ai/test_agent_types.py -q`

Expected: collection fails because `app.services.ai.agent_types` does not exist.

- [ ] **Step 3: Implement the minimal type contract**

```python
from enum import StrEnum


class AgentType(StrEnum):
    GENERAL = "GENERAL"
    CHATBI = "CHATBI"
    KNOWLEDGE_BASE = "KNOWLEDGE_BASE"


LOCKED_CAPABILITY_BY_TYPE = {
    AgentType.GENERAL: "general_chat",
    AgentType.CHATBI: "data_query",
    AgentType.KNOWLEDGE_BASE: "knowledge_base",
}
PRIMARY_CAPABILITIES = frozenset(LOCKED_CAPABILITY_BY_TYPE.values())


def normalize_agent_capabilities(agent_type: AgentType | str, values: list[str] | None) -> list[str]:
    normalized_type = AgentType(agent_type)
    extensions = sorted({
        str(value).strip()
        for value in values or []
        if str(value).strip() and str(value).strip() not in PRIMARY_CAPABILITIES
    })
    return [LOCKED_CAPABILITY_BY_TYPE[normalized_type], *extensions]
```

Add `agent_type = Column(String(32), nullable=False, default="GENERAL")`, expose `agent_type: AgentType = AgentType.GENERAL` in schemas, and call the normalizer from both `create_agent()` and `update_agent()`.

- [ ] **Step 4: Add the migration**

```sql
ALTER TABLE `ai_agents`
  ADD COLUMN `agent_type` VARCHAR(32) NOT NULL DEFAULT 'GENERAL' AFTER `capabilities`;

UPDATE `ai_agents`
SET `agent_type` = CASE
  WHEN JSON_CONTAINS(COALESCE(`capabilities`, JSON_ARRAY()), JSON_QUOTE('data_query')) OR `name` = 'chat-bi' THEN 'CHATBI'
  WHEN JSON_CONTAINS(COALESCE(`capabilities`, JSON_ARRAY()), JSON_QUOTE('knowledge_base')) OR `name` = 'knowledge-base' THEN 'KNOWLEDGE_BASE'
  ELSE 'GENERAL'
END;
```

Use `JSON_ARRAY_APPEND` guarded by `JSON_CONTAINS` to add the corresponding locked tag without removing historical extensions.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run: `venv/bin/python -m pytest tests/ai/test_agent_types.py tests/services/ai/test_agent_manager.py -q`

Expected: all selected tests pass.

### Task 2: Centralize readiness evaluation and enforce it on publish

**Files:**
- Create: `app/services/ai/agent_readiness.py`
- Modify: `app/services/ai/agent_manager.py`
- Modify: `app/api/portal/endpoints/agents.py`
- Test: `tests/ai/test_agent_readiness.py`
- Test: `tests/services/ai/test_agent_manager.py`

- [ ] **Step 1: Write failing readiness tests**

```python
def test_chatbi_requires_dataset_and_query_tool():
    result = evaluate_agent_readiness(
        agent_type="CHATBI",
        capabilities=["data_query"],
        engine_config={"dataset_ids": []},
        tools=[],
        has_published_version=True,
    )
    assert result.ready is False
    assert result.missing == ("dataset_binding", "data_query_tool")


def test_knowledge_base_ready_with_binding_and_search_tool():
    result = evaluate_agent_readiness(
        agent_type="KNOWLEDGE_BASE",
        capabilities=["knowledge_base"],
        engine_config={"dataset_ids": ["kb-1"]},
        tools=["search_knowledge_base"],
        has_published_version=True,
    )
    assert result.ready is True
    assert result.missing == ()
```

- [ ] **Step 2: Run tests and verify RED**

Run: `venv/bin/python -m pytest tests/ai/test_agent_readiness.py -q`

Expected: import failure for the missing readiness module.

- [ ] **Step 3: Implement deterministic readiness evaluation**

```python
@dataclass(frozen=True)
class AgentReadiness:
    ready: bool
    missing: tuple[str, ...]


DATA_QUERY_TOOLS = frozenset({"get_dataset_schema", "execute_sql_query"})


def evaluate_agent_readiness(*, agent_type, capabilities, engine_config, tools, has_published_version):
    missing = []
    if not has_published_version:
        missing.append("published_version")
    # Append type-specific locked capability, resource, and tool failures in stable order.
    return AgentReadiness(ready=not missing, missing=tuple(missing))
```

Tool parsing must accept string entries and `{name: ...}` entries. CHATBI passes when at least one recognized data-query tool exists; KNOWLEDGE_BASE requires `search_knowledge_base`.

- [ ] **Step 4: Enforce readiness before mutating publication state**

Load the target version and evaluate it before archiving the current published version. Raise `AgentNotReadyError(missing)` on failure; map it to HTTP 409 with:

```json
{"detail": {"code": "AGENT_NOT_READY", "missing": ["dataset_binding"]}}
```

- [ ] **Step 5: Verify GREEN**

Run: `venv/bin/python -m pytest tests/ai/test_agent_readiness.py tests/services/ai/test_agent_manager.py -q`

Expected: all selected tests pass and failed publication leaves the previous published version unchanged.

### Task 3: Make Main roster and delegation use runnable candidates

**Files:**
- Modify: `app/services/ai/tools/agent_delegate_tool.py`
- Modify: `app/services/ai/agent_roster.py`
- Modify: `app/services/ai/runners/assistant_agent_runner.py`
- Test: `tests/ai/test_sub_agent_delegation.py`
- Test: `tests/ai/test_agent_roster.py`

- [ ] **Step 1: Write failing candidate tests**

```python
@pytest.mark.asyncio
async def test_delegable_candidates_exclude_agent_without_loadable_published_config():
    candidates = await resolve_runnable_delegable_agents(
        session,
        agents=[ready_agent, unpublished_agent],
        user_id=7,
        is_admin=False,
        current_agent_id="main-id",
    )
    assert [agent.name for agent in candidates] == ["ready-data-agent"]


def test_capability_target_is_stable_by_sort_order_then_id():
    targets = AssistantAgentRunner._build_sub_agent_targets_by_capability([
        {"id": "b", "name": "later", "sort_order": 1, "capabilities": ["data_query"]},
        {"id": "a", "name": "preferred", "sort_order": 5, "capabilities": ["data_query"]},
    ])
    assert targets["data_query"] == "preferred"
```

- [ ] **Step 2: Run tests and verify RED**

Run: `venv/bin/python -m pytest tests/ai/test_sub_agent_delegation.py tests/ai/test_agent_roster.py -q`

Expected: unpublished agents remain in the list and input order wins capability selection.

- [ ] **Step 3: Implement one shared runnable resolver**

After permission filtering, load the latest published configuration and evaluate readiness for each candidate. Return candidates ordered by `sort_order DESC, id ASC`. Make `agent_roster.py`, `assistant_agent_runner.py`, and the tool execution path call this resolver.

- [ ] **Step 4: Remove name fallbacks from nudge targeting**

Change `_target_for_capability(capability, fallback_name)` into `_target_for_capability(capability)` and return no nudge when the capability mapping has no runnable target. Keep explicit user selection limited to names returned by the runnable resolver.

- [ ] **Step 5: Return actionable tool errors**

When the requested agent exists but is not ready, return:

```text
错误：智能体 `finance-agent` 当前尚未就绪，缺少：已发布版本、数据集绑定。请完成配置后重试。
```

Do not include that agent in the subsequent valid-candidate list.

- [ ] **Step 6: Verify GREEN**

Run: `venv/bin/python -m pytest tests/ai/test_sub_agent_delegation.py tests/ai/test_agent_roster.py tests/ai/test_tool_nudge_policy.py -q`

Expected: all selected tests pass.

### Task 4: Replace the free-form primary capability UI

**Files:**
- Modify: `frontend/src/api/agent.ts`
- Modify: `frontend/src/views/AgentManagement.vue`
- Create: `tests/frontend/test_agent_type_form_contract.py`

- [ ] **Step 1: Write the failing frontend contract test**

```python
def test_agent_form_uses_fixed_primary_types_and_locked_capabilities():
    source = Path("frontend/src/views/AgentManagement.vue").read_text()
    assert "AGENT_TYPE_OPTIONS" in source
    assert 'value: "GENERAL"' in source
    assert 'value: "CHATBI"' in source
    assert 'value: "KNOWLEDGE_BASE"' in source
    assert "lockedCapabilityForType" in source
    assert "输入能力并回车" not in source
```

- [ ] **Step 2: Run the contract test and verify RED**

Run: `venv/bin/python -m pytest tests/frontend/test_agent_type_form_contract.py -q`

Expected: failure because fixed type options do not exist and free-form input remains.

- [ ] **Step 3: Add typed frontend contracts**

```typescript
export type AgentType = 'GENERAL' | 'CHATBI' | 'KNOWLEDGE_BASE'

export interface AgentReadiness {
  ready: boolean
  missing: string[]
}
```

Add `agent_type: AgentType` and optional `readiness` to `AIAgent`/`AIAgentBase`.

- [ ] **Step 4: Implement type cards and locked tag synchronization**

```typescript
const AGENT_TYPE_OPTIONS = [
  { value: "GENERAL", label: "通用助手", capability: "general_chat" },
  { value: "CHATBI", label: "数据分析（ChatBI）", capability: "data_query" },
  { value: "KNOWLEDGE_BASE", label: "知识库助手", capability: "knowledge_base" },
] as const;
```

Default new forms to `GENERAL`; when editing old records, use `agent_type` from the API. Render three selectable cards, a type-specific resource hint, and a collapsed administrator-only advanced capability section. System capability chips are locked; extension chips remain removable.

- [ ] **Step 5: Verify frontend tests and build**

Run: `venv/bin/python -m pytest tests/frontend/test_agent_type_form_contract.py -q`

Run: `npm --prefix frontend run build`

Expected: contract passes and Vite build completes successfully.

### Task 5: Surface readiness and finish regression validation

**Files:**
- Modify: `app/services/ai/agent_manager.py`
- Modify: `frontend/src/views/AgentManagement.vue`
- Modify: `tests/services/ai/test_agent_manager.py`
- Modify: `tests/CHECKLIST.md`

- [ ] **Step 1: Write failing response/readiness summary tests**

Assert list responses distinguish `ready`, `missing`, and an agent without a published version. Verify the Vue source renders “已就绪” and “尚未就绪”.

- [ ] **Step 2: Run tests and verify RED**

Run: `venv/bin/python -m pytest tests/services/ai/test_agent_manager.py tests/frontend/test_agent_type_form_contract.py -q`

Expected: readiness fields and labels are absent.

- [ ] **Step 3: Add readiness summaries to agent list responses**

Compute readiness from the already-loaded latest published-version map to avoid N+1 queries. Render a compact status badge and missing-item message in the management UI.

- [ ] **Step 4: Run the combined focused suite**

Run:

```bash
venv/bin/python -m pytest \
  tests/ai/test_agent_types.py \
  tests/ai/test_agent_readiness.py \
  tests/services/ai/test_agent_manager.py \
  tests/ai/test_sub_agent_delegation.py \
  tests/ai/test_agent_roster.py \
  tests/ai/test_tool_nudge_policy.py \
  tests/frontend/test_agent_type_form_contract.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Run final static checks**

Run: `npm --prefix frontend run build`

Run: `git diff --check`

Expected: frontend build succeeds and `git diff --check` returns no output.

- [ ] **Step 6: Update verification checklist**

Add the exact focused pytest count, frontend build result, and `git diff --check` result to `tests/CHECKLIST.md`. Do not stage or commit unless explicitly requested by the user.

### Task 6: Atomically create an agent with an initial template snapshot

**Files:**
- Modify: `app/models/agent.py`
- Modify: `app/schemas/agent.py`
- Modify: `app/services/ai/agent_manager.py`
- Modify: `app/api/portal/endpoints/agents.py`
- Modify: `db-prod/V103-add-agent-primary-type.sql`
- Test: `tests/services/ai/test_agent_onboarding.py`

- [ ] **Step 1: Write failing tests for snapshot creation and idempotency**

Create an onboarding request with `onboarding_key="key-1"`; assert one `AIAgent` and one V1 `DRAFT` are added, the reference template is copied, and a repeated request returns the existing pair without adding records.

- [ ] **Step 2: Run the onboarding tests and verify RED**

Run: `venv/bin/python -m pytest tests/services/ai/test_agent_onboarding.py -q`

Expected: imports fail because onboarding schemas and service do not exist.

- [ ] **Step 3: Implement the atomic onboarding service**

Add `onboarding_key` and `onboarding_step` to `AIAgent`. Resolve reference slugs with `GENERAL -> main`, `CHATBI -> chat-bi`, `KNOWLEDGE_BASE -> knowledge-base`; copy the latest published version into the new agent's V1 draft. If no template is loadable, use the platform default model, a type-specific safe prompt, and the necessary default tools. Commit only after both records are added.

- [ ] **Step 4: Expose `POST /api/portal/agents/onboarding`**

Accept `AgentOnboardingCreateRequest` and return `AgentOnboardingResponse(agent, version, onboarding_step, template_fallback)`.

- [ ] **Step 5: Verify GREEN**

Run: `venv/bin/python -m pytest tests/services/ai/test_agent_onboarding.py tests/services/ai/test_agent_manager.py -q`

Expected: all selected tests pass.

### Task 7: Extend the version editor into the resumable five-step creation wizard

**Files:**
- Modify: `frontend/src/api/agent.ts`
- Modify: `frontend/src/views/AgentManagement.vue`
- Modify: `frontend/src/components/agent/AgentVersionEditorDrawer.vue`
- Modify: `app/services/ai/agent_manager.py`
- Test: `tests/frontend/test_agent_type_form_contract.py`
- Test: `tests/services/ai/test_agent_onboarding.py`

- [ ] **Step 1: Write failing unified-drawer contract tests**

Assert the version drawer supports an optional `agent` step before `model`, renders the three fixed type choices, and emits agent-form updates. Assert AgentManagement opens this drawer directly for a new agent, keeps existing version creation on `model`, supports draft exit/resume, and the API client exposes `createAgentOnboarding`.

- [ ] **Step 2: Run tests and verify RED**

Run: `venv/bin/python -m pytest tests/frontend/test_agent_type_form_contract.py -q`

Expected: the drawer has only the four version steps and the unified creation symbols are absent.

- [ ] **Step 3: Add the optional agent-information step to the existing drawer**

Add an `agent` step and `agentForm` props to `AgentVersionEditorDrawer`. Render it only for first-time creation. Keep the original four steps unchanged for existing agents. Extend the review page with the primary type and display name.

- [ ] **Step 4: Route new-agent creation directly into the unified drawer**

Remove the separate three-step onboarding markup from AgentManagement. New-agent actions initialize agent/version forms and open `AgentVersionEditorDrawer` at `agent`; existing version actions still start at `model`. Final confirmation calls the atomic onboarding endpoint with both forms, then updates the returned V1 when necessary.

- [ ] **Step 5: Implement save-on-close and resume from the agent card**

When closing an unsaved new flow, create the agent and V1 draft before closing. Show “继续配置” when `onboarding_step != COMPLETE`; load the V1 draft and reopen the unified drawer at the earliest incomplete version step. Publishing sets `onboarding_step=COMPLETE`.

- [ ] **Step 6: Keep primary type and onboarding fields in one migration**

Keep `agent_type`, `onboarding_key`, `onboarding_step`, and the owner-scoped unique key together in V103. Do not create a separate V104 migration.

- [ ] **Step 7: Verify focused contracts and final checks**

Run the onboarding backend tests, frontend contract test, full focused 77-test slice, Python compile, and `git diff --check`. Record the new counts in `tests/CHECKLIST.md`.

### Task 8: Make first-time creation engine-first and adaptive

**Files:**
- Modify: `frontend/src/views/AgentManagement.vue`
- Modify: `frontend/src/components/agent/AgentVersionEditorDrawer.vue`
- Modify: `tests/frontend/test_agent_type_form_contract.py`

- [ ] **Step 1: Write failing engine-flow contract tests**

Assert engine selection precedes type selection; type and extension tags are LOCAL-only; external engines expose read-only built-in capabilities; the parent step list collapses to `agent` for RAGFlow/OpenClaw; and external creation uses `createAgent` rather than onboarding/V1 creation.

- [ ] **Step 2: Verify RED**

Run `venv/bin/python -m pytest tests/frontend/test_agent_type_form_contract.py -q` and confirm the adaptive-flow assertions fail.

- [ ] **Step 3: Implement engine-first drawer UX**

Move engine cards to the top. Selecting an external engine resets `agent_type=GENERAL` and `capabilities=['general_chat']`, hides type/extension controls, shows engine-specific configuration and read-only capability badges, and makes the current page the only step.

- [ ] **Step 4: Split persistence by engine**

LOCAL uses `createAgentOnboarding` and V1 update. RAGFlow/OpenClaw validate required engine fields and call the compatible `createAgent` endpoint without creating a local version. Close/save uses the same branch and remains duplicate-submit guarded.

- [ ] **Step 5: Verify regression**

Run the focused Python suite, parse both Vue SFCs, inspect relevant `vue-tsc` output, and run `git diff --check`.
