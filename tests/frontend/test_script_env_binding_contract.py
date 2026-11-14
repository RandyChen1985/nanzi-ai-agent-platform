from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
def test_structured_binding_contract():
    wizard = (ROOT / 'frontend/src/components/system/ScriptToolWizard.vue').read_text()
    assert wizard.count('value_field:') >= 3
    assert 'envValueField' in wizard
    assert 'system-only' in wizard

def test_script_scene_available():
    for name in ('SecretCenter.vue', 'AdminSecretCenter.vue'):
        text = (ROOT / 'frontend/src/views' / name).read_text()
        scene = next(line for line in text.splitlines() if "key: 'script_env'" in line)
        assert 'wired: true' in scene

def test_only_published_fields_and_stale_requests_are_guarded():
    wizard = (ROOT / 'frontend/src/components/system/ScriptToolWizard.vue').read_text()
    start = wizard.index('async function onEnvSecretPicked')
    end = wizard.index('async function saveEnvBinding', start)
    body = wizard[start:end]
    assert '...item.extra_fields' not in body
    assert 'Object.entries(type.schema.properties)' in body
    assert 'requestId === envFieldsRequestId' in body
    assert body.count('requestId === envFieldsRequestId') >= 2
