"""委派工具「调度白名单」的前端契约。

用户场景：在智能体编辑页给 `sub_agent_call` / `sub_agent_batch_call` 指定可调度的
子智能体。锁住四个关键点：

1. 配置字段能存进工具配置（`delegable_agent_ids`），否则保存即丢；
2. 工具卡片上有独立入口，且「已配置」要看得见（白名单是会被遗忘的配置）；
3. `sub_agent_batch_call` 未单独配置时数量回落到 `sub_agent_call`，与后端一致；
4. 「清空」必须二次确认——它是「禁止委派」而不是「恢复默认」。
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


def test_drawer_renders_delegation_entry_for_both_tools():
    assert "openDelegationScope" in DRAWER
    assert "hasDelegationAllowlist" in DRAWER
    # 两个工具都要有入口：只给 sub_agent_call 配，批量调用就成了绕过白名单的后门。
    button = DRAWER.split("emit('openDelegationScope'", 1)[0]
    assert "sub_agent_call" in button
    assert "sub_agent_batch_call" in button


def test_management_persists_allowlist_into_tool_config():
    save = MANAGEMENT.split("const saveDelegationScope", 1)[1].split("const canEditVersion", 1)[0]
    assert "delegable_agent_ids" in save
    # 字符串形态的工具项没有字段可写，必须升级成对象
    assert "name: toolName" in save


def test_management_batch_count_falls_back_to_single_call():
    counter = MANAGEMENT.split("const delegationScopeCount", 1)[1].split(
        "const hasDelegationAllowlist", 1
    )[0]
    assert "sub_agent_batch_call" in counter
    assert "sub_agent_call" in counter
    assert "??" in counter  # 未单独配置时取沿用值


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
