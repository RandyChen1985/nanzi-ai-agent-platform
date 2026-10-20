"""表单模式也要能试跑，不能只在向导第 4 步提供。"""
from pathlib import Path

REGISTRY = Path("frontend/src/components/system/ToolRegistry.vue")


def test_form_mode_embeds_preview_tester_with_live_form_values():
    source = REGISTRY.read_text(encoding="utf-8")
    assert "import ApiToolTester from './ApiToolTester.vue'" in source
    assert source.count("<ApiToolTester") >= 1
    assert ':method="toolForm.method || \'GET\'"' in source
    assert ':url-template="toolForm.url_template || \'\'"' in source
    assert ':headers-str="toolForm.headers_str || \'{}\'"' in source
    assert ':schema-str="toolForm.parameter_schema_str || \'{}\'"' in source
