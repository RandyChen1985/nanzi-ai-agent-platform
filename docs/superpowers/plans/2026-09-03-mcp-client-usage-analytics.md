# MCP Client 使用统计 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在外部 Client 的“更多操作”中增加“使用统计”弹框，以权限安全的后端聚合数据展示每日趋势及多维调用分析。

**Architecture:** 新增一个按 Client 聚合的服务台 API，沿用 `element:mcp_service:audit:read` 和实际调用用户隔离规则；前端在 `McpServiceDesk.vue` 内维护独立弹框状态并使用现有 Tailwind/CSS/SVG 渲染图表。只读现有 `McpInboundAuditLog`，不新增迁移、不改 OAuth 或 MCP 调用链。

**Tech Stack:** FastAPI、SQLAlchemy 2.x async、pytest、Vue 3、TypeScript、Tailwind CSS、内联 SVG/CSS。

---

## 文件清单

- Modify: `app/api/portal/endpoints/mcp_service.py` — 增加 Client 使用统计的时间范围、权限过滤、聚合和响应序列化。
- Modify: `frontend/src/views/McpServiceDesk.vue` — 增加统计入口、请求状态、统计计算辅助函数和响应式弹框。
- Modify: `tests/api/test_mcp_service_desk_contract.py` — 增加后端路由、权限和返回维度契约。
- Modify: `tests/frontend/test_frontend_mcp_service_desk_contract.py` — 增加入口、接口、维度展示和弹框布局契约。

不修改 `db-prod/`、`db-prod-pg/`，因为本功能只读现有审计表。

### Task 1: 先写后端统计契约测试

**Files:**
- Modify: `tests/api/test_mcp_service_desk_contract.py`
- Test: 同一文件中的 `pytestmark = pytest.mark.no_infrastructure` 测试

- [ ] **Step 1: 写出统计接口和字段的失败测试**

在现有审计趋势测试后新增以下测试，先要求当前代码不存在的新接口和字段：

```python
def test_mcp_service_exposes_client_usage_analytics_with_audit_scope():
    source = Path("app/api/portal/endpoints/mcp_service.py").read_text(encoding="utf-8")

    paths = {getattr(route, "path", None) for route in mcp_service.router.routes}
    assert "/clients/{client_id}/usage" in paths
    assert 'async def client_usage(' in source
    assert 'range: Literal["7d", "30d", "90d"]' in source
    assert "element:mcp_service:audit:read" in source
    usage_segment = source.split('@router.get("/clients/{client_id}/usage")', 1)[1].split("@router.get", 1)[0]
    assert "McpInboundAuditLog.client_id == client_id" in usage_segment
    assert "McpInboundAuditLog.user_id == current_user_id" in usage_segment
    for field in (
        '"summary"', '"daily_trend"', '"method_distribution"',
        '"status_distribution"', '"auth_distribution"',
        '"user_distribution"', '"resource_distribution"',
        '"p95_latency_ms"', '"active_user_count"',
    ):
        assert field in usage_segment


def test_client_usage_analytics_never_serializes_credentials_or_request_payload():
    source = Path("app/api/portal/endpoints/mcp_service.py").read_text(encoding="utf-8")
    usage_segment = source.split('@router.get("/clients/{client_id}/usage")', 1)[1].split("@router.get", 1)[0]
    assert "access_token" not in usage_segment
    assert "client_secret" not in usage_segment
    assert "request_headers" not in usage_segment
    assert "tool_input" not in usage_segment
    assert "tool_output" not in usage_segment
```

- [ ] **Step 2: 运行测试确认正确失败**

运行：

```bash
pytest --confcutdir=tests/frontend tests/api/test_mcp_service_desk_contract.py -q
```

预期：新增测试失败，原因是 `/clients/{client_id}/usage` 路由和 `client_usage` 尚未定义；已有测试不应因环境依赖失败。

### Task 2: 实现后端权限安全的聚合接口

**Files:**
- Modify: `app/api/portal/endpoints/mcp_service.py:7-20, 429-508, 932` 附近
- Test: `tests/api/test_mcp_service_desk_contract.py`

- [ ] **Step 1: 增加时间计算和测试可调用的聚合辅助函数**

将导入改为包含 `date`，并在 `audit_summary` 前增加以下辅助函数。辅助函数只接收已经完成权限过滤的审计记录映射，方便对空数据、日期补零和资源字段做纯逻辑测试：

```python
def _usage_date_range(usage_range: Literal["7d", "30d", "90d"]) -> tuple[datetime, datetime, list[str]]:
    days = {"7d": 7, "30d": 30, "90d": 90}[usage_range]
    today = datetime.utcnow().date()
    start_date = today - timedelta(days=days - 1)
    start_at = datetime.combine(start_date, datetime.min.time())
    end_at = datetime.combine(today, datetime.max.time())
    dates = [(start_date + timedelta(days=offset)).isoformat() for offset in range(days)]
    return start_at, end_at, dates


def _sort_usage_distribution(items: dict[str, int]) -> list[dict[str, Any]]:
    return [
        {"name": name, "total": total}
        for name, total in sorted(items.items(), key=lambda item: (-item[1], item[0]))
    ]


def _usage_resource_counts(rows: list[dict[str, Any]]) -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = {}
    for row in rows:
        resources = [
            ("agent", row.get("agent_id")),
            ("conversation", row.get("conversation_id")),
            ("dataset", row.get("dataset_id")),
        ]
        visible = False
        for resource_type, resource_name in resources:
            if resource_name:
                visible = True
                key = (resource_type, str(resource_name))
                counts[key] = counts.get(key, 0) + 1
        if not visible:
            counts[("other", "其他")] = counts.get(("other", "其他"), 0) + 1
    return counts
```

当前文件已有 `datetime` 和 `timedelta` 导入，无需引入同名的 `time` 模块；使用 `datetime.min.time()` 和 `datetime.max.time()` 生成自然日边界。

- [ ] **Step 2: 写纯逻辑聚合测试并确认 RED**

在 API 契约测试中增加：

```python
def test_usage_date_range_returns_complete_natural_day_buckets():
    start_at, end_at, dates = mcp_service._usage_date_range("7d")
    assert len(dates) == 7
    assert dates[0] == start_at.date().isoformat()
    assert dates[-1] == end_at.date().isoformat()
    assert start_at.hour == 0
    assert end_at.hour == 23


def test_usage_resource_counts_keeps_multiple_resource_dimensions_and_other():
    counts = mcp_service._usage_resource_counts([
        {"agent_id": "agent-1", "conversation_id": "conv-1", "dataset_id": None},
        {"agent_id": None, "conversation_id": None, "dataset_id": None},
    ])
    assert counts[("agent", "agent-1")] == 1
    assert counts[("conversation", "conv-1")] == 1
    assert counts[("other", "其他")] == 1
```

运行：

```bash
pytest --confcutdir=tests/frontend tests/api/test_mcp_service_desk_contract.py::test_usage_date_range_returns_complete_natural_day_buckets tests/api/test_mcp_service_desk_contract.py::test_usage_resource_counts_keeps_multiple_resource_dimensions_and_other -q
```

预期：在辅助函数实现前失败，错误为属性不存在；确认失败后进入实现。

- [ ] **Step 3: 增加 `/clients/{client_id}/usage` API 实现**

路由放在 `/clients/{client_id}/tokens` 前后均可，但必须使用精确路径；实现遵循以下执行顺序：

```python
@router.get("/clients/{client_id}/usage")
async def client_usage(
    client_id: str,
    range: Literal["7d", "30d", "90d"] = Query(default="30d"),
    user: dict = Depends(require_mcp_service_permission("element:mcp_service:audit:read")),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    client = await _get_owned_client(db, client_id, user, allow_shared=True)
    if client is None or client.status == "deleted":
        raise HTTPException(status_code=404, detail="OAuth Client 不存在")

    start_at, end_at, dates = _usage_date_range(range)
    filters = [
        McpInboundAuditLog.client_id == client_id,
        McpInboundAuditLog.created_at >= start_at,
        McpInboundAuditLog.created_at <= end_at,
    ]
    if user.get("role") != "admin":
        current_user_id = _current_user_id(user)
        filters.append(McpInboundAuditLog.user_id == current_user_id)

    rows = (await db.execute(
        select(
            McpInboundAuditLog.created_at,
            McpInboundAuditLog.result_status,
            McpInboundAuditLog.method_name,
            McpInboundAuditLog.auth_type,
            McpInboundAuditLog.user_id,
            McpInboundAuditLog.agent_id,
            McpInboundAuditLog.conversation_id,
            McpInboundAuditLog.dataset_id,
            McpInboundAuditLog.latency_ms,
        ).where(*filters)
    )).mappings().all()

    # 在服务端以已过滤的最小字段集合完成日期、分布、用户、资源和延迟聚合。
    daily = {day: {"total": 0, "completed": 0, "failed": 0, "denied": 0} for day in dates}
    methods: dict[str, dict[str, int]] = {}
    statuses: dict[str, int] = {}
    auth_types: dict[str, int] = {}
    users: dict[str, int] = {}
    resource_rows: list[dict[str, Any]] = []
    latencies: list[int] = []
    for row in rows:
        status = str(row["result_status"] or "unknown")
        day = row["created_at"].date().isoformat()
        if day in daily:
            daily[day]["total"] += 1
            if status in daily[day]:
                daily[day][status] += 1
        method = str(row["method_name"] or "unknown")
        method_item = methods.setdefault(method, {"total": 0, "completed": 0})
        method_item["total"] += 1
        if status == "completed":
            method_item["completed"] += 1
        statuses[status] = statuses.get(status, 0) + 1
        auth_type = str(row["auth_type"] or "unknown")
        auth_types[auth_type] = auth_types.get(auth_type, 0) + 1
        if row["user_id"] is not None:
            user_key = str(row["user_id"])
            users[user_key] = users.get(user_key, 0) + 1
        resource_rows.append(dict(row))
        if row["latency_ms"] is not None:
            latencies.append(int(row["latency_ms"]))

    total_calls = len(rows)
    completed_calls = sum(1 for row in rows if row["result_status"] == "completed")
    sorted_latencies = sorted(latencies)
    p95_latency = sorted_latencies[max(0, (len(sorted_latencies) * 95 + 99) // 100 - 1)] if sorted_latencies else None
    resource_counts = _usage_resource_counts(resource_rows)
    return {
        "range": range,
        "start_at": start_at.isoformat(),
        "end_at": end_at.isoformat(),
        "summary": {
            "total_calls": total_calls,
            "success_rate": round(completed_calls / total_calls * 100, 2) if total_calls else 0,
            "failed_calls": sum(1 for row in rows if row["result_status"] == "failed"),
            "denied_calls": sum(1 for row in rows if row["result_status"] == "denied"),
            "average_latency_ms": round(sum(latencies) / len(latencies), 2) if latencies else None,
            "p95_latency_ms": p95_latency,
            "active_user_count": len(users),
        },
        "daily_trend": [{"date": day, **values} for day, values in daily.items()],
        "method_distribution": [
            {"name": name, "total": item["total"], "success_rate": round(item["completed"] / item["total"] * 100, 2)}
            for name, item in sorted(methods.items(), key=lambda item: (-item[1]["total"], item[0]))
        ],
        "status_distribution": _sort_usage_distribution(statuses),
        "auth_distribution": _sort_usage_distribution(auth_types),
        "user_distribution": [
            {"user_id": name, "total": total}
            for name, total in sorted(users.items(), key=lambda item: (-item[1], item[0]))
        ],
        "resource_distribution": [
            {"type": resource_type, "name": name, "total": total}
            for (resource_type, name), total in sorted(resource_counts.items(), key=lambda item: (-item[1], item[0][0], item[0][1]))
        ],
    }
```

若 SQLAlchemy 映射结果不支持 `row["field"]`，统一使用 `.mappings()` 返回的映射对象，并保持查询字段为上述最小脱敏集合；不得查询 `request_id` 之外的凭证或正文列（当前模型也不保存正文）。

- [ ] **Step 4: 运行后端测试确认 GREEN**

运行：

```bash
pytest --confcutdir=tests/frontend tests/api/test_mcp_service_desk_contract.py -q
```

预期：后端新增测试与原有服务台 API 契约全部通过。

### Task 3: 先写前端入口和状态契约

**Files:**
- Modify: `tests/frontend/test_frontend_mcp_service_desk_contract.py`
- Test: 同一文件中的前端源码契约

- [ ] **Step 1: 写前端失败契约**

在 Client 卡片和审计相关测试后新增：

```python
def test_client_more_actions_exposes_usage_analytics_modal():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")
    clients_section = view.split("activeTab === 'clients'", 1)[1].split("activeTab === 'methods'", 1)[0]
    assert "使用统计" in clients_section
    assert "openClientUsage" in view
    assert "/api/portal/mcp-service/clients/${encodeURIComponent(client.client_id)}/usage" in view
    for field in ("daily_trend", "method_distribution", "status_distribution", "auth_distribution", "user_distribution", "resource_distribution"):
        assert field in view


def test_client_usage_modal_supports_ranges_loading_error_and_scroll():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")
    assert "clientUsageRange" in view
    assert "近 7 天" in view
    assert "近 30 天" in view
    assert "近 90 天" in view
    assert "使用统计" in view
    assert "重新加载" in view
    assert "当前周期暂无调用数据" in view
    assert "max-h-[calc(100vh-2rem)]" in view
    assert "overflow-y-auto" in view
```

- [ ] **Step 2: 运行测试确认 RED**

运行：

```bash
pytest --confcutdir=tests/frontend tests/frontend/test_frontend_mcp_service_desk_contract.py::test_client_more_actions_exposes_usage_analytics_modal tests/frontend/test_frontend_mcp_service_desk_contract.py::test_client_usage_modal_supports_ranges_loading_error_and_scroll -q
```

预期：测试因新入口、状态和 API 字符串不存在而失败。

### Task 4: 实现前端请求状态与可视化弹框

**Files:**
- Modify: `frontend/src/views/McpServiceDesk.vue` — `<script setup>` 状态/函数和 Client 卡片/弹框模板
- Test: `tests/frontend/test_frontend_mcp_service_desk_contract.py`

- [ ] **Step 1: 增加类型、状态和展示辅助函数**

在 `AuditLog` 类型附近增加 `ClientUsage` 类型，包含设计文档中的 `summary`、`daily_trend`、6 个分布数组；在现有 Client 状态附近增加：

```ts
type UsageRange = '7d' | '30d' | '90d'

const showClientUsage = ref(false)
const clientUsageTarget = ref<Client | null>(null)
const clientUsageRange = ref<UsageRange>('30d')
const clientUsage = ref<ClientUsage | null>(null)
const clientUsageLoading = ref(false)
const clientUsageError = ref('')

const clientUsageMax = computed(() => Math.max(...(clientUsage.value?.daily_trend || []).map(item => item.total), 1))
const usageBarWidth = (total: number, max: number) => `${Math.max(total ? 4 : 0, Math.round(total / Math.max(max, 1) * 100))}%`
const usagePercent = (total: number, overall: number) => overall ? `${Math.round(total / overall * 100)}%` : '0%'
const usageStatusLabel = (name: string) => ({ completed: '成功', failed: '失败', denied: '拒绝' }[name] || name)
const usageAuthLabel = (name: string) => name === 'user_delegated' ? '用户授权' : name
const usageResourceLabel = (name: string) => name || '其他'
```

保持已有 `trendBarHeight` 等审计页函数不变，避免影响全局审计趋势。

- [ ] **Step 2: 写请求、周期切换、关闭和重试逻辑**

加入以下函数，使用请求前清空旧错误和旧数据，保证切换 Client 时不会短暂显示上一个 Client 的图表：

```ts
const loadClientUsage = async () => {
  const client = clientUsageTarget.value
  if (!client) return
  clientUsageLoading.value = true
  clientUsageError.value = ''
  clientUsage.value = null
  try {
    const response = await api.get(`/api/portal/mcp-service/clients/${encodeURIComponent(client.client_id)}/usage`, {
      params: { range: clientUsageRange.value },
    })
    clientUsage.value = response.data
  } catch (err: any) {
    clientUsageError.value = err?.response?.data?.detail || '使用统计加载失败，请稍后重试'
  } finally {
    clientUsageLoading.value = false
  }
}

const openClientUsage = async (client: Client) => {
  clientActionMenuId.value = null
  clientUsageTarget.value = client
  clientUsageRange.value = '30d'
  showClientUsage.value = true
  await loadClientUsage()
}

const closeClientUsage = () => {
  if (clientUsageLoading.value) return
  showClientUsage.value = false
  clientUsageTarget.value = null
  clientUsage.value = null
  clientUsageError.value = ''
}
```

周期 `<select>` 使用 `@change="loadClientUsage"`；重试按钮也调用 `loadClientUsage`。不要调用 `loadAudit` 或改变 `auditFilters`。

- [ ] **Step 3: 在“更多操作”中插入入口**

在 `frontend/src/views/McpServiceDesk.vue` 的“编辑 Scope”按钮前加入：

```vue
<button
  v-if="canReadAudit"
  type="button"
  class="block w-full rounded-lg px-3 py-2 text-left text-xs font-bold text-indigo-700 hover:bg-indigo-50"
  @click="openClientUsage(client)"
>
  使用统计
</button>
```

入口只要求审计读取权限，不要求 Client 管理权限，且对共享 Client 和自有 Client 使用同一入口；后端负责最终可见性校验。

- [ ] **Step 4: 添加弹框模板**

在现有 Client 权限详情弹框之前增加 `v-if="showClientUsage && clientUsageTarget"` 的弹框，外层使用 `fixed inset-0 z-50 overflow-y-auto bg-slate-900/50 p-4`，内容使用 `max-h-[calc(100vh-2rem)] overflow-y-auto`。模板必须包含：

```vue
<div v-if="showClientUsage && clientUsageTarget" class="fixed inset-0 z-50 overflow-y-auto bg-slate-900/50 p-4" @click.self="closeClientUsage">
  <div class="flex min-h-full items-center justify-center">
    <div class="w-full max-w-6xl max-h-[calc(100vh-2rem)] overflow-y-auto rounded-2xl bg-white shadow-2xl">
      <header class="sticky top-0 z-10 flex items-start justify-between border-b border-slate-100 bg-white px-6 py-5">
        <div>
          <h2 class="text-xl font-black">使用统计</h2>
          <p class="mt-1 text-sm text-slate-500">{{ clientUsageTarget.client_name }} · {{ clientUsageTarget.client_id }}</p>
          <p class="mt-1 text-xs text-slate-400">{{ isAdmin ? '管理员：统计该 Client 的全部用户调用' : '仅统计当前用户发起的调用' }}</p>
        </div>
        <div class="flex items-center gap-3">
          <select v-model="clientUsageRange" class="rounded-lg border border-slate-200 px-3 py-2 text-xs font-bold" :disabled="clientUsageLoading" @change="loadClientUsage">
            <option value="7d">近 7 天</option><option value="30d">近 30 天</option><option value="90d">近 90 天</option>
          </select>
          <button type="button" class="text-2xl text-slate-400" aria-label="关闭使用统计" @click="closeClientUsage">×</button>
        </div>
      </header>
      <div v-if="clientUsageLoading" class="px-6 py-16 text-center text-sm text-slate-500">使用统计加载中…</div>
      <div v-else-if="clientUsageError" class="px-6 py-16 text-center">
        <p class="text-sm text-rose-600">{{ clientUsageError }}</p>
        <button type="button" class="mt-4 rounded-lg bg-indigo-600 px-4 py-2 text-xs font-bold text-white" @click="loadClientUsage">重新加载</button>
      </div>
      <div v-else-if="clientUsage" class="space-y-5 px-6 py-5">
        <div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <div v-for="item in [
            ['调用总量', clientUsage.summary.total_calls],
            ['成功率', `${clientUsage.summary.success_rate}%`],
            ['失败数', clientUsage.summary.failed_calls],
            ['拒绝数', clientUsage.summary.denied_calls],
            ['平均耗时', clientUsage.summary.average_latency_ms == null ? '—' : `${clientUsage.summary.average_latency_ms} ms`],
            ['P95 耗时', clientUsage.summary.p95_latency_ms == null ? '—' : `${clientUsage.summary.p95_latency_ms} ms`],
            ['活跃用户', clientUsage.summary.active_user_count],
          ]" :key="item[0]" class="rounded-xl border border-slate-100 bg-slate-50/80 p-4">
            <div class="text-xs font-bold text-slate-500">{{ item[0] }}</div>
            <div class="mt-2 text-xl font-black text-slate-800">{{ item[1] }}</div>
          </div>
        </div>
        <div class="grid gap-5 lg:grid-cols-2">
          <section class="rounded-xl border border-slate-200 p-4">
            <h3 class="text-sm font-black text-slate-800">每日调用趋势</h3>
            <div v-if="!clientUsage.daily_trend.length" class="py-10 text-center text-xs text-slate-400">当前周期暂无调用数据</div>
            <div v-else class="mt-4 flex h-44 items-end gap-1 overflow-x-auto">
              <div v-for="item in clientUsage.daily_trend" :key="item.date" class="flex min-w-8 flex-1 flex-col items-center justify-end gap-1" :title="`${item.date}：${item.total} 次`">
                <div class="flex h-32 w-full items-end"><div class="w-full rounded-t bg-indigo-400" :style="{ height: `${Math.max(item.total ? 8 : 0, Math.round(item.total / clientUsageMax * 120))}px` }" /></div>
                <span class="text-[10px] text-slate-500">{{ item.total }}</span>
                <span class="text-[9px] text-slate-400">{{ item.date.slice(5) }}</span>
              </div>
            </div>
          </section>
          <section class="rounded-xl border border-slate-200 p-4">
            <h3 class="text-sm font-black text-slate-800">按方法分布</h3>
            <div v-if="!clientUsage.method_distribution.length" class="py-10 text-center text-xs text-slate-400">当前周期暂无调用数据</div>
            <div v-else class="mt-4 space-y-3">
              <div v-for="item in clientUsage.method_distribution" :key="item.name">
                <div class="flex items-center justify-between gap-3 text-xs"><code class="min-w-0 break-all font-bold text-indigo-700">{{ item.name }}</code><span class="shrink-0 text-slate-500">{{ item.total }} 次 · {{ item.success_rate }}%</span></div>
                <div class="mt-1 h-2 rounded-full bg-slate-100"><div class="h-2 rounded-full bg-indigo-500" :style="{ width: usageBarWidth(item.total, clientUsage.summary.total_calls) }" /></div>
              </div>
            </div>
          </section>
          <section v-for="group in [
            { title: '结果状态分布', items: clientUsage.status_distribution, label: usageStatusLabel },
            { title: '认证类型分布', items: clientUsage.auth_distribution, label: usageAuthLabel },
          ]" :key="group.title" class="rounded-xl border border-slate-200 p-4">
            <h3 class="text-sm font-black text-slate-800">{{ group.title }}</h3>
            <div v-if="!group.items.length" class="py-10 text-center text-xs text-slate-400">当前周期暂无调用数据</div>
            <div v-else class="mt-4 space-y-3">
              <div v-for="item in group.items" :key="item.name">
                <div class="flex items-center justify-between gap-3 text-xs"><span class="font-bold text-slate-700">{{ group.label(item.name) }}</span><span class="text-slate-500">{{ item.total }} 次 · {{ usagePercent(item.total, clientUsage.summary.total_calls) }}</span></div>
                <div class="mt-1 h-2 rounded-full bg-slate-100"><div class="h-2 rounded-full bg-emerald-500" :style="{ width: usageBarWidth(item.total, clientUsage.summary.total_calls) }" /></div>
              </div>
            </div>
          </section>
          <section class="rounded-xl border border-slate-200 p-4">
            <h3 class="text-sm font-black text-slate-800">用户调用排行</h3>
            <div v-if="!clientUsage.user_distribution.length" class="py-10 text-center text-xs text-slate-400">当前周期暂无调用数据</div>
            <div v-else class="mt-4 space-y-2">
              <div v-for="item in clientUsage.user_distribution.slice(0, 10)" :key="item.user_id" class="flex items-center justify-between rounded-lg bg-slate-50 px-3 py-2 text-xs"><span class="font-mono text-slate-700">user_id={{ item.user_id }}</span><span class="font-bold text-indigo-700">{{ item.total }} 次 · {{ usagePercent(item.total, clientUsage.summary.total_calls) }}</span></div>
            </div>
          </section>
          <section class="rounded-xl border border-slate-200 p-4">
            <h3 class="text-sm font-black text-slate-800">资源关联排行</h3>
            <div v-if="!clientUsage.resource_distribution.length" class="py-10 text-center text-xs text-slate-400">当前周期暂无调用数据</div>
            <div v-else class="mt-4 space-y-2">
              <div v-for="item in clientUsage.resource_distribution.slice(0, 10)" :key="`${item.type}-${item.name}`" class="flex items-center justify-between gap-3 rounded-lg bg-slate-50 px-3 py-2 text-xs"><span class="min-w-0 break-all text-slate-700"><span class="mr-1 rounded bg-indigo-50 px-1.5 py-0.5 font-bold text-indigo-700">{{ item.type }}</span>{{ usageResourceLabel(item.name) }}</span><span class="shrink-0 font-bold text-indigo-700">{{ item.total }} 次</span></div>
            </div>
          </section>
        </div>
      </div>
    </div>
  </div>
</div>
```

模板中的 KPI 直接读取 `clientUsage.summary`；每日趋势遍历 `daily_trend` 生成柱状条；三类分布使用横向比例条和次数/占比文本；用户和资源列表使用 `.slice(0, 10)` 控制展示量并保留总量；每个数组为空时显示“当前周期暂无调用数据”。图表容器使用 `lg:grid-cols-2`，窄屏为单列。

- [ ] **Step 5: 运行前端契约和类型检查确认 GREEN**

运行：

```bash
pytest --confcutdir=tests/frontend tests/frontend/test_frontend_mcp_service_desk_contract.py -q
cd frontend && ./node_modules/.bin/vue-tsc --noEmit
```

预期：前端契约全部通过，`vue-tsc` 无错误。

### Task 5: 完成后端聚合回归与差异检查

**Files:**
- Verify: `app/api/portal/endpoints/mcp_service.py`
- Verify: `frontend/src/views/McpServiceDesk.vue`
- Verify: `tests/api/test_mcp_service_desk_contract.py`
- Verify: `tests/frontend/test_frontend_mcp_service_desk_contract.py`

- [ ] **Step 1: 运行两组完整针对性测试**

```bash
pytest --confcutdir=tests/frontend tests/api/test_mcp_service_desk_contract.py tests/frontend/test_frontend_mcp_service_desk_contract.py -q
```

预期：与本功能相关的后端和前端契约全部通过；若出现 MySQL、Redis 或其他基础设施错误，只记录为环境阻塞并与纯契约结果分开。

- [ ] **Step 2: 检查工作区差异和格式**

```bash
git diff --check -- app/api/portal/endpoints/mcp_service.py frontend/src/views/McpServiceDesk.vue tests/api/test_mcp_service_desk_contract.py tests/frontend/test_frontend_mcp_service_desk_contract.py
git status --short
```

预期：无空白错误；只报告本次文件和用户原有未提交改动，不 stage、不 commit。

- [ ] **Step 3: 明确未执行的验收项**

最终汇报区分已完成的源代码/契约/typecheck 验证，以及未执行的真实浏览器操作、服务启动、数据库迁移、Redis、OAuth/MCP 联调和部署验收。提醒用户自行在控制台执行 `./dev.sh` 做服务级验证。
