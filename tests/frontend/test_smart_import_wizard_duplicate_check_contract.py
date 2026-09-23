"""智能导入向导数据集 ID 重名检测契约测试。

验证并锁定以下行为：
1. SmartImportWizard 声明了 existingDatasetNames、isDuplicateDatasetName 与 isSaveDisabled。
2. 在向导打开时（!props.datasetId）触发 fetchExistingDatasetNames 拉取已有数据集清单。
3. 发现重名时：输入框高亮红色警示样式，并呈现「该数据集 ID 已存在」文字提示。
4. 底部保存按钮绑定 isSaveDisabled，重名或未通过校验时禁止点击并呈现 disabled:cursor-not-allowed 样式与 title 提示。
5. handleSave 中包含针对重名的前置安全守卫，并在 catch 中容错解析 message/detail 错误信息。
"""

from pathlib import Path
import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).parents[2]
WIZARD_FILE = ROOT / "frontend/src/components/metadata/SmartImportWizard.vue"


def test_smart_import_wizard_has_duplicate_name_state_and_fetch_logic() -> None:
    content = WIZARD_FILE.read_text(encoding="utf-8")

    assert "existingDatasetNames = ref<Set<string>>(new Set())" in content
    assert "isDuplicateDatasetName = computed(" in content
    assert "isSaveDisabled = computed(" in content
    assert "fetchExistingDatasetNames" in content
    assert "metadataApi.getDatasets()" in content


def test_smart_import_wizard_renders_duplicate_warning_and_red_border() -> None:
    content = WIZARD_FILE.read_text(encoding="utf-8")

    # 错误提示文案与图标
    assert "该数据集 ID 已存在，请更换其他 ID" in content
    assert "isDuplicateDatasetName" in content
    # 红色边框高亮类
    assert "!border-red-400" in content


def test_smart_import_wizard_save_button_disabled_when_invalid() -> None:
    content = WIZARD_FILE.read_text(encoding="utf-8")

    # 确认保存按钮绑定了 isSaveDisabled
    assert ':disabled="isSaveDisabled"' in content
    assert "disabled:cursor-not-allowed" in content
    assert "数据集 ID 已存在，请更换后再保存" in content


def test_smart_import_wizard_handle_save_guard_and_message_error_handling() -> None:
    content = WIZARD_FILE.read_text(encoding="utf-8")

    # 前置守卫
    assert "if (isDuplicateDatasetName.value) {" in content
    # catch 块兼容 message 与 detail
    assert "const errorMsg = e.response?.data?.message || e.response?.data?.detail || ''" in content
    assert "errorMsg.includes('存在')" in content
