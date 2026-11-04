"""委派工具「调度白名单」的前端契约。

用户场景：在智能体编辑页给 `sub_agent_call` 指定可调度的子智能体。锁住六个关键点：

1. 配置字段能存进工具配置（`delegable_agent_ids`），否则保存即丢；
2. **页面只有一个开关**：工具列表只显示 `sub_agent_call`，勾选它自动带上
   `sub_agent_batch_call`（并行委派入口），batch 不需要单独勾选或配置；
3. **名单只有一份**：入口只挂 `sub_agent_call`，批量委派跟随它；
4. 存量只配在 batch 上时，计数、回显、保存都要与后端读取一致；
5. 存量只存了其中一个工具时，选中态按「已启用委派」显示，保存时补成对；
6. 「清空」必须二次确认——它是「禁止委派」而不是「恢复默认」。
"""
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

API = Path("frontend/src/api/agent.ts").read_text()
MANAGEMENT = Path("frontend/src/views/AgentManagement.vue").read_text()
DRAWER = Path("frontend/src/components/agent/AgentVersionEditorDrawer.vue").read_text()
MODAL = Path("frontend/src/components/agent/DelegationScopeModal.vue").read_text()


def test_agent_api_exposes_delegable_agent_ids():
    assert "delegable_agent_ids" in API
    assert "调度白名单" in API


def test_drawer_renders_a_single_delegation_entry():
    """入口只保留 sub_agent_call：两个工具各挂一个按钮，用户就会各配一份。"""
    assert "openDelegationScope" in DRAWER
    assert "hasDelegationAllowlist" in DRAWER
    button = DRAWER.split("emit('openDelegationScope'", 1)[0]
    assert "tool.name === 'sub_agent_call'" in button
    # 反向：批量委派不再有独立入口
    assert "tool.name === 'sub_agent_call' || tool.name === 'sub_agent_batch_call'" not in button


def test_tool_list_shows_only_one_delegation_card():
    """batch 卡片直接从列表里拿掉：它不是独立的配置对象，由 sub_agent_call 自动带上。"""
    display = MANAGEMENT.split("const displayAvailableTools", 1)[1].split(");", 1)[0]
    assert "DELEGATION_BATCH_TOOL_NAME" in display
    # 分组、计数、分母都必须走过滤后的清单，否则「已选 X/Y」永远差 1
    assert "displayAvailableTools.value" in MANAGEMENT.split("const groupedTools", 1)[1]
    assert 'displayAvailableTools.length' in MANAGEMENT
    assert "displayAvailableTools.value.filter((t) => isToolSelected(t.name))" in MANAGEMENT


def test_toggling_delegation_writes_both_tools():
    """勾选/取消都成对处理：只写 single 会让模型没有并行委派入口。"""
    toggle = MANAGEMENT.split("const toggleTool", 1)[1].split("const isVersionToolSelected", 1)[0]
    assert "isDelegationToolName(toolName)" in toggle
    assert "DELEGATION_TOOL_NAME, DELEGATION_BATCH_TOOL_NAME" in toggle


def test_submit_normalizes_delegation_pair():
    normalize = MANAGEMENT.split("const normalizeDelegationToolsForSubmit", 1)[1].split(
        "const toggleTool", 1
    )[0]
    assert "DELEGATION_TOOL_NAME" in normalize
    assert "DELEGATION_BATCH_TOOL_NAME" in normalize
    # 两处保存（普通保存 / 发布）都要过一遍
    assert MANAGEMENT.count("normalizeDelegationToolsForSubmit(versionForm.value.tools") >= 2


def test_legacy_single_tool_version_counts_as_delegation_enabled():
    """老版本可能只存了 batch 或只存了 single：选中态按「已启用委派」显示。"""
    for marker in ("const isToolSelected", "const isVersionToolSelected"):
        block = MANAGEMENT.split(marker, 1)[1].split("const ", 1)[0]
        assert "isDelegationToolName(toolName)" in block
        assert "isDelegationToolName(toolItemName(item))" in block
    # 计数只算一个：两个都存了进去也不能显示已选 2 个
    count = MANAGEMENT.split("const selectedToolsCount", 1)[1].split(
        "const selectedSkillsCount", 1
    )[0]
    assert "isDelegationToolName" in count
    assert "hasDelegation ? 1 : 0" in count


def test_management_persists_allowlist_into_tool_config():
    save = MANAGEMENT.split("const saveDelegationScope", 1)[1].split("const canEditVersion", 1)[0]
    assert "delegable_agent_ids" in save
    # 名单固定写 sub_agent_call，不接受调用方传别的工具名
    assert "const toolName = 'sub_agent_call'" in save
    # 字符串形态的工具项没有字段可写，必须升级成对象
    assert "name: toolName" in save
    # 清掉 batch 上的历史字段，避免两份再次分叉
    assert "delete rest.delegable_agent_ids" in save


def test_management_reads_one_shared_allowlist():
    counter = MANAGEMENT.split("const effectiveDelegationAllowlist", 1)[1].split(
        "const delegationScopeCount", 1
    )[0]
    assert "sub_agent_call" in counter
    # 存量兜底：只配在 batch 上时也要读到
    assert "sub_agent_batch_call" in counter
    count = MANAGEMENT.split("const delegationScopeCount", 1)[1].split(
        "const hasDelegationAllowlist", 1
    )[0]
    assert "effectiveDelegationAllowlist" in count


def test_management_wires_modal_with_authorized_agent_ids():
    assert "DelegationScopeModal" in MANAGEMENT
    assert ":authorized-agent-ids=\"delegationAuthorizedAgentIds\"" in MANAGEMENT
    # admin 不需要标灰；拿不到权限信息时返回 null 而不是空数组（空数组会误标全部无权限）
    auth = MANAGEMENT.split("const delegationAuthorizedAgentIds", 1)[1].split(
        "const delegationScopeAgents", 1
    )[0]
    assert "return null" in auth
    assert "permissions?.agents" in auth


def test_modal_requires_confirmation_before_clearing():
    assert "清空调度白名单？" in MODAL
    assert "showClearConfirm" in MODAL
    # 文案必须说清「清空 = 禁止委派」，否则用户会以为是恢复默认
    assert "将无法委派任何子智能体" in MODAL


def test_modal_marks_mine_as_disabled_by_default():
    assert "默认不可被委派" in MODAL
    # 自建智能体未加入名单前不能出现在已选里
    assert "未加入" in MODAL


def test_modal_surfaces_stale_ids_instead_of_silently_dropping():
    assert "missingIds" in MODAL
    assert "已失效" in MODAL


def test_modal_uses_platform_shell_so_it_cannot_sink_behind_the_drawer():
    """曾手写 fixed 容器 + z-50，结果被右侧抽屉（z-[9990]）压住。

    用户实测反馈「配置显示到后面去了」——这不是 Tailwind 缺类，是纯层级竞争：
    版本编辑抽屉 9990、它内置的小弹窗 10000，白名单弹窗必须更高。
    """
    assert 'import Modal from "../Modal.vue"' in MODAL
    assert "<Modal" in MODAL
    # 抽屉是 z-[9990]、其内置弹窗是 z-[10000]，白名单弹窗必须压过它们
    assert "DELEGATION_SCOPE_Z_INDEX = 10010" in MODAL
    assert ':z-index="DELEGATION_SCOPE_Z_INDEX"' in MODAL
    # 确认层还要在 Modal 之上
    assert "DELEGATION_SCOPE_Z_INDEX + 10" in MODAL


def test_search_input_opts_out_of_global_input_border():
    """全局 `@layer base` 给所有 input 加了 1px 边框和 padding。

    搜索栏由外层容器画边框，内部 input 必须 `border-0 p-0`，否则出现双线、
    高度被 padding 撑开（用户实测截图里搜索框比右侧按钮高出一大截）。
    """
    search_block = MODAL.split('placeholder="搜索名称、标识或能力…"', 1)[1].split("/>", 1)[0]
    assert "border-0" in search_block
    assert "p-0" in search_block
    # 外层容器负责定高，保证与「只看已选」等高
    assert "h-9" in MODAL


def test_modal_splits_system_and_personal_into_tabs():
    """系统 / 我的智能体分两个 tab：分组标题挤在一屏里既看不出层次，也滚不动。"""
    assert 'type ScopeTab = "system" | "mine" | "stale"' in MODAL
    assert 'label: "系统智能体"' in MODAL
    assert 'label: "我的智能体"' in MODAL
    # 搜索时 tab 上的数字要跟着变，否则用户不知道命中在哪个 tab
    assert "visibleSystem.value.length" in MODAL
    assert "visibleMine.value.length" in MODAL


def test_team_lead_can_be_selected_as_a_sub_expert():
    """团主专家可以被选进调度范围：它在自己的团里是主专家，但同时也可以是
    另一个团的子专家，此时只能用个人能力（深度 1 无法再委派）。

    这条锁的是「不许因为深度限制就一刀切禁止」——不少智能体本身就带一个团，
    同时又是别处需要的专才，「当子专家用」和「当团用」只是两种取用方式。
    """
    assert "const isSelectable = (agent: DelegationScopeAgent) => isAuthorized(agent);" in MODAL
    # 反向：不能再出现「授权 + 非团主」的组合式禁止
    assert "isAuthorized(agent) && !isTeamLead(agent)" not in MODAL


def test_team_lead_badge_states_the_personal_only_scope():
    """徽标不能再说「不可选」，要说清代价：只能用个人能力、不会调度它的团成员。"""
    assert "团主 · 仅个人能力" in MODAL
    assert "团主专家 · 不可选" not in MODAL
    assert "只能用个人能力" in MODAL
