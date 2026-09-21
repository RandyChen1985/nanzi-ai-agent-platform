"""角色「分配用户」弹窗 UI/UX 契约（源码级）。

对应一轮 UX 评审后落地的改动，锁定以下不变量：

1. **数据安全（P0）**
   - 角色成员加载中 / 加载失败时禁止提交。保存是“整集替换”语义，若此时
     `assignedUserIds` 还是空占位就点确认，会把角色成员全部清空；
   - 加载失败必须显式报错并提供重试，不能显示成“该角色没有成员”；
   - 有未保存改动时，X / 遮罩 / 「取消更改」都要先确认，避免误关丢改动。
2. **双列穿梭框模型（P1）**
   - 左右两栏各自维护“勾选态”，勾选 **不等于** 已经移动；
   - 左栏候选只列“还没加入该角色”的用户，左右不会出现同一个人；
   - 「全选本页 / 全选筛选结果」只勾选，「取消全选」只取消勾选，都不移动用户；
   - 必须点中间 `»`（或双击条目）才把勾选的人移到右侧；未勾选时 `»` 禁用；
   - 停用用户（status=0）需要有标记。
3. **可访问性（P2）**
   - 列表项是可聚焦按钮并带 aria-pressed/aria-label，图标按钮有替代文本，
     数量变化有 aria-live。
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ROLES_VUE = ROOT / "frontend/src/views/Roles.vue"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _source() -> str:
    return _read(ROLES_VUE)


def _segment(text: str, start: str, end: str) -> str:
    return text.split(start, 1)[1].split(end, 1)[0]


def _template() -> str:
    return _segment(_source(), "<template>", "<script setup")


def _left_column() -> str:
    return _segment(_template(), "可选用户 ·候选", "Middle Transfer")


def _right_column() -> str:
    return _segment(_template(), "已选用户 ·成员", "<!-- Footer -->")


def _save_assignments() -> str:
    return _segment(
        _source(), "const saveUserAssignments = async () => {", "const closeUserAssignmentDialog"
    )


def _request_close() -> str:
    return _segment(
        _source(), "const requestCloseUserAssignmentDialog = () => {", "onMounted(() => {"
    )


# --- P0：提交安全 ---

def test_submit_is_blocked_while_members_are_loading_or_failed():
    """成员加载中 / 加载失败时不能提交（整集替换语义下会清空成员）。"""
    source = _source()
    can_submit = _segment(source, "const canSubmitAssignment = computed(", "const clearBulkUndo")
    save = _save_assignments()
    footer = _segment(_template(), "当前选择状态", "取消更改")

    assert "!submittingUserAssignment.value" in can_submit
    assert "!loadingAssigned.value" in can_submit
    assert "!assignedLoadFailed.value" in can_submit
    # 保存函数内部再兜一层，防止绕过按钮状态
    assert "if (loadingAssigned.value || assignedLoadFailed.value) return" in save
    assert ':disabled="!canSubmitAssignment"' in _template()
    assert "加载成员中..." in _template()


def test_member_load_failure_is_explicit_with_retry():
    """加载失败要显式报错 + 提供重试，并清空成员避免误保存。"""
    source = _source()
    fetch_users = _segment(source, "const fetchRoleUsers = async", "const openUserAssignmentDialog")
    right = _right_column()

    assert "assignedLoadFailed.value = true" in fetch_users
    assert "assignedUserIds.value = []" in fetch_users
    assert "assignedSnapshot.value = []" in fetch_users
    assert "获取角色成员失败，请重试" in fetch_users
    assert "assignedLoadFailed" in right
    assert "重新加载" in right
    # 必须与“该角色没有成员”区分
    assert "角色成员加载失败，为避免误清空成员，保存已禁用" in right


def test_unsaved_changes_require_confirmation_before_closing():
    """有未保存改动时，关闭入口统一走确认。"""
    source = _source()
    request_close = _request_close()

    assert "const assignedSnapshot = ref<number[]>([])" in source
    assert "assignedSnapshot.value = [...assignedUserIds.value]" in source
    assert "const hasUnsavedChanges = computed(" in source
    assert "if (!hasUnsavedChanges.value)" in request_close
    assert "@click.self=\"requestCloseUserAssignmentDialog\"" in _template()
    assert "未保存" in _template()


def test_successful_save_closes_without_confirmation():
    """保存成功直接关闭，不再弹放弃确认。"""
    save = _save_assignments()

    assert "closeUserAssignmentDialog()" in save
    assert "requestCloseUserAssignmentDialog" not in save
    assert "已保存，当前共" in save


# --- P1：候选列表只显示未加入的用户 ---

def test_candidate_list_excludes_assigned_users():
    """左侧候选池必须排除已选用户：服务端排除角色既有成员，前端排掉本次新勾选的。"""
    source = _source()
    computed = _segment(
        source, "const filteredAvailableUsers = computed(", "const cacheAssignedUsers"
    )
    fetch = _segment(source, "const fetchAvailableUsers = async () => {", "let userSearchTimeout")

    assert "availableUsers.value.filter(u => !assignedUserIds.value.includes(u.id))" in computed
    # 后端分页下 total 必须是排除后的候选池大小，否则分页与计数都会错
    assert "params.exclude_role_id = currentRole.value.id" in fetch
    assert "if (currentRole.value?.id) {" in fetch
    # 本地待保存的勾选还在服务端候选池里，展示计数要扣掉
    assert "const availableRemaining = computed(" in source
    assert "availableTotal.value - pendingAddedCount.value" in source
    # 旧的「可选包含已加入 + 隐藏开关」实现不应残留
    assert "hideAssigned" not in source
    assert "isUserAssigned" not in source


def test_removed_existing_members_come_back_to_candidate_column():
    """本次移出的原有成员要回到左栏并标记，否则移出后想加回来会找不到人。"""
    source = _source()
    left = _left_column()

    assert "const pendingRemovedUsers = computed(" in source
    assert "assignedSnapshot.value\n        .filter(id => !assignedUserIds.value.includes(id))" in source
    assert "const isPendingRemoved = (userId: number) =>" in source
    assert "isPendingRemoved(user.id)" in left
    assert "已移出" in left
    # 计数要把待移出的人加回“可分配”
    assert "availableTotal.value - pendingAddedCount.value + pendingRemovedCount.value" in source


# --- P1：穿梭框模型（勾选 ≠ 移动）---

def test_two_columns_have_independent_checked_state():
    """左右两栏各有独立勾选态，勾选不等于移动。"""
    source = _source()

    assert "const selectedCandidateIds = ref<number[]>([])" in source
    assert "const selectedAssignedIds = ref<number[]>([])" in source
    assert "const isCandidateChecked = (userId: number) => selectedCandidateIds.value.includes(userId)" in source
    assert "const isAssignedChecked = (userId: number) => selectedAssignedIds.value.includes(userId)" in source
    # 勾选函数只操作勾选集合，不碰 assignedUserIds
    toggle = _segment(source, "const toggleCandidateChecked = (userId: number) => {", "const toggleAssignedChecked")
    assert "assignedUserIds" not in toggle
    assert "selectedCandidateIds.value.splice(idx, 1)" in toggle


def test_select_all_only_checks_without_moving():
    """「全选本页 / 全选筛选结果」只勾选，绝不移动用户。"""
    source = _source()
    check_all = _segment(source, "const checkAllVisible = () => {", "const removeAll")
    select_all = _segment(source, "const selectAllFiltered = async () => {", "// 「取消全选」")

    assert "selectedCandidateIds.value = Array.from(merged)" in check_all
    assert "assignedUserIds" not in check_all
    assert "selectedCandidateIds.value = Array.from(merged)" in select_all
    assert "assignedUserIds" not in select_all
    assert "已勾选" in select_all
    # 跨页勾选仍要拉全并带上限保护
    assert "await collectFilteredUsers(true)" in select_all


def test_cancel_all_only_unchecks():
    """「取消全选」只是取消勾选，不移动用户、也无破坏性、因此不需要确认。"""
    source = _source()
    deselect = _segment(source, "const deselectAllFiltered = () => {", "const openUserAssignmentDialog")

    assert "clearCandidateSelection()" in deselect
    assert "assignedUserIds" not in deselect
    assert "askConfirm" not in deselect


def test_transfer_buttons_are_the_only_way_to_move():
    """中间 » / « 才是移动入口：未勾选时禁用（不点亮）。"""
    left = _left_column()
    template = _template()

    assert "const moveCheckedToAssigned = () => {" in _source()
    assert "const moveCheckedToCandidate = () => {" in _source()
    assert '@click="moveCheckedToAssigned"' in template
    assert '@click="moveCheckedToCandidate"' in template
    assert ':disabled="candidateCheckedCount === 0"' in template
    assert ':disabled="assignedCheckedCount === 0"' in template
    # 未勾选时用灰底、勾选后才点亮成蓝色
    assert "candidateCheckedCount > 0\n                            ? 'bg-blue-600 text-white shadow-lg shadow-blue-200 hover:bg-blue-700'" in template
    assert "bg-gray-100 text-gray-300 cursor-not-allowed" in template
    # 左栏不再有“点击即加入”的旧行为
    assert "toggleUser(" not in left
    assert "从左侧点击添加用户" not in _template()
    assert "在左侧勾选用户后点 » 加入" in _right_column()


def test_double_click_moves_single_row():
    """双击条目直接移动：左→右加入，右→左移出。"""
    template = _template()

    assert '@dblclick="moveOneToAssigned(user.id)"' in template
    assert '@dblclick="moveOneToCandidate(user.id)"' in template
    assert "const moveOneToAssigned = (userId: number) => {" in _source()
    assert "const moveOneToCandidate = (userId: number) => {" in _source()
    assert "双击加入" in template
    assert "双击直接移出" in template


def test_checkbox_state_is_rendered_and_toggleable():
    """两栏条目都要有勾选框视觉与 aria-pressed。"""
    left = _left_column()
    right = _right_column()

    for column in (left, right):
        assert ':aria-pressed="isCandidateChecked(user.id)"' in column or ':aria-pressed="isAssignedChecked(user.id)"' in column
        assert "w-4 h-4 rounded border-2" in column
        assert 'aria-hidden="true"' in column

    assert '@click="toggleCandidateChecked(user.id)"' in left
    assert '@click="toggleAssignedChecked(user.id)"' in right


def test_footer_shows_pending_checked_count():
    """底部要提示“已勾选待加入/待移出”的人数，避免勾了不知道下一步。"""
    footer = _segment(_template(), "当前选择状态", "取消更改")

    assert "已勾选 {{ candidateCheckedCount }} 人待加入，点 » 移动" in footer
    assert "已勾选 {{ assignedCheckedCount }} 人待移出，点 « 移动" in footer
    assert 'aria-live="polite"' in footer


def test_manual_edits_invalidate_bulk_undo():
    """移出、清空等回退操作后，批量撤销记录失效。"""
    source = _source()
    move_back = _segment(source, "const moveCheckedToCandidate = () => {", "const moveOneToAssigned")
    move_one_back = _segment(source, "const moveOneToCandidate = (userId: number) => {", "// 撤销上一次批量移入")
    remove_all = _segment(source, "const removeAll = () => {", "const requestRemoveAll")

    assert "clearBulkUndo()" in move_back
    assert "clearBulkUndo()" in move_one_back
    assert "clearBulkUndo()" in remove_all


# --- P1：停用用户标记 ---

def test_disabled_users_are_marked_in_both_columns():
    """两栏都要标记停用用户。"""
    source = _source()
    template = _template()

    assert "const isUserDisabled = (user: any) => Number(user?.status) === 0" in source
    assert template.count(">已停用</span>") == 2
    assert template.count("isUserDisabled(user)") == 2
    assert "分配后需先启用才能登录" in template


# --- P1：分页与批量确认 ---

def test_bulk_destructive_action_confirms_with_explicit_count():
    """「移除已选」是破坏性操作，必须带人数二次确认。"""
    template = _template()
    remover = _segment(_source(), "const requestRemoveAll = () => {", "const requestSelectAllFiltered")

    assert "移除已选 {{ assignedUserIds.length }} 人" in template
    assert "将把已选的 ${count} 名用户全部移出该角色" in remover
    assert "是该角色原本的成员" in remover
    assert "confirmText: `移除 ${count} 人`" in remover
    assert "onConfirm: removeAll" in remover


def test_pagination_page_size_and_jump():
    """页大小提升到 50 并支持跳页。"""
    source = _source()
    template = _template()

    assert "const availableSize = ref(50)" in source
    assert "const jumpToAvailablePage = () => {" in source
    assert "const availablePageInput = ref('')" in source
    assert '@keyup.enter="jumpToAvailablePage"' in template
    assert ">跳转</button>" in template


# --- P2：可访问性 ---

def test_list_items_are_keyboard_accessible_buttons():
    """列表项改为 button 并带 aria-label/aria-pressed，键盘可操作。"""
    template = _template()

    assert '<button\n                            v-for="user in filteredAvailableUsers"' in template
    assert '<button\n                            v-for="user in assignedUsers"' in template
    assert template.count('role="listitem"') == 2
    assert 'role="list"' in template
    assert "勾选 ${user.real_name || user.user_name}（双击直接加入）" in template
    assert "取消勾选 ${user.real_name || user.user_name}" in template


def test_dialog_and_icon_buttons_have_accessible_names():
    """弹窗、关闭按钮、中间移动按钮都要有可访问名称。"""
    template = _template()

    assert 'role="dialog"' in template
    assert 'aria-modal="true"' in template
    assert 'aria-labelledby="assign-users-title"' in template
    assert 'id="assign-users-title"' in template
    assert 'aria-label="关闭分配角色用户弹窗"' in template
    assert "把勾选的 ${candidateCheckedCount} 名用户加入已选" in template
    assert "把勾选的 ${assignedCheckedCount} 名用户移出已选" in template


# --- P2：文案与细节 ---

def test_empty_states_distinguish_no_result_and_all_assigned():
    """左侧空态要区分“搜不到”“全部都已加入”“没有可分配用户”。"""
    left = _left_column()

    assert "全部用户都已加入该角色" in left
    assert "暂无可分配用户" in left
    assert "没有匹配" in left


def test_long_names_have_tooltips():
    """长用户名截断处要有 title 悬浮完整名称。"""
    template = _template()

    assert template.count(':title="user.real_name || user.user_name"') == 2
    assert ':title="`@${user.user_name}`"' in template


def test_bulk_undo_entry_is_visible_in_selected_column():
    """批量移入后要能看到撤销入口。"""
    right = _right_column()

    assert "刚批量加入 {{ bulkAddedIds.length }} 名用户" in right
    assert '@click="requestUndoBulkAdd"' in right
    assert "const requestUndoBulkAdd = () => {" in _source()
    assert "此前已在该角色中的成员保持不变" in _source()


def test_dialog_is_wide_and_compact_with_non_wrapping_headers():
    """弹窗更宽更紧凑；栏头必须禁止折行，否则会被右侧操作挤成两行。"""
    template = _template()
    dialog = _segment(template, "User Assignment Dialog", "<!-- Footer -->")

    assert "max-w-6xl max-h-[92vh]" in dialog
    assert "max-w-4xl" not in dialog
    assert 'tracking-widest whitespace-nowrap">已选用户 ·成员' in template
    assert 'tracking-widest whitespace-nowrap">可选用户 ·候选' in template
    assert "仅显示未加入" in template
    # 右栏「全选 / 取消勾选」合并成一个切换按钮，省出标题宽度
    assert "{{ assignedCheckedCount > 0 ? `取消勾选(${assignedCheckedCount})` : '全选' }}" in template
    # 行距/内边距整体收紧
    assert 'space-y-1" role="list"' in template
    assert "p-2 rounded-lg border" in template
    assert "rounded-xl border border-gray-100 p-2.5" in template
    assert "rounded-xl border border-blue-100/50 p-2.5" in template
