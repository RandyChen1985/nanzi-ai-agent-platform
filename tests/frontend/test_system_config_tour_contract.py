"""系统配置引导契约。

锁定容易在改版中悄悄坏掉的部分：
1. 13 步的顺序与锚点必须真的渲染在页面里；
2. 自动播只对 element:system:config_save 开放，移动端不播；
3. 重播按钮只在「当前版本已完成」时出现，且只重播给自己、不写全局；
4. 全局状态走 /api/portal/system/tour，断点走 sessionStorage；
5. 配置项 id 与搜索定位前缀一致（修掉 config-key/config-item 漂移）。
"""

from pathlib import Path
import re

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).parents[2]

STEP_IDS = [
    "sys-tabs",
    "model-add",
    "model-row-actions",
    "config-toolbar",
    "config-app_public_url",
    "config-agent_context_max_tokens",
    "config-llm_model_name",
    "config-multimodal_model_name",
    "config-embed_api_url",
    "config-metadata_provider",
    "config-knowledge_base_enabled",
    "config-sandbox_policy",
    "config-email_service_enabled",
]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _steps() -> str:
    return _read("frontend/src/components/system/systemConfigTourSteps.ts")


def _page() -> str:
    return _read("frontend/src/views/SystemConfig.vue")


def _composable() -> str:
    return _read("frontend/src/composables/system/useSystemConfigTour.ts")


def test_tour_has_thirteen_steps_in_agreed_order():
    found = re.findall(r"id: '([a-z0-9_-]+)'", _steps())

    assert found == STEP_IDS


def test_every_selector_is_data_tour_attribute():
    selectors = re.findall(r'selector: \'\[data-tour="([a-z0-9_-]+)"\]\'', _steps())

    assert selectors == STEP_IDS


def test_anchors_render_in_page_sources():
    ui = _page()
    models = _read("frontend/src/components/system/ModelRegistry.vue")

    assert 'data-tour="sys-tabs"' in ui
    assert 'data-tour="config-toolbar"' in ui
    assert ':data-tour="`config-${item.key}`"' in ui
    assert 'data-tour="model-add"' in models
    # 行内操作只给第一行挂锚点，避免同一条选择器在列表里重复 N 份
    assert ":data-tour=\"modelIndex === 0 ? 'model-row-actions' : undefined\"" in models


def test_autoplay_is_gated_by_config_save_desktop_and_query_switch():
    ui = _page()

    assert "if (!canSave) return" in ui
    assert "window.innerWidth < 768" in ui
    assert "=== '0'" in ui
    assert "=== 'off'" in ui


def test_replay_button_appears_only_for_completed_current_version():
    ui = _page()

    assert "sysTour.isCompletedForCurrentVersion.value" in ui
    assert "replaySystemTour" in ui


def test_replay_does_not_write_global_completion():
    ui = _page()

    replay_body = ui.split("const replaySystemTour")[1][:600]

    assert "markCompleted" not in replay_body
    assert "sysTourActive.value = true" in replay_body


def test_progress_uses_session_storage_and_global_endpoint():
    composable = _composable()

    assert "sessionStorage" in composable
    assert "/api/portal/system/tour" in composable
    assert "SYSTEM_CONFIG_TOUR_VERSION" in composable


def test_skip_and_finish_share_global_mark_completed():
    ui = _page()

    assert "finishSystemTour(false)" in ui
    assert "finishSystemTour(true)" in ui
    finish_body = ui.split("const finishSystemTour")[1][:400]
    assert "sysTour.markCompleted(skipped)" in finish_body


def test_config_row_id_matches_search_lookup_prefix():
    ui = _page()

    assert ':id="`config-item-${item.key}`"' in ui
    assert "getElementById(`config-item-${result.item.key}`)" in ui
    assert "config-key-" not in ui


def test_chat_tour_exposes_configurable_missing_grace():
    tour = _read("frontend/src/components/embed/ChatTour.vue")

    assert "missingGraceMs" in tour
    assert "props.missingGraceMs" in tour
