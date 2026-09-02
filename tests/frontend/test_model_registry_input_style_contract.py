from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_model_registry_modal_marks_editable_controls_with_visible_form_style():
    source = (ROOT / "frontend/src/components/system/ModelRegistry.vue").read_text(encoding="utf-8")

    assert source.count('class="model-form-control mt-1"') == 4
    assert 'class="model-form-control font-mono pr-3"' in source
    assert 'class="provider-select-trigger model-form-control"' in source
    for field in ("api_base_url", "api_key", "model_id", "type", "name"):
        assert f'v-model="modelForm.{field}"' in source
    assert "model-form-control-invalid" in source
    assert '.model-form-control {' in source
    assert 'border: 1.5px solid rgb(203 213 225);' in source
    assert 'background: rgb(248 250 252);' in source
    assert '.model-form-control:focus {' in source
    assert 'box-shadow: 0 0 0 3px rgb(219 234 254)' in source
