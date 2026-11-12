"""案例集管理「废弃可删除 + 各 Tab 批量操作」的前端契约。

风格与 ``tests/frontend`` 其他用例一致：读 SFC 源码断言关键锚点，不跑浏览器。
布局与手感仍需人工验收，这里守住「复选框在、全选绑对、危险动作走确认、
删除接口对得上后端、单条删除只对已废弃开放」。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VIEW = ROOT / "frontend/src/views/ExampleManagement.vue"
ENDPOINTS = ROOT / "app/api/portal/endpoints/examples.py"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_batch_selection_state_and_helpers_exist():
    source = _source(VIEW)
    for anchor in (
        "const selectedIds = ref<number[]>([])",
        "const selectedExamples = computed(",
        "const allPageSelected = computed(",
        "const somePageSelected = computed(",
        "const toggleSelectAllOnPage = () =>",
        "const toggleRowSelected = (id: number, checked: boolean) =>",
    ):
        assert anchor in source, anchor


def test_selection_is_pruned_to_current_page_after_fetch():
    source = _source(VIEW)
    body = source[source.index("const fetchExamples = async () => {"):]
    body = body[: body.index("let fetchTimer")]
    assert "new Set(examples.value.map((ex) => ex.id))" in body, (
        "fetch 成功后必须按当前页 id 过滤选择，否则翻页/切 Tab 后会在看不见的行上批量操作"
    )
    assert "selectedIds.value = selectedIds.value.filter((id) => visibleIds.has(id))" in body


def test_table_has_checkbox_column_and_header_select_all():
    source = _source(VIEW)
    assert 'v-if="canBatchOperate"' in source, "无任何案例操作权限时不渲染复选框列"
    assert "@change=\"toggleSelectAllOnPage\"" in source
    assert ':checked="selectedIds.includes(ex.id)"' in source
    # 半选态只能落到 DOM property 上
    assert "indeterminate = somePageSelected.value" in source


def test_empty_state_colspan_accounts_for_checkbox_column():
    source = _source(VIEW)
    assert ':colspan="(isLocalMode ? 6 : 7) + (canBatchOperate ? 1 : 0)"' in source


def test_batch_toolbar_renders_only_when_selected():
    source = _source(VIEW)
    assert 'v-if="selectedCount > 0"' in source
    assert 'v-for="action in batchActions"' in source
    assert '@click="requestBatchAction(action)"' in source
    assert '@click="clearSelection"' in source


def test_batch_actions_follow_selected_status():
    source = _source(VIEW)
    body = source[source.index("const batchActions = computed<BatchActionItem[]>"):]
    body = body[: body.index("/** 「全部」Tab 跨状态混选时提示用户切到具体 Tab")]

    assert 'uniformStatus === "pending" && canAuditExample.value' in body
    assert 'uniformStatus === "approved" && canSyncExample.value' in body
    assert '!statusSet.has("deprecated") && canDeleteExample.value' in body
    assert 'uniformStatus === "deprecated" && canDeleteExample.value' in body
    # 删除必须排在废弃之后且只在全废弃时出现
    assert body.index('"deprecate"') < body.index('"delete"')


def test_batch_actions_cover_all_five_backend_actions():
    view = _source(VIEW)
    endpoints = _source(ENDPOINTS)
    for action in ("approve", "reject", "deprecate", "delete", "sync"):
        assert f'"{action}"' in view, action
        assert f'"{action}"' in endpoints, action


def test_dangerous_batch_actions_go_through_confirm_modal():
    source = _source(VIEW)
    assert 'type="danger"' in source
    assert 'v-if="batchConfirm"' in source
    assert ':loading="batchRunning"' in source
    assert '@confirm="runBatchAction"' in source


def test_single_row_delete_only_offered_for_deprecated():
    source = _source(VIEW)
    assert re.search(
        r'v-if="openRowMenuExample\.status === \'deprecated\' && canDeleteExample"', source
    )
    assert '@click="requestDeleteExample(openRowMenuExample)"' in source
    # 入口再做一次守卫，避免历史状态不一致时绕过
    body = source[source.index("const requestDeleteExample = ("):]
    body = body[: body.index("const confirmDeleteExample")]
    assert 'ex.status !== "deprecated"' in body


def test_frontend_calls_delete_and_batch_endpoints():
    source = _source(VIEW)
    assert "axios.delete(`/api/portal/examples/${target.id}`)" in source
    assert 'axios.post("/api/portal/examples/batch", { ids, action: action.key })' in source


def test_batch_failure_detail_is_surfaced_to_user():
    source = _source(VIEW)
    body = source[source.index("const runBatchAction = async () => {"):]
    body = body[: body.index("const deleteTarget")]
    assert "res.data.data?.failed" in body
    assert 'showToast(`${message}。原因：${reasons}`, "warning")' in body


def test_sync_batch_is_guarded_by_engine_readiness():
    source = _source(VIEW)
    body = source[source.index("const requestBatchAction ="):]
    body = body[: body.index("const runBatchAction")]
    assert 'action.key === "sync" && !isLocalMode.value && !isEngineReady.value' in body


def test_backend_maps_batch_actions_to_element_permissions():
    endpoints = _source(ENDPOINTS)
    assert "BATCH_ACTION_PERMISSIONS" in endpoints
    for code in (
        "element:chatbi_example:audit",
        "element:chatbi_example:delete",
        "element:chatbi_example:sync",
    ):
        assert code in endpoints, code
    # 批量与单条共用同一套权限判定
    assert "await _ensure_element_permission(db, user, required)" in endpoints
