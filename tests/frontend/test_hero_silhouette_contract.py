"""四个管理页顶部标题卡的剪影契约。

知识库原本自己内联一份书脊剪影，数据源/元数据/案例集后来也要同款效果。
如果每页各写一份，`hover` tooltip 的层级、圆角裁切、窄屏高度这些细节
迟早会在某一页上对不上，所以这里守住"共用一套配方"这件事：

1. 四页都用 `.hero-surface`（底色 + 光晕）和 `HeroSilhouette`（底部剪影）；
2. 卡片**不能**加 `overflow-hidden` —— 卡里挂的是 `absolute top-full` 的
   引擎信息 tooltip，卡片只有 ~100px 高，一裁就把下半截切掉；
   圆角溢出由组件自己的 clip-path 处理。
3. 组件必须是纯装饰：`aria-hidden` + `pointer-events-none`。
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure

HERO_PATH = "frontend/src/components/common/HeroSilhouette.vue"

# 页面 → 该页应有的剪影 variant
PAGES = {
    "frontend/src/views/KnowledgeBaseManagement.vue": "books",
    "frontend/src/views/DataSourceManagement.vue": "racks",
    "frontend/src/views/MetadataDatasets.vue": "fields",
    "frontend/src/views/ExampleManagement.vue": "cards",
}


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


@pytest.mark.parametrize("page,variant", PAGES.items())
def test_page_uses_shared_hero_surface_and_silhouette(page, variant):
    view = _read(page)

    assert "hero-surface" in view, f"{page} 没挂共用的标题卡样式"
    assert f'<HeroSilhouette variant="{variant}"' in view, f"{page} 没声明 variant={variant}"


@pytest.mark.parametrize("page", PAGES)
def test_hero_card_does_not_clip_tooltips(page):
    """卡片不裁切：裁了会把卡内的悬浮 tooltip 切掉半截。"""
    view = _read(page)

    # 定位到标题卡那一行的 class 属性，避免误伤页面里其它 overflow-hidden 容器
    hero_line = next(
        (line for line in view.splitlines() if "hero-surface" in line and "class=" in line),
        "",
    )
    assert hero_line, f"{page} 找不到标题卡元素"
    assert "overflow-hidden" not in hero_line, f"{page} 的标题卡不该加 overflow-hidden"


def test_silhouette_component_is_decorative():
    hero = _read(HERO_PATH)

    assert 'aria-hidden="true"' in hero
    assert "pointer-events: none" in hero
    # 窄屏要压矮，否则会盖住副标题那一行
    assert "@media (max-width: 640px)" in hero
    assert "clip-path" in hero, "父级不裁切，圆角得由剪影自己切"
