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
