"""工具管理的防错与效率改进契约。

覆盖：未保存保护、保存反馈、Headers 键值编辑、试跑过期/cURL、列表快捷操作、
分组管理、移动端与高级配置折叠、参数落位提示。都是对 UI 源码的静态断言，
避免这些体验被后续重构悄悄改回去。
"""
from pathlib import Path

REGISTRY = Path("frontend/src/components/system/ToolRegistry.vue")
WIZARD = Path("frontend/src/components/system/ToolWizard.vue")
HEADERS = Path("frontend/src/components/system/ToolHeadersEditor.vue")
TESTER = Path("frontend/src/components/system/ApiToolTester.vue")
PARAM_EDITOR = Path("frontend/src/components/system/ToolParamEditor.vue")


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_editor_guards_unsaved_changes_including_wizard_paste():
    registry = _source(REGISTRY)
    wizard = _source(WIZARD)
    assert "const isDirty = computed" in registry
    assert "wizardDirty.value" in registry
    assert "showDiscardConfirm" in registry
    assert "requestCloseModal" in registry
    assert "beforeunload" in registry
    assert 'title="放弃未保存的修改？"' in registry
    assert "dirty-change" in registry
    assert "emit('dirty-change'" in wizard
    # 取消按钮必须走带保护的入口，不能直接 showModal = false。
    assert '@click="requestCloseModal"' in registry


def test_save_has_loading_inline_errors_and_sticky_footer():
    source = _source(REGISTRY)
    assert "const saving = ref(false)" in source
    assert "const formError = ref('')" in source
    assert "const fieldErrors" in source
    assert "data-tool-field=\"name\"" in source
    assert "data-tool-field=\"url_template\"" in source
    assert "sticky bottom-0" in source
    assert "保存中..." in source
    assert ':disabled="saving"' in source
    assert "focusField(" in source
    assert "fieldErrors.value.signing" in source
    assert 'v-if="fieldErrors.signing"' in source


def test_headers_editor_offers_rows_and_json_advanced_mode():
    source = _source(HEADERS)
    registry = _source(REGISTRY)
    assert "键值对" in source
    assert "JSON 高级" in source
    assert "parseHeaders" in source
    assert "rowsToJson" in source
    assert "ToolHeadersEditor" in registry
    assert 'v-model="toolForm.headers_str"' in registry


def test_tester_marks_stale_results_and_offers_curl_and_response_views():
    source = _source(TESTER)
    assert "resultStale" in source
    assert "测试值或签名配置已修改" in source
    assert "curlCommand" in source
    assert 'label="cURL"' in source
    assert "response_headers" in source
    assert "bodyView" in source
    assert "美化" in source and "原文" in source
    assert "autoOpen" in source


def test_registry_has_quick_actions_and_group_manager():
    source = _source(REGISTRY)
    assert "quickTestTool" in source
    assert 'hide-trigger' in source
    assert "试跑工具：" not in source
    assert "requestToggleActive" in source
    assert 'role="switch"' in source
    assert "showGroupManager" in source
    assert "renameOrMergeGroup" in source
    assert "toolApi.batchSetGroup" in source
    assert "confirmToggleActive" in source
    assert "分组管理" in source


def test_editor_uses_stacked_mobile_grid_and_collapsible_advanced_config():
    source = _source(REGISTRY)
    assert "grid grid-cols-1 gap-4 sm:grid-cols-2" in source
    assert "高级请求配置" in source
    assert "headerCount" in source
    assert '签名已开启' in source
    assert ":open=\"advancedOpen\"" in source
    assert "从请求重新识别参数" in source
    # 新建默认走向导，编辑已有工具默认走表单。
    assert "editorMode.value = tool && !isClone ? 'form' : 'wizard'" in source


def test_param_editor_shows_location_in_form_mode_too():
    source = _source(PARAM_EDITOR)
    assert "resolveParamLocation" in source
    assert 'v-if="locationOf(p.name)"' in source
    assert "mode === 'wizard' && locationLabel" not in source
