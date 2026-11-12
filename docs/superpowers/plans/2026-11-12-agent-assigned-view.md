# 智能体中心「分配给我」只读视图 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让被管理员分配了系统级智能体的普通用户，在智能体中心看到一个常驻的「分配给我」只读 tab，能进入对话但改不了配置。

**Architecture:** 后端新增一个专用只读接口 `GET /api/portal/agents/assigned`，口径复用 `PermissionService.get_user_permissions().permissions.agents`（与 `_user_can_execute_agent` 同源），三个只读字段在服务层定死。前端在智能体中心加一个独立 tab + 独立数据源，只读卡片只保留「进入对话」，不并入 `agents.value`（避免污染管理台的计数、分组页签与筛选器）。

**Tech Stack:** FastAPI + SQLAlchemy 2.x async + Pydantic 2；Vue 3 + TypeScript + Tailwind；pytest（后端）、`pytest --confcutdir=tests/frontend`（前端契约）、`vue-tsc --noEmit`。

**参考文档：** 设计 `docs/superpowers/specs/2026-11-12-agent-assigned-view-design.md`、原型 `prototypes/2026-11-12-agent-assigned-view.html`

**环境说明：** Python 用仓库内 `.venv/bin/python`（3.11）。`python3` 会解析到 3.13 且没装 pytest。

**提交说明：** 仓库规范禁止 Agent 自行 `git commit`，每个 Task 末尾的提交命令由用户执行。

---

## 文件结构

| 文件 | 职责 |
| --- | --- |
| `app/services/ai/agent_manager.py` | 新增 `list_assigned_agents()`：口径 + 只读字段的唯一来源 |
| `app/api/portal/endpoints/agents.py` | 新增 `GET /assigned` 路由（必须在 `/{agent_id}` 系列之前） |
| `frontend/src/api/agent.ts` | `agentApi.listAssignedAgents()` |
| `frontend/src/views/AgentManagement.vue` | tab、独立数据源、只读面板、`openAssignedChat()` |
| `tests/api/portal/test_agent_assigned_list.py` | 后端口径与只读断言（不依赖 DB） |
| `tests/frontend/test_agent_assigned_view_contract.py` | 前端只读边界契约 |

---

### Task 1: 服务层 `list_assigned_agents`

**Files:**
- Test: `tests/api/portal/test_agent_assigned_list.py`（新建）
- Modify: `app/services/ai/agent_manager.py`（在 `list_allowed_agents` 之后插入，约 `:1155`）

- [ ] **Step 1: 写失败的测试**

新建 `tests/api/portal/test_agent_assigned_list.py`。测试**不连数据库**：SQL 过滤口径用编译后的语句断言，只读字段与管理员短路用假 session 断言。

```python
"""「分配给我」列表的口径与只读边界（不依赖真实 DB / Redis）。

过滤发生在 SQL 里，假 session 不会真的筛行，所以：
- 可见性口径用「编译后的 SQL 片段」断言（created_by / is_enabled / 授权 id 集合）；
- 只读三字段与管理员短路用假 session 里预置的行断言（这两段在 Python 侧）。
"""

from types import SimpleNamespace

import pytest

from app.services.ai.agent_manager import AgentManagerService

pytestmark = pytest.mark.no_infrastructure


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class _FakeSession:
    """只记录语句、返回固定行；不解析 SQL。"""

    def __init__(self, rows):
        self._rows = rows
        self.statements = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        return _FakeResult(self._rows)


class _FakePerms:
    def __init__(self, agents):
        self.permissions = SimpleNamespace(agents=agents)


def _agent(**overrides):
    base = dict(
        id="a-chatbi",
        name="chatbi",
        display_name="ChatBI 数据智能助手",
        description="用自然语言查数据",
        is_system=True,
        is_enabled=True,
        created_by="zhangqiming",
        is_editable=True,
        can_edit_versions=True,
        can_publish_version=True,
        sort_order=0,
        is_expert_team_lead=False,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _patch_perms(monkeypatch, agents):
    """把权限服务换成固定集合，避免连库。"""

    class _FakePermissionService:
        def __init__(self, _db):
            pass

        async def get_user_permissions(self, _uid):
            return _FakePerms(agents)

    monkeypatch.setattr(
        "app.services.permission_service.PermissionService", _FakePermissionService
    )


async def test_admin_gets_empty_list(monkeypatch):
    """管理员已有「全部智能体」，这里返回空，避免重复视图。"""
    _patch_perms(monkeypatch, ["a-chatbi"])
    session = _FakeSession([_agent()])
    result = await AgentManagerService.list_assigned_agents(
        session, user={"role": "admin", "user_name": "admin", "user_id": 1}
    )
    assert result == []
    assert session.statements == []


async def test_no_granted_agents_returns_empty_without_query(monkeypatch):
    """没有任何智能体授权时不查库（避免每次进页面白跑一条 SQL）。"""
    _patch_perms(monkeypatch, [])
    session = _FakeSession([])
    result = await AgentManagerService.list_assigned_agents(
        session, user={"role": "user", "user_name": "chenxiaolong", "user_id": 7}
    )
    assert result == []
    assert session.statements == []


async def test_visibility_clause_excludes_self_and_disabled(monkeypatch):
    """口径：授权集合 + 启用 + 非自己创建（含 created_by 为空的历史数据）。"""
    _patch_perms(monkeypatch, ["a-chatbi", "a-contract"])
    session = _FakeSession([])
    await AgentManagerService.list_assigned_agents(
        session, user={"role": "user", "user_name": "chenxiaolong", "user_id": 7}
    )
    assert len(session.statements) == 1
    sql = str(session.statements[0].compile(compile_kwargs={"literal_binds": True}))
    # 三个口径同时生效
    assert "ai_agents.is_enabled = true" in sql
    assert "ai_agents.created_by IS NULL" in sql
    assert "ai_agents.created_by != 'chenxiaolong'" in sql
    # 授权 id 集合真的进了 IN
    assert "a-chatbi" in sql and "a-contract" in sql


async def test_returns_readonly_flags(monkeypatch):
    """三个只读字段由服务端定死——AIAgentResponse.is_editable 默认 True。"""
    _patch_perms(monkeypatch, ["a-chatbi"])
    session = _FakeSession([_agent()])
    result = await AgentManagerService.list_assigned_agents(
        session, user={"role": "user", "user_name": "chenxiaolong", "user_id": 7}
    )
    assert len(result) == 1
    agent = result[0]
    assert agent.is_editable is False
    assert agent.can_edit_versions is False
    assert agent.can_publish_version is False


async def test_non_numeric_user_id_degrades_to_empty(monkeypatch):
    """user_id 不是数字（历史脏数据）时按无授权处理，不抛异常。"""
    session = _FakeSession([_agent()])
    result = await AgentManagerService.list_assigned_agents(
        session, user={"role": "user", "user_name": "chenxiaolong", "user_id": "not-a-number"}
    )
    assert result == []
    assert session.statements == []
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/api/portal/test_agent_assigned_list.py -x -q
```

期望：`AttributeError: type object 'AgentManagerService' has no attribute 'list_assigned_agents'`（5 条全失败）。

- [ ] **Step 3: 实现服务方法**

在 `app/services/ai/agent_manager.py` 的 `list_allowed_agents` 方法结束后（`return list((await session.execute(stmt)).scalars().all())` 之后、`@classmethod attach_team_metadata` 之前）插入：

```python
    @staticmethod
    async def list_assigned_agents(
        session: AsyncSession,
        user: Any = None,
    ) -> List[Any]:
        """他人创建、经管理员授权、且当前启用的智能体，供「分配给我」只读视图使用。

        口径与 ``_user_can_execute_agent`` **同源**：两者都取
        ``PermissionService.get_user_permissions().permissions.agents``。
        刻意不另写一套 SQL 判授权——「能用」与「看得见」一旦分叉，就会出现
        「对话页选得到、智能体中心看不到」这种问题（本功能正是为此而加）。

        与 ``list_agents`` 的三点区别：
        1. 不返回自己创建的（那些在「我的智能体」里，重复出现只会让人以为有两份）；
        2. 只返回已启用的（停用的智能体执行时会被 ``resolve_embed_agent_access``
           直接 404，列出来只会让人以为系统坏了）；
        3. 三个可编辑字段在服务端定死为 False，不交给前端判断。

        管理员返回空列表：他已有「全部智能体」，再来一份「分配给我的」是重复视图。
        """
        if not user:
            return []

        is_admin, username, user_id = AgentManagerService._extract_user_identity(user)
        if is_admin:
            return []

        from app.services.permission_service import PermissionService

        perm_service = PermissionService(session)
        try:
            uid_int = int(user_id)
            perms = await perm_service.get_user_permissions(uid_int)
            allowed_ids = list(perms.permissions.agents or [])
        except (ValueError, TypeError):
            # 与 list_allowed_agents 同一套容错：身份脏数据按「无授权」处理，不 500
            allowed_ids = []

        if not allowed_ids:
            return []

        stmt = select(AIAgent).where(
            AIAgent.id.in_(allowed_ids),
            AIAgent.is_enabled == True,  # noqa: E712 — 与同文件既有写法保持一致
            # created_by 为空的历史数据在 list_agents 里同样不出现，
            # 属「有授权但看不见」，必须收进来
            or_(AIAgent.created_by != username, AIAgent.created_by.is_(None)),
        )

        main_first = case(
            (or_(AIAgent.id == MAIN_GENERAL_AGENT_ID, AIAgent.name.in_(MAIN_GENERAL_AGENT_NAMES)), 0),
            else_=1,
        )
        stmt = stmt.order_by(
            main_first,
            AIAgent.sort_order.desc(),
            AIAgent.is_system.desc(),
            AIAgent.display_name,
        )

        agents = list((await session.execute(stmt)).scalars().all())

        for agent in agents:
            # AIAgentResponse.is_editable 默认值是 True —— 默认值就是「可编辑」，
            # 所以这三行是安全边界，不是装饰。漏算任何一处都等于放行。
            agent.is_editable = False
            agent.can_edit_versions = False
            agent.can_publish_version = False

        return agents
```

- [ ] **Step 4: 跑测试确认通过**

```bash
.venv/bin/python -m pytest tests/api/portal/test_agent_assigned_list.py -q
```

期望：`5 passed`。

- [ ] **Step 5: 提交（由用户执行）**

```bash
git add app/services/ai/agent_manager.py tests/api/portal/test_agent_assigned_list.py
git commit -m "feat(agent): 新增 list_assigned_agents 只读口径"
```

---

### Task 2: 端点 `GET /agents/assigned`

**Files:**
- Test: `tests/api/portal/test_agent_assigned_list.py`（追加 1 条路由顺序契约）
- Modify: `app/api/portal/endpoints/agents.py`（在 `list_allowed_agents` 之后、`/{agent_id}/embed-access` 之前）

- [ ] **Step 1: 追加失败的路由顺序测试**

在 `tests/api/portal/test_agent_assigned_list.py` 末尾追加：

```python
def test_assigned_route_registered_before_agent_id_routes():
    """`/assigned` 必须注册在 `/{agent_id}` 之前。

    FastAPI 按注册顺序匹配：放在后面的话 `GET /agents/assigned` 会被
    `/{agent_id}` 系列当成 agent_id="assigned" 吃掉。本仓库在 `/shares/*`
    与 `transfer-owner/candidates` 上已经踩过两次。
    """
    from pathlib import Path

    src = Path("app/api/portal/endpoints/agents.py").read_text(encoding="utf-8")
    assert '@router.get("/assigned"' in src
    assert src.index('@router.get("/assigned"') < src.index('@router.get("/{agent_id}/embed-access"')
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/api/portal/test_agent_assigned_list.py::test_assigned_route_registered_before_agent_id_routes -q
```

期望：`AssertionError`（`/assigned` 还不存在）。

- [ ] **Step 3: 实现端点**

在 `app/api/portal/endpoints/agents.py` 中，`list_allowed_agents` 函数结束之后（`@router.get("/{agent_id}/embed-access"` 之前）插入：

```python
@router.get(
    "/assigned",
    response_model=List[AIAgentResponse],
    summary="分配给我的智能体（只读）",
)
async def list_assigned_agents(
    session: AsyncSession = Depends(get_db_session),
    user: Dict[str, Any] = Depends(require_permission("menu", "menu:agent_management")),
):
    """他人创建、管理员授权给我使用、且当前启用的智能体。

    只读视图：``is_editable`` / ``can_edit_versions`` / ``can_publish_version``
    由服务层定死为 False，接口不做二次判定——单一来源才不会漏。

    权限沿用 ``menu:agent_management``（与智能体中心页面一致），不新增权限点。
    """
    return await AgentManagerService.list_assigned_agents(session, user=user)
```

- [ ] **Step 4: 跑全部本文件测试**

```bash
.venv/bin/python -m pytest tests/api/portal/test_agent_assigned_list.py -q
```

期望：`6 passed`。

- [ ] **Step 5: 提交（由用户执行）**

```bash
git add app/api/portal/endpoints/agents.py tests/api/portal/test_agent_assigned_list.py
git commit -m "feat(agent): 新增 GET /agents/assigned 只读接口"
```

---

### Task 3: 前端 API 方法

**Files:**
- Modify: `frontend/src/api/agent.ts`（`listAllowedAgents` 之后）

- [ ] **Step 1: 加方法**

在 `agentApi` 对象的 `listAllowedAgents` 之后插入：

```ts
  /**
   * 「分配给我」：管理员授权我使用、但不是我创建的智能体（服务端保证只读）。
   *
   * 与 `listAllowedAgents` 不是一回事：那个是「我能执行的」，包含我自己创建的，
   * 也不带只读语义；这个专供智能体中心的只读视图。
   */
  listAssignedAgents: () => axios.get<AIAgent[]>('/api/portal/agents/assigned'),
```

- [ ] **Step 2: 类型检查**

```bash
cd frontend && npx vue-tsc --noEmit 2>&1 | tail -3
```

期望：错误数等于改动前的基线（先记下基线数：`npx vue-tsc --noEmit 2>&1 | grep -c "error TS"`）。

- [ ] **Step 3: 提交（由用户执行）**

```bash
git add frontend/src/api/agent.ts
git commit -m "feat(agent): agentApi 增加 listAssignedAgents"
```

---

### Task 4: 前端状态、tab 与「进入对话」

**Files:**
- Modify: `frontend/src/views/AgentManagement.vue`

- [ ] **Step 1: 扩展视图类型**

`:847` 附近：

```ts
type AgentWorkspaceTab = 'all' | 'mine' | 'assigned' | 'received' | 'sent' | 'teams'
```

- [ ] **Step 2: 加独立数据源**

在 `refreshShareCounts` 定义之后（约 `:1193`）插入：

```ts
// 「分配给我」独立数据源：刻意不并进 agents.value —— 那是管理台列表，
// 混入会让 totalAgentCount、业务分组页签、管理筛选器全部算错。
const assignedAgents = ref<AIAgent[]>([])
const assignedAgentsLoaded = ref(false)

/**
 * 拉取被分配的智能体。
 *
 * 失败一律当空列表：这个视图是附加信息，它 403/500 不该让整个智能体中心白屏。
 * 管理员不请求——后端对他恒返回空，前端也不显示该 tab。
 */
const fetchAssignedAgents = async () => {
  if (isAdminViewer.value) {
    assignedAgents.value = []
    assignedAgentsLoaded.value = true
    return
  }
  try {
    const res = await agentApi.listAssignedAgents()
    assignedAgents.value = res.data || []
  } catch (e) {
    console.warn('Failed to load assigned agents', e)
    assignedAgents.value = []
  } finally {
    assignedAgentsLoaded.value = true
  }
}

/** 「分配给我」的关键词过滤：只按名称/描述，不接管理筛选器 */
const filteredAssignedAgents = computed<AIAgent[]>(() => {
  const k = searchKeyword.value.trim().toLowerCase()
  if (!k) return assignedAgents.value
  return assignedAgents.value.filter((a) =>
    [a.name, a.display_name, a.description]
      .filter(Boolean)
      .join(' ')
      .toLowerCase()
      .includes(k)
  )
})
```

- [ ] **Step 3: tab 定义插入「分配给我」**

`:1207-1222` 的 `workspaceTabs` 改为：

```ts
/** 管理员多一个「全部」：他要能看别人名下的智能体；普通用户多一个「分配给我」 */
const workspaceTabs = computed<Array<{ key: AgentWorkspaceTab; label: string; badge: number }>>(
  () => [
    // 「全部 / 我的」不带数字：页头已经写着「共 N 个」，同一屏说两遍是冗余，
    // 而 14 − 11 这个差值才是用户想知道的——摆两个绝对值等于把减法留给用户。
    ...(isAdminViewer.value ? [{ key: 'all' as AgentWorkspaceTab, label: '全部智能体', badge: 0 }] : []),
    { key: 'mine', label: '我的智能体', badge: 0 },
    // 「分配给我」：平台授权我用、不是我创建的。放在「我的」之后、「分享给我」之前——
    // 顺序即语义：我能管的 → 我被给的 → 等我去接的。
    // 管理员不显示：他已有「全部智能体」，再来一份是重复视图。
    ...(isAdminViewer.value
      ? []
      : [{ key: 'assigned' as AgentWorkspaceTab, label: '分配给我', badge: assignedAgents.value.length }]),
    // 分享类 tab 只在真有东西时才带数字（0 没有行动价值，还分心），
    // 且数字是「这个视图有多少条」而非「有几个待处理」——后者会被读成收到了 2 个智能体。
    { key: 'received', label: '分享给我', badge: shareCounts.value.received_total },
    { key: 'sent', label: '我分享的', badge: shareCounts.value.sent_total },
    // 专家团不是独立实体，是「主专家」的另一种聚合视图；放在权属 tab 之后，
    // 默认仍停在「我的智能体」，避免让人以为平台多了个东西要维护。
    { key: 'teams', label: '专家团', badge: teamLeadAgents.value.length },
  ],
)
```

- [ ] **Step 4: 落地白名单（两个地方都要加）**

`resolveInitialTab`（`:1198-1205`）与 `watch` 里的 `allowed`（`:1239`）都要包含 `'assigned'`：

```ts
  if (raw === 'received' || raw === 'sent' || raw === 'mine' || raw === 'all' || raw === 'assigned' || raw === 'teams') {
```

```ts
    const allowed: AgentWorkspaceTab[] = ['received', 'sent', 'mine', 'all', 'assigned', 'teams']
```

- [ ] **Step 5: 初始化时拉一次**

`onMounted`（`:4829`）里 `void refreshShareCounts();` 之后加一行：

```ts
  void fetchAssignedAgents();
```

- [ ] **Step 6: 「进入对话」（不带 preview=1）**

在 `openPreview`（`:3712-3733`）之后插入：

```ts
/**
 * 「分配给我」的进入对话。与 openPreview 同一条取票链路，但**不带 preview=1**：
 * preview=1 是管理者的试跑语义（强制干净临时会话、不写续接指针），
 * 使用者要的是正常对话，应该续接自己的历史。
 *
 * 刻意复制而不复用 openPreview：那是管理台「预览对话」在用的函数，
 * 去掉 preview=1 会让管理员每次预览都续接上次的临时会话。
 */
const openAssignedChat = async (agent: AIAgent) => {
  const agentKey = agent.name || agent.id
  try {
    const res = await axios.post('/api/v1/embed/tickets', {
      agent_id: agentKey,
      expires_in: 300,
    })
    if (res.data?.code === 200 && res.data.data?.ticket) {
      const ticket = res.data.data.ticket
      const url = `/embed/chat?ticket=${encodeURIComponent(ticket)}&agent_id=${encodeURIComponent(agentKey)}&theme=light`
      window.open(url, '_blank')
      return
    }
  } catch (e) {
    console.warn('Failed to generate embed ticket for assigned agent', e)
  }
  // 降级兜底：同样不带 preview=1
  window.open(`/dashboard/chat?agent_id=${encodeURIComponent(agentKey)}`, '_blank')
}
```

- [ ] **Step 7: 搜索框 placeholder 加一支**

`:5034` 附近的 `:placeholder` 三元链改为：

```ts
          :placeholder="
            workspaceTab === 'teams'
              ? '搜索专家团…'
              : workspaceTab === 'assigned'
                ? '搜索分配给我的智能体…'
                : workspaceTab === 'mine' || workspaceTab === 'all'
                  ? '搜索名称或标识符…'
                  : '搜索智能体或分享人…'
          "
```

- [ ] **Step 8: 类型检查**

```bash
cd frontend && npx vue-tsc --noEmit 2>&1 | grep -c "error TS"
```

期望：等于 Task 3 记下的基线数（此时模板还没用到新状态，不应新增错误）。

- [ ] **Step 9: 提交（由用户执行）**

```bash
git add frontend/src/views/AgentManagement.vue
git commit -m "feat(agent): 智能体中心增加「分配给我」视图状态与进入对话"
```

---

### Task 5: 只读面板模板

**Files:**
- Modify: `frontend/src/views/AgentManagement.vue`（`:5442` 的 `<AgentShareReceivedPanel` 之前）

- [ ] **Step 1: 插入面板**

在 `<AgentShareReceivedPanel v-if="workspaceTab === 'received'"` 之前插入。这是**独立**的 `v-if` 面板，不改后面的互斥链：

```html
    <!-- 「分配给我」：平台授权我使用、但不是我创建的智能体，只读。
         独立面板而不复用下面的管理列表：列表区挂着批量操作、分组页签、
         管理筛选器，那些对使用者既无意义也会算错口径。 -->
    <div v-if="workspaceTab === 'assigned'" class="space-y-3">
      <div class="flex items-start gap-2 rounded-xl border border-blue-100 bg-blue-50 px-3 py-2 text-xs leading-relaxed text-blue-800">
        <svg class="mt-0.5 h-4 w-4 shrink-0 text-blue-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" aria-hidden="true">
          <path stroke-linecap="round" stroke-linejoin="round" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
        </svg>
        <span>
          这些智能体由平台分配给你，<b class="font-semibold">可以直接对话，但不能修改配置</b>。想定制请新建自己的智能体。
        </span>
      </div>

      <!-- 空态要说清「为什么是空的」：只说「暂无数据」会让用户拿着空页面去找管理员，
           而管理员那边看到的却是「明明授权了」 -->
      <div
        v-if="assignedAgentsLoaded && !filteredAssignedAgents.length"
        class="rounded-xl border border-dashed border-gray-300 bg-white py-12 text-center"
      >
        <p class="text-sm font-semibold text-gray-700">
          {{ assignedAgents.length ? '没有匹配的智能体' : '还没有智能体被分配给你' }}
        </p>
        <p class="mx-auto mt-1.5 max-w-md text-xs leading-relaxed text-gray-400">
          <template v-if="assignedAgents.length">换个关键词试试。</template>
          <template v-else>
            分配来的智能体可以直接对话，但不能修改配置。<br />
            如果你确认已被授权却看不到，可能是该智能体已被停用。
          </template>
        </p>
      </div>

      <div v-else class="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        <article
          v-for="agent in filteredAssignedAgents"
          :key="agent.id"
          class="flex flex-col rounded-xl border border-gray-200 bg-white shadow-sm transition-colors hover:border-primary/40"
        >
          <div class="flex items-start gap-3 px-4 pt-4">
            <div class="grid h-9 w-9 shrink-0 place-items-center overflow-hidden rounded-lg border border-blue-100 bg-blue-50 text-sm font-semibold text-blue-700">
              <img v-if="agent.avatar_url" :src="agent.avatar_url" :alt="agent.display_name" class="h-full w-full object-cover" />
              <span v-else>{{ (agent.display_name || agent.name || '?').slice(0, 1) }}</span>
            </div>
            <div class="min-w-0 flex-1">
              <p class="truncate text-sm font-bold text-gray-900" :title="agent.display_name">
                {{ agent.display_name || agent.name }}
              </p>
              <div class="mt-1 flex flex-wrap items-center gap-1.5">
                <span class="rounded-full bg-indigo-50 px-2 py-px text-[10px] font-semibold text-indigo-700">平台提供</span>
                <span class="rounded-full bg-amber-50 px-2 py-px text-[10px] font-semibold text-amber-700">只读</span>
              </div>
            </div>
          </div>

          <p class="mt-2 line-clamp-2 min-h-[2.75rem] px-4 text-xs leading-relaxed text-gray-500">
            {{ agent.description || '暂无描述' }}
          </p>

          <!-- 动作区只有一个入口：编辑/复制/删除/版本发布整块不渲染，
               不是置灰——置灰会暗示「你可能有权」，反而引投诉。 -->
          <div class="mt-auto flex items-center justify-between gap-2 border-t border-gray-100 px-4 py-2.5">
            <span class="truncate text-[11px] text-gray-400">只读 · 不可修改配置</span>
            <button
              type="button"
              class="shrink-0 rounded-lg bg-primary px-3 py-1.5 text-xs font-semibold text-white transition-colors hover:bg-primary/90"
              @click="openAssignedChat(agent)"
            >
              进入对话
            </button>
          </div>
        </article>
      </div>
    </div>
```

- [ ] **Step 2: 编译模板**

```bash
cd frontend && node ../tests/frontend/compile_vue_templates.cjs 2>&1 | tail -5
```

期望：`0 problem`（该脚本会编译全部 SFC）。若脚本参数不同，改为任务末尾统一验证。

- [ ] **Step 3: 类型检查**

```bash
cd frontend && npx vue-tsc --noEmit 2>&1 | grep -c "error TS"
```

期望：仍等于基线（若 `AIAgent` 类型缺 `description` 会在这里暴露）。

- [ ] **Step 4: 提交（由用户执行）**

```bash
git add frontend/src/views/AgentManagement.vue
git commit -m "feat(agent): 智能体中心「分配给我」只读卡片"
```

---

### Task 6: 前端只读边界契约测试

**Files:**
- Test: `tests/frontend/test_agent_assigned_view_contract.py`（新建）

- [ ] **Step 1: 写测试**

```python
"""「分配给我」只读视图的前端契约。

源码级断言而不是渲染断言：这个视图的价值全在「哪些入口不存在」，
而「不存在」在源码里最直观——一旦有人加了编辑/复制按钮，这里必须红。
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_MGMT = REPO_ROOT / "frontend/src/views/AgentManagement.vue"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_assigned_tab_exists_and_is_admin_guarded():
    """tab 存在，且对管理员不显示（他已有「全部智能体」）。"""
    src = _read(AGENT_MGMT)
    tabs_block = src[src.index("const workspaceTabs"): src.index("const workspaceTabs") + 1600]
    assert "label: '分配给我'" in tabs_block
    # 必须是「管理员为空」的那一支，不能无条件插入
    assert "isAdminViewer.value\n      ? []" in tabs_block or "isAdminViewer.value ? []" in tabs_block


def test_assigned_panel_has_no_edit_or_copy_entry():
    """只读面板里不得出现编辑/复制/删除/版本的入口。"""
    src = _read(AGENT_MGMT)
    start = src.index('v-if="workspaceTab === \'assigned\'"')
    # 面板到下一个面板之间
    end = src.index("<AgentShareReceivedPanel", start)
    panel = src[start:end]
    for forbidden in ["编辑智能体", "复制智能体", "删除", "设置分组", "版本与发布", "toggleCardMenu"]:
        assert forbidden not in panel, f"只读面板不应出现 {forbidden}"
    # 唯一的动作入口
    assert "进入对话" in panel
    assert "@click=\"openAssignedChat(agent)\"" in panel


def test_assigned_chat_url_has_no_preview_flag():
    """进入对话不带 preview=1：那是管理者试跑语义。"""
    src = _read(AGENT_MGMT)
    block = src[src.index("const openAssignedChat"): src.index("const openAssignedChat") + 1100]
    assert "/embed/chat?ticket=" in block
    assert "preview=1" not in block


def test_preview_function_is_untouched():
    """openPreview 仍带 preview=1 —— 不得为了新视图把管理台预览一起改掉。"""
    src = _read(AGENT_MGMT)
    block = src[src.index("const openPreview"): src.index("const openPreview") + 900]
    assert "preview=1" in block


def test_assigned_data_not_merged_into_agents_list():
    """独立数据源：不能把分配来的智能体并进 agents.value。"""
    src = _read(AGENT_MGMT)
    assert "const assignedAgents = ref<AIAgent[]>([])" in src
    block = src[src.index("const fetchAssignedAgents"): src.index("const fetchAssignedAgents") + 900]
    assert "assignedAgents.value = res.data" in block
    assert "agents.value =" not in block
```

- [ ] **Step 2: 跑测试**

```bash
.venv/bin/python -m pytest --confcutdir=tests/frontend tests/frontend/test_agent_assigned_view_contract.py -q
```

期望：`5 passed`。

- [ ] **Step 3: 提交（由用户执行）**

```bash
git add tests/frontend/test_agent_assigned_view_contract.py
git commit -m "test(frontend): 「分配给我」只读边界契约"
```

---

### Task 7: 全量验证与文档回写

- [ ] **Step 1: 后端相关测试**

```bash
.venv/bin/python -m pytest tests/api/portal/test_agent_assigned_list.py tests/frontend/test_agent_share_contract.py -q
```

期望：全部 passed（后者确认没破坏既有视图契约）。

- [ ] **Step 2: 前端契约全量**

```bash
.venv/bin/python -m pytest --confcutdir=tests/frontend tests/frontend -q 2>&1 | tail -5
```

期望：`passed` 数 ≥ 改动前基线，无 failed。

- [ ] **Step 3: 类型检查基线比对**

```bash
cd frontend && npx vue-tsc --noEmit 2>&1 | grep -c "error TS"
```

期望：等于 Task 3 记录的基线数（新增 0）。

- [ ] **Step 4: 回写文档**

- `tests/CHECKLIST.md` 该行状态由 `🎨 原型待确认（尚未实现）` 改为实际状态；
- 在「改动文件」列补上最终文件清单与测试文件；
- 若实现过程中口径有变，同步 `docs/superpowers/specs/2026-11-12-agent-assigned-view-design.md`。

- [ ] **Step 5: 提醒用户**

Agent 不跑 `./dev.sh`。交付时提醒用户在控制台重启，并用**只有智能体授权、没有其它菜单权限**的普通账号实测一次「进入对话」——这是本功能唯一的动作入口（spec §9.1）。

---

## 未包含（YAGNI）

- 不做「申请编辑权限」流程；
- 不处理非系统级智能体被写进 `ResourcePermission` 的静默无效数据；
- 不改 `list_agents` / `list_allowed_agents` 现有口径；
- 无数据库迁移。
