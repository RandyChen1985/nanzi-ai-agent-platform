"""契约：刷新后的历史消息必须恢复引用详情并标记为历史，点不开时按状态提示。

背景：引用详情此前只在实时流里存在，刷新页面后历史消息的 [ID:n] 徽章点了没反应、
下方引用列表整块消失。这些断言锁住前端三件事：历史映射、历史标记、降级提示。
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EMBED = ROOT / "frontend/src/views/EmbedChat.vue"
DEBUG = ROOT / "frontend/src/views/AgentDebug.vue"
RENDERER = ROOT / "frontend/src/components/MessageRenderer.vue"
RAG_PREVIEW = ROOT / "frontend/src/components/RagPreviewDrawer.vue"

pytestmark = pytest.mark.no_infrastructure


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_embed_history_maps_citations_and_marks_history():
    source = _read(EMBED)

    assert "citations: item.citations ?? undefined" in source, "主历史必须映射引用详情"
    assert re.search(r"isHistory:\s*true", source), "历史消息必须带 isHistory 标记"
    assert "isHistory?: boolean" in source, "Message 接口需声明 isHistory"


def test_embed_visibility_sync_restores_citations():
    source = _read(EMBED)

    assert "currentMsg.citations = latestServerItem.citations ?? currentMsg.citations" in source
    assert "citations: latestServerItem.citations ?? undefined" in source


def test_agent_debug_history_maps_citations():
    source = _read(DEBUG)

    assert "citations: m.citations ?? undefined" in source


def test_both_chat_views_prompt_when_citation_detail_unavailable():
    """两处 handleShowCitation 都必须按状态提示，而不是静默返回。"""
    for path in (EMBED, DEBUG):
        source = _read(path)

        assert "引用详情已过期" in source, f"{path.name} 缺少过期提示"
        assert "引用详情加载中" in source, f"{path.name} 缺少加载中提示"
        assert "isKnowledgeCitationBadge" in source, f"{path.name} 缺少引用标记形态校验"


def test_figure_reference_is_not_treated_as_citation_badge():
    """Fig. n 只是渲染器的兼容匹配面，不是知识库引用，不能据此弹「已过期」。"""
    source = _read(EMBED)

    match = re.search(r"const isKnowledgeCitationBadge = \(text: string\) =>\s*(.+);", source)
    assert match, "必须存在引用标记形态校验函数"
    pattern = match.group(1)
    assert "ID" in pattern
    assert "Fig" not in pattern, "形态校验不得把 Fig. 也算作引用标记"


def test_prompt_distinguishes_history_from_in_flight():
    """历史消息提示已过期，流式进行中提示加载中，两者不可混为一谈。"""
    source = _read(EMBED)

    assert 'msg.isHistory ? "引用详情已过期' in source
    assert '"引用详情加载中' in source


def test_rag_preview_iframe_has_loading_feedback():
    """PDF 只能走原生 iframe（file-viewer 的 pdf 渲染器不认 #page fragment），
    而原生 iframe 本身没有任何加载反馈，必须自己补一层遮罩。"""
    source = _read(RAG_PREVIEW)

    assert "正在加载原档预览" in source, "PDF 分支必须有加载文案"
    assert "animate-spin" in source, "加载态要有转圈指示"
    assert '@load="onPreviewLoaded"' in source, "遮罩必须由 iframe 的 load 收起"
    assert 'v-if="previewLoading"' in source, "遮罩必须真的绑定到加载态"
    # 存在加载态还不够：必须真的被启动，否则遮罩永不显示、功能等于没做
    assert "startPreviewLoading();" in source, "打开时必须启动加载态"
    assert "previewLoading" in source


def test_rag_preview_loading_has_timeout_fallback():
    """iframe 的 load 在个别情况下不触发（插件未就绪、加载被中断），
    没有兜底就会让遮罩永久盖住文档——比不加加载态更糟。"""
    source = _read(RAG_PREVIEW)

    assert "PREVIEW_LOAD_TIMEOUT_MS" in source, "必须有超时兜底"
    # 注意不能只断言 "setTimeout"：类型注解 `ReturnType<typeof setTimeout>` 里也有这个词，
    # 会变成假绿（该假绿已由变异验证抓出一次）。必须锚定真实调用。
    assert "previewLoadingTimer = setTimeout(" in source, "兜底必须是真正的定时器调用"
    assert "clearTimeout(previewLoadingTimer)" in source, "load 到达或组件卸载时必须清掉兜底定时器"
    # 同理，只有清理函数还不够：必须真的挂到卸载钩子上（否则卸载后仍会触发）
    assert "onUnmounted(clearPreviewLoadingTimer)" in source, "组件卸载时必须清理兜底定时器"
