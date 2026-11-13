"""专家团设置里的成员就地编辑：前端契约。

背景：成员名单属于**版本级配置**，而这个弹窗原本只改智能体级元数据（立即生效）。
这次让两者共处一屏，最怕的不是样式，而是**把「用户没碰」误读成「用户要清空」**：
后端把空数组当成「显式禁止委派任何子专家」，一旦前端在只读状态下也提交空数组，
保存一次团名字就会把团里的成员全部清掉。

所以这里锁的是几条语义，不是外观：

- 只有「有已发布版本 + 工具已启用（或用户同意一并启用）」才允许改成员；
- 不可编辑或没改动时，`scope_dirty=false`，父组件据此**不写版本**；
- 交付给后端的名单永远是子专家（团主不进名单）；
- 成员选择复用「配置调度范围」那个弹窗，且层级必须高于团设置本身。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
MODAL = ROOT / "frontend/src/components/agent/AgentTeamEditorModal.vue"
PAGE = ROOT / "frontend/src/views/AgentManagement.vue"
API = ROOT / "frontend/src/api/agent.ts"
SCOPE_MODAL = ROOT / "frontend/src/components/agent/DelegationScopeModal.vue"


def _read(path: Path) -> str:
    assert path.exists(), f"文件不存在：{path}"
    return path.read_text(encoding="utf-8")


def test_dirty_flag_gates_the_version_write():
    """没改动就不写版本：否则只改团名字也会复制一份新版本，历史里全是空改动。"""
    modal = _read(MODAL)
    assert "scope_dirty" in modal
    # 提交时名单与脏标记必须联动：脏 → 带名单；不脏 → 空数组且不可编辑时不写
    assert "scope_dirty: scopeDirty.value" in modal
    assert "delegable_agent_ids: scopeDirty.value ? [...draftMemberIds.value] : []" in modal

    page = _read(PAGE)
    # 父组件必须真的用这个标记来决定是否调用保存接口
    assert "if (payload.scope_dirty) {" in page
    assert "await agentApi.saveDelegationScope(" in page


def test_members_editable_only_with_published_version_and_tool():
    modal = _read(MODAL)
    # 三项前置条件缺一不可：没版本 → 只能给说明；工具没启用 → 需要用户同意一并启用
    assert "const scopeReady = computed(() => props.scopeState?.exists_published === true)" in modal
    assert "props.scopeState?.sub_agent_call_enabled === true" in modal
    assert "scopeReady.value && (toolEnabled.value || enableTool.value)" in modal
    # 不可编辑时不能留一个「添加成员」的入口骗用户点
    assert 'v-if="canEditMembers"' in modal


def test_lead_agent_is_shown_but_never_sent():
    modal = _read(MODAL)
    # 团主单独一行、不给移除按钮
    assert "memberRows.lead" in modal
    assert "removeMember(row.id)" in modal
    # 草稿名单只装子专家，团主是从 members 里单独取的
    assert "props.members.find((row) => row.is_lead)" in modal


def test_member_picker_reuses_the_scope_modal_above_team_editor():
    modal = _read(MODAL)
    assert "MEMBER_PICKER_Z_INDEX = 10020" in modal
    assert '<DelegationScopeModal' in modal
    assert ':z-index="MEMBER_PICKER_Z_INDEX"' in modal
    assert ':selected-ids="draftMemberIds"' in modal
    # 层级必须真的比团设置高，否则两个同层窗口互相盖住
    assert "TEAM_EDITOR_Z_INDEX = 10010" in modal

    scope_modal = _read(SCOPE_MODAL)
    # 默认值不能变：版本抽屉里的调用方依赖 10010
    assert "zIndex ?? DELEGATION_SCOPE_Z_INDEX" in scope_modal


def test_scope_api_is_wired_to_the_version_endpoint():
    api = _read(API)
    assert "getDelegationScope" in api
    assert "saveDelegationScope" in api
    assert "`/api/portal/agents/${agentId}/delegation-scope`" in api


def test_page_fetches_scope_before_opening():
    """打开弹窗先取回三项前置条件，避免「能点但点了报错」。"""
    page = _read(PAGE)
    open_block = page[page.index("const openTeamEditor = async"):]
    open_block = open_block[: open_block.index("const saveTeamProfile")]
    assert "await agentApi.getDelegationScope(team.id)" in open_block
    assert "teamEditorScope.value = data" in open_block
    # 读取失败要关掉弹窗，不能留一个空壳让用户在里面瞎点
    assert "showTeamEditorModal.value = false" in open_block


def test_backend_error_codes_get_actionable_copy():
    page = _read(PAGE)
    for code in (
        "NO_PUBLISHED_VERSION",
        "DELEGATION_TOOL_DISABLED",
        "TOO_MANY_MEMBERS",
        "AGENT_NOT_READY",
    ):
        assert code in page, f"缺少对 {code} 的提示文案"


def test_enabling_tool_is_opt_in_not_default():
    """「一并启用委派工具」默认不能勾上。

    默认勾选会让「我只想改团名」也复制一个新版本、强行开启委派工具；
    而关掉团开关时成员区是隐藏的，用户根本看不到这个勾。
    """
    modal = _read(MODAL)
    assert "const enableTool = ref(false)" in modal
    assert "enableTool.value = false" in modal


def test_closing_team_switch_never_writes_a_version():
    """关掉团开关时成员区被隐藏，不能替他发版本。"""
    modal = _read(MODAL)
    assert "if (!isLead.value) return false" in modal


def test_unconfigured_scope_is_flagged_before_narrowing():
    """未配置名单 = 全量可委派，加人会收窄，必须提前说清。"""
    modal = _read(MODAL)
    assert "scope_configured" in modal
    assert "当前没有限定调度范围" in modal
    assert "收窄为" in modal


def test_member_picker_disabled_at_cap():
    """到上限时入口就关掉，别等后端 422。"""
    modal = _read(MODAL)
    assert ':disabled="draftMemberIds.length >= memberCap"' in modal


def test_team_editor_entry_uses_is_editable_not_can_edit_versions():
    """入口必须按 is_editable 判定，不能退化成 can_edit_versions。

    两者对「普通用户自己创建、后来被上架成系统级」的智能体是分叉的：
    后端允许他建版本/改草稿（can_edit_versions=True），但把元数据与线上基线
    收归管理员（is_editable=False）。团设置改的正是后者，用错字段的结果是
    按钮看着能点，用户改完一屏成员、点保存才被 403 拦住。
    """
    page = _read(PAGE)
    block = page[page.index("const isTeamEditable"):]
    block = block[: block.index("const openTeamEditor")]
    assert "is_editable" in block
    assert "can_edit_versions" not in block


def test_switching_team_ignores_stale_scope_response():
    """切团后晚到的响应不能覆盖当前团的状态。"""
    page = _read(PAGE)
    block = page[page.index("const openTeamEditor = async"):]
    block = block[: block.index("const saveTeamProfile")]
    assert "teamEditorAgent.value?.id !== team.id" in block


def test_member_cap_is_enforced_inside_the_picker():
    """上限要在选择时就拦住，别等提交后端 422。"""
    modal = _read(MODAL)
    assert ':max-selectable="memberCap"' in modal

    scope = _read(SCOPE_MODAL)
    assert "maxSelectable?: number" in scope
    assert "atLimit" in scope
    # 把比较运算钉住：只断言 atLimit 存在，改坏「>=」也照样绿
    assert "localSelected.value.length >= props.maxSelectable" in scope
    # 上限只拦「加」，不拦「减」：否则用户被卡死在满员状态，一个都取消不掉
    assert "!localSelected.value.includes(agent.id)" in scope


def test_unconfigured_empty_list_does_not_say_no_members():
    """未限制态下不能写「还没有成员」，那和上方「全部可委派」自相矛盾。"""
    modal = _read(MODAL)
    assert "名单未配置：全部已授权的子智能体都可被委派" in modal
    assert "还没有成员（当前禁止委派任何子专家）" in modal


def test_retry_after_step_two_failure_does_not_duplicate_version():
    """成员存成功、团信息失败后重试，本地状态要跟着更新，否则会再发一版。"""
    page = _read(PAGE)
    block = page[page.index("const saveTeamProfile"):]
    block = block[: block.index("const openTeamScopeConfigurator")]
    assert "teamEditorScope.value = savedScope" in block


def test_tool_disabled_never_claims_unrestricted():
    """工具没启用时不能说「未限制 / 保存后会收窄」。

    后端在「工具不存在」和「工具在但没配名单」两种情况下都返回
    scope_configured=false，只看这一个是分不开的。工具没启用时一个子专家
    都委派不了，写成「现在可以委派全部」是从头到尾的错误描述。
    """
    modal = _read(MODAL)
    assert "const scopeUnrestricted = computed(() => toolEnabled.value && !scopeConfigured.value)" in modal
    # 三处展示口径都必须用它，不能退回裸的 scopeConfigured
    assert 'v-else-if="scopeUnrestricted && !draftMemberIds.length"' in modal
    assert 'v-if="canEditMembers && scopeUnrestricted"' in modal
    assert '<template v-if="scopeUnrestricted">' in modal


def test_newly_added_members_keep_avatar_and_warning():
    """新加的成员不在打开时的快照里，头像与警示要从候选列表回落。"""
    modal = _read(MODAL)
    assert "fromAgents?.avatar_url" in modal
    assert "scopeAgentIssue" in modal
    page = _read(PAGE)
    assert "published_version_number: (a as any).published_version_number" in page


def test_footer_does_not_flash_publish_button_while_loading():
    """加载中不能先闪一下「去发布版本」。"""
    modal = _read(MODAL)
    assert 'v-if="scopeReady || scopeLoading"' in modal


def test_adopt_team_members_respects_cap():
    """批量导入「改用这 N 位成员」也要受上限约束。"""
    scope = _read(SCOPE_MODAL)
    block = scope[scope.index("const adoptTeamMembers"):]
    block = block[: block.index("const toggle = ")]
    assert "next.size >= props.maxSelectable" in block
