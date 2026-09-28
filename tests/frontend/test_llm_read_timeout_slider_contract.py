"""大模型请求读取超时配置的前端契约。

重点固化「滑块边界 == 后端校验范围」这条契约：两者一旦漂移，用户会在界面上
选到一个后端直接拒收的值，而且只有保存时才报错。
"""

import re
from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
SETTINGS = ROOT / "frontend/src/views/SystemConfig.vue"
BACKEND = ROOT / "app/services/ai/runtime/agentscope/request_timeout.py"


def _number(source: str, pattern: str) -> float:
    match = re.search(pattern, source)
    assert match, f"未匹配到 {pattern}"
    return float(match.group(1))


def test_read_timeout_rendered_as_bounded_slider():
    source = SETTINGS.read_text(encoding="utf-8")

    assert "item.key === 'llm_request_read_timeout'" in source
    assert 'type="range"' in source
    assert ':min="LLM_READ_TIMEOUT_MIN"' in source
    assert ':max="LLM_READ_TIMEOUT_MAX"' in source
    assert ':step="LLM_READ_TIMEOUT_STEP"' in source
    # 拖动结果必须经过收敛，不能把越界值直接写进 item.value
    assert "handleLlmReadTimeoutInput" in source


def test_slider_range_matches_backend_validation():
    """滑块边界必须与后端校验范围一致，否则会出现「前端能选、后端拒收」。"""
    vue = SETTINGS.read_text(encoding="utf-8")
    backend = BACKEND.read_text(encoding="utf-8")

    for vue_name, backend_name in (
        ("MIN", "MIN_LLM_REQUEST_READ_TIMEOUT"),
        ("MAX", "MAX_LLM_REQUEST_READ_TIMEOUT"),
        ("DEFAULT", "DEFAULT_LLM_REQUEST_READ_TIMEOUT"),
    ):
        frontend_value = _number(vue, rf"LLM_READ_TIMEOUT_{vue_name}\s*=\s*([\d.]+)")
        backend_value = _number(backend, rf"{backend_name}\s*=\s*([\d.]+)")
        assert frontend_value == backend_value, (
            f"{vue_name} 前后端不一致：前端 {frontend_value} vs 后端 {backend_value}"
        )


def test_read_timeout_is_grouped_with_other_llm_configs():
    """排序数组里紧跟 llm_temperature，确保与默认模型/温度同组相邻展示。"""
    source = SETTINGS.read_text(encoding="utf-8")

    assert re.search(r"'llm_temperature',\s*'llm_request_read_timeout',", source)


def test_read_timeout_keeps_editable_numeric_input():
    """滑块旁必须保留可编辑数值框，并在失焦时归一化，与其余两组滑块保持一致。"""
    source = SETTINGS.read_text(encoding="utf-8")

    block = re.search(
        r"""v-else-if="item\.key === 'llm_request_read_timeout'">(.*?)<div v-else-if=""",
        source,
        re.DOTALL,
    )
    assert block, "未定位到超时配置的模板分支"
    body = block.group(1)

    assert 'type="number"' in body
    assert '@blur="normalizeLlmReadTimeoutInput(item)"' in body
    # 滑块与数值框必须同属一套等高对齐，而不是各排各的
    assert "flex items-start space-x-4" in body
    assert "h-2 my-3.5" in body


def test_all_parameter_sliders_share_the_same_alignment():
    """三组滑块（0.0-1.0、0-100%、超时）共用同一套等高对齐。

    数字 3 是「当前同类滑块组数」。若将来新增同类滑块，请同步排版并更新此数，
    这正是本用例想要提醒的事。
    """
    source = SETTINGS.read_text(encoding="utf-8")

    assert source.count('class="flex items-start space-x-4"') == 3
    assert source.count("w-full h-2 my-3.5 bg-gray-200") == 3
