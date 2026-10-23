"""模型配置页「错误详情可诊断」契约测试。

安装方拿不到后端日志，所以加载模型列表 / 测试连接 / 保存失败时，界面必须
同时给出三样东西：简短标题、后端拼好的排查原文、以及可复制的入口。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "frontend/src/components/system/ModelRegistry.vue"
TOAST = ROOT / "frontend/src/components/Toast.vue"
MODELS_ENDPOINT = ROOT / "app/api/portal/endpoints/models.py"
DIAGNOSTICS = ROOT / "app/utils/provider_diagnostics.py"


def _registry_source() -> str:
    return REGISTRY.read_text(encoding="utf-8")


def test_model_registry_keeps_diagnostic_text_in_modal():
    source = _registry_source()

    assert "const modalError = ref('')" in source
    assert "const diagnosticSteps = ref<ModelDiagnosticStep[]>([])" in source
    assert (
        "const showModalError = (title: string, detail: string, steps: ModelDiagnosticStep[] = []) => {"
        in source
    )
    assert "modalError.value = detail" in source
    # 长文本要能留在弹窗里慢慢读，而不是 3.5 秒就消失的 toast
    assert "duration: 8000" in source


def test_every_model_form_failure_routes_through_shared_handler():
    source = _registry_source()

    assert "showModalError('加载模型列表失败'" in source
    assert "showModalError('测试连接失败'" in source
    assert "showModalError('保存失败'" in source
    # 失败响应把步骤放在 data.steps，必须一起带回弹窗
    assert "providerErrorSteps(e)" in source
    # 旧实现只拼一句话，异常文本常常是空的，排查无从下手
    assert "showToast(`加载模型列表失败: " not in source
    assert "showToast('保存失败: '" not in source


def test_successful_calls_also_render_the_step_timeline():
    source = _registry_source()

    assert "const showDiagnosticSteps = (steps?: ModelDiagnosticStep[] | null) => {" in source
    assert "showDiagnosticSteps(response.data.steps)" in source
    assert 'v-for="(step, index) in diagnosticSteps"' in source
    assert "step.elapsed_ms ?? 0" in source
    assert "step.status === 'ok' ? '✓' : '✗'" in source


def test_error_panel_is_copyable():
    source = _registry_source()

    assert "const copyModalError = async () => {" in source
    assert "const diagnosticsText = (): string => {" in source
    assert "navigator.clipboard.writeText(text)" in source
    assert 'aria-label="关闭过程详情"' in source
    assert 'whitespace-pre-wrap break-all font-mono' in source


def test_toast_preserves_line_breaks_from_backend_details():
    source = TOAST.read_text(encoding="utf-8")

    # 后端诊断原文是多行文本，toast 若按默认空白折叠会挤成一行
    assert source.count("whitespace-pre-wrap") >= 2


def test_discovery_endpoint_uses_structured_diagnostics():
    source = MODELS_ENDPOINT.read_text(encoding="utf-8")
    # 只看 discover 函数体：embedding 连通性测试里另有 raise_for_status
    discover = source[
        source.index("async def discover_models") : source.index('@router.post("", response_model=AIModelResponse)')
    ]

    assert "describe_http_failure(" in discover
    assert "describe_transport_failure(" in discover
    assert "describe_invalid_response(" in discover
    # 旧的裸异常拼接把空 message 直接暴露成「…列表：」
    assert 'detail=f"无法获取供应商模型列表：{exc}"' not in discover
    assert "response.raise_for_status()" not in discover

    # 返回状态码非 2xx 时要带上供应商原文，而不是只报「请求失败」
    assert "body=response.text" in discover
    # 测试连接走 SDK 异常分类
    assert "describe_sdk_failure(e)" in source


def test_diagnostics_module_redacts_secrets_and_names_exception_type():
    source = DIAGNOSTICS.read_text(encoding="utf-8")

    assert "def redact_secrets(" in source
    assert 'f"{type(exc).__name__}: {raw}"' in source
    assert "HTTP_PROXY" in source


def test_discovery_reports_missing_api_key_separately():
    source = MODELS_ENDPOINT.read_text(encoding="utf-8")

    # Key 为空时 401 很容易被误读成「Key 填错了」，要点明是没带上 Key
    assert "本次请求没有携带 API Key" in source


def test_error_toast_offers_copy_affordance():
    """错误 toast 常常是一整段后端诊断，必须能一键复制带走。"""

    source = TOAST.read_text(encoding="utf-8")

    assert "copyToastText" in source
    assert "navigator.clipboard.writeText(copyPayload.value)" in source
    # 标题 + 正文一起复制，避免漏掉「哪个模型/哪个操作失败」
    assert "props.title, props.description || props.message" in source
    # 悬浮态才显眼的浮标，但静态态保持可见（触屏也能发现）
    assert "group-hover:opacity-100" in source
    assert '复制错误详情' in source
    # 复制后给出即时反馈，不额外弹 toast
    assert "copied.value = true" in source
