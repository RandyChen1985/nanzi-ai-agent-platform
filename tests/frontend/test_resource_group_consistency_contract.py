"""分组功能与周边交互的回归契约（代码评审后补）。

这些条目单独成一组，是因为它们都不是「新功能」而是**静默的坑**：功能看起来完全正常，
只在特定路径下才暴露——回车加标签报 TypeError、筛选状态下分组候选少一半、
页签条消失后用户卡在一个看不见的分组里、超时定时器在离开页面后还发请求。
每一类都配一条断言，避免以后重构时又悄悄改回去。
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DATASETS_VIEW = ROOT / "frontend/src/views/MetadataDatasets.vue"
MCP_REGISTRY = ROOT / "frontend/src/components/system/McpServerRegistry.vue"
AGENT_VIEW = ROOT / "frontend/src/views/AgentManagement.vue"
SKILLS_VIEW = ROOT / "frontend/src/views/SkillsManagement.vue"
EXAMPLE_VIEW = ROOT / "frontend/src/views/ExampleManagement.vue"
SIDEBAR = ROOT / "frontend/src/components/layout/UserModeSidebar.vue"
MCP_ENDPOINTS = ROOT / "app/api/portal/endpoints/mcp.py"

pytestmark = pytest.mark.no_infrastructure


def _read(path: Path) -> str:
    assert path.exists(), f"文件不存在：{path}"
    return path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# 一、回车加标签：必须把目标对象传进去
# --------------------------------------------------------------------------


def test_add_tag_enter_passes_target_object() -> None:
    """`@keyup.enter="addTag"` 会把 KeyboardEvent 当参数，`target.tags` 直接 undefined。

    新建数据集弹窗里按回车加标签曾是必崩的（编辑弹窗写对了、新建那处漏了括号），
    这种「两个弹窗只改对一个」的差异只能靠断言分开盯。
    """
    source = _read(DATASETS_VIEW)
    assert '@keyup.enter="addTag(newDataset)"' in source
    assert '@keyup.enter="addTag(editingDataset)"' in source
    assert '@keyup.enter="addTag"' not in source


# --------------------------------------------------------------------------
# 二、分组候选取全量，不受当前筛选影响
# --------------------------------------------------------------------------


def test_mcp_group_options_come_from_all_servers() -> None:
    """开着搜索/「仅运行中」时，候选里也不能缺分组。

    `mcpGroupTabsData` 建立在筛完的列表上，用它做候选会让用户以为某分组不存在，
    再打一遍（打错一个字就多一个只有一条服务的分组）。
    """
    source = _read(MCP_REGISTRY)
    start = source.index("const mcpGroupOptions = computed(")
    block = source[start : source.index("})", start)]
    assert "for (const server of servers.value)" in block
    assert "mcpGroupTabsData" not in block


# --------------------------------------------------------------------------
# 三、页签条整个消失时必须退回「全部」
# --------------------------------------------------------------------------


def test_mcp_falls_back_to_all_when_tab_row_disappears() -> None:
    source = _read(MCP_REGISTRY)
    assert "watch(showMcpGroupTabs, (visible) => {" in source
    block = source[source.index("watch(showMcpGroupTabs, (visible) => {") :][:400]
    assert "mcpGroupFilter.value !== ALL_GROUP_KEY" in block
    assert "mcpGroupFilter.value = ALL_GROUP_KEY" in block
    # TDZ 守卫：watch 必须声明在 showMcpGroupTabs 之后
    assert source.index("const showMcpGroupTabs = computed(") < source.index("watch(showMcpGroupTabs")


def test_agent_falls_back_to_all_when_tab_row_disappears() -> None:
    """在「未分组」页签里把最后一个未分组项归组后，页签条会消失。

    此时 `agentGroupFilter` 还停在 `__ungrouped__`，列表空着且没有按钮能点回「全部」，
    用户只能刷新页面。这条断言盯的就是这个出口。
    """
    source = _read(AGENT_VIEW)
    assert "watch(showAgentGroupTabs, (visible) => {" in source
    block = source[source.index("watch(showAgentGroupTabs, (visible) => {") :][:400]
    assert "agentGroupFilter.value = ALL_GROUP_KEY" in block
    assert source.index("const showAgentGroupTabs = computed(") < source.index(
        "watch(showAgentGroupTabs"
    )


# --------------------------------------------------------------------------
# 四、技能分组输入：限长、可 Esc 关闭、保存中不能被遮罩关掉
# --------------------------------------------------------------------------


def test_skill_group_inputs_are_length_limited() -> None:
    """另三个模块的输入框都限了 64，技能这两处漏了：超长只能靠后端静默截断。"""
    source = _read(SKILLS_VIEW)
    assert source.count('maxlength="64"') >= 2


def test_skill_group_dialog_closes_with_esc_but_not_while_saving() -> None:
    source = _read(SKILLS_VIEW)
    assert '@keydown.esc.prevent="!savingGroup && (groupDialog = null)"' in source
    # 遮罩：保存中关掉弹窗 = 请求还在飞却看不到结果
    assert '@click.self="!savingGroup && (groupDialog = null)"' in source


# --------------------------------------------------------------------------
# 五、创建 MCP 服务的响应要带上分组
# --------------------------------------------------------------------------


def test_create_mcp_server_returns_group_name() -> None:
    """接口契约要跟落库状态一致：创建时传了分组，响应里就得有。"""
    source = _read(MCP_ENDPOINTS)
    start = source.index("async def create_mcp_server(")
    block = source[start : start + 9000]
    assert '"group_name": new_server.group_name,' in block


# --------------------------------------------------------------------------
# 六、侧栏滚动定位不依赖 offsetParent 巧合
# --------------------------------------------------------------------------


def test_reveal_group_body_uses_rect_instead_of_offset_top() -> None:
    """`offsetTop` 相对 offsetParent（容器自己没定位，挂在带 relative 的父级上）。

    现在恰好因为父级没有纵向内边距才对得上，外层样式一调就静默错位，而错位表现为
    「点了更多却滚到别处」——用两个 rect 的差值算才稳。
    """
    source = _read(SIDEBAR)
    assert "body.getBoundingClientRect().top - el.getBoundingClientRect().top" in source
    assert "const bodyTop = body.offsetTop" not in source


# --------------------------------------------------------------------------
# 七、延时刷新定时器必须可清理
# --------------------------------------------------------------------------


def test_delayed_refresh_timers_are_tracked() -> None:
    """批量同步 / 智能增强后「3 秒后再拉一次」的定时器要登记，卸载时清掉。

    否则用户 3 秒内切走页面，对已卸载组件发请求、写 ref。
    """
    source = _read(EXAMPLE_VIEW)
    assert "const refreshTimers = new Set<ReturnType<typeof setTimeout>>()" in source
    assert "onBeforeUnmount(() => {" in source
    assert "refreshTimers.forEach((timer) => clearTimeout(timer))" in source
    # 裸的超时回调不应再有：全部走 runLater 登记
    assert source.count("setTimeout(") == 2  # scheduleFetch 与 runLater 各一处封装
