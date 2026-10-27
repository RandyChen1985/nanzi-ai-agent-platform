"""只读概览页「不许白屏」契约测试。

背景：用户反馈「已有权限的知识库点进去空白」。排查结论——

- 后端 `GET /api/portal/resource-square/knowledge/{id}` 对该知识库以普通用户身份
  实测返回 **200**，`data` 结构完整（含 `documents` 数组），耗时 0.19s；
- 即后端无问题，白屏发生在前端渲染层。

白屏之所以难查，是因为它有四种成因且表现完全一样（面包屑正常、正文全白、
没有任何提示）：

1. 请求还在跑，而骨架屏用 `bg-gray-100`，与页面背景 `#f5f6f8` 几乎同色；
2. 渲染期抛错——模板里直接读 `data.documents.length`，接口少给一个数组字段
   就是 `TypeError`，而渲染期抛错**不走** `catch`，页面静默空白；
3. 四个 `v-if/v-else-if` 分支全为假（如接口返回 200 但 `data` 为空）；
4. 真出错但 `console` 里没有线索。

下面逐条锁住，任何一条被改回去都会在这里失败。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
KNOWLEDGE = ROOT / "frontend/src/views/ResourceSquareKnowledge.vue"
DATASET = ROOT / "frontend/src/views/ResourceSquareDataset.vue"
API = ROOT / "frontend/src/api/resourceSquare.ts"
TYPES = ROOT / "frontend/src/types/resourceSquare.ts"
BACKEND = ROOT / "app/api/portal/endpoints/resource_square.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("page", [KNOWLEDGE, DATASET], ids=["knowledge", "dataset"])
def test_page_has_fallback_branch_so_it_never_renders_nothing(page: Path):
    """分支全假时必须还有话说——否则就是一片空白。"""
    src = _read(page)
    # v-else-if 链末端必须有兜底 v-else，且文案可读
    assert "v-else-if=\"error\"" in src
    assert src.count("v-else") >= 1
    assert "没有拿到这个" in src
    assert "重新加载" in src


@pytest.mark.parametrize("page", [KNOWLEDGE, DATASET], ids=["knowledge", "dataset"])
def test_loading_state_is_visible_not_background_colored(page: Path):
    """骨架屏只有 bg-gray-100（≈ 页面背景）时，加载中看起来就是空白。"""
    src = _read(page)
    assert "animate-spin" in src
    assert "正在读取" in src
    # 白底 + 边框，保证在灰色页面背景上看得出来
    assert "border-gray-100 bg-white" in src


@pytest.mark.parametrize("page", [KNOWLEDGE, DATASET], ids=["knowledge", "dataset"])
def test_load_failure_is_logged_for_diagnosis(page: Path):
    """白屏时最需要的就是 console 里那条线索。"""
    src = _read(page)
    assert "console.error('[ResourceSquare]" in src


@pytest.mark.parametrize("page", [KNOWLEDGE, DATASET], ids=["knowledge", "dataset"])
def test_error_state_offers_retry(page: Path):
    """出错要能就地重试，而不是让用户自己刷新整个页面。"""
    src = _read(page)
    assert "async function load()" in src
    assert "@click=\"load\"" in src
    assert "onMounted(load)" in src


def test_knowledge_api_normalises_array_fields():
    """渲染期 TypeError 的根源：模板直接读 .length，接口少给字段就炸。"""
    src = _read(API)
    assert "documents: Array.isArray(raw.documents) ? raw.documents : []" in src
    assert "tags: Array.isArray(raw.tags) ? raw.tags : []" in src
    assert "raw.dataset_id ?? datasetId" in src


def test_dataset_api_normalises_array_fields():
    src = _read(API)
    assert "tables: Array.isArray(raw.tables) ? raw.tables : []" in src
    assert "metrics: Array.isArray(raw.metrics) ? raw.metrics : []" in src
    assert "raw.relationship_count ?? 0" in src


def test_documents_error_is_typed_and_surfaced():
    """引擎读不到文档 ≠ 这个库没有文档，不能摆一个骗人的 0。"""
    types_src = _read(TYPES)
    assert "documents_error?: boolean" in types_src

    api_src = _read(API)
    assert "documents_error: raw.documents_error === true" in api_src

    page_src = _read(KNOWLEDGE)
    assert "data.documents_error" in page_src
    assert "文档数未取到" in page_src
    # 空状态文案也要区分「真的没有」与「暂时读不到」
    assert "文档列表暂时读不到，请稍后重试。" in page_src


def test_backend_document_listing_is_time_limited_and_reported():
    """RAGFlow 默认 20s 超时，期间前端就是一个长时间空白页。"""
    src = _read(BACKEND)
    assert "asyncio.wait_for(" in src
    assert "timeout=8" in src
    assert "documents_error = True" in src
    assert '"documents_error": documents_error' in src
