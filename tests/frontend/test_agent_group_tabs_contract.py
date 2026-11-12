"""智能体中心「业务分组」契约：DB 单值列 + 页签与筛选。

为什么这组测试值得存在：

* 表里本来就有三个「像分组」的字段，选错了不会报错、只会在半年后变成「按用户组授权
  智能体没地方放」或者「一个智能体出现在三个页签里」——所以字段选型也锁进测试；
* 这一页有十来个筛选条件（状态 / 类型 / 来源 / 引擎 / 就绪度 / 权属人 / 团队三项）。
  如果页签计数不建立在「视图 + 关键词 + 全部筛选」之后，用户勾了「只看已启用」就会看到
  「页签说 5、点进去 3」——这类 bug 只有把口径写成断言才拦得住；
* 改分组必须走专用端点。走整表单 `PUT /{id}` 会连带校验待审冻结 / 是否系统级 /
  引擎是否可改，用户只是归个类，不该因为「正在审核中」被拒。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PROBE = ROOT / "tests" / "frontend" / "agent_group_tabs_probe.cjs"
VIEW = ROOT / "frontend" / "src" / "views" / "AgentManagement.vue"
API = ROOT / "frontend" / "src" / "api" / "agent.ts"
MODEL = ROOT / "app" / "models" / "agent.py"
SCHEMA = ROOT / "app" / "schemas" / "agent.py"
SERVICE = ROOT / "app" / "services" / "ai" / "agent_manager.py"
ENDPOINTS = ROOT / "app" / "api" / "portal" / "endpoints" / "agents.py"
MIGRATION_MYSQL = ROOT / "db-prod" / "V214-add-agent-group-name.sql"
MIGRATION_PG = ROOT / "db-prod-pg" / "V114-add-agent-group-name.sql"


def _read(path: Path) -> str:
    assert path.exists(), f"文件不存在：{path}"
    return path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def view() -> str:
    return _read(VIEW)


@pytest.fixture(scope="module")
def probe() -> dict:
    completed = subprocess.run(
        ["node", str(PROBE)], capture_output=True, text=True, timeout=120
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


# --------------------------------------------------------------------------
# 页面行为（真跑 toolGroupTabs.ts）
# --------------------------------------------------------------------------


def test_tabs_order_and_counts(probe: dict) -> None:
    """全部 → 各组（命中数降序、同数量按中文名）→ 未分组（固定末位）。"""
    assert probe["base_total"] == 12
    assert probe["base_ungrouped"] == 4
    assert probe["base_items"] == [
        ["全部", 12],
        ["法务合规", 3],
        ["财务分析", 2],
        ["数据分析", 2],
        ["客户服务", 1],
        ["未分组", 4],
    ]
    assert probe["base_first_key"] == "all"
    assert probe["base_last_key"] == "__ungrouped__"


def test_tab_count_equals_rows_after_filters(probe: dict) -> None:
    """页签数字必须等于「切到这一组能看到的条数」——这一页的筛选恰恰是最多的。"""
    assert probe["enabled_total"] == 10
    assert probe["enabled_items"] == [
        ["全部", 10],
        ["财务分析", 2],
        ["法务合规", 2],
        ["客户服务", 1],
        ["数据分析", 1],
        ["未分组", 4],
    ]
    # 逐组核对：页签上的数字与按分组过滤出来的条数完全一致
    for name, rows in probe["per_group_rows"]:
        assert dict(probe["enabled_items"])[name] == rows, name


def test_tab_counts_use_preselected_rows(probe: dict) -> None:
    """页面把列表算好再交给纯函数（filters 传空），所以禁用项也要算进来。

    这正是与工具页的区别：工具页把 status 交给纯函数判，智能体页在上游已筛完。
    如果哪天有人「顺手」把 status 也塞进 filters，禁用项会被二次过滤，数字立刻不对。
    """
    assert probe["empty_filters_ignores_status"] == 2


def test_mine_view_only_shows_own_groups(probe: dict) -> None:
    """「我的智能体」视图下页签集合只剩自己名下有的分组。"""
    assert probe["mine_total"] == 4
    assert probe["mine_items"] == [
        ["全部", 4],
        ["财务分析", 1],
        ["法务合规", 1],
        ["未分组", 2],
    ]
    assert probe["mine_only_own_groups"] is True


def test_search_narrows_tabs(probe: dict) -> None:
    """关键词把某组筛空后该组页签消失（点不到空组）。"""
    assert probe["kw_total"] == 1
    assert probe["kw_items"] == [["全部", 1], ["客户服务", 1]]


def test_blank_null_missing_group_are_all_ungrouped(probe: dict) -> None:
    assert probe["blank_is_empty"] is True
    assert probe["null_is_empty"] is True
    assert probe["missing_is_empty"] is True
    assert probe["trimmed"] == "法务合规"


def test_ungrouped_tab_hidden_when_all_grouped(probe: dict) -> None:
    assert probe["all_grouped_has_ungrouped"] is False


def test_single_group_still_renders_bar(probe: dict) -> None:
    assert probe["single_group_items_length"] == 2


# --------------------------------------------------------------------------
# 前端源码契约
# --------------------------------------------------------------------------


def test_view_reuses_shared_group_module(view: str) -> None:
    """页签顺序、计数口径、溢出方案复用共享模块，页面不另写一套聚合。"""
    assert "computeGroupTabs" in view
    assert "ToolGroupTabs" in view
    assert "@/utils/toolGroupTabs" in view
    assert "ALL_GROUP_KEY" in view


def test_counts_come_from_filtered_rows(view: str) -> None:
    """分组过滤必须在下游：`agentsMatchingFilters` 不含分组，`filteredAgents` 才套分组。

    顺序反过来（先按分组再算页签）会让页签数字包含分组条件，未选中的组永远显示满额，
    点过去才发现被搜索词筛空。
    """
    assert view.count("const agentsMatchingFilters = computed") == 1
    assert view.count("const filteredAgents = computed") == 1
    assert "computeGroupTabs(agentsMatchingFilters.value.map(toGroupableAgent), {}, UNGROUPED_AGENT_GROUP)" in view
    assert "filteredAgents" in view and "agentsMatchingFilters.value" in view
    # 分组过滤出现在 filteredAgents 里，而不是 agentsMatchingFilters 的函数体内
    # （两段之间还夹着分组块本身，所以只截到分组块的起点为止）
    matching_block = view.split("const agentsMatchingFilters")[1].split("/* ---------------- 业务分组")[0]
    assert "agentGroupFilter" not in matching_block


def test_group_block_declared_after_filtered_rows(view: str) -> None:
    """分组块必须声明在 `agentsMatchingFilters` **之后**。

    `watch(agentGroupTabsData, …)` 在 setup 期就会执行一次 getter 来收集依赖，
    如果分组块写在上游 computed 之前，getter 里访问 `agentsMatchingFilters.value`
    会命中暂时性死区（TDZ）抛 ReferenceError，setup 直接失败 →
    整个「智能体中心」内容区白屏（实测踩过：工具栏还在，列表区一片空白）。
    """
    upstream = view.index("const agentsMatchingFilters")
    group_block = view.index("const agentGroupTabsData")
    watch_at = view.index("watch(agentGroupTabsData")
    filtered = view.index("const filteredAgents = computed")
    assert upstream < group_block < filtered, "分组块被放在了上游筛选之前"
    assert upstream < watch_at, "watch 早于上游筛选声明，会触发 TDZ"


def test_view_maps_agent_shape_once(view: str) -> None:
    """智能体 → 共享形状的映射只允许一处（`toGroupableAgent`）。"""
    assert view.count("const toGroupableAgent") == 1
    assert view.count("group_name: agent.group_name ?? ''") == 1
    assert view.count("is_active: agent.is_enabled ?? true") == 1


def test_tabs_only_on_browse_views(view: str) -> None:
    """页签只服务「全部 / 我的智能体」；分享与专家团是待办队列，不做分组。"""
    assert ':items="agentGroupTabs"' in view
    assert "workspaceTab.value === 'mine' || workspaceTab.value === 'all'" in view
    assert 'v-if="showAgentGroupTabs"' in view


def test_group_bar_can_be_collapsed(view: str) -> None:
    """分组页签可收起，而且收起后按钮仍在原位（不能出现「藏起来就找不回」）。"""
    assert "const agentGroupTabsVisible = ref(" in view
    assert "@click=\"agentGroupTabsVisible = !agentGroupTabsVisible\"" in view
    assert 'v-if="canToggleAgentGroupTabs"' in view
    # 开关放在一级页签行右端
    assert 'class="ml-auto' in view
    assert ":aria-pressed=\"agentGroupTabsVisible\"" in view
    # 收起状态要持久化：换个页面回来还是收起
    assert "agent-management:group-tabs-visible" in view
    assert "localStorage.setItem(AGENT_GROUP_TABS_VISIBLE_KEY" in view


def test_group_bar_hidden_when_no_group_exists(view: str) -> None:
    """一个分组都没建时不显示整行（连开关一起），否则先教用户点一个没用的开关。"""
    block = view.split("const agentGroupTabsAvailable")[1].split("/** 全量分组名")[0]
    assert "agentGroupTabs.value.length > 2" in block


def test_group_filter_resets_on_view_switch(view: str) -> None:
    """切视图回「全部」，分组被筛空后也回「全部」，但跳过首屏加载期。"""
    assert "watch([workspaceTab]" in view
    assert "if (!agents.value.length) return" in view


def test_badge_hidden_when_locked(view: str) -> None:
    """锁定某组时卡片不再逐条显示分组徽标；未分组显示虚线徽标。"""
    assert 'v-if="agentGroupFilter === ALL_GROUP_KEY"' in view
    assert 'agentGroupFilter = agentGroupName(agent)' in view
    assert "未分组" in view


def test_empty_state_distinguishes_group(view: str) -> None:
    """选中分组后的空列表不能说成「你还没有创建智能体」。

    分组过滤发生在 `filteredAgents`，而空状态文案在分组选择之前判断的话，
    点「未分组」看到「你还没有创建智能体」会让用户以为数据丢了。
    """
    block = view.split("const emptyStateMessage")[1].split("const dragSourceId")[0]
    assert "agentGroupFilter.value !== ALL_GROUP_KEY" in block
    assert "没有未分组的智能体" in block
    # 分组分支必须排在「你还没有创建智能体」之前（比对代码行，避免命中注释里的同名字样）
    assert block.index("if (agentGroupFilter.value !== ALL_GROUP_KEY)") < block.index(
        'return "你还没有创建智能体'
    )


def test_drag_disabled_while_group_locked(view: str) -> None:
    """锁定某个分组时停用拖拽排序：分组是一种筛选，筛选视图里的顺序没有意义。"""
    block = view.split("const canDragAgents")[1].split("const emptyStateMessage")[0]
    assert "agentGroupFilter.value === ALL_GROUP_KEY" in block


def test_two_entries_for_setting_group(view: str) -> None:
    """编辑弹窗里有分组字段；卡片菜单里也有「设置分组」，两处共用同一份候选。"""
    assert 'v-model="agentForm.group_name"' in view
    assert "openAgentGroupDialog(agent)" in view
    assert "saveAgentGroup" in view
    assert 'id="agent-group-options"' in view
    assert view.count('list="agent-group-options"') == 2


def test_group_write_uses_dedicated_endpoint(view: str) -> None:
    """改分组走专用端点，不走整表单 PUT。"""
    assert "agentApi.updateAgentGroup(" in view
    api = _read(API)
    assert "updateAgentGroup: (id: string, groupName: string | null)" in api
    assert "/api/portal/agents/${id}/group" in api


def test_dialog_is_teleported(view: str) -> None:
    """遮罩弹窗必须 Teleport 到 body：卡片 hover 带 transform，否则遮罩只盖住卡片。"""
    title = view.find('id="agent-group-title"')
    assert title != -1, "没找到分组弹窗"
    assert view.rfind("<Teleport", 0, title) != -1, "弹窗不在 Teleport 内"
    assert view.find("</Teleport>", title) != -1, "Teleport 未闭合"


# --------------------------------------------------------------------------
# 后端契约
# --------------------------------------------------------------------------


@pytest.mark.parametrize("path", [MIGRATION_MYSQL, MIGRATION_PG])
def test_migration_adds_group_name(path: Path) -> None:
    sql = _read(path)
    assert "ai_agents" in sql
    assert "group_name" in sql
    assert "VARCHAR(64)" in sql
    # 与 owner_group 的区分必须写在迁移里，否则下一个人还会想复用 owner_group
    assert "owner_group" in sql
    assert "capabilities" in sql


def test_model_column_added() -> None:
    text = _read(MODEL)
    assert "group_name = Column(String(64), nullable=True)" in text
    # 不能把分组写进 owner_group（用户组 / 权限维度）
    assert "owner_group = Column(String(50), nullable=True)" in text


def test_schema_has_dedicated_request() -> None:
    text = _read(SCHEMA)
    assert "class AgentGroupUpdateRequest(BaseModel)" in text
    assert "group_name: Optional[str] = Field(None, max_length=GROUP_NAME_MAX_LEN)" in text
    # 整表单模型也要带上，否则编辑弹窗保存时分组会被丢掉
    assert 'description="业务分组名：智能体中心按它出分组页签；为空表示未分组"' in text


def test_update_is_partial_for_group_name() -> None:
    """整表单更新只在「显式传了该字段」时落库。

    无条件赋值会让版本配置 / 专家团 / 排版样式这些不带分组的表单把分组悄悄清空。
    """
    text = _read(SERVICE)
    assert 'if "group_name" in data.model_fields_set:' in text
    assert "agent.group_name = data.group_name" in text


def test_duplicate_keeps_group() -> None:
    """复制智能体时分组跟着副本走。"""
    text = _read(SERVICE)
    assert "group_name=source.group_name," in text


def test_dedicated_service_and_endpoint() -> None:
    service = _read(SERVICE)
    assert "async def set_agent_group(" in service
    # 只改一列：不调用 update_agent、不碰版本
    block = service.split("async def set_agent_group(")[1].split("async def update_agent(")[0]
    assert "agent.group_name = normalize_group_name(group_name)" in block
    assert "update_agent(" not in block

    endpoints = _read(ENDPOINTS)
    assert '@router.put(\n    "/{agent_id}/group",' in endpoints
    assert "AgentManagerService.set_agent_group(" in endpoints


def test_normalize_group_name_shared() -> None:
    """四处业务分组共用同一个归一口径（工具 / 数据集 / 智能体 / 技能）。"""
    sys.path.insert(0, str(ROOT))
    from app.utils.group_name import GROUP_NAME_MAX_LEN, normalize_group_name

    assert normalize_group_name("  法务合规  ") == "法务合规"
    assert normalize_group_name("   ") is None
    assert normalize_group_name(None) is None
    assert normalize_group_name("a\nb") == "a b"
    assert len(normalize_group_name("x" * 100)) == GROUP_NAME_MAX_LEN == 64
    # 老的导入路径仍然可用（script_tool schema 等在用）
    from app.schemas.tool import normalize_group_name as legacy

    assert legacy("  x  ") == "x"
