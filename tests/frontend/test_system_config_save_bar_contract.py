from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_system_config_save_bar_contract():
    content = _source("frontend/src/views/SystemConfig.vue")

    # 1. 验证变更检测与未保存计算属性
    assert "hasUnsavedConfigChanges" in content
    assert "unsavedConfigCount" in content
    assert "changedConfigsList" in content
    assert "resetUnsavedConfigs" in content

    # 2. 验证快捷键支持与生命周期监听
    assert "handleGlobalKeydown" in content
    assert "window.addEventListener('keydown', handleGlobalKeydown)" in content
    assert "window.removeEventListener('keydown', handleGlobalKeydown)" in content

    # 3. 验证顶部常驻工具栏中的修改提示与操作按钮
    assert "已修改 {{ unsavedConfigCount }} 项参数（未保存）" in content
    assert "保存变更 (⌘S)" in content

    # 4. 验证底部浮动吸底保存提示条 (Sticky Save Bar)
    # 只锁定“吸底浮动”这一行为特征；具体偏移量（bottom-6 / bottom-8 …）属视觉微调，不应让契约失效
    assert "hasUnsavedConfigChanges && canSave" in content
    assert "放弃修改" in content
    assert "fixed bottom-" in content
