"""MCP 工具集「业务分组」契约：DB 单值列 + 左栏页签 + 右栏概览。

为什么这组测试值得存在：

* `sys_mcp_servers` 里有两个「看起来能当分组」的字段，选错了不会报错、只会在以后变成
  「个人服务与平台服务再也无法按同一业务域聚合」或者「按备注分类，改一次文案换一次组」
  ——所以字段选型也锁进测试；
* 改分组**绝不能**走现有的整表单 `PUT /servers/{id}`：那条路要求回传 server_name /
  sse_url，改名会迁移智能体的工具名引用，服务启用时还会触发一次远端 sync_tools。
  用户只是想归个类，不该顺手打一次外部服务；
* 这一页的筛选分两层（「仅运行中 + 关键词」与「待办」）。若页签计数不建立在两层之后，
  勾上「一个工具都没发布」再看页签，数字会虚高，用户切过去才发现是空的。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PROBE = ROOT / "tests" / "frontend" / "mcp_group_tabs_probe.cjs"
REGISTRY = ROOT / "frontend" / "src" / "components" / "system" / "McpServerRegistry.vue"
ENDPOINTS = ROOT / "app" / "api" / "portal" / "endpoints" / "mcp.py"
MODEL = ROOT / "app" / "models" / "mcp.py"
MIGRATION_MYSQL = ROOT / "db-prod" / "V215-add-mcp-server-group-name.sql"
MIGRATION_PG = ROOT / "db-prod-pg" / "V115-add-mcp-server-group-name.sql"


def _read(path: Path) -> str:
    assert path.exists(), f"文件不存在：{path}"
    return path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def registry() -> str:
    return _read(REGISTRY)


@pytest.fixture(scope="module")
def probe() -> dict:
    result = subprocess.run(
        ["node", str(PROBE)],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        timeout=120,
    )
    assert result.returncode == 0, f"探针执行失败：{result.stderr}"
    return json.loads(result.stdout)


# --------------------------------------------------------------------------
# 一、页签口径（真跑 toolGroupTabs.ts）
# --------------------------------------------------------------------------


def test_probe_reports_all_servers(probe: dict) -> None:
    """「全部」必须等于当前筛选后的服务条数，而不是后端返回的总条数。"""
    assert probe["base_total"] == 14
    assert probe["base_items"][0] == ["全部", 14]


def test_tab_counts_descend_with_group_name_order(probe: dict) -> None:
    """各组按命中数降序、同数量按中文名排序：「全部」打头，「未分组」固定末位。"""
    assert probe["base_items"][1:] == [
        ["研发效能", 3],
        ["数据平台", 2],
        ["协作办公", 2],
        ["运维监控", 2],
        ["测试验证", 1],
        ["未分组", 4],
    ]
    assert probe["base_first_key"] == "all"
    assert probe["base_last_key"] == "__ungrouped__"


def test_status_filter_participates_in_counts(probe: dict) -> None:
    """「仅运行中」参与计数：两条停用服务被排除，协作办公 2→1、未分组 4→3。"""
    assert probe["running_total"] == 12
    assert ["协作办公", 1] in probe["running_items"]
    assert ["未分组", 3] in probe["running_items"]


def test_tab_number_equals_rows_when_clicked(probe: dict) -> None:
    """页签数字 = 点进去的条数：逐组核对，不许出现「写着 3 点进去 2」。"""
    counts = {label: count for label, count in probe["running_items"]}
    for label, actual in probe["per_group_rows"]:
        assert counts[label] == actual, f"{label} 页签 {counts[label]} ≠ 实际 {actual}"


def test_keyword_matches_remark_and_url(probe: dict) -> None:
    """搜索要覆盖备注与地址，不是只搜服务名（历史 bug：字段名写成了 description / url）。"""
    assert probe["kw_remark_total"] == 1
    assert probe["kw_remark_items"] == [["全部", 1], ["数据平台", 1]]
    assert probe["kw_url_items"] == [["全部", 1], ["测试验证", 1]]


def test_todo_filters_participate_in_counts(probe: dict) -> None:
    """右栏待办点下去就是筛选，那么页签数字必须跟着它一起变。"""
    assert probe["todo_unpublished_total"] == 2
    assert probe["todo_unpublished_items"] == [["全部", 2], ["测试验证", 1], ["研发效能", 1]]
    assert probe["todo_offline_items"] == [["全部", 2], ["协作办公", 1], ["未分组", 1]]
    assert probe["todo_stale_tools_items"] == [["全部", 2], ["测试验证", 1], ["数据平台", 1]]


def test_blank_null_missing_all_count_as_ungrouped(probe: dict) -> None:
    """空白 / null / 字段缺失都算「未分组」，不能各自变成一个页签。"""
    assert probe["blank_is_empty"] is True
    assert probe["null_is_empty"] is True
    assert probe["missing_is_empty"] is True
    assert probe["trimmed"] == "研发效能"


def test_no_ungrouped_tab_when_every_server_is_grouped(probe: dict) -> None:
    assert probe["all_grouped_has_ungrouped"] is False


def test_single_group_hides_the_tab_row(probe: dict) -> None:
    """只有一个真实分组时 items 只有「全部 + 该组」，页面据此整行隐藏（含开关）。"""
    assert probe["single_group_items_length"] == 2


def test_empty_filters_do_not_reapply_status(probe: dict) -> None:
    """页面已在上游筛过，纯函数拿到的 filters 是空的：停用服务也要计入它所在的组。"""
    assert probe["empty_filters_ignores_status"] == 2


# --------------------------------------------------------------------------
# 二、前端契约
# --------------------------------------------------------------------------


def test_reuses_shared_group_tabs_module(registry: str) -> None:
    """页签组件与纯函数必须复用共享实现，不能为 MCP 另写一份。"""
    assert "import ToolGroupTabs from './ToolGroupTabs.vue'" in registry
    assert "computeGroupTabs" in registry
    assert "ALL_GROUP_KEY" in registry
    assert '<ToolGroupTabs' in registry


def test_counts_come_from_filtered_rows(registry: str) -> None:
    """计数输入必须是「已过两层筛选」的列表，filters 传空。"""
    assert "computeGroupTabs(serversMatchingFilters.value.map(toGroupableServer), {}, UNGROUPED_MCP_GROUP)" in registry


def test_group_block_declared_after_filtered_rows(registry: str) -> None:
    """TDZ 守卫：分组块必须声明在 `serversMatchingFilters` 之后。

    `watch([() => props.scope, mcpGroupTabsData], ...)` 在 setup 期就会同步求值
    computed 的 getter 来收集依赖。若分组块写在 `serversMatchingFilters` 之前，
    启动时立刻 ReferenceError → 内容区白屏（vue-tsc 与模板编译都查不出来）。
    """
    filter_pos = registry.index("const serversMatchingFilters = computed(")
    group_pos = registry.index("const mcpGroupTabsData = computed(")
    assert filter_pos < group_pos


def test_tabs_only_render_via_shared_toggle(registry: str) -> None:
    """页签行受「开关 + 有真实分组」双重控制，且左侧仍是同一个分组状态。"""
    assert 'v-if="showMcpGroupTabs"' in registry
    assert 'v-model="mcpGroupFilter"' in registry
    assert 'aria-label="按业务分组浏览 MCP 服务"' in registry
    assert "const showMcpGroupTabs = computed(() => mcpGroupTabsAvailable.value && mcpGroupTabsVisible.value)" in registry


def test_tabs_visibility_persisted(registry: str) -> None:
    """收起开关记住选择；一个分组都没建时开关也不出现。"""
    assert "mcp-console:group-tabs-visible" in registry
    assert "localStorage.setItem(MCP_GROUP_TABS_VISIBLE_KEY" in registry
    assert 'v-if="mcpGroupTabsAvailable"' in registry


def test_card_density_toggle(registry: str) -> None:
    """卡片密度可切换并记住：55 个服务时紧凑模式一屏多看一倍。"""
    assert "mcp-console:card-density" in registry
    assert "cardDensity === 'compact'" in registry
    # 紧凑模式隐藏备注与同步时间，但不隐藏「一个都没发布」这类要紧提示
    assert 'v-if="server.remark && cardDensity !== \'compact\'"' in registry
    assert 'v-if="server.last_sync_at && cardDensity !== \'compact\'"' in registry


def test_group_badge_on_card_clicking_locks_group(registry: str) -> None:
    """卡片名字右侧的分组徽标：点一下只看该组；锁定某组时不再重复显示。"""
    assert 'v-if="mcpGroupFilter === ALL_GROUP_KEY"' in registry
    assert '@click.stop="mcpGroupFilter = mcpGroupName(server)"' in registry
    assert "`只看「${mcpGroupName(server)}」分组`" in registry


def test_group_entry_in_card_menu(registry: str) -> None:
    """⋯ 菜单里有「设置分组」，走专用动作而不是打开编辑向导。"""
    assert "runServerAction('group')" in registry
    assert "action: 'share' | 'logs' | 'edit' | 'group' | 'sync' | 'delete'" in registry
    assert "else if (action === 'group') openGroupDialog(server)" in registry


def test_group_dialog_uses_dedicated_endpoint(registry: str) -> None:
    """保存走 PUT /servers/{id}/group，只提交 group_name 一个字段。"""
    assert "`/api/portal/mcp/servers/${target.server.id}/group`" in registry
    assert "group_name: nextValue || null" in registry
    # 弹窗必须 Teleport 出去，否则会被列表滚动容器裁掉
    assert '<Teleport to="body">' in registry
    title_pos = registry.rindex('<Teleport to="body">', 0, registry.index('id="mcp-group-title"'))
    assert registry.index("</Teleport>", title_pos) > registry.index('id="mcp-group-title"')


def test_datalist_shared_by_dialog_and_form(registry: str) -> None:
    """候选 datalist 必须在弹窗之外：编辑弹窗打开时 groupDialog 为空，放里面会引用不到。"""
    datalist_pos = registry.index('id="mcp-group-name-options"')
    dialog_pos = registry.index('id="mcp-group-title"')
    assert datalist_pos < dialog_pos, "datalist 不能放在分组弹窗内部"
    assert registry.count('list="mcp-group-name-options"') == 2


def test_edit_form_refills_group_name(registry: str) -> None:
    """编辑回填必须带 group_name：整表单保存会把没带的字段写回默认值，漏了就丢分组。"""
    start = registry.index("const openEditModal = (server: any) => {")
    block = registry[start:start + 1200]
    assert "group_name: server.group_name || ''" in block
    assert "group_name: ''" in registry  # resetWizard 初始值


def test_todo_filters_drive_the_list(registry: str) -> None:
    """右栏待办不是装饰：点一下要改变左栏列表，且页签计数跟着变。"""
    assert "type McpTodoKey = 'offline' | 'unpublished' | 'stale-tools' | 'stale-sync'" in registry
    filter_pos = registry.index("const serversMatchingFilters = computed(")
    block = registry[filter_pos:registry.index("const filteredServers = computed(")]
    assert "matchesTodo(s, todoFilter.value as McpTodoKey)" in block
    assert "toggleTodoFilter(todo.key)" in registry


def test_right_panel_empty_state_has_overview(registry: str) -> None:
    """未选中服务时那块面积要回答问题：分组分布 + 四类待办。"""
    assert "mcpGroupDistribution" in registry
    assert "mcpTodoCounts" in registry
    assert "从左侧选一个服务查看工具，或先看这里的分布" in registry
    assert "分组分布" in registry
    assert "值得处理" in registry


# --------------------------------------------------------------------------
# 三、后端契约
# --------------------------------------------------------------------------


def test_model_has_group_name_column() -> None:
    model = _read(MODEL)
    assert "group_name = Column(String(64), nullable=True)" in model


def test_migration_files() -> None:
    mysql = _read(MIGRATION_MYSQL)
    assert "ALTER TABLE `sys_mcp_servers`" in mysql
    assert "ADD COLUMN `group_name` VARCHAR(64) NULL" in mysql
    # 选型理由要留在文件里，否则下一个人还要再想一遍 remark / scope 能不能用
    assert "remark" in mysql and "scope" in mysql

    pg = _read(MIGRATION_PG)
    assert 'ALTER TABLE "sys_mcp_servers"' in pg
    assert 'ADD COLUMN IF NOT EXISTS "group_name" VARCHAR(64) NULL' in pg
    assert "COMMENT ON COLUMN" in pg


def test_schema_exposes_group_name_with_normalizer() -> None:
    endpoints = _read(ENDPOINTS)
    # 写入与响应共用 McpServerBase，归一也在这里做一次
    assert "group_name: Optional[str] = Field(default=None, max_length=GROUP_NAME_MAX_LEN)" in endpoints
    assert "from app.utils.group_name import GROUP_NAME_MAX_LEN, normalize_group_name" in endpoints
    assert "class McpServerGroupUpdateRequest(BaseModel):" in endpoints


def test_dedicated_group_endpoint() -> None:
    endpoints = _read(ENDPOINTS)
    assert '@router.put(\n    "/servers/{server_id}/group"' in endpoints
    start = endpoints.index('"/servers/{server_id}/group"')
    block = endpoints[start:start + 2200]
    # 只写一列；不碰名字迁移、不触发远端同步（docstring 里提到这两件事是解释为什么不用整表单 PUT，
    # 所以只断言「没有调用」）
    assert "server.group_name = normalize_group_name(data.group_name)" in block
    assert "_migrate_server_name_references(" not in block
    assert "McpClientService.sync_tools(" not in block
    assert "await db.commit()" in block
    # 个人服务只能本人改，平台服务只能管理员改
    assert 'server.scope == "personal" and server.user_id != _get_user_id(user)' in block
    assert 'server.scope == "global" and user.get("role") != "admin"' in block


def test_whole_form_update_protects_group_name() -> None:
    """整表单 PUT 必须按 model_fields_set 局部落库，否则凭据面板等表单会清空分组。"""
    endpoints = _read(ENDPOINTS)
    assert 'if "group_name" in data.model_fields_set:' in endpoints
    assert 'response_data["group_name"] = server.group_name' in endpoints


def test_group_endpoint_returns_counts() -> None:
    """返回值要和列表项同形：改完分组后卡片上的「已发布 N」不能瞬间变 0。"""
    endpoints = _read(ENDPOINTS)
    assert "async def _server_response_with_counts(" in endpoints
    start = endpoints.index("async def _server_response_with_counts(")
    block = endpoints[start:start + 1600]
    assert "item.tool_count" in block
    assert "item.published_tool_count" in block
    assert "item.stale_tool_count" in block
