"""系统级参数编辑器与试跑提示的源码契约。"""
from pathlib import Path

PARAM_EDITOR = Path("frontend/src/components/system/ToolParamEditor.vue")
SYSTEM_EDITOR = Path("frontend/src/components/system/ToolSystemParamEditor.vue")
TESTER = Path("frontend/src/components/system/ApiToolTester.vue")
REGISTRY = Path("frontend/src/components/system/ToolRegistry.vue")


def _source(path: Path) -> str:
    assert path.exists(), f"缺少文件：{path}"
    return path.read_text(encoding="utf-8")


def test_param_editor_embeds_system_param_editor():
    source = _source(PARAM_EDITOR)
    assert "ToolSystemParamEditor" in source
    assert "systemParams" in source
    assert "validateSystemParams" in source


def test_system_editor_has_name_type_value_description_and_variable_picker():
    source = _source(SYSTEM_EDITOR)
    for field in ("p.name", "p.type", "p.value", "p.description"):
        assert field in source
    assert "PermissionVariableMenu" in source
    assert "添加系统级参数" in source
    assert "模型不可见" in source


def test_save_path_blocks_invalid_system_params():
    source = _source(REGISTRY)
    assert "validateSystemParams" in source
    assert "parseToolSchema" in source
    assert "showToast" in source


def test_tester_shows_system_params_are_injected_not_entered():
    source = _source(TESTER)
    assert "systemParams" in source
    assert "平台自动注入" in source
    assert "systemParams.length" in source


def test_tester_shows_the_actual_request_with_injected_values():
    source = _source(TESTER)
    assert "实际发出的请求" in source
    assert "result.request" in source
    assert "Headers（已脱敏）" in source
    assert "prettyJson" in source
