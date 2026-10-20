"""请求签名前端契约：开关、密钥只写、帮助入口与试跑透传。"""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend" / "src"
UTILS = FRONTEND / "utils" / "requestSigning.ts"
EDITOR = FRONTEND / "components" / "system" / "ToolSigningEditor.vue"
HELP = FRONTEND / "components" / "system" / "RequestSigningHelpModal.vue"
API = FRONTEND / "api" / "tool.ts"
COPY_BUTTON = FRONTEND / "components" / "common" / "CopyButton.vue"


def _run_node(script: Path) -> None:
    subprocess.run(
        ["node", "--experimental-strip-types", "--no-warnings", str(script)],
        cwd=ROOT,
        check=True,
    )


def test_signing_utils_generate_and_validate():
    _run_node(ROOT / "tests" / "frontend" / "request_signing_utils.mjs")


def test_signing_utils_expose_the_fixed_vector():
    source = UTILS.read_text(encoding="utf-8")
    for text in (
        "9cc4a7371ed4f5c9e62baffa7614f688c7fdf3a8e420b97c46ec56aea42168ad",
        "191f8fb213eb5569db7324218ab13737cd75320638510f914392ddba0ea1c164",
        "8f3c2a1b7d9e4c60",
        "SIGNING_TEST_VECTOR",
        "https://api.example.com/api/orders?a=1&b=2",
    ):
        assert text in source


def test_editor_has_switch_secret_and_help_entry():
    source = EDITOR.read_text(encoding="utf-8")
    for text in (
        "请求签名",
        "签名密钥",
        "生成密钥",
        "留空沿用已保存密钥",
        "查看请求签名接入说明",
        "RequestSigningHelpModal",
        "generateSigningSecret",
        "update:keyId",
        "update:secret",
        "ConfirmModal",
        "重新生成签名密钥？",
        "min-w-0 flex-1",
        "shrink-0 whitespace-nowrap",
        "显示签名密钥",
    ):
        assert text in source
    # 已保存密钥且输入框为空时不渲染「显示」和「复制」，避免无效按钮。
    assert source.count('v-if="secret"') >= 2
    # 两个字段改为上下两行，避免在窄列里把「复制 / 隐藏」挤成竖排。
    assert "sm:grid-cols-2" not in source


def test_help_modal_documents_headers_steps_and_test_vector():
    source = HELP.read_text(encoding="utf-8")
    for text in (
        "X-Nanzi-Signature-Version",
        "X-Nanzi-Key-Id",
        "X-Nanzi-Timestamp",
        "X-Nanzi-Nonce",
        "X-Nanzi-Signature",
        "HMAC-SHA256",
        "canonical_query",
        "SIGNING_TEST_VECTOR",
        "SIGNING_CANONICAL_EXAMPLE",
        "SIGNING_PYTHON_EXAMPLE",
        "SIGNING_CURL_EXAMPLE",
        "不能替代 HTTPS",
        ':z-index="12000"',
        "canonicalLineRows",
        "行号只是对照说明，不参与签名",
    ):
        assert text in source
    for field in (
        "VERSION",
        "METHOD",
        "PATH",
        "CANONICAL_QUERY",
        "SHA256_HEX_RAW_BODY",
        "TIMESTAMP",
        "NONCE",
        "KEY_ID",
    ):
        assert f"field: '{field}'" in source, f"8 行说明缺少字段：{field}"


def test_copy_button_never_wraps_its_label():
    source = COPY_BUTTON.read_text(encoding="utf-8")
    assert "shrink-0" in source
    assert "whitespace-nowrap" in source


def test_api_types_expose_signing_and_invalid_config_kind():
    source = API.read_text(encoding="utf-8")
    for text in (
        "signing_enabled",
        "signing_key_id",
        "signing_secret_configured",
        "signing_secret",
        "invalid_signing_config",
        "tool_id",
    ):
        assert text in source


REGISTRY = FRONTEND / "components" / "system" / "ToolRegistry.vue"
WIZARD = FRONTEND / "components" / "system" / "ToolWizard.vue"
TESTER = FRONTEND / "components" / "system" / "ApiToolTester.vue"


def test_registry_form_embeds_signing_editor_and_passes_draft_to_tester():
    source = REGISTRY.read_text(encoding="utf-8")
    for text in (
        "import ToolSigningEditor from './ToolSigningEditor.vue'",
        "v-model:enabled=\"toolForm.signing_enabled\"",
        "v-model:key-id=\"toolForm.signing_key_id\"",
        "v-model:secret=\"toolForm.signing_secret\"",
        "signing_enabled: signingEnabled",
        "signing_secret: signingSecret",
        ":signing-enabled=\"!!toolForm.signing_enabled\"",
        ":signing-secret=\"toolForm.signing_secret || ''\"",
        ":tool-id=\"toolForm.id\"",
    ):
        assert text in source


def test_wizard_step_four_reuses_the_same_signing_editor_and_tester_props():
    source = WIZARD.read_text(encoding="utf-8")
    for text in (
        "import ToolSigningEditor from './ToolSigningEditor.vue'",
        "v-model:enabled=\"toolForm.signing_enabled\"",
        "v-model:key-id=\"toolForm.signing_key_id\"",
        "v-model:secret=\"toolForm.signing_secret\"",
        ":signing-enabled=\"!!toolForm.signing_enabled\"",
        ":tool-id=\"toolForm.id\"",
    ):
        assert text in source


def test_tester_sends_signing_fields_and_shows_signature_headers():
    source = TESTER.read_text(encoding="utf-8")
    for text in (
        "signingEnabled?: boolean",
        "signingSecret?: string",
        "toolId?: string | null",
        "signing_enabled: !!props.signingEnabled",
        "signing_key_id: props.signingKeyId || null",
        "tool_id: props.toolId || null",
        "请求签名已开启",
        "签名 Header（已脱敏）",
        "invalid_signing_config",
    ):
        assert text in source
