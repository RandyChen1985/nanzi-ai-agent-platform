"""Contract: 模型选择下拉菜单支持键盘上下翻动、视口自动滚动跟随与回车激活选中。"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHAT_INPUT = ROOT / "frontend" / "src" / "components" / "embed" / "ChatInput.vue"


def test_model_dropdown_keyboard_scroll_contract():
    text = CHAT_INPUT.read_text(encoding="utf-8")

    # 1. 响应式与引用定义
    assert "activeModelIndex" in text
    assert "modelSearchInputRef" in text
    assert 'ref="modelSearchInputRef"' in text
    assert 'ref="modelListScrollRef"' in text

    # 2. 键盘调度与视口滚动函数
    assert "scrollActiveModelIntoView" in text
    assert "navigateModelList" in text
    assert "selectActiveModel" in text
    assert "initActiveModelIndex" in text
    assert "activeEl.scrollIntoView({ block: 'nearest' })" in text

    # 3. 模板中的数据标记与键盘事件绑定
    assert 'data-model-index="0"' in text
    assert ':data-model-index="index + 1"' in text
    assert ':data-model-active="activeModelIndex === 0' in text
    assert ':data-model-active="activeModelIndex === index + 1' in text
    assert '@keydown.down.stop.prevent="navigateModelList(1)"' in text
    assert '@keydown.up.stop.prevent="navigateModelList(-1)"' in text
    assert '@keydown.enter.stop.prevent="selectActiveModel"' in text
    assert "↑↓ 切换 · ↵ 选择" in text

    # 4. 全局快捷键拦截支持
    assert "showModelDropdown.value && !showThinkingPanel.value" in text
