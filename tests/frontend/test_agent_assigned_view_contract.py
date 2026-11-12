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
    tabs_block = src[src.index("const workspaceTabs"): src.index("const workspaceTabs") + 2000]
    assert "label: '分配给我'" in tabs_block
    # 必须是「管理员为空」的那一支，不能无条件插入
    assert "isAdminViewer.value" in tabs_block
    assert "? []" in tabs_block


def test_assigned_panel_has_no_edit_or_copy_entry():
    """只读面板里不得出现编辑/复制/删除/版本的入口。"""
    import re

    src = _read(AGENT_MGMT)
    start = src.index("v-if=\"workspaceTab === 'assigned'\"")
    end = src.index("<AgentShareReceivedPanel", start)
    # 先剥掉 HTML 注释：注释里写「编辑/复制/删除整块不渲染」是设计说明，
    # 不该被当成「面板里出现了删除入口」
    panel = re.sub(r"<!--.*?-->", "", src[start:end], flags=re.S)
    for forbidden in ["编辑智能体", "复制智能体", "删除", "设置分组", "版本与发布", "toggleCardMenu"]:
        assert forbidden not in panel, f"只读面板不应出现 {forbidden}"
    # 唯一的动作入口
    assert "进入对话" in panel
    assert '@click="openAssignedChat(agent)"' in panel


def test_assigned_chat_url_has_no_preview_flag():
    """进入对话的 URL 与降级兜底都不带 preview=1（那是管理者试跑语义）。"""
    src = _read(AGENT_MGMT)
    block = src[src.index("const openAssignedChat"): src.index("const openAssignedChat") + 1200]
    url_lines = [ln for ln in block.splitlines() if "/embed/chat?ticket=" in ln]
    assert url_lines, "找不到 embed/chat 跳转行"
    for ln in url_lines:
        assert "preview=1" not in ln
    fallback_lines = [ln for ln in block.splitlines() if "/dashboard/chat?agent_id=" in ln]
    assert fallback_lines, "找不到降级兜底跳转行"
    for ln in fallback_lines:
        assert "preview=1" not in ln


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
    assert "assignedAgents.value = res.data || []" in block
    # 注意大小写：assignedAgents 里的 "Agents" 是大写，不会被这条误伤
    assert "agents.value =" not in block


def test_header_count_follows_assigned_view():
    """头部「共 N 个」要跟着视图走。

    「分配给我」的数据不在 agents.value 里，直接用 totalAgentCount 会出现
    「共 2 个」却摆着 3 张卡片的错位。
    """
    src = _read(AGENT_MGMT)
    assert "const headerAgentCount" in src
    block = src[src.index("const headerAgentCount"): src.index("const headerAgentCount") + 300]
    assert "workspaceTab.value === 'assigned'" in block
    assert "assignedAgents.value.length" in block
    # 模板必须用新的计数，不能还是 totalAgentCount
    assert "共 {{ headerAgentCount }} 个" in src


def test_assigned_tab_url_is_role_guarded():
    """管理员带 ?agents_tab=assigned 不能落到一个没有 tab 高亮的空视图。

    该 tab 只对普通用户渲染，所以 URL 落地也必须按角色过滤，否则会得到
    「没有任何 tab 选中 + 空列表 + 没有切回入口」的页面。
    """
    src = _read(AGENT_MGMT)
    assert "const isTabAvailableForRole" in src
    guard = src[src.index("const isTabAvailableForRole"): src.index("const isTabAvailableForRole") + 400]
    assert "tab === 'assigned'" in guard
    assert "tab === 'all'" in guard
    assert "isAdminViewer.value" in guard
    # 两处落地都要过守卫：初始化 + watch
    init_block = src[src.index("const resolveInitialTab"): src.index("const resolveInitialTab") + 800]
    assert "isTabAvailableForRole(raw)" in init_block
    watch_block = src[src.index("() => route.query.agents_tab"): src.index("() => route.query.agents_tab") + 600]
    assert "isTabAvailableForRole(next)" in watch_block


def test_assigned_load_failure_is_distinguishable_from_empty():
    """加载失败不能伪装成「还没有智能体被分配给你」。"""
    src = _read(AGENT_MGMT)
    assert "const assignedAgentsError = ref(false)" in src
    fetch_block = src[src.index("const fetchAssignedAgents"): src.index("const fetchAssignedAgents") + 1200]
    assert "assignedAgentsError.value = true" in fetch_block
    assert "assignedAgentsError.value = false" in fetch_block
    panel_start = src.index("v-if=\"workspaceTab === 'assigned'\"")
    panel = src[panel_start: src.index("<AgentShareReceivedPanel", panel_start)]
    assert "分配列表加载失败" in panel


def test_assigned_chat_prefers_platform_chat_when_menu_granted():
    """有 menu:ai_chat 就走平台内 /dashboard/chat，没有才退回 embed 面。

    /dashboard/chat 有门户外壳与返回入口、与 openTeamChat 的落点一致；
    但它的 meta.perm 是 menu:ai_chat，只被授了智能体的用户会被挡在门外，
    那类用户只能走 /embed/chat（凭 ticket 校验可执行性）。
    """
    src = _read(AGENT_MGMT)
    block = src[src.index("const openAssignedChat"): src.index("const openAssignedChat") + 1600]
    assert "hasMenuPerm('menu:ai_chat')" in block
    assert "/dashboard/chat?agent_id=" in block
    assert "/embed/chat?ticket=" in block
    # 平台内落点要排在取票之前
    assert block.index("/dashboard/chat?agent_id=") < block.index("/embed/chat?ticket=")


def test_assigned_panel_has_loading_placeholder():
    """加载中要有占位，否则请求回来时面板高度会跳一下。"""
    src = _read(AGENT_MGMT)
    panel_start = src.index("v-if=\"workspaceTab === 'assigned'\"")
    panel = src[panel_start: src.index("<AgentShareReceivedPanel", panel_start)]
    assert 'v-if="!assignedAgentsLoaded"' in panel
    assert "正在加载" in panel
    # 三个分支必须串成 if / else-if / else，否则会同时渲染
    assert 'v-else-if="!filteredAssignedAgents.length"' in panel
