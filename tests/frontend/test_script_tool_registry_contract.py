"""脚本工具前端契约（源码级）。

跨文件的四个不变量，任一处漏掉都会让功能「看起来有、其实不通」：

1. 注册表页有「脚本工具」tab，且脚本列表是独立组件（不塞进 API 工具表）；
2. 运行环境提示来自后端 capabilities，**不得**在页面写死「在沙箱内执行」；
3. 内置脚本可编辑但不可删除/改名；编辑入口不挡，删除入口和改名必须挡；
4. 智能体配置加载脚本工具时透传 ``group_name``，且过滤掉停用工具。
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "frontend/src/components/system/ToolRegistry.vue"
SCRIPT_LIST = ROOT / "frontend/src/components/system/ScriptToolList.vue"
WIZARD = ROOT / "frontend/src/components/system/ScriptToolWizard.vue"
SCRIPT_WIZARD = ROOT / "frontend/src/components/system/ScriptToolWizard.vue"
SCRIPT_HELP = ROOT / "frontend/src/components/system/ScriptToolHelpModal.vue"
API_TOOL = ROOT / "frontend/src/api/tool.ts"
AGENT_MANAGEMENT = ROOT / "frontend/src/views/AgentManagement.vue"


def test_registry_has_script_tab_and_mounts_list():
    source = REGISTRY.read_text(encoding="utf-8")
    assert "脚本工具" in source
    assert "ScriptToolList" in source
    assert "activeToolType" in source
    # 页面标题不再是「API 工具注册表」：两类工具都在这张卡里
    assert "工具注册表" in source


def test_script_list_component_exists():
    assert SCRIPT_LIST.is_file()
    source = SCRIPT_LIST.read_text(encoding="utf-8")
    assert "scriptToolApi" in source
    # 试运行入口
    assert "preview" in source


def test_sandbox_notice_comes_from_backend():
    """local 策略下页面不能声称在沙箱内执行——文案必须来自 capabilities。"""
    source = SCRIPT_LIST.read_text(encoding="utf-8")
    assert "capabilities" in source
    assert "capabilities.notice" in source or "capabilities?.notice" in source

    # 前端任何地方都不允许出现写死的「在沙箱内执行」承诺
    for path in (SCRIPT_LIST, SCRIPT_WIZARD, SCRIPT_HELP):
        text = path.read_text(encoding="utf-8")
        assert "在沙箱内执行" not in text, f"{path.name} 写死了沙箱承诺，应使用后端文案"


def test_builtin_rows_are_editable_but_flagged_when_overridden():
    """内置脚本可编辑，但「改过」必须看得见，并且能一键退回平台版本。

    用户明确选择「允许直接编辑内置」，前提是页面如实标注：否则平台后续修复
    悄悄失效，排障时会以为是脚本本身有问题。
    """
    source = SCRIPT_LIST.read_text(encoding="utf-8")
    # 编辑入口不再对内置行设限
    open_edit = source[source.index("function openEdit"):]
    open_edit = open_edit[: open_edit.index("function openCopy")]
    assert "source === 'builtin'" not in open_edit, "内置行必须能进编辑向导"
    # 覆盖后要有徽标与恢复入口
    assert "tool.is_overridden" in source
    assert "已覆盖" in source
    assert "pendingReset" in source
    assert "resetToBuiltin" in source
    # 复制为新工具仍然保留（改名走这条路）
    assert "openCopy" in source


def test_wizard_lets_builtin_edit_code_but_locks_name():
    """代码可改、名字锁死：改名会让「恢复默认」和内置兜底匹配失效。"""
    source = WIZARD.read_text(encoding="utf-8")
    assert "codeReadonly" not in source, "代码区不应再留只读死代码"
    assert "const nameReadonly = computed(() => isBuiltin.value)" in source
    assert ":readonly=\"nameReadonly\"" in source
    assert "已覆盖" in source, "向导里要说明保存后的后果"


def test_api_client_covers_all_script_endpoints():
    source = API_TOOL.read_text(encoding="utf-8")
    assert "scriptToolApi" in source
    for path in (
        "/api/portal/tools/script",
        "/api/portal/tools/script/capabilities",
        "/api/portal/tools/script/batch-group",
        "/api/portal/tools/script/preview",
    ):
        assert path in source, path
    assert "ScriptToolCapabilities" in source
    assert "ScriptToolPreviewResult" in source


def test_agent_management_merges_script_tools_with_group():
    source = AGENT_MANAGEMENT.read_text(encoding="utf-8")
    assert "scriptToolApi" in source
    assert "scriptTools" in source
    # 停用工具不进候选列表
    assert "t.is_active !== false" in source
    # 分组必须透传，否则智能体配置里的分组会静默失效
    assert "group_name: t.group_name ?? null" in source


def test_agent_management_guards_unsupported_script_tools():
    source = AGENT_MANAGEMENT.read_text(encoding="utf-8")
    # e2b / ssh 没有会话工作区映射，不能让人选完到运行时才报错
    assert "scriptToolApi.capabilities()" in source
    assert "scriptToolSupported" in source
    assert "isUnsupportedScriptTool" in source
    # 全选状态只看可选工具；否则含不支持脚本的分组永远无法再次点击取消全选
    assert "getSelectableStaticGroupTools" in source
    all_selected_block = source[source.index("const isAllStaticGroupSelected"):]
    all_selected_block = all_selected_block[: all_selected_block.index("const toggleSelectAllStatic")]
    assert "getSelectableStaticGroupTools(label)" in all_selected_block


def test_wizard_and_help_exist():
    assert SCRIPT_WIZARD.is_file(), "缺少新建/编辑脚本工具的向导组件"
    assert SCRIPT_HELP.is_file(), "缺少「?」使用指南弹窗"
    help_source = SCRIPT_HELP.read_text(encoding="utf-8")
    # 指南必须讲清运行契约，而不是只写「写个 python 脚本」
    assert "stdin" in help_source.lower()
    assert "sys.exit" in help_source


def test_help_matches_real_output_cap_and_scope():
    source = SCRIPT_HELP.read_text(encoding="utf-8")
    assert "80KB" in source
    assert "100KB" not in source
    # local 策略下脚本就是后端进程权限，不能写「一律访问不到」
    assert "一律访问不到" not in source
    assert "也不会访问外部网络" not in source


def test_preview_notice_uses_script_tool_supported():
    source = SCRIPT_LIST.read_text(encoding="utf-8")
    assert "capabilities?.script_tool_supported" in source
    assert "capabilities?.sandbox_enabled" not in source
    # 迁移漏跑时的兜底行没有库 id，操作必须禁用并给出提示
    assert "isFallback(tool)" in source
    assert "脚本工具表尚未初始化" in source


def test_script_tool_count_is_loaded_before_tab_switch():
    """tab 上的脚本工具数量不能等切过去才出现。

    脚本列表是懒挂载的（``v-if``），它上报的 count 在首次进入页面时并不存在，
    于是 tab 显示 0、切过去才变真实值——用户会以为「一个都没有」。
    这条断言盯住「父组件在挂载时自己拉一次」这个前提。
    """
    source = REGISTRY.read_text(encoding="utf-8")
    assert "loadScriptToolCount" in source
    assert "scriptToolApi.list()" in source
    # 必须在 onMounted 里调用，而不是等子组件的 count 事件
    on_mounted = source[source.index("onMounted(() => {"):]
    on_mounted = on_mounted[: on_mounted.index("})")]
    assert "loadScriptToolCount()" in on_mounted


def test_preview_form_uses_schema_driven_fields():
    """试运行不能只给一个空 JSON 框：要按参数定义生成表单。"""
    source = SCRIPT_LIST.read_text(encoding="utf-8")
    assert "previewFields" in source
    assert "parseToolSchema" in source
    # 类型转换与必填判定抽到纯函数模块，组件不再自留一份
    assert "@/utils/scriptToolParams" in source
    for helper in ("buildParamsFromForm", "defaultValues", "valuesFromObject"):
        assert helper in source


def test_script_tool_params_behaviour_via_node():
    """表单值 → stdin JSON 的类型转换必须真跑一遍。

    静态断言看不出「布尔 false 被当成没填」「1.5 传给 integer」这类错误，
    而这些错在界面上看起来都是「成功」。
    """
    import shutil
    import subprocess

    if shutil.which("node") is None:
        pytest.skip("需要 node 才能执行纯函数测试")

    script = Path("tests/frontend/script_tool_params.mjs")
    assert script.exists(), "参数构造测试脚本缺失"
    proc = subprocess.run(
        ["node", "--experimental-strip-types", "--no-warnings", str(script)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "试运行参数构造测试全部通过" in proc.stdout


def test_code_area_uses_codemirror_editor():
    """脚本代码必须是带行号与高亮的编辑器，不是纯 textarea。

    纯文本框没有行号、没有语法高亮，长脚本里定位一行都费劲——而脚本工具的
    全部价值就在这段代码上。
    """
    wizard = WIZARD.read_text(encoding="utf-8")
    assert "<CodeEditor" in wizard, "代码区应改用 CodeMirror 组件"
    assert "rows=\"16\"" not in wizard, "旧的 textarea 应已移除"

    editor = (ROOT / "frontend/src/components/CodeEditor.vue").read_text(encoding="utf-8")
    assert "basicSetup" in editor, "basicSetup 提供行号、括号匹配、搜索"
    assert "@codemirror/lang-python" in editor, "需要 Python 语法高亮"
    assert "lineWrapping" in editor

    pkg = (ROOT / "frontend/package.json").read_text(encoding="utf-8")
    assert "@codemirror/lang-python" in pkg, "语言包必须在依赖里，否则构建后运行时报错"


def test_fallback_flag_comes_from_backend_not_id_prefix():
    """「迁移未执行」的判定必须用后端标记，不能按 id 前缀猜。

    库里内置行的 id 就叫 builtin-script-*，按前缀判断会把正常行全判成兜底，
    页面永远显示「迁移未执行」并锁死新建入口——这正是线上踩到的那个 bug。
    """
    source = SCRIPT_LIST.read_text(encoding="utf-8")
    assert "tool.is_fallback === true" in source
    assert "startsWith('builtin-script-')" not in source


def test_script_list_has_selection_and_batch_group():
    """脚本工具列表要和 API 工具一样支持勾选 + 批量归类。

    没有批量入口时，给一批新脚本归类只能一个个点开改，十几个就点不动了。
    """
    source = SCRIPT_LIST.read_text(encoding="utf-8")
    assert "selectedIds" in source
    assert "批量设置分组" in source
    assert "batchSetGroup" in source
    assert 'type="checkbox"' in source, "需要行首复选框与全选"
    assert "toggleAll" in source and "toggleOne" in source


def test_filters_collapsed_into_panel():
    """来源/状态/分组三个下拉应收进「筛选」面板，不再常驻占位。"""
    script_list = SCRIPT_LIST.read_text(encoding="utf-8")
    assert "showFilterPanel" in script_list
    assert "activeFilterCount" in script_list
    # 常驻下拉的痕迹：三个 select 并排 + 旧标题文案
    assert "来源：全部" not in script_list
    assert "状态：全部" not in script_list

    registry = (ROOT / "frontend/src/components/system/ToolRegistry.vue").read_text(encoding="utf-8")
    assert "showToolFilterPanel" in registry
    assert "Method：全部" not in registry


def test_group_input_uses_shared_group_select():
    """新建/编辑的分组控件要能「选已有」也能「建新的」。

    原来 API 工具用 `<input list>` + datalist，脚本工具用纯 input：前者看不出
    有现成可选，后者完全不知道有哪些分组，两边还各写一套。
    """
    group_select = ROOT / "frontend/src/components/system/GroupSelect.vue"
    assert group_select.exists(), "应抽出共用分组选择器"
    source = group_select.read_text(encoding="utf-8")
    assert 'role="combobox"' in source
    assert "listbox" in source
    assert "将新建分组" in source, "输入新名字时要明确提示会新建分组"

    for path in (
        ROOT / "frontend/src/components/system/ScriptToolWizard.vue",
        ROOT / "frontend/src/components/system/ToolRegistry.vue",
    ):
        text = path.read_text(encoding="utf-8")
        assert "GroupSelect" in text, f"{path.name} 应改用共用分组选择器"
