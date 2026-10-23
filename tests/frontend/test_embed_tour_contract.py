"""EmbedChat 功能引导契约。

锁定的是「引导和数据怎么配对」这类容易在改版中悄悄坏掉的部分：
1. 十个步骤的锚点必须真的渲染在页面里；
2. 进度必须走账号级 portal_prefs（跨设备不重播）；
3. 自动播的几个开关（移动端、?tour=0）不能被误删。
"""

from pathlib import Path
import re

import pytest


pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).parents[2]

STEP_IDS = [
    "header-status",
    "history-toggle",
    "header-actions",
    "welcome-cards",
    "composer",
    "composer-plus",
    "composer-pills",
    "sandbox-block",
    "composer-routing",
    "composer-approval",
]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _tour_steps_source() -> str:
    return _read("frontend/src/components/embed/tourSteps.ts")


def _ui_source() -> str:
    """所有挂锚点的页面/组件源码合并，锚点只要落在其中一处即可。"""
    return "\n".join(
        _read(path)
        for path in (
            "frontend/src/views/EmbedChat.vue",
            "frontend/src/components/embed/ChatInput.vue",
            "frontend/src/components/embed/WelcomeDashboard.vue",
        )
    )


def test_tour_has_ten_steps_in_agreed_order():
    source = _tour_steps_source()

    found = re.findall(r"id: '([a-z-]+)'", source)

    assert found == STEP_IDS


def test_every_tour_step_has_a_rendered_anchor():
    selectors = re.findall(r'data-tour="([a-z-]+)"', _tour_steps_source())

    assert selectors == STEP_IDS

    ui = _ui_source()
    for anchor in selectors:
        assert f'data-tour="{anchor}"' in ui, f"锚点 {anchor} 没有渲染在页面里"


def test_chat_tour_is_mounted_in_embedchat():
    source = _read("frontend/src/views/EmbedChat.vue")

    assert "<ChatTour" in source
    assert ":steps=\"TOUR_STEPS\"" in source
    assert '@enter="handleTourEnter"' in source
    assert '@finish="tour.finish()"' in source
    assert '@skip="tour.skip()"' in source


def test_chat_input_exposes_context_panel_controls():
    source = _read("frontend/src/components/embed/ChatInput.vue")

    assert "openContextUsageForTour" in source
    assert "closeContextUsageForTour" in source
    assert "const closeContextUsageDetails = () =>" in source


def test_tour_step_changes_reach_the_state_machine():
    """ChatTour 的索引在组件内部；不接 @change 的话断点/跳过会永远记在旧步。"""
    source = _read("frontend/src/views/EmbedChat.vue")
    tour = _read("frontend/src/components/embed/ChatTour.vue")

    assert '@change="handleTourChange"' in source
    assert "tour.goTo(index)" in source
    assert "defineExpose({ refresh, goNext, goPrev })" in tour
    assert "tourRef.value?.goNext?.()" in source


def test_tour_overlay_does_not_leak_clicks_to_global_listeners():
    """遮罩点击不能冒泡到 document：ChatInput 的全局点击监听会顺手关掉上下文弹层。"""
    tour = _read("frontend/src/components/embed/ChatTour.vue")

    assert 'style="z-index: 15000" @click.stop' in tour


def test_tour_does_not_measure_while_inactive():
    """未播放时滚动/缩放不得触发缺失判定，否则会把持久化进度推着走。"""
    tour = _read("frontend/src/components/embed/ChatTour.vue")

    assert "const measure = () => {\n  if (!props.modelValue) return" in tour
    assert "const onScroll = () => {\n  if (!props.modelValue) return" in tour


def test_tour_waits_for_async_targets_before_skipping():
    """沙箱步骤要先展开弹层；缺失判定必须留出宽限，不能被首测误跳过。"""
    tour = _read("frontend/src/components/embed/ChatTour.vue")

    # 宽限默认 450ms（系统配置页会传更大的值，所以做成了可调 prop）
    assert "missingGraceMs: 450" in tour
    assert "}, props.missingGraceMs)" in tour in tour
    assert "if (resolveTarget()) return" in tour
    assert "window.clearTimeout(missingTimer)" in tour


def test_tour_progress_is_account_level_portal_prefs():
    backend = _read("app/api/portal/endpoints/portal_prefs.py")
    store = _read("frontend/src/composables/embed/useEmbedTour.ts")

    assert "embed_tour_version" in backend
    assert "embed_tour_step" in backend
    assert "embed_tour_completed_at" in backend
    assert '"/embed-tour"' in backend
    assert "/api/portal/portal-prefs/embed-tour" in store
    assert "localStorage" in store


def test_tour_skips_autoplay_when_host_disables_it():
    store = _read("frontend/src/composables/embed/useEmbedTour.ts")

    assert "get('tour')" in store
    assert "value === '0' || value === 'off'" in store


def test_tour_does_not_autoplay_on_mobile():
    store = _read("frontend/src/composables/embed/useEmbedTour.ts")

    assert "if (options.isMobile()) return false" in store


def test_tour_resumes_from_last_step():
    store = _read("frontend/src/composables/embed/useEmbedTour.ts")

    assert "Math.min(Math.max(state.value.step, 0), total - 1)" in store
    assert "stepIndex.value = startIndex" in store


def test_replay_does_not_reset_completion_marker():
    """重播只是再看一遍：中途跳过不能把「已看完」清掉，否则下次又自动播。"""
    store = _read("frontend/src/composables/embed/useEmbedTour.ts")

    assert "state.value = { ...state.value, version: TOUR_VERSION, step: 0 }" in store
    assert "completed_at: state.value.completed_at," in store


def test_tour_persists_completion_timestamp():
    store = _read("frontend/src/composables/embed/useEmbedTour.ts")

    assert "step: total" in store
    assert "completed_at: new Date().toISOString()" in store


def test_settings_has_replay_entry():
    settings = _read("frontend/src/components/embed/ChatSettings.vue")

    assert "功能引导 / Feature Tour" in settings
    assert "重新播放功能引导" in settings
    assert "emit('replay-tour')" in settings
    assert "tourCompletedAt ?" in settings


def test_tour_opens_empty_conversation_before_playing():
    """欢迎卡片只在空会话渲染：要播且当前有内容时，起播前先开一个空会话。"""
    source = _read("frontend/src/views/EmbedChat.vue")

    assert "prepareEmptyConversationForTour" in source
    assert "startNewConversation();" in source
    assert "if (!tour.shouldAutoPlay()) return;" in source
    # 断点已过欢迎卡片时不再动当前会话
    assert "if (resumeIndex <= welcomeStepIndex)" in source
    # 生成中不打断
    assert "if (isProcessing.value) return;" in source


def test_embedchat_waits_for_permission_before_autoplay():
    source = _read("frontend/src/views/EmbedChat.vue")

    assert "tour.resumeOrStart(true)" in source
    assert "if (!hasPermission.value) return;" in source
