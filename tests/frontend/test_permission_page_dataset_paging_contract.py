"""权限配置页拉知识库列表时的分页参数。

线上现象：知识库管理页能看到公共级/部门级库，但到「角色/用户 → 分配权限 →
知识库」里一个都选不到，只剩一个「显示失联知识库（N）」开关。

根因：`/api/portal/ragflow/datasets` 在 page_size <= 100 时只向 RAGFlow 要
第一页，而 RAGFlow 的 `/datasets` 里混着大量 `meta-*` 元数据数据集（平台自己
建的），第一页往往被它们占满；后端过滤掉 `meta-*` 后 items 为空，
`merge_with_ragflow` 于是把所有本地知识库都标成「RAGFlow 侧已失联」，
而权限页默认隐藏失联库 —— 结果就是一个都选不到。

只要 page_size > 100，客户端就会自动翻页聚合，能拿到全量。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
VIEWS = ROOT / "frontend/src/views"

# 所有会拉知识库列表、且依赖「失联」判定或全量清单的页面/组件
DATASET_LIST_CALLERS = {
    "Users.vue": VIEWS / "Users.vue",
    "Roles.vue": VIEWS / "Roles.vue",
    "KnowledgeBaseManagement.vue": VIEWS / "KnowledgeBaseManagement.vue",
}

# 走 ragflowApi.listDatasets 的调用方用的是位置参数，单独校验
SELECTOR = ROOT / "frontend/src/components/RagFlowResourceSelector.vue"


@pytest.fixture(scope="module")
def sources() -> dict[str, str]:
    out = {}
    for name, path in DATASET_LIST_CALLERS.items():
        assert path.exists(), f"文件不存在：{path}"
        out[name] = path.read_text(encoding="utf-8")
    return out


@pytest.mark.parametrize("name", sorted(DATASET_LIST_CALLERS))
def test_dataset_list_requests_are_not_capped_at_one_page(name, sources):
    src = sources[name]
    # 出现过的 page_size 值都必须 > 100，否则只拿到第一页
    import re

    values = [int(v) for v in re.findall(r"page_size:\s*(\d+)", src)]
    assert values, f"{name} 里没有找到 page_size 参数"
    too_small = [v for v in values if v <= 100]
    assert not too_small, (
        f"{name} 的知识库列表请求用了 {too_small}，<=100 只会拿到第一页，"
        "第一页被 meta-* 元数据数据集占满后知识库会被全部判成失联"
    )


def test_permission_pages_request_full_dataset_list(sources):
    for name in ("Users.vue", "Roles.vue"):
        src = sources[name]
        assert "page_size: 1000" in src, f"{name} 应一次拉全量"
        # 说明为什么必须这样，避免后来的人"优化"回 100
        assert "meta-*" in src or "meta-" in src, f"{name} 缺少原因注释"


def test_dataset_selector_requests_full_list():
    """选择器传的是位置参数 listDatasets(page, pageSize, ...)，单独校验。"""
    import re

    assert SELECTOR.exists()
    src = SELECTOR.read_text(encoding="utf-8")
    match = re.search(r"listDatasets\(1,\s*(\d+)", src)
    assert match, "选择器应显式传 pageSize"
    assert int(match.group(1)) > 100, (
        "选择器的 pageSize <=100 只会拿到第一页，库多了以后排在后面的知识库选不到"
    )
