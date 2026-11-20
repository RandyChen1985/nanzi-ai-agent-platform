"""图像设置页「从列表选择模型」的禁选契约。

这个框填的是 `image_model`，选错了**不会当场报错**：真出图时才暴露成 404、
Model mismatch，或者网关静默按默认尺寸出图——比「选不到」难查得多。
所以列表里非图像模型必须置灰不可点。

唯一的出路是手填：`/models` 只回 id、没有类型字段，判定只能靠命名习惯，
总有名字里不带出图关键词的模型（有些网关就是这样）。因此：

- 非图像模型置灰、不可点，标签写「其他 · 不可选」；
- 一个都没认出来时，整张列表都点不了，必须明确提示「请手填模型 id」；
- 手填路径（`applyImageModel` 的提示）保留，只提示不拦截。
"""

from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
CONFIG = "frontend/src/views/SystemConfig.vue"


def _source() -> str:
    return (ROOT / CONFIG).read_text(encoding="utf-8")


def test_non_image_models_are_disabled():
    source = _source()
    assert ':disabled="!isImageModelId(mid)"' in source
    # 置灰 + 指针，不能只是「点得动但没反应」
    assert "cursor-not-allowed opacity-45" in source
    # 光有个「其他」看不出来能不能点，得写明白
    assert "其他 · 不可选" in source


def test_disabled_rows_explain_the_manual_escape_hatch():
    """禁用之后必须告诉用户「名字认不出就手填」，否则等于把人逼死。"""
    source = _source()
    assert "请直接在上方「模型 id」输入框手填" in source


def test_zero_recognised_models_shows_guidance():
    """整张列表都置灰时没有可点项，不给引导会被当成页面坏了。"""
    source = _source()
    assert 'v-else-if="imageModelCandidates.length && !imageModelCounts.image"' in source
    assert "这个网关里没识别到图像生成模型" in source


def test_manual_input_is_still_allowed_with_a_warning():
    """手填是最后出路，只提示不拦截。"""
    source = _source()
    assert "if (!isImageModelId(target)) {" in source
    assert "请用「测试生成」确认能出图" in source


def test_recognition_keywords_cover_common_image_models():
    """禁用让这份名单变成「能不能选」的开关，漏认就等于用户选不到。"""
    source = _source()
    for keyword in (
        "image",
        "flux",
        "diffusion",
        "dall-?e",
        "imagen",
        "kolors",
        "seedream",
        "cogview",
        "wanx",
        "midjourney",
        "nano-?banana",
        "sd[-_]?[0-9x]",
    ):
        assert keyword in source, f"缺少关键词 {keyword}"


def test_image_models_stay_selectable():
    """别把开关改过头：认出来的必须还能选，且标签还在。"""
    source = _source()
    assert "imageModelSelected === mid ? 'bg-primary/10' : 'hover:bg-gray-50'" in source
    assert ">图像生成</span>" in source
