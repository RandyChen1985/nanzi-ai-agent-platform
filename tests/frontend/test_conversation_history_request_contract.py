"""会话历史快速切换的并发契约。

旧实现用一个全局 ``isLoadingHistory`` 挡住所有请求：切换会话时，上一条会话的历史
还在飞，新会话的加载会被直接丢弃，界面只剩「正在加载历史记录...」。这里锁住修复后的
两个关键点：闸门允许不同会话抢占，且旧请求会被 AbortController 取消。
"""

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.no_infrastructure


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_embed_chat_allows_conversation_switch_to_supersede_inflight_history():
    source = _source("frontend/src/views/EmbedChat.vue")

    assert "shouldStartConversationHistoryLoad" in source
    # 旧的无差别拦截必须移除：它会把切换后的目标会话加载直接吞掉
    assert "if (isLoadingHistory.value) return;" not in source
    assert "historyAbortController?.abort()" in source
    assert "signal: abortController.signal" in source
    # 归属校验同样只保留最后一次点击，避免 5-6 个请求占满同源连接
    assert "historyAdoptionAbortController?.abort()" in source
    assert "checkConversationAdoption(target, adoptionAbortController.signal)" in source


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能执行历史闸门行为测试")
def test_history_request_gate_behaviour_via_node():
    script = Path("tests/frontend/conversation_history_request.mjs")
    assert script.exists(), "行为测试脚本缺失"
    proc = subprocess.run(
        ["node", "--experimental-strip-types", "--no-warnings", str(script)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "会话历史请求闸门测试全部通过" in proc.stdout
