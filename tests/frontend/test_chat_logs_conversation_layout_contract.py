"""契约：聊天日志「对话」Tab 改为左右分栏回放，并在 AI 回复头标出归属智能体。

锁三件事：
1. 用户消息靠右（主色气泡、限宽）、AI 回复靠左（白底文档流、不限宽，正文含表格/代码/图表）；
2. AI 回复头显示本轮实际回复的智能体与版本——同一会话常在主助手/专家/子代理之间切换，
   只看「AI 回复」四个字分不清是谁答的；
3. 轮次号、时间、状态、耗时压成居中分隔带（不再独占一行），一眼认出「新的一问一答」。

同时锁定原有能力不被这次改版弄丢：执行 N 步、查看此轮轨迹、渲染/源码切换、空回复说明、
主动提问卡回放。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "frontend" / "src" / "views" / "ChatLogs.vue"


def _src() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_user_message_is_right_aligned_bubble_and_ai_reply_keeps_wide_document_flow():
    src = _src()

    # 用户行：flex-row-reverse 把它推到右侧，头像仍在最右
    assert 'class="flex flex-row-reverse items-start gap-2.5 mb-3.5"' in src
    # 主色气泡 + 限宽（长问题不撑满整行）
    assert "rounded-2xl rounded-tr-sm bg-primary text-white" in src
    assert "flex flex-col items-end min-w-0 max-w-[74%]" in src

    # AI 行：正常方向（靠左），正文容器 flex-1 撑满内容列而不是缩成气泡
    assert 'class="flex items-start gap-2.5 group/reply"' in src
    assert "rounded-2xl rounded-tl-sm border border-gray-200/80 bg-white" in src
    assert '<div class="flex-1 min-w-0">' in src
    # 正文仍是宽幅文档流渲染器，不能被气泡限宽挤坏
    assert "message-host chat-log-message-host max-w-none text-slate-800 break-words" in src


def test_ai_reply_header_shows_which_agent_answered():
    src = _src()

    # 归属优先取后端已带的 agent_display_name，回退到本地智能体列表
    assert "turn.agent_display_name || getAgentName(turn.agent_id)" in src
    assert 'v-if="turn.agent_display_name || turn.agent_id"' in src
    # 版本只在有时才出现
    assert 'v-if="turn.agent_version"' in src
    assert "{{ turn.agent_version }}" in src
    # 归属说明作为 title，鼠标悬停能看全名（长名在 chip 里截断）
    assert "本轮由「" in src


def test_turn_meta_is_a_centered_divider_and_existing_controls_survive():
    src = _src()

    # 居中分隔带：两条 h-px 夹住「第 N 轮 · 时间 · 状态 · 耗时」
    assert src.count('<span class="h-px flex-1 bg-gray-100" aria-hidden="true"></span>') == 2
    assert "第 {{ Number(index) + 1 }} 轮" in src
    assert "formatDurationMs(turn.execution_time_ms)" in src
    # 轮次之间不再额外加 border-b（分隔带已经承担视觉分隔）
    assert 'class="scroll-mt-4 pb-6 border-b border-gray-100 last:border-b-0 transition-colors"' not in src

    # 原有能力一个都不能丢
    assert "执行 {{ countTurnToolSteps(turn.process_timeline) }} 步" in src
    assert "查看此轮轨迹" in src
    assert "setTurnReplyMode(turn.id, 'source')" in src
    assert "emptyReplyView(turn).detail" in src
    assert '<UserQuestionCard\n                        v-if="turn.userQuestion"' in src
    # 模型名只出现在 AI 回复头（原来在轮次头，别重复渲染两处）
    assert src.count("{{ turn.model_id }}") == 1
