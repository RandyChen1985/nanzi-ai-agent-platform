"""提问轮正文小节的接线契约：正文区拆块、卡片去重、默认展开。

纯逻辑在 `tests/frontend/test_user_question_body_parts.py` 里定；这里管「接线」：
- `EmbedChat.vue` 是否真的在识别成功时用两个小节替代 markdown 正文（且**不会同时**渲染）；
- 小节是不是默认展开（用户的明确要求：不要默认折叠）、收起态由消息对象持有；
- 提问卡是否收到 `summary-rendered`，从而不再重复一遍问题与进度；
- 组件渲染结果见 `user_question_body_block_ssr.cjs`（真跑 SFC 编译 + SSR）。
"""

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure

EMBED_CHAT = ROOT / "frontend/src/views/EmbedChat.vue"
BLOCK = ROOT / "frontend/src/components/chat/UserQuestionBodyBlock.vue"
CARD = ROOT / "frontend/src/components/UserQuestionCard.vue"
SSR_PROBE = ROOT / "tests/frontend/user_question_body_block_ssr.cjs"


@pytest.fixture(scope="module")
def embed_chat_source() -> str:
    return EMBED_CHAT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def card_source() -> str:
    return CARD.read_text(encoding="utf-8")


def test_body_blocks_replace_markdown_body_when_recognized(embed_chat_source: str):
    """识别成功时渲染两个小节，markdown 正文改用 v-else-if —— 两者互斥，不会都渲染。"""
    assert "import UserQuestionBodyBlock from" in embed_chat_source
    assert 'v-if="questionBodyParts(msg) && !msg.groundingBlocked' in embed_chat_source
    # 原来的 markdown 正文必须降级成 v-else-if：否则正文会被渲染两遍（小节 + markdown）
    assert 'v-else-if="!msg.groundingBlocked && !msg.datasetNavigation?.groups?.length"' in embed_chat_source
    assert embed_chat_source.count("<UserQuestionBodyBlock") == 2


def test_blocks_are_expanded_by_default(embed_chat_source: str):
    """收起态只在用户点过之后才为 true；字段缺失/undefined 一律按展开渲染。"""
    assert ':collapsed="msg.userQuestionProgressCollapsed === true"' in embed_chat_source
    assert ':collapsed="msg.userQuestionPendingCollapsed === true"' in embed_chat_source
    # 三个状态字段都必须默认未设置（等号换 true 才算收起）
    assert "userQuestionProgressCollapsed?: boolean;" in embed_chat_source
    assert "userQuestionPendingCollapsed?: boolean;" in embed_chat_source


def test_blocks_use_structured_fields_not_parsed_text(embed_chat_source: str):
    """小节文字取提问卡字段（questionBodyParts 的返回值），不从正文里抠。"""
    assert ':text="questionBodyParts(msg)?.progress || \'\'"' in embed_chat_source
    assert ':text="questionBodyParts(msg)?.question || \'\'"' in embed_chat_source


def test_card_is_told_the_body_already_shows_question(embed_chat_source: str):
    """正文已承载问题与进度时通知提问卡，卡片只留选项。"""
    assert ':summary-rendered="Boolean(questionBodyParts(msg))"' in embed_chat_source


def test_card_stops_repeating_when_summary_rendered(card_source: str):
    """卡片的展开正文、折叠态预览、无障碍标签三处都要让路。"""
    assert "summaryRendered?: boolean;" in card_source
    # 展开态的问题 + 进度块
    assert '<div v-if="!props.summaryRendered">' in card_source
    # 折叠态的逐字预览
    assert 'v-if="!expanded && payload.question && !props.summaryRendered"' in card_source
    # 读屏用的 aria-label 不能把同一段问题再念一遍
    assert "props.summaryRendered ? '需要你的补充'" in card_source


def test_block_component_keeps_tag_when_collapsed():
    """收起只藏正文、不藏标签：用户仍能看到「任务进度」这一行并点开。"""
    source = BLOCK.read_text(encoding="utf-8")
    assert 'data-question-body-block' in source
    assert 'v-if="!props.collapsed"' in source
    assert '{{ props.label }}' in source


def test_block_and_card_share_the_same_width_rules():
    """小节与提问卡必须同宽 —— 用户实测反馈：小节通栏、卡片有 max-w，宽屏下右边缘错开。"""
    chain = "w-full min-w-0 max-w-[42rem] lg:max-w-[48rem] 2xl:max-w-[52rem]"
    assert chain in CARD.read_text(encoding="utf-8"), "提问卡的宽度规则变了，小节会跟着错开"
    assert chain in BLOCK.read_text(encoding="utf-8"), "小节缺少与提问卡一致的宽度规则"


def test_rendered_components_behave_as_expected():
    """真编译 SFC 并 SSR 渲染：默认展开能读到正文、summaryRendered 后卡片不再重复。"""
    completed = subprocess.run(
        ["node", str(SSR_PROBE)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)
    for scope in ("block", "card"):
        for name, passed in result[scope].items():
            assert passed is True, f"{scope}.{name} 未通过"
