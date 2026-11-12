"""模型选择器可搜索化的接线契约。

版本配置的「编排模型 / 合成模型」原先用原生 select，模型一多只能逐条翻找；
契约锁住三件事：可搜索组件确实存在、两处下拉确实换成了它、搜索/键盘/防裁剪能力没有被改回 select。
"""

from pathlib import Path

import json
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure

COMPONENT = ROOT / "frontend/src/components/common/ModelSelect.vue"
DRAWER = ROOT / "frontend/src/components/agent/AgentVersionEditorDrawer.vue"
TOOL_MODAL = ROOT / "frontend/src/components/agent/ToolRuntimeConfigModal.vue"
SYSTEM_CONFIG = ROOT / "frontend/src/views/SystemConfig.vue"


def test_model_select_component_supports_search_keyboard_and_no_clipping():
    source = COMPONENT.read_text(encoding="utf-8")

    assert "Teleport to=\"body\"" in source
    assert "position: 'fixed'" in source
    assert "filterSelectableModels" in source
    assert "shouldShowModelSearch" in source
    assert "buildModelOptions" in source
    assert "buildPinnedModelOptions" in source
    # 搜索框、空结果文案、键盘导航
    assert "searchPlaceholder" in source
    assert "无匹配模型" in source
    assert "暂无可选模型" in source
    assert "ArrowDown" in source and "ArrowUp" in source and "Escape" in source
    assert "role=\"listbox\"" in source and "role=\"option\"" in source
    # 点击浮层外关闭 + 视口变化重新定位
    assert "onDocumentPointerDown" in source
    assert "onViewportChange" in source


def test_version_editor_uses_searchable_model_select_for_both_pickers():
    source = DRAWER.read_text(encoding="utf-8")
    model_step = source[source.index("versionConfigStep === 'model'"):source.index("<!-- Step 2: Tools -->")]

    assert "import ModelSelect from '../common/ModelSelect.vue'" in source
    assert "selectableLlmModels" in source
    # 两个下拉都换成组件；原生 select 不再承担模型列表
    assert model_step.count("<ModelSelect") == 2
    assert '<option v-for="m in llmModels()"' not in model_step
    assert "<select" not in model_step
    # 编排模型：占位 + 变更后联动温度
    assert 'placeholder="选择编排模型..."' in model_step
    assert "@update:model-value=\"setOrchestratorModel\"" in model_step
    assert "const setOrchestratorModel = (modelName: string) => {" in source
    assert "props.versionForm.temperature = selectedModel.temperature ?? props.globalModelTemperature;" in source
    # 合成模型：保留「跟随编排模型」空选项与绿色主题
    assert "allow-empty" in model_step
    assert 'empty-label="跟随编排模型"' in model_step
    assert 'accent="emerald"' in model_step


def test_tool_runtime_modal_no_longer_offers_model_picker():
    """工具级「执行模型」已下架：AgentScope 单模型 agent，工具是纯 callable，换模型无落点。

    历史上这个下拉只把值写进 `model_name`，而运行链没有任何消费者——
    留着它等于承诺一个不存在的功能。参数位改由「执行超时」承接。
    """
    source = TOOL_MODAL.read_text(encoding="utf-8")

    assert "ModelSelect" not in source
    # 「已移除」的占位说明后来也被去掉了：用户明确不需要在弹窗里看到这块。
    assert "执行模型" not in source
    assert "Override Model" not in source
    assert "执行超时 (Execute Timeout)" in source
    assert "timeout_seconds" in source
    assert "<select" not in source


def _can_render_sfc() -> bool:
    if shutil.which("node") is None:
        return False
    node_modules = ROOT / "frontend/node_modules"
    return all((node_modules / name).exists() for name in ("vue", "esbuild", "@vue/server-renderer"))


@pytest.mark.skipif(not _can_render_sfc(), reason="前端依赖未安装，跳过真渲染探针")
def test_model_select_renders_trigger_label_and_disabled_state():
    """真编译真渲染：触发器文案与禁用态必须是组件渲染出来的，而不是契约里的字符串。"""
    script = ROOT / "tests/frontend/model_select_ssr.cjs"
    proc = subprocess.run(["node", str(script)], cwd=ROOT / "frontend", capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout)

    assert data["selected"]["showsModelName"] is True, "选中模型后触发器要显示模型名"
    assert data["selected"]["closedNoListbox"] is True, "未展开时不得渲染下拉浮层"
    assert data["placeholder"]["showsPlaceholder"] is True, "未选择且不允许空值时显示占位文案"
    assert data["followUpstream"]["showsEmptyLabel"] is True, "允许空值时显示「跟随编排模型」"
    assert data["multimodalWithId"]["showsId"] is True, "多模态模型带视觉前缀并可按需附带 model_id"
    assert data["disabled"]["triggerDisabled"] is True
    assert data["disabled"]["keepsHint"] is True, "禁用态要保留说明，避免用户以为控件坏了"
    assert data["unknownValue"]["showsUnknownLabel"] is True, "列表外的当前值（环境变量注入）也要显示出处"


def test_system_config_model_pickers_use_searchable_select():
    source = SYSTEM_CONFIG.read_text(encoding="utf-8")

    assert "import ModelSelect from '../components/common/ModelSelect.vue'" in source
    # 「全局默认模型」与相邻的「多模态模型」两个下拉都换成可搜索组件
    assert "item.key === 'llm_model_name'" in source
    assert "item.key === 'multimodal_model_name'" in source
    assert source.count("<ModelSelect") == 2
    assert ':models="llmModelsForConfig"' in source
    assert ':models="multimodalModelsForConfig"' in source
    # 候选仍在页面侧算好（默认大模型要放行多模态），组件只做搜索
    assert "isMultimodalModelType" in source
    # 列表外的当前值（环境变量注入 / 已停用）必须保留成置顶项，换了组件也不能丢
    assert 'unknown-option-label="未知/环境变量"' in source
    assert 'unknown-option-label="未知/未启用"' in source
    # 「默认大模型」仍是必选（无清空项）；「多模态模型」保留未配置语义与多模态标记
    assert 'multimodal-badge="· 多模态"' in source
    assert 'empty-label="未配置（不支持识图时提示用户）"' in source
    assert 'placeholder="选择默认模型..."' in source
    # 两个 key 分支里不得再有原生 select
    llm_block = source[source.index("item.key === 'llm_model_name'"):source.index("item.key === 'metadata_provider'")]
    assert "<select" not in llm_block
