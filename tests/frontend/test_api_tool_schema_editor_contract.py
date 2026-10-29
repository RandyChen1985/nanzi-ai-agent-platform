"""API 工具「参数定义」可视化编辑器契约（静态源码断言）。

背景：``sys_api_tools.parameter_schema`` 由运行时的 ``GenericApiToolFactory`` 消费，它同时兼容
三种写法——标准 JSON Schema（``properties`` + 顶层 ``required``）、扁平 map（顶层即参数表）、
极简（``"param": "描述"``）。此外还有两个反直觉行为必须被可视化编辑器尊重：

1. 参数默认是**必填**（``param_def.get("required", True)``），与标准 JSON Schema 的默认可选相反；
2. 标准形态下 ``required: []`` 表达不了「全部可选」——空列表是 falsy，会掉回默认必填分支，
   因此非必填参数必须写**内联 ``required: false``**。

结论：可视化编辑器只接管 ``properties`` / ``required``，其余顶层键（如 ``x-nanzi-evidence-types``）
与参数级其余键（如 ``items`` / ``properties`` 子结构）必须原样保留，且不改变原有结构形态。

**锚点迁移说明（2026-10-15 加向导模式时）**：编辑器从 ``ToolRegistry.vue`` 抽成了共享组件
``ToolParamEditor.vue``——因为「添加工具」有了表单/向导两个 tab，参数编辑 UI 若各写一份必然
逐渐漂移，而参数定义正是运行时真正消费的东西。以下与编辑器 UI 相关的断言随之指向新组件；
与入口（``openModal``）相关的断言留在 ``ToolRegistry.vue``。
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REGISTRY = Path("frontend/src/components/system/ToolRegistry.vue")
PARAM_EDITOR = Path("frontend/src/components/system/ToolParamEditor.vue")
SCHEMA_UTIL = Path("frontend/src/utils/toolSchema.ts")
GENERIC_API = Path("app/services/ai/tools/generic_api.py")
# schema 解析已抽成公共模块（API 工具与脚本工具共用同一套口径）
PARAMETER_SCHEMA = Path("app/services/ai/tools/parameter_schema.py")


def _source(path: Path) -> str:
    if not path.exists():
        pytest.fail(f"缺少文件：{path}")
    return path.read_text(encoding="utf-8")


def _body(source: str, marker: str) -> str:
    """取以 ``marker`` 开头那个块的括号平衡内容（函数体 / 对象字面量）。"""
    start = source.index(marker)
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[brace : index + 1]
    raise AssertionError(f"未找到平衡块：{marker}")


# --------------------------------------------------------------------------- #
# 前端：JSON / 可视化 双模式
# --------------------------------------------------------------------------- #
def test_schema_editor_offers_both_modes():
    source = _source(PARAM_EDITOR)
    assert "schemaEditorMode" in source
    assert "switchSchemaMode" in source
    assert ">JSON</button>" in source
    assert ">可视化</button>" in source


def test_textarea_only_renders_in_json_mode():
    """可视化模式下仍然露出 JSON 文本框就等于两套输入在打架。"""
    source = _source(PARAM_EDITOR)
    assert 'v-if="schemaEditorMode === \'json\'"' in source
    # 组件对文本是受控的：JSON 模式直接写回 modelValue
    assert ':value="modelValue"' in source


def test_switching_to_visual_keeps_invalid_json_untouched():
    """JSON 写错时不能切走：否则用户既看不到原文本，也不知道错在哪。"""
    body = _body(_source(PARAM_EDITOR), "const switchSchemaMode")
    assert "loadVisualFromJson()" in body
    assert re.search(r"if\s*\(\s*!\s*loadVisualFromJson\s*\(\s*\)\s*\)", body), body
    assert "return" in body


def test_switching_back_serializes_visual_rows():
    body = _body(_source(PARAM_EDITOR), "const switchSchemaMode")
    assert "buildSchemaJson()" in body
    # 组件不再直接摸 toolForm，而是通过受控值写回
    assert "update:modelValue" in body


def test_visual_rows_expose_name_type_required_description_default():
    source = _source(PARAM_EDITOR)
    for field in ("p.name", "p.type", "p.required", "p.description", "p.defaultValue"):
        assert field in source, f"可视化行缺少 {field}"
    # 类型下拉由常量数组驱动（比硬编码 6 个 option 更好维护），这里校验那个数组
    options_line = next(
        line for line in _source(SCHEMA_UTIL).splitlines()
        if line.strip().startswith("export const PARAM_TYPE_OPTIONS")
    )
    for type_name in ("string", "number", "integer", "boolean", "array", "object"):
        assert f"'{type_name}'" in options_line, f"类型选项缺少 {type_name}"


def test_visual_rows_can_be_added_and_removed():
    source = _source(PARAM_EDITOR)
    assert "addVisualParam" in source
    assert "removeVisualParam" in source
    assert "添加参数" in source


def test_default_value_only_applies_to_optional_params():
    """运行时只在非必填时读取 ``default``，必填参数上填默认值是误导。"""
    source = _source(PARAM_EDITOR)
    assert ':disabled="p.required"' in source


def test_visual_editor_preserves_unknown_top_level_keys():
    """顶层扩展键（如 x-nanzi-evidence-types）不能被可视化编辑吃掉。"""
    body = _body(_source(SCHEMA_UTIL), "export const parseToolSchema")
    assert "rest[key] = value" in body
    serialize = _body(_source(SCHEMA_UTIL), "export const serializeToolSchema")
    assert "...schema.rest" in serialize
    # 组件不得自己拼 schema，必须走纯函数
    editor = _source(PARAM_EDITOR)
    assert "serializeToolSchema" in editor and "parseToolSchema" in editor


def test_visual_editor_preserves_per_param_extras():
    """参数级其余键（items / properties / format …）必须原样带回，否则嵌套结构会被抹掉。"""
    source = _source(SCHEMA_UTIL)
    assert "extra: Record<string, any>" in source
    body = _body(source, "export const serializeToolSchema")
    assert "...param.extra" in body
    load_body = _body(source, "export const parseToolSchema")
    assert "extra" in load_body


def test_optional_params_emit_inline_required_false():
    """标准形态下 ``required: []`` 在运行时是无效的，必须靠内联 required: false。"""
    body = _body(_source(SCHEMA_UTIL), "export const serializeToolSchema")
    assert re.search(r"required\s*=\s*false", body), body


def test_visual_editor_detects_standard_and_flat_shapes():
    """扁平 map 与标准 JSON Schema 都要能打开，且保存时维持原有形态。"""
    source = _source(SCHEMA_UTIL)
    assert "ToolSchemaShape" in source
    body = _body(source, "export const parseToolSchema")
    assert "parsed.properties" in body
    build_body = _body(source, "export const serializeToolSchema")
    assert "schema.shape" in build_body
    # 原本没有顶层 required 数组时不要凭空添加（保真）
    assert "schema.hadRequiredList" in build_body


def test_nested_params_are_flagged_to_the_user():
    source = _source(PARAM_EDITOR)
    assert "visualNestedCount" in source
    assert "JSON 模式" in source
    assert "嵌套" in source


def test_imported_tool_schema_is_loaded_by_shape_not_assumption():
    """编辑已有工具时要先按形态解析，不能假定某一种写法。"""
    body = _body(_source(PARAM_EDITOR), "const loadVisualFromJson")
    assert "parseToolSchema(" in body
    assert "modelValue" in body
    # 外部值变化时必须重新同步，否则行内改动会把外部刚写进来的 schema 覆盖掉
    assert re.search(r"watch\s*\(", _source(PARAM_EDITOR)), "缺少对 modelValue 的同步"


def test_both_tabs_share_one_param_editor():
    """表单模式与向导模式必须共用同一个编辑器——两份参数 UI 必然逐渐漂移。"""
    registry = _source(REGISTRY)
    assert "ToolParamEditor" in registry, "表单/向导应通过 ToolParamEditor 渲染参数区"
    # 编辑器仍绑在 toolForm 上：向导只是「填它的另一种交互」，不是第二个数据源
    assert 'v-model="toolForm.parameter_schema_str"' in registry


# --------------------------------------------------------------------------- #
# 后端：锁住可视化编辑器依赖的运行时行为
# --------------------------------------------------------------------------- #
def test_runtime_reads_standard_properties_block():
    """解析逻辑在 parameter_schema.py；generic_api 必须复用而不是另抄一份。"""
    assert '"properties" in schema_def' in _source(PARAMETER_SCHEMA)
    assert "_parse_parameter_schema(" in _source(GENERIC_API)


def test_runtime_defaults_params_to_required():
    """这条反直觉的默认值正是「非必填要显式写 required: false」的原因。"""
    source = _source(GENERIC_API)
    assert 'param_def.get("required", True)' in source


def test_runtime_keeps_platform_extension_keys():
    source = _source(GENERIC_API)
    assert "x-nanzi-evidence-types" in source


def test_runtime_accepts_flat_map_and_plain_string_forms():
    # 扁平 map：顶层直接当参数表（解析在公共模块）
    assert "properties = schema_def" in _source(PARAMETER_SCHEMA)
    # 极简写法：值即描述（构造工具 schema 仍在本文件）
    assert "p_desc = str(param_def)" in _source(GENERIC_API)


# --------------------------------------------------------------------------- #
# 行为验证：静态断言看不出「转换会不会写坏用户数据」，这里用 Node 真跑一遍纯函数
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能执行纯函数往返测试")
def test_schema_roundtrip_behaviour_via_node():
    """覆盖静态断言覆盖不到的核心风险：解析/序列化是否保真。"""
    script = Path("tests/frontend/tool_schema_roundtrip.mjs")
    assert script.exists(), "往返测试脚本缺失"
    proc = subprocess.run(
        ["node", "--experimental-strip-types", "--no-warnings", str(script)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "往返测试全部通过" in proc.stdout
