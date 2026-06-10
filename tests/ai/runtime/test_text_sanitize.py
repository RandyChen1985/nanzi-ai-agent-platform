import pytest

from app.services.ai.runtime.agentscope.text_sanitize import sanitize_assistant_stream_text

pytestmark = pytest.mark.no_infrastructure


def test_sanitize_strips_think_blocks():
    raw = f"<{'think'}>hidden</{'think'}>可见正文"
    assert sanitize_assistant_stream_text(raw) == "可见正文"


def test_sanitize_strips_function_calls():
    raw = '<function_calls><invoke name="Bash"/></function_calls>回答'
    assert sanitize_assistant_stream_text(raw) == "回答"
