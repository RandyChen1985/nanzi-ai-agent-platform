"""角色「分配用户」弹窗：按部门筛选候选用户（源码级契约）。

背景（用户需求 + 截图）：919 名候选用户平铺在一个列表里，只能靠搜索找人，
管理员实际想的是「先把市场部的人挑出来」。

口径（与「用户管理」页一致，不改后端）：

1. 筛选器只在**左栏候选池**生效——右栏是已加入的成员，按部门筛会让人误以为成员丢了；
2. 部门目录走 `GET /api/portal/departments/options`（**仅要求登录**），
   不能复用 `/departments/tree`（要求部门菜单读权限）——否则只读角色管理员
   一打开弹窗就 403，筛选器根本加载不出来；
3. 条件必须三处同源：候选池分页、跨页「全选筛选结果」、以及「上次移出的成员补回」
   的部门范围判断。漏一处就会出现「筛了 A 部门却勾进 B 部门的人」；
4. 筛选口径变化要清空左栏勾选态：勾选的人已经不在视图里，再点 » 会把看不见的人加进角色；
5. 目录加载失败静默降级（不显示筛选器 + 一行提示），不能拦住分配用户主流程。
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ROLES_VUE = ROOT / "frontend/src/views/Roles.vue"

pytestmark = pytest.mark.no_infrastructure


def _source() -> str:
    return ROLES_VUE.read_text(encoding="utf-8")


def _segment(text: str, start: str, end: str) -> str:
    return text.split(start, 1)[1].split(end, 1)[0]


def _template() -> str:
    return _segment(_source(), "<template>", "<script setup")


def _left_column() -> str:
    return _segment(_template(), "可选用户 ·候选", "Middle Transfer")


def _right_column() -> str:
    return _segment(_template(), "已选用户 ·成员", "<!-- Footer -->")


def _apply_params() -> str:
    return _segment(_source(), "const applyDeptFilterParams = (params: any) => {", "// 部门目录：")


def _load_options() -> str:
    return _segment(_source(), "const loadDeptOptions = async () => {", "const clearDeptFilter")


def _filter_change() -> str:
    return _segment(_source(), "const onCandidateFilterChange = () => {", "const debouncedUserSearch")


def _fetch_available_users() -> str:
    return _segment(_source(), "const fetchAvailableUsers = async () => {", "let userSearchTimeout")


def _collect_filtered_users() -> str:
    return _segment(_source(), "const collectFilteredUsers = async (excludeRoleMembers: boolean) => {", "// 全选筛选结果")


def _opener() -> str:
    return _segment(_source(), "const openUserAssignmentDialog = async (role: any) => {", "const fetchAvailableUsers")


# --- 控件与作用范围 ---

def test_left_column_has_department_filter_controls():
    """左栏要有部门下拉 + 含子部门开关 + 清除入口，且下拉按层级缩进。"""
    left = _left_column()

    assert 'aria-label="按部门筛选候选用户"' in left
    assert 'v-model="deptFilter"' in left
    assert '@change="onCandidateFilterChange"' in left
    assert ">全部部门</option>" in left
    assert '\\u3000".repeat(dept.depth)' in left
    # 选中具体部门后才出现「含子部门」与「清除」，不占常态宽度
    assert 'v-model="deptIncludeSub"' in left
    assert "含子部门" in left
    assert 'v-if="deptFilter"' in left
    assert '>清除</button>' in left


def test_filter_only_affects_candidate_column_not_members():
    """部门筛选只作用于左栏：右栏是已加入成员，不能被筛掉。"""
    right = _right_column()

    assert "deptFilter" not in right
    assert "onCandidateFilterChange" not in right
    assert "deptIncludeSub" not in right


def test_department_directory_uses_login_only_options_endpoint():
    """目录必须用 /departments/options（仅登录），不能复用需要部门菜单权限的 /departments/tree。"""
    loader = _load_options()
    source = _source()

    assert "axios.get('/api/portal/departments/options'" in loader
    # 注释里提到 /departments/tree 是解释取舍，这里锁的是「没有真的去请求它」
    assert "axios.get('/api/portal/departments/tree'" not in source
    assert 'axios.get("/api/portal/departments/tree"' not in source
    assert "include_disabled: false" in loader


# --- 条件同源 ---

def test_department_params_are_shared_by_paged_fetch_and_select_all():
    """候选池分页与「全选筛选结果」必须用同一份部门条件。"""
    params = _apply_params()
    fetch = _fetch_available_users()
    collect = _collect_filtered_users()

    assert "params.dept_id = Number(deptFilter.value)" in params
    assert "params.include_sub = true" in params
    assert "applyDeptFilterParams(params)" in fetch
    assert "applyDeptFilterParams({ page, size: USERS_PAGE_SIZE })" in collect
    # 「全选筛选结果」不能退回裸参数，否则会跨部门勾人
    assert "const params: any = { page, size: USERS_PAGE_SIZE }" not in _source()


def test_department_filter_is_combined_with_role_exclusion():
    """部门条件与 exclude_role_id 并存：候选池口径仍是「该部门里还没加入角色的人」。"""
    fetch = _fetch_available_users()

    assert "params.exclude_role_id = currentRole.value.id" in fetch
    assert "applyDeptFilterParams(params)" in fetch


def test_pending_removed_members_respect_department_scope():
    """本次移出的原成员补回左栏时，必须落在当前部门范围内，否则筛选语义被破坏。"""
    source = _source()
    scope = _segment(
        source, "const selectedDeptScopeIds = computed<Set<number> | null>(() => {", "const deptFilterActive"
    )
    pending = _segment(source, "const pendingRemovedUsers = computed(() => {", "const isPendingRemoved")

    # 子树判定与后端 subtree_like_prefix 同口径（物化路径前缀）
    assert "if (!deptIncludeSub.value || !node.path) return new Set([node.id])" in scope
    assert "(d.path || '').startsWith(node.path)" in scope
    # 部门归属统一从详情里读，缺信息时判 false（不能凭空放行）
    assert "const userDeptIds = (user: any): number[] =>" in source
    assert "Array.isArray(user?.dept_ids) ? user.dept_ids.map(Number) : []" in source
    assert "if (!scope) return true" in source
    assert "const scope = selectedDeptScopeIds.value" in pending
    assert "userDeptIds(u).some(id => scope.has(id))" in pending


def test_pending_counts_are_scoped_to_department():
    """待保存新增 / 待移出的计数也要按部门范围：范围外的人不占 availableTotal、也不在列表里。"""
    source = _source()
    counts = _segment(source, "const pendingAddedCount = computed(() => {", "const availableRemaining")

    assert counts.count("const scope = selectedDeptScopeIds.value") == 2
    assert counts.count(".filter(id => inDeptScope(id, scope))") == 2
    # 徽标与底部计数仍复用同一份 availableRemaining
    assert "availableTotal.value - pendingAddedCount.value + pendingRemovedCount.value" in source


# --- 切换筛选的状态一致性 ---

def test_filter_change_resets_page_and_clears_checked_candidates():
    """切部门 / 改搜索词都走同一入口：回第一页、清空勾选、作废批量撤销范围。"""
    change = _filter_change()
    searcher = _segment(_source(), "let userSearchTimeout: any = null", "const goAvailablePage")

    assert "availablePage.value = 1" in change
    assert "availablePageInput.value = ''" in change
    assert "clearCandidateSelection()" in change
    assert "clearBulkUndo()" in change
    assert "fetchAvailableUsers()" in change
    # 清勾选是安全操作，不该弹确认、也不该动已选成员
    assert "askConfirm" not in change
    assert "assignedUserIds" not in change
    # 搜索仍保留 300ms 防抖，并且落到同一个重置入口
    assert "setTimeout(onCandidateFilterChange, 300)" in searcher


def test_clearing_filter_restores_full_candidate_pool():
    """「清除」要把部门与含子部门一起复位并重新拉候选池。"""
    clearer = _segment(_source(), "const clearDeptFilter = () => {", "const openUserAssignmentDialog")

    assert "deptFilter.value = ''" in clearer
    assert "deptIncludeSub.value = false" in clearer
    assert "onCandidateFilterChange()" in clearer


def test_opening_dialog_resets_department_filter_and_loads_directory():
    """换角色打开弹窗必须回到「全部部门」，否则新角色一进来就少一批人。"""
    opener = _opener()

    assert "deptFilter.value = ''" in opener
    assert "deptIncludeSub.value = false" in opener
    assert "loadDeptOptions()" in opener


def test_directory_is_loaded_once_and_cached():
    """部门目录只拉一次：目录是低频变化数据，每次开弹窗都请求没有意义。"""
    loader = _load_options()

    assert "if (deptOptionsLoaded.value) return" in loader
    assert "deptOptionsLoaded.value = true" in loader


# --- 降级与空态 ---

def test_directory_failure_degrades_without_blocking_assignment():
    """目录失败只隐藏筛选器并给一行提示，不能挡住分配用户。"""
    source = _source()
    loader = _load_options()
    left = _left_column()

    assert "deptOptions.value = []" in loader
    assert "部门目录加载失败，暂不支持按部门筛选" in loader
    # 失败提示与筛选器互斥展示，且候选列表照常渲染
    assert 'v-else-if="deptOptionsError"' in left
    assert "deptOptionsError" in source
    # 只影响筛选器，不影响 fetchAvailableUsers 的请求
    assert "deptOptionsError" not in _fetch_available_users()


def test_empty_state_distinguishes_department_filter_from_other_causes():
    """空态要区分「该部门没人」「搜不到」「全部已加入」，并提供清除筛选。"""
    left = _left_column()

    assert "该部门下没有可分配的用户" in left
    assert "没有匹配" in left
    assert "全部用户都已加入该角色" in left
    assert "暂无可分配用户" in left
    assert ">清除部门筛选</button>" in left
    # 部门分支必须排在「全部已加入」之前，否则筛出空列表会被误报成「都加完了」
    assert left.index("该部门下没有可分配的用户") < left.index("全部用户都已加入该角色")


def test_candidate_badge_hint_mentions_department_scope():
    """徽标悬浮说明要带部门口径，数字对不上时能判断是筛选还是数据问题。"""
    source = _source()
    hint = _segment(source, "const candidatePoolHint = computed(() => {", "const cacheAssignedUsers")
    template = _template()

    assert "部门筛选：${selectedDeptLabel.value}" in hint
    assert "candidatePoolHint" in template
    assert 'aria-live="polite" :title="candidatePoolHint"' in template
