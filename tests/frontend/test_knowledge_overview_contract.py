"""知识库管理「概览态」的契约。

这里守三件容易被改回去的事：

1. **进页面不许默认选中第一个库**。原来的默认选中必须顺手把 `activeVisibility`
   改成那个库的分类去对齐 tab，否则左侧「授权可见 0 个」右侧却显示 TEAM 级库。
   补偿逻辑和默认选中是一体的，删一个留一个就会重新长出那个错位。
2. **概览按全部可见范围聚合，不跟随 tab**。切 tab 只换左栏列表；右侧数字若跟着跳，
   用户会以为总量变了。
3. **概览只算列表接口已返回的字段**。不许为了「解析中 / 失败文档数」逐库拉文档
   （N 次请求），也不许凭空写「上次同步时间」。
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure

VIEW_PATH = "frontend/src/views/KnowledgeBaseManagement.vue"


def _source() -> str:
    return (ROOT / VIEW_PATH).read_text(encoding="utf-8")


def test_no_auto_selection_on_entry():
    view = _source()

    # 默认选中某个库的那段逻辑（含对齐 tab 的补偿）必须彻底消失：断言具体代码，
    # 而不是「默认选中第一个」这句词——新注释里正好会提到它。
    assert "!selectedId.value && datasets.value.length > 0" not in view
    assert "visibilityScopeDatasets.value[0]" not in view
    assert "selectDatasetNode(firstDataset)" not in view
    # 概览态用 selectedType === null 表达，不额外引入 boolean
    assert "const showOverview = () => {" in view
    assert "selectedType.value = null" in view


def test_overview_aggregates_globally_not_per_tab():
    view = _source()

    assert "const overviewStats = computed(" in view
    start = view.index("const overviewStats = computed(")
    body = view[start : view.index("/** 概览里各类角标百分比", start)]
    # 聚合的是全量 datasets，而不是当前 tab 过滤后的集合
    assert "const all = datasets.value" in body
    assert "visibilityScopeDatasets" not in body


def test_overview_panel_blocks_exist():
    view = _source()

    assert "知识库概览" in view
    assert "可见知识库" in view and "文档总数" in view
    assert "切片总数" in view and "被智能体引用" in view
    assert "最近更新" in view
    assert "可见范围分布" in view
    assert "需要关注" in view
    assert "引擎与配额" in view
    # 三类待办都要在
    assert "个知识库在 RAGFlow 侧已失效" in view
    assert "个知识库还没有文档" in view
    assert "个知识库未被任何智能体引用" in view


def test_overview_is_the_default_pane_and_reachable():
    view = _source()

    # 左栏有概览入口，右侧默认分支是概览而不是「请选择节点」
    assert '@click="showOverview"' in view
    assert "请选择节点" not in view
    # 概览里的行可以点进详情，且会先把左栏 tab 对齐到该库的分类
    assert "openDatasetFromOverview" in view
    assert "activeVisibility.value = visibilityOf(dataset)" in view


def test_deep_link_still_opens_detail_and_no_extra_requests():
    view = _source()

    # ?dataset= 下钻仍然直接进详情，不被概览拦掉
    assert "applyDeepLinkFromQuery" in view
    # 概览不为统计新开接口
    assert "datasets/overview" not in view
    # 相对时间复用既有工具，不自己写一份
    assert "import { formatDate }" in view


def test_hero_card_has_decorative_shelf():
    """顶部卡片的书架剪影是纯装饰：不接事件、不进无障碍树、不压住按钮。"""
    view = _source()

    assert "kb-hero relative overflow-hidden" in view
    assert 'class="kb-hero-art pointer-events-none"' in view
    assert 'aria-hidden="true"' in view

    # 标题与按钮都要压在剪影之上，否则按钮点不到、文字也会被削
    assert 'class="relative z-10 flex min-w-0 flex-col gap-1"' in view
    assert 'class="relative z-10 flex shrink-0 items-center gap-2 lg:gap-3"' in view

    # 书脊由固定种子生成：刷新一次不会换个样子，也不用为调布局重排几十个坐标
    assert "const HERO_SHELF_SPINES" in view
    assert "let seed = 20261030" in view
