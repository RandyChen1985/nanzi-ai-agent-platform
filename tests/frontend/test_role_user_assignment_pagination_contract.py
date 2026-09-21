"""角色「分配用户」弹窗分页/全集一致性契约（源码级）。

背景（线上真实数据：用户 1171 人，default 角色 6 名成员）：
弹窗原来用 `GET /management/users?page=1&size=1000` 一次拉 1000 条当“全部用户”，
而右侧已选成员来自 `GET /roles/{id}/users` 返回的全量 ID，两个数据源范围不一致，
导致角色徽标显示 6、弹窗里只列出 2 个（另外 4 个排在第 1000 名之后被截断），
同时候选池里有 171 个用户永远选不到。

这里锁定修复后的不变量：

1. `user_ids` 必须是**全量**成员 ID，不受 search/page 影响 —— 保存是“整集替换”语义，
   一旦被过滤，未出现在响应里的成员会在保存时被静默移除（最危险的一条）；
2. 候选列表走服务端分页 + 搜索，并用 `exclude_role_id` 由后端排除角色成员，
   使 total 与实际可分配人数一致；
3. 已选列表由全量 ID 驱动渲染（详情缺失时给占位行），保证「徽标数字 == 列表行数」。
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ROLES_PY = ROOT / "app/api/portal/endpoints/roles.py"
MANAGEMENT_PY = ROOT / "app/api/portal/endpoints/management.py"
ROLES_VUE = ROOT / "frontend/src/views/Roles.vue"

pytestmark = pytest.mark.no_infrastructure


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _segment(text: str, start: str, end: str) -> str:
    return text.split(start, 1)[1].split(end, 1)[0]


def _template() -> str:
    """截取根模板区（Roles.vue 内有嵌套 <template v-if>，不能用 </template> 收尾）。"""
    return _segment(_read(ROLES_VUE), "<template>", "<script setup")


def _get_role_users_endpoint() -> str:
    return _segment(_read(ROLES_PY), "async def get_role_users(", '@router.post("/{role_id}/users")')


def _list_users_endpoint() -> str:
    return _segment(_read(MANAGEMENT_PY), "async def list_users(", '@router.post("/users")')


def _fetch_available_users() -> str:
    return _segment(_read(ROLES_VUE), "const fetchAvailableUsers = async () => {", "let userSearchTimeout")


def _fetch_role_users() -> str:
    return _segment(_read(ROLES_VUE), "const fetchRoleUsers = async (roleId: number) => {", "const saveUserAssignments")


def _assigned_users_computed() -> str:
    return _segment(_read(ROLES_VUE), "const assignedUsers = computed(() => {", "const normalizeIds")


# --- 后端 ---

def test_role_members_endpoint_returns_full_id_set_and_paged_details():
    """user_ids 全量返回，详情支持分页与搜索。"""
    endpoint = _get_role_users_endpoint()

    assert '"user_ids": user_ids' in endpoint
    assert '"total": total' in endpoint
    assert '"page": page' in endpoint
    assert '"size": size' in endpoint
    assert '"items": items' in endpoint
    assert "page: int = Query(1, ge=1)" in endpoint
    assert "size: int = Query(50, ge=1, le=1000)" in endpoint
    assert "search: Optional[str] = None" in endpoint


def test_user_ids_query_is_not_affected_by_search():
    """全量 ID 查询必须在 search 过滤之前独立执行，避免保存丢成员。"""
    endpoint = _get_role_users_endpoint()

    ids_query = _segment(endpoint, "id_stmt = select(UserRoleRelation.user_id)", "stmt = (")
    assert "search" not in ids_query
    assert "id_stmt = select(UserRoleRelation.user_id).where(UserRoleRelation.role_id == role_id)" in endpoint
    # 全量语义必须在接口里写明，防止后人“顺手”给它加上分页
    assert "不受 search / page 影响" in endpoint


def test_role_members_endpoint_documents_replace_semantics():
    """接口注释需说明保存是整集替换语义。"""
    endpoint = _get_role_users_endpoint()

    assert "整集替换" in endpoint
    assert "静默移除" in endpoint


def test_list_users_supports_exclude_role_id():
    """候选列表由后端排除角色成员，让 total 反映真实可分配人数。"""
    endpoint = _list_users_endpoint()

    assert "exclude_role_id: Optional[int] = Query(" in endpoint
    assert "~User.id.in_(member_ids)" in endpoint
    assert "select(UserRoleRelation.user_id).where(" in endpoint


# --- 前端：候选区 ---

def test_candidate_column_uses_server_side_pagination():
    """候选区按服务端分页请求，不再一次拉 1000 条当全集。"""
    source = _read(ROLES_VUE)
    fetcher = _fetch_available_users()

    assert "const availableUsers = ref<any[]>([])" in source
    assert "const availableTotal = ref(0)" in source
    assert "const availablePage = ref(1)" in source
    assert "const availableTotalPages = computed(" in source
    assert "const goAvailablePage = (delta: number) => {" in source

    assert "page: availablePage.value" in fetcher
    assert "size: availableSize.value" in fetcher
    assert "size: 1000" not in fetcher
    assert "params.search = keyword" in fetcher
    assert "params.exclude_role_id = currentRole.value.id" in fetcher


def test_candidate_search_hits_backend_with_debounce():
    """候选搜索改为服务端检索，页面重置到第一页。"""
    source = _read(ROLES_VUE)
    searcher = _segment(source, "let userSearchTimeout: any = null", "const goAvailablePage")

    assert "const debouncedUserSearch = () => {" in source
    assert "availablePage.value = 1" in searcher
    assert "fetchAvailableUsers()" in searcher
    assert '@input="debouncedUserSearch"' in source


def test_candidate_column_shows_pool_total_and_pagination_controls():
    """候选徽标显示“还没加入”的人数（扣掉本地待保存的），并带翻页控件。"""
    left = _segment(_template(), "可选用户 ·候选", "Middle Icon")

    assert "{{ availableRemaining }}" in left
    assert "goAvailablePage(-1)" in left
    assert "goAvailablePage(1)" in left
    assert "v-if=\"availableTotalPages > 1\"" in left


def test_legacy_full_list_fetch_is_gone():
    """旧的“1000 条当全集”实现必须彻底移除。"""
    source = _read(ROLES_VUE)

    assert "const users = ref<any[]>([])" not in source
    assert "const fetchAllUsers = async () => {" not in source
    assert "users.value" not in source
    assert "selectedUsersDetails" not in source


# --- 前端：已选区 ---

def test_selected_column_is_driven_by_full_id_set():
    """已选列表由全量 ID 驱动，详情缺失也要占位，保证数字与行数一致。"""
    source = _read(ROLES_VUE)
    computed = _assigned_users_computed()

    assert "assignedUserIds.value" in computed
    assert "assignedUserMap.value[id]" in computed
    assert "detail_missing: true" in computed
    assert "assignedUsers" in source

    right = _segment(_template(), "已选用户 ·成员", "Footer")
    assert "v-for=\"user in assignedUsers\"" in right
    assert "{{ assignedUserIds.length }}" in right
    assert "detail_missing" in right


def test_selected_column_badge_and_footer_use_same_source():
    """徽标与底部计数都取全量 ID 长度，与列表行数同源。"""
    template = _template()

    assert template.count("assignedUserIds.length") >= 3
    assert "{{ users.length }}" not in template


def test_role_members_details_are_requested_separately_from_candidates():
    """成员详情来自角色成员接口，不再从候选池里过滤。"""
    fetcher = _fetch_role_users()

    assert "axios.get(`/api/portal/roles/${roleId}/users`" in fetcher
    assert "response.data.user_ids" in fetcher
    assert "cacheAssignedUsers(response.data.items" in fetcher


def test_moving_users_caches_detail_for_immediate_render():
    """移动勾选用户到右侧时缓存详情，右侧立即显示用户名而不是占位。"""
    source = _read(ROLES_VUE)
    mover = _segment(source, "const moveCheckedToAssigned = () => {", "const moveCheckedToCandidate")
    single = _segment(source, "const moveOneToAssigned = (userId: number) => {", "const moveOneToCandidate")

    assert "cacheAssignedUsers(collectCheckedCandidateDetails())" in mover
    assert "cacheAssignedUsers(filteredAvailableUsers.value.filter(u => u.id === userId))" in single


def test_assignment_dialog_resets_pagination_state():
    """打开弹窗重置页码与详情缓存，避免沿用上一次角色的状态。"""
    opener = _segment(
        _read(ROLES_VUE), "const openUserAssignmentDialog = async (role: any) => {", "const fetchAvailableUsers"
    )

    assert "availablePage.value = 1" in opener
    assert "assignedUserMap.value = {}" in opener
    assert "assignedSearchQuery.value = ''" in opener
    assert "fetchAvailableUsers()" in opener
    assert "fetchRoleUsers(role.id)" in opener
