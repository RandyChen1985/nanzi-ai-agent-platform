from pathlib import Path
import pytest

pytestmark = pytest.mark.no_infrastructure

EMBED_CHAT_PATH = Path("frontend/src/views/EmbedChat.vue")


def test_embed_chat_has_stream_render_throttling_contract():
    """验证 EmbedChat.vue 具备流式 requestAnimationFrame 文本缓冲与安全 flush 逻辑。"""
    assert EMBED_CHAT_PATH.exists(), f"{EMBED_CHAT_PATH} must exist"
    content = EMBED_CHAT_PATH.read_text(encoding="utf-8")

    # 1. 具备缓冲区变量与 RAF 调度
    assert "pendingContentBuffer" in content
    assert "contentRafId" in content
    assert "requestAnimationFrame(flushContentBuffer)" in content
    assert "cancelAnimationFrame" in content

    # 2. 具备首字零延迟直接输出判断
    assert "!agentMsg.value.content && !pendingContentBuffer" in content

    # 3. 关键终止与异常路径均包含安全 flush
    assert "flushContentBuffer();" in content
    assert "finally {" in content

    # 4. SSE 接收到 [DONE] 终止标志时立即跳出读取循环并释放 reader，防止连接挂起导致输入框未释放
    assert "isChatStreamDone = true;" in content
    assert "await reader.cancel();" in content


@pytest.mark.parametrize('scenario', ['standard', 'retraction', 'mixed', 'promotion', 'repeated', 'terminal'])
def test_embed_stream_event_runtime(scenario):
    import subprocess
    result = subprocess.run(
        ['node', 'tests/frontend/stream_throttle_runtime.cjs', scenario],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
