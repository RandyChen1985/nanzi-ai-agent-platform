"""生图「允许的分辨率」预设里必须有 512x512。

背景：界面上这一排可勾选的尺寸按钮，来源只有 `SystemConfig.vue` 的 `IMAGE_SIZE_PRESETS`
一处（没有自由输入入口）。也就是说**列表里没有的尺寸，管理员就永远勾不上**——这跟后端
白名单本身是两件事：后端 `parse_allowed_sizes` 对 `512x512` 毫无意见（正则 `\\d{2,5}`
通过），真正挡路的是前端这份预设。

所以这里锁三件事：
1. 预设里含 `512x512`，且排在最前（列表按尺寸由小到大阅读）。
2. 预设仍是勾选按钮的唯一数据源——被换成别的常量时，按钮上的尺寸会静默消失。
3. 后端默认白名单**不含** `512x512`：这是有意为之的取舍。能不能真出 512 取决于上游
   模型（gpt-image-1 最小只到 1024），所以预设里提供、默认不勾，由管理员按自己配的
   模型能力决定；若哪天有人顺手把它加进 `DEFAULT_CONFIG`，新实例的默认行为会跟着变，
   这条断言会先炸。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
VIEW = ROOT / "frontend/src/views/SystemConfig.vue"
BACKEND = ROOT / "app/services/ai/tools/image_generation_tool.py"
pytestmark = pytest.mark.no_infrastructure


def _view_source() -> str:
    return VIEW.read_text(encoding="utf-8")


def _preset_sizes(source: str) -> list[str]:
    start = source.index("const IMAGE_SIZE_PRESETS = [")
    end = source.index("]", start)
    block = source[start:end]
    return re.findall(r"'(\d+x\d+)'", block)


def test_preset_list_contains_512x512():
    sizes = _preset_sizes(_view_source())

    assert "512x512" in sizes, f"生图分辨率预设里没有 512x512，实际为：{sizes}"


def test_512x512_is_listed_first():
    """列表按尺寸由小到大阅读，512 是最小的一档。"""
    sizes = _preset_sizes(_view_source())

    assert sizes[0] == "512x512"


def test_size_buttons_read_from_the_preset_list_only():
    """勾选按钮只能从预设取值，否则新增尺寸会「加了但点不到」。"""
    source = _view_source()

    assert "v-for=\"size in imageSizeOptions(item)\"" in source
    options_fn = source[source.index("const imageSizeOptions = (item: ConfigItem)") :]
    options_fn = options_fn[: options_fn.index("\n}\n")]
    assert "IMAGE_SIZE_PRESETS" in options_fn


def test_backend_default_does_not_enable_512x512():
    """预设提供、默认不勾：后端默认白名单里不能出现 512x512。"""
    backend = BACKEND.read_text(encoding="utf-8")
    match = re.search(r'"image_allowed_sizes":\s*"([^"]*)"', backend)

    assert match, "没找到后端 image_allowed_sizes 默认值"
    assert "512x512" not in match.group(1)
