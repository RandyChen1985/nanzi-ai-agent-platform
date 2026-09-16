from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_token_format_usage_functions_exist_in_utils():
    content = _read("frontend/src/utils/tokenFormat.ts")
    assert "export function formatTokenUsageAmount" in content
    assert "export function formatTokenUsageTooltip" in content
    assert "1_000_000_000" in content
    assert "K tok" in content
    assert "M tok" in content


def test_embed_chat_uses_database_icon_and_compact_token_usage():
    content = _read("frontend/src/views/EmbedChat.vue")

    # Should have the new database icon and label
    assert "<span>用量 {{ getMessageTokenAmount(msg) }}</span>" in content
    assert "getMessageTokenTooltip(msg)" in content
    assert "formatTokenUsageAmount" in content
    assert "formatTokenUsageTooltip" in content

    # Should have the database cylinder SVG
    assert '<ellipse cx="12" cy="5" rx="9" ry="3"' in content
    assert '<path d="M3 5v14a9 3 0 0 0 18 0V5"' in content
    assert '<path d="M3 12a9 3 0 0 0 18 0"' in content

    # Old in/out badge should be replaced
    assert 'text-gray-400/80">in:</span>' not in content
    assert 'text-gray-400/80">out:</span>' not in content


def test_agent_debug_uses_database_icon_and_compact_token_usage():
    content = _read("frontend/src/views/AgentDebug.vue")

    # Should have the new database icon and label
    assert "<span>用量 {{ getMessageTokenAmount(msg) }}</span>" in content
    assert "getMessageTokenTooltip(msg)" in content
    assert "formatTokenUsageAmount" in content
    assert "formatTokenUsageTooltip" in content

    # Should have the database cylinder SVG
    assert '<ellipse cx="12" cy="5" rx="9" ry="3"' in content

    # Old in/out badge should be replaced
    assert 'text-gray-400/80">in:</span>' not in content
    assert 'text-gray-400/80">out:</span>' not in content


def test_embed_chat_actions_order_and_custom_tooltips():
    content = _read("frontend/src/views/EmbedChat.vue")

    # 1. 顺序验证：copyMessage -> regenerate -> handleFeedback(msg, 'up') -> handleFeedback(msg, 'down') -> mode="data" -> 用量
    idx_copy = content.find("copyMessage(visibleStreamBody(msg))", content.find("isGeneralAgentMessage"))
    # 从 AI 回复区域开始定位
    ai_section_idx = content.find("!(isProcessing && msg.id === lastAgentMessage?.id)")
    assert ai_section_idx != -1

    idx_copy = content.find("copyMessage(visibleStreamBody(msg))", ai_section_idx)
    idx_regen = content.find("@click=\"regenerate\"", ai_section_idx)
    idx_like = content.find("handleFeedback(msg, 'up')", ai_section_idx)
    idx_dislike = content.find("handleFeedback(msg, 'down')", ai_section_idx)
    idx_data = content.find('mode="data"', ai_section_idx)
    idx_token = content.find("用量 {{ getMessageTokenAmount(msg) }}", ai_section_idx)

    assert idx_copy != -1
    assert idx_regen != -1
    assert idx_like != -1
    assert idx_dislike != -1
    assert idx_data != -1
    assert idx_token != -1

    assert idx_copy < idx_regen, "复制应在重新生成之前"
    assert idx_regen < idx_like, "点赞应在重新生成之后"
    assert idx_like < idx_dislike, "点踩应在点赞之后"
    assert idx_dislike < idx_data, "数据/文件应在点踩之后"
    assert idx_data < idx_token, "用量应在数据/文件之后"

    # 截取 AI 操作栏切片进行断言
    action_bar_section = content[ai_section_idx : idx_token + 200]

    # 2. 纯图标：AI 操作栏不再有按钮内联文字
    assert '<span class="hidden sm:inline">复制</span>' not in action_bar_section
    assert '<span class="hidden sm:inline">重新生成</span>' not in action_bar_section

    # 3. 自定义 Tooltip，不用原生 title
    assert 'title="复制"' not in action_bar_section
    assert 'title="重新生成"' not in action_bar_section
    assert 'title="很有帮助"' not in action_bar_section
    assert 'title="回答不准确"' not in action_bar_section

    # 确保自定义 Tooltip 元素存在且位于下方 (top-full)
    assert "复制" in action_bar_section
    assert "重新生成" in action_bar_section
    assert "很有帮助" in action_bar_section
    assert "回答不准确" in action_bar_section
    assert "top-full" in action_bar_section
    assert "bottom-full" not in action_bar_section

    # 4. 图标尺寸和按钮样式统一性校验
    assert 'class="w-3.5 h-3.5"' in action_bar_section
    # 点赞点踩不再是 w-4 h-4
    assert 'class="w-4 h-4"' not in action_bar_section

    # 5. 时间戳防折行：必须带 whitespace-nowrap shrink-0 避免移动端折行
    assert 'formatBubbleTime(msg.timestamp)' in action_bar_section
    assert 'whitespace-nowrap shrink-0' in action_bar_section
