"""API 工具业务分组：注册表页的归类能力 + 智能体配置按业务分组归组。

背景（用户反馈）：通过「API 工具注册表」登记的自定义接口，在智能体配置的「工具能力」
步骤里全部落进「其他扩展工具」兜底组（截图 64/67）——因为分组是按工具名关键字硬编码
猜测的，而这张表没有分组信息。用户选定方案：给 ``sys_api_tools`` 加业务分组字段。

用户补充约束：「我截屏的这些不是我们环境的，不能写死处理」→ 本文件同时守住：
归类必须由使用者在页面上决定（多选 + 批量设置分组），代码里不得按具体前缀写死归类。

取体用「顶层 const 边界」切分（``_statement``），而不是固定长度窗口或花括号配平：
SFC 里存在模板字面量与对象字面量，按 const 边界切分既不截断也不越界。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL_API = ROOT / "frontend/src/api/tool.ts"
REGISTRY = ROOT / "frontend/src/components/system/ToolRegistry.vue"
MANAGEMENT = ROOT / "frontend/src/views/AgentManagement.vue"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _statement(source: str, name: str) -> str:
    """取 ``const <name> …`` 到下一个顶层 ``const`` 之间的完整定义。"""
    start = source.index(f"const {name}")
    end = source.find("\nconst ", start)
    return source[start:] if end == -1 else source[start:end]


# --------------------------------------------------------------------------- #
# 前端 API 客户端
# --------------------------------------------------------------------------- #
def test_tool_api_model_carries_group_name():
    source = _source(TOOL_API)
    assert re.search(r"group_name\??:\s*string", source), "SysApiTool 等接口需要 group_name 字段"


def test_tool_api_exposes_batch_group_call():
    source = _source(TOOL_API)
    assert re.search(r"batchSetGroup:\s*\(", source)
    body = source[source.index("batchSetGroup:"):]
    assert "/api/portal/tools/batch-group" in body
    assert "post" in body


# --------------------------------------------------------------------------- #
# 注册表页：分组列 / 表单 / 筛选 / 多选批量
# --------------------------------------------------------------------------- #
def test_registry_lists_group_column_and_cell():
    source = _source(REGISTRY)
    assert "业务分组" in source
    assert "t.group_name" in source


def test_registry_form_collects_group_name_with_reusable_options():
    source = _source(REGISTRY)
    assert "toolForm.group_name" in source
    assert "datalist" in source
    assert "existingGroupNames" in source, "表单需要复用已有分组名，避免同域被写成多个组"


def test_registry_filters_by_group_including_ungrouped():
    source = _source(REGISTRY)
    assert "groupFilter" in source
    assert "未分组" in source


def test_registry_supports_multi_select_and_batch_group_assignment():
    """存量工具靠这里归类：选中筛选结果 → 批量设置分组。"""
    source = _source(REGISTRY)
    assert "selectedToolIds" in source
    assert "选中当前筛选结果" in source
    # 断言按钮本体（带已选计数）而不是同名字样：否则只匹配到弹窗标题也会假绿
    assert ">批量设置分组（{{ selectedToolIds.size }}）</button>" in source
    assert '@click="openBatchGroupModal"' in source
    assert "batchSetGroup" in source
    assert "batchGroupName" in source


def test_registry_does_not_hardcode_customer_prefixes():
    """归类由使用者决定，代码里不写死任何前缀规则。"""
    assert "yunying" not in _source(REGISTRY)
    assert "yunying" not in _source(MANAGEMENT)


# --------------------------------------------------------------------------- #
# 智能体配置：业务分组优先 + 内置回落不变 + 兜底组殿后
# --------------------------------------------------------------------------- #
def test_grouping_prefers_business_group_and_keeps_keyword_fallback():
    body = _statement(_source(MANAGEMENT), "groupedTools = computed")
    assert re.search(r"if\s*\(\s*businessGroup\s*\)", body), "命中业务分组时必须提前归位并跳过关键字猜测"
    assert "(tool.group_name || '')" in body, "分组名要取工具自身的 group_name"
    # 老数据（无分组）必须继续走原有关键字回落，行为不变
    assert "browser" in body and "knowledge" in body


def test_fallback_group_is_a_named_constant_and_last():
    source = _source(MANAGEMENT)
    assert "BUSINESS_GROUP_FALLBACK_LABEL" in source
    body = _statement(source, "groupedTools = computed")
    order = re.search(
        r"return\s*\[\s*\.\.\.(\w+),\s*\.\.\.(\w+),\s*\.\.\.(\w+)\s*\]",
        body,
    )
    assert order, "分组顺序需要写成显式的 return 展开，便于校验"
    assert order.group(1) == "builtinGroups", "内置关键字组保持原有顺序、排最前"
    assert order.group(3) == "fallbackGroups", "兜底组必须殿后"


def test_business_groups_are_sorted_by_size():
    body = _statement(_source(MANAGEMENT), "groupedTools = computed")
    assert re.search(r"businessGroups", body)
    assert "sort(" in body, "业务分组按工具数降序，工具多的域一眼可见"


def test_grouped_tools_uses_plain_array_helpers():
    """分组结果本来就是数组；顺手清掉 Object.values(数组) 这种侥幸写法。"""
    source = _source(MANAGEMENT)
    assert "Object.values(groupedTools.value)" not in source
    assert "groupedTools.value\n" in source or "groupedTools.value." in source
    assert "groupedTools.value.map" in _statement(source, "collapseAllStaticGroups")


def test_dynamic_tools_keep_business_group_when_mapped():
    """动态工具映射时必须透传 group_name：漏掉这一步分组会静默失效，全部落回兜底组。"""
    body = _statement(_source(MANAGEMENT), "allAvailableTools = computed")
    assert "group_name" in body


def test_business_groups_collapse_by_default_in_read_only_mode():
    """已发布版本是只读的：业务分组默认折叠，避免一屏几十张卡；用户仍可点开。"""
    source = _source(MANAGEMENT)
    body = _statement(source, "isStaticGroupCollapsed")
    assert "canEditVersion" in body, "只读态才默认折叠"
    assert "isBusinessGroupLabel" in body
    assert "expandedBusinessGroups" in body, "只读态下用户手动展开的分组要单独记录"
    assert "expandedBusinessGroups" in _statement(source, "resetVersionEditorUi")


# --------------------------------------------------------------------------- #
# 注册表页：分组维度的可读性与操作（下拉计数 / 徽章筛选 / 未分组提醒 / 分组视图）
# --------------------------------------------------------------------------- #
def test_registry_group_filter_shows_counts():
    """看不到「每组多少、还有多少没归类」，就无从开始治理。"""
    source = _source(REGISTRY)
    assert "groupFilterOptions" in source
    # 三个选项都要带计数，否则「每组多少 / 还剩多少没归类」又看不见了
    assert ">分组：全部 ({{" in source
    assert ">未分组 ({{" in source
    assert "{{ name }} ({{ count }})" in source


def test_registry_group_badge_filters_on_click():
    """分组徽章从「只能看」变成「点一下就筛出这一组」。"""
    source = _source(REGISTRY)
    assert "filterByGroup" in source
    assert '@click="filterByGroup(t.group_name)"' in source


def test_registry_surfaces_ungrouped_backlog():
    source = _source(REGISTRY)
    assert "ungroupedToolsCount" in source
    assert "个工具未分组" in source


def test_registry_offers_group_view_toggle():
    source = _source(REGISTRY)
    assert "viewMode" in source
    assert "分组视图" in source and "列表视图" in source


def test_group_view_reuses_the_very_same_row_markup():
    """两种视图必须共用同一份行标记：写两套迟早漂移（列数、操作按钮不一致）。"""
    source = _source(REGISTRY)
    assert source.count('v-for="t in group.tools"') == 1
    assert "displayGroups" in source


def test_group_view_expands_groups_while_searching():
    source = _source(REGISTRY)
    body = _statement(source, "isGroupCollapsed")
    assert "toolSearchQuery" in body, "搜索命中时该组要自动展开"


def test_group_view_can_select_a_whole_group():
    source = _source(REGISTRY)
    assert "全选本组" in source
    assert "selectGroupTools" in source
    assert "collapsedRegistryGroups" in source


def test_registry_toolbar_keeps_title_on_one_line():
    """控件增多时曾把「API 工具注册表」挤成两行、把「+ 添加工具」挤到下一行。

    标题必须禁止折行，筛选控件必须独占一行且搜索框自适应吃满剩余宽度。
    """
    source = _source(REGISTRY)
    assert 'text-lg font-medium text-gray-900 whitespace-nowrap">API 工具注册表' in source
    assert "min-w-[12rem] flex-1" in source


def test_registry_puts_url_under_the_tool_name():
    """URL 是名称的补充信息：并入名称列第三行后，省掉最吃宽度的一整列。"""
    source = _source(REGISTRY)
    assert ">URL Template</th>" not in source, "独立的 URL 列应已移除"
    cell = source[source.index("{{ t.name }}"):]
    cell = cell[: cell.index("</td>")]
    assert "t.description" in cell, "描述仍在名称列"
    assert "t.url_template" in cell, "URL 应落在名称列内"


def test_registry_table_has_six_columns():
    """列数一变，空态 colspan 与分组视图的复用都要跟着改。"""
    source = _source(REGISTRY)
    header = source[source.index("<thead"):source.index("</thead>")]
    assert len(re.findall(r"<th[\s>]", header)) == 6
    assert 'colspan="6"' in source


def test_registry_only_the_name_column_absorbs_spare_width():
    """固定布局下只能有一列自适应，否则多出的空间会被塞给首列。

    踩过的坑：勾选列声明 48px、其余列用百分比（合计 84%），表格多出的空间在
    ``table-fixed`` 下被浏览器全部算到首列头上——复选框那一列空出一大块。
    正确做法：名称列不声明宽度（吸收剩余），其余列给固定宽度。
    """
    source = _source(REGISTRY)
    header = source[source.index("<thead"):source.index("</thead>")]

    name_th = next(tag for tag in header.split("<th") if ">名称</th>" in tag)
    assert not re.search(r'class="[^"]*\bw-', name_th), f"名称列不应声明宽度：{name_th}"

    fixed_columns = {
        ">选择</span>": "w-12",
        ">Method</th>": "w-24",
        ">业务分组</th>": "w-32",
        ">状态</th>": "w-24",
        ">操作</th>": "w-32",
    }
    for marker, width in fixed_columns.items():
        tag = next(tag for tag in header.split("<th") if marker in tag)
        assert f'class="{width} ' in tag, f"{marker} 应为固定宽度 {width}：{tag}"

    assert "w-[40%]" not in header and "w-[10%]" not in header, "不应再混用百分比列宽"



def test_registry_table_never_grows_sideways_from_long_text():
    """长描述/长 URL 不能把表格撑宽、把后面的列推出视野。

    ``truncate`` 在 auto 布局的表格里挡不住内容：单元格按 max-content 撑开整张表，
    用户就得横向滚动才能看到 Method / 状态。这里要求固定布局 + 长文本折行。
    """
    source = _source(REGISTRY)
    table_tag = source[source.index("<table"):source.index(">", source.index("<table"))]
    assert "table-fixed" in table_tag, "表格应使用固定布局"

    # 固定布局下每列都要有宽度，否则勾选列会吞掉剩余空间
    header = source[source.index("<thead"):source.index("</thead>")]
    assert 'class="w-12 px-4 py-3"' in header, "勾选列需要明确宽度"

    assert "break-words" in source, "描述应折行"
    assert "break-all" in source, "URL 应折行（无空格，必须 break-all）"
    assert "truncate\">{{ t.description }}" not in source, "描述不应再单行截断"
    assert "truncate select-all" not in source, "URL 不应再单行截断"
