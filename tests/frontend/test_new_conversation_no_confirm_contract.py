"""契约：新会话不再弹确认框。

用户诉求（原文）：「新会话有个确认框，去掉吧」

此前 `/new`（及别名 `/clear`）会先弹「确定要开始新对话吗？／当前内容将保存至历史记录。」，
确认后才 `resetSession()`。现在直接开始新对话——当前内容本来就会保存进历史，这一步没有
任何会失去的东西，多一次点击只是阻力。

注意：这里只去掉**确认框**，`resetSession()` + 输入框聚焦这两件事必须保留。
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure

EMBED_CHAT = ROOT / "frontend/src/views/EmbedChat.vue"


def _source() -> str:
    return EMBED_CHAT.read_text(encoding="utf-8")


def _body(signature: str) -> str:
    source = _source()
    start = source.index(signature)
    return source[start : source.index("\n};", start)]


def test_the_confirmation_modal_is_gone():
    source = _source()
    assert "确定要开始新对话吗" not in source, "确认框文案还在"
    assert "showConfirmModal" not in source, "确认框的状态变量还在"


def test_new_command_starts_a_conversation_directly():
    source = _source()
    start = source.index('case "/new":')
    branch = source[start : source.index('case "/project":', start)]
    assert "startNewConversation()" in branch, "/new 没有直接开始新对话"
    assert "Confirm" not in branch, "/new 分支里还留着确认相关的调用"
    # 别名要与主命令同一处理，不能只改一个。
    assert 'case "/clear"' in branch


def test_starting_a_new_conversation_still_resets_and_focuses():
    """去掉的只是那一次点击，行为本身不能少：清空当前会话 + 把光标放回输入框。"""
    body = _body("const startNewConversation = ")
    assert "resetSession()" in body
    assert "chatInputRef.value?.focus()" in body
