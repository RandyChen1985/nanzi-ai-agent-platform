# Personal Business Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a permission-safe personal business workbench that aggregates real attention items, results, resumable work, available agents, and scenario templates into active, quiet, or new-user states.

**Architecture:** Add a read-only `WorkbenchHomeService` with isolated source loaders and a pure payload composer, expose it through one Portal endpoint, and render the response with focused Vue components. Existing task, notification, report, conversation, agent, and scenario modules remain the owners of business actions; the workbench only normalizes and navigates.

**Tech Stack:** FastAPI, SQLAlchemy async ORM, Pydantic/standard Portal response wrapper, Vue 3 Composition API, TypeScript, Tailwind CSS, pytest contract and unit tests.

---

## File map

- Create `app/services/workbench_home_service.py`: isolated source loading, normalization, deduplication, mode selection, and partial-failure composition.
- Create `app/api/portal/endpoints/workbench.py`: authenticated `GET /home` endpoint.
- Modify `app/api/portal/api.py`: register `/workbench` router.
- Create `tests/services/test_workbench_home_service.py`: pure composition, permissions-adjacent normalization, ordering, deduplication, and failure tests.
- Create `tests/api/portal/test_workbench_home_api.py`: endpoint user-context contract.
- Create `frontend/src/types/workbench.ts`: response and normalized item types.
- Create `frontend/src/composables/useWorkbenchHome.ts`: load, refresh, stale-data retention, and error state.
- Create `frontend/src/components/workbench/WorkbenchAttention.vue`: attention and healthy-state surface.
- Create `frontend/src/components/workbench/WorkbenchResults.vue`: latest result cards.
- Create `frontend/src/components/workbench/WorkbenchResume.vue`: resumable work cards.
- Create `frontend/src/components/workbench/WorkbenchAgents.vue`: available agent shortcuts.
- Create `frontend/src/components/workbench/WorkbenchScenarios.vue`: new-user scenario onboarding.
- Create `frontend/src/views/PersonalWorkbench.vue`: page layout, three modes, refresh, and action dispatch.
- Modify `frontend/src/router/index.ts`: add workbench route.
- Modify `frontend/src/views/Dashboard.vue`: add navigation entry and ordinary-user default handling without replacing admin overview.
- Create `tests/frontend/test_personal_workbench_contract.py`: route, state, component, and action-navigation contracts.
- Modify `tests/CHECKLIST.md`: record focused verification evidence.

No commit is performed automatically. The repository already contains unrelated in-progress changes; git finalization remains user-controlled.

### Task 1: Pure workbench payload contract

**Files:**
- Create: `tests/services/test_workbench_home_service.py`
- Create: `app/services/workbench_home_service.py`

- [ ] **Step 1: Write failing tests for active, quiet, and new-user modes**

Create fixtures with `SimpleNamespace` and assert the public composer:

```python
from datetime import datetime
from types import SimpleNamespace

from app.services.workbench_home_service import build_workbench_payload


def test_active_mode_prioritizes_attention_and_deduplicates_report_delivery():
    payload = build_workbench_payload(
        now=datetime(2026, 7, 18, 10, 30),
        notifications=[SimpleNamespace(id=1, title="库存巡检失败", notification_type="task_failed", created_at=datetime(2026, 7, 18, 9, 12), is_read=False, payload={"task_id": 17, "run_id": 3})],
        task_items=[],
        report_items=[{"id": "digest:8", "business_key": "report-run:9", "type": "digest", "title": "经营日报", "occurred_at": "2026-07-18T09:30:00", "action": "open_digest", "target": {"report_id": "r1", "run_id": 9}}],
        conversation_items=[],
        agent_items=[],
        scenario_items=[],
        source_status={},
    )
    assert payload["mode"] == "active"
    assert payload["attention"][0]["action"] == "open_task_log"
    assert payload["latest_results"][0]["business_key"] == "report-run:9"


def test_quiet_mode_keeps_resumable_work_without_zero_cards():
    payload = build_workbench_payload(
        now=datetime(2026, 7, 18, 10, 30), notifications=[], task_items=[], report_items=[],
        conversation_items=[{"id": "conversation:c1", "type": "conversation", "title": "费用趋势", "occurred_at": "2026-07-17T16:20:00", "action": "open_conversation", "target": {"conversation_id": "c1"}}],
        agent_items=[], scenario_items=[], source_status={},
    )
    assert payload["mode"] == "quiet"
    assert payload["attention"] == []
    assert payload["resume_items"][0]["target"]["conversation_id"] == "c1"


def test_new_user_mode_uses_authorized_scenarios():
    payload = build_workbench_payload(
        now=datetime(2026, 7, 18, 10, 30), notifications=[], task_items=[], report_items=[], conversation_items=[], agent_items=[],
        scenario_items=[{"id": "finance-expense-analysis", "name": "财务费用分析助手", "description": "查看费用和预算", "available": True}],
        source_status={},
    )
    assert payload["mode"] == "new_user"
    assert payload["recommended_scenarios"][0]["id"] == "finance-expense-analysis"
```

- [ ] **Step 2: Run the focused tests and confirm RED**

Run:

```bash
REDIS_ENABLE=false venv/bin/python -m pytest tests/services/test_workbench_home_service.py -q
```

Expected: collection fails because `app.services.workbench_home_service` does not exist.

- [ ] **Step 3: Implement normalized types as dictionaries and the pure composer**

Implement `build_workbench_payload(...)` with these rules:

```python
def build_workbench_payload(*, now, notifications, task_items, report_items,
                            conversation_items, agent_items, scenario_items,
                            source_status):
    attention = normalize_notifications(notifications) + [item for item in task_items if item.get("needs_attention")]
    attention = _sort_limit(_dedupe(attention), limit=5, severity_first=True)
    latest_results = _sort_limit(_dedupe(report_items), limit=6)
    resume_items = _sort_limit(_dedupe(conversation_items), limit=4)
    has_history = bool(attention or latest_results or resume_items)
    mode = "active" if attention else ("quiet" if has_history else "new_user")
    return {
        "mode": mode,
        "attention": attention,
        "latest_results": latest_results,
        "resume_items": resume_items,
        "favorite_agents": agent_items[:6],
        "recommended_scenarios": [item for item in scenario_items if item.get("available", True)][:4],
        "next_scheduled_item": _next_scheduled(task_items),
        "source_status": _complete_source_status(source_status),
        "generated_at": now.isoformat(),
    }
```

Use `business_key` before `id` for deduplication. Normalize unread task-failure notifications to `open_task_log`; ignore read informational notifications in `attention`.

- [ ] **Step 4: Add ordering, cap, and partial-source tests**

Assert severity order `critical > warning > info`, newest-first ordering within one severity, list caps `5/6/4/6/4`, and default `source_status` keys for notifications, tasks, reports, conversations, agents, and scenarios.

- [ ] **Step 5: Run the service tests and confirm GREEN**

Run the same pytest command. Expected: all tests pass.

### Task 2: Permission-safe source loaders and Portal endpoint

**Files:**
- Modify: `app/services/workbench_home_service.py`
- Create: `app/api/portal/endpoints/workbench.py`
- Modify: `app/api/portal/api.py`
- Create: `tests/api/portal/test_workbench_home_api.py`
- Modify: `tests/services/test_workbench_home_service.py`

- [ ] **Step 1: Write failing endpoint contract test**

```python
@pytest.mark.asyncio
async def test_workbench_home_passes_current_user_and_roles(monkeypatch):
    build = AsyncMock(return_value={"mode": "new_user"})
    monkeypatch.setattr(workbench, "get_user_role_ids", AsyncMock(return_value=[2, 3]))
    monkeypatch.setattr(workbench.WorkbenchHomeService, "build", build)
    response = await workbench.get_workbench_home(
        user_info={"user_id": "7", "user_name": "alice", "role": "user"},
        db=AsyncMock(),
    )
    assert response.data["mode"] == "new_user"
    assert build.await_args.kwargs["user_id"] == 7
    assert build.await_args.kwargs["role_ids"] == [2, 3]
```

- [ ] **Step 2: Run endpoint test and confirm RED**

Run `REDIS_ENABLE=false venv/bin/python -m pytest tests/api/portal/test_workbench_home_api.py -q`.

- [ ] **Step 3: Add the endpoint and router registration**

Expose `GET /api/portal/workbench/home`, use `require_api_key` at router registration, resolve role ids with the existing saved-report helper, and return `StandardResponse(data=payload)` following `data_portal.py`.

- [ ] **Step 4: Implement isolated source loading**

In `WorkbenchHomeService.build`, execute source functions independently:

```python
sources = {
    "notifications": lambda: _load_notifications(db, user_id),
    "tasks": lambda: _load_tasks(db, user_id),
    "reports": lambda: _load_report_activity(db, user_id, role_ids, now),
    "conversations": lambda: _load_conversations(db, user_id),
    "agents": lambda: _load_agents(db, user),
    "scenarios": _load_scenarios,
}
```

Each loader is wrapped by `_safe_load(name, loader)` and returns `([], "error")` on exception while logging only server-side details. Reuse `PortalNotificationService.list_for_user`, current task models, `DataPortalHomeService.build`, `AgentManagerService.list_agents`, and `ScenarioTemplateService.list_templates`; do not duplicate their permission rules.

- [ ] **Step 5: Test one-source failure isolation**

Monkeypatch the notifications loader to raise and other loaders to return data. Assert HTTP response remains successful, `source_status.notifications == "error"`, and other data remains present.

- [ ] **Step 6: Run backend focused tests**

```bash
REDIS_ENABLE=false venv/bin/python -m pytest tests/services/test_workbench_home_service.py tests/api/portal/test_workbench_home_api.py tests/api/portal/test_data_portal_home_api.py -q
```

Expected: all pass.

### Task 3: Frontend types and stale-safe composable

**Files:**
- Create: `frontend/src/types/workbench.ts`
- Create: `frontend/src/composables/useWorkbenchHome.ts`
- Create: `tests/frontend/test_personal_workbench_contract.py`

- [ ] **Step 1: Write failing source contract tests**

Assert the composable calls `"/api/portal/workbench/home"`, retains `payload.value` on refresh failure, exposes `loading`, `error`, `refresh`, and uses neither fabricated counts nor multiple business endpoint calls.

- [ ] **Step 2: Run contract test and confirm RED**

Run `venv/bin/python -m pytest tests/frontend/test_personal_workbench_contract.py -q`.

- [ ] **Step 3: Define exact TypeScript contracts**

Define `WorkbenchMode`, `WorkbenchSourceState`, `WorkbenchItem`, `WorkbenchAgent`, `WorkbenchScenario`, and `WorkbenchHomePayload`. `WorkbenchItem.target` allows only known optional ids: `task_id`, `run_id`, `report_id`, `conversation_id`, and `agent_id`.

- [ ] **Step 4: Implement the composable**

```ts
const payload = ref<WorkbenchHomePayload | null>(null)
const loading = ref(false)
const error = ref("")

async function load() {
  loading.value = true
  error.value = ""
  try {
    const response = await axios.get("/api/portal/workbench/home")
    payload.value = response.data?.data
  } catch {
    error.value = "工作台暂时无法更新，已保留最近一次成功内容。"
  } finally {
    loading.value = false
  }
}
```

- [ ] **Step 5: Run contract tests and confirm GREEN**

Run the same pytest command. Expected: all assertions pass.

### Task 4: Focused workbench components and dynamic states

**Files:**
- Create: `frontend/src/components/workbench/WorkbenchAttention.vue`
- Create: `frontend/src/components/workbench/WorkbenchResults.vue`
- Create: `frontend/src/components/workbench/WorkbenchResume.vue`
- Create: `frontend/src/components/workbench/WorkbenchAgents.vue`
- Create: `frontend/src/components/workbench/WorkbenchScenarios.vue`
- Create: `frontend/src/views/PersonalWorkbench.vue`
- Modify: `tests/frontend/test_personal_workbench_contract.py`

- [ ] **Step 1: Add failing tests for the three visible modes**

Assert `PersonalWorkbench.vue` branches on `active`, `quiet`, and `new_user`; contains “今日运行正常” only for quiet mode; new-user content contains no numeric zero summary; and each section is delegated to a focused component.

- [ ] **Step 2: Run contract test and confirm RED**

Run the focused frontend pytest file.

- [ ] **Step 3: Implement the page shell**

Use the existing Dashboard content styling, skeleton only when there is no prior payload, and an inline stale-data warning when refresh fails. Preserve the payload during background refresh.

- [ ] **Step 4: Implement active and quiet components**

`WorkbenchAttention` emits `open-item`; it renders up to five attention items or the compact healthy-state row. `WorkbenchResults` and `WorkbenchResume` render only when their arrays contain items, so empty regions do not reserve space.

- [ ] **Step 5: Implement new-user onboarding**

`WorkbenchScenarios` renders up to four available templates, then authorized agents. If both arrays are empty, show “当前还没有可用的业务场景，请联系管理员开通智能体或相关资源。” Do not show mocked sample metrics.

- [ ] **Step 6: Implement responsive behavior**

Use one-column action cards on mobile, two-column content grids from `md`, and two-column scenario cards on small screens. Avoid tables and fixed widths.

- [ ] **Step 7: Run contract and Vue parse checks**

Run:

```bash
venv/bin/python -m pytest tests/frontend/test_personal_workbench_contract.py -q
```

Then parse the six Vue SFCs using the repository's existing Vue compiler contract helper or the same Node parser used by adjacent frontend tests. Expected: no parse errors.

### Task 5: Navigation and action closure

**Files:**
- Modify: `frontend/src/router/index.ts`
- Modify: `frontend/src/views/Dashboard.vue`
- Modify: `frontend/src/views/PersonalWorkbench.vue`
- Modify: `tests/frontend/test_personal_workbench_contract.py`

- [ ] **Step 1: Add failing route and action tests**

Assert route `path: 'workbench'`, name `PersonalWorkbench`, Dashboard label “我的工作台”, and mappings for `open_task_log`, `open_digest`, `open_report`, `open_conversation`, `open_agent`, and `open_scenario`.

- [ ] **Step 2: Add route and role-aware entry**

Register `/dashboard/workbench`. Add it as the first ordinary-user navigation item. Keep `Overview` and `/dashboard` unchanged for administrators; redirect ordinary authenticated users visiting the bare dashboard to workbench through a role-aware guard or Dashboard entry logic without remount loops.

- [ ] **Step 3: Implement action mapping**

Map normalized actions to existing destinations:

```ts
const actionRoutes = {
  open_task_log: () => ({ path: "/dashboard/tasks", query: { task_id, run_id } }),
  open_digest: () => ({ path: "/dashboard/personal", query: { tab: "data", report_id, run_id, delivery: "1" } }),
  open_report: () => ({ path: "/dashboard/personal", query: { tab: "data", report_id, run_id } }),
  open_conversation: () => ({ path: "/dashboard/chat", query: { conversation_id } }),
  open_agent: () => ({ path: "/dashboard/chat", query: { agent_id } }),
  open_scenario: () => ({ path: `/dashboard/scenario-templates/${scenarioId}` }),
}
```

Before finalizing mappings, verify the target pages' actual query contracts and adapt the mapper to those existing names; do not change target page contracts unless a focused regression test proves navigation cannot close otherwise.

- [ ] **Step 4: Preserve return behavior**

Use router navigation instead of full reloads. Preserve workbench data in the mounted component while navigating within Dashboard; browser back returns to the prior scroll position where supported by the current router.

- [ ] **Step 5: Run frontend contracts**

Run the focused workbench contract plus existing data-portal and PersonalCenter navigation contract tests.

### Task 6: Regression, checklist, and handoff

**Files:**
- Modify: `tests/CHECKLIST.md`
- Review all files from Tasks 1-5.

- [ ] **Step 1: Run backend regression slice**

```bash
REDIS_ENABLE=false venv/bin/python -m pytest \
  tests/services/test_workbench_home_service.py \
  tests/api/portal/test_workbench_home_api.py \
  tests/api/portal/test_data_portal_home_api.py \
  tests/api/portal/test_scenario_templates.py -q
```

- [ ] **Step 2: Run frontend contract slice**

```bash
venv/bin/python -m pytest \
  tests/frontend/test_personal_workbench_contract.py \
  tests/frontend/test_data_portal_home_contract.py \
  tests/frontend/test_scenario_templates_contract.py -q
```

- [ ] **Step 3: Run static hygiene checks**

```bash
git diff --check
```

Inspect `git diff --stat` and ensure no unrelated user changes were altered. Do not run `./dev.sh` or frontend build/start scripts; the repository instructions reserve service compilation and restart for the user.

- [ ] **Step 4: Update the checklist with actual evidence**

Add one row describing the workbench aggregation, dynamic states, permission isolation, navigation closure, exact passing test count, and Vue parse evidence. Do not claim a frontend build result because it is intentionally not run.

- [ ] **Step 5: Hand off for manual testing**

Report the implemented routes, data sources, automated test evidence, known second-phase exclusions, and ask the user to run the service manually from their console. Do not stage or commit unless explicitly requested.

