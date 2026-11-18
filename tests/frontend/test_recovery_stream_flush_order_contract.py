"""恢复流 / 权限确认流的收尾顺序契约。

这两条流的 SSE 事件先进合帧泵（正文约一帧、思考约 80ms），再写进消息。所以 `catch` 里
追加失败提示时，缓冲里往往还有没刷出去的正文：谁先谁后决定了用户看到的是

    前半段正文 + [失败提示] + 剩下的正文      ← 错
    完整正文 + [失败提示]                      ← 对

`finally` 里的 flush 在提示之后才执行，修不了这个问题，必须在 `catch` 开头先刷一次。
这条顺序没有类型错误、不抛异常，只能靠契约钉住。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
EMBED_CHAT = ROOT / "frontend/src/views/EmbedChat.vue"
AGENT_DEBUG = ROOT / "frontend/src/views/AgentDebug.vue"

FAILURE_NOTICES = (
    "外部执行恢复失败",
    "已取消提交外部执行结果",
    "工具确认失败",
    "已终止工具确认流",
)


@pytest.mark.parametrize("vue_file", [EMBED_CHAT, AGENT_DEBUG], ids=["embed-chat", "agent-debug"])
def test_recovery_stream_flushes_buffer_before_failure_notice(vue_file: Path):
    source = vue_file.read_text(encoding="utf-8")

    for notice in FAILURE_NOTICES:
        at = source.find(notice)
        if at < 0:
            continue
        # 同一条 catch 分支里，flush 必须出现在提示文案之前
        head = source[:at]
        catch_at = head.rfind("catch (error")
        assert catch_at > 0, f"{notice} 不在 catch 分支内"
        branch = source[catch_at:at]
        assert ".flush()" in branch, (
            f"{vue_file.name}: 追加「{notice}」之前必须先 flush 合帧缓冲，"
            "否则残留正文会排到提示后面"
        )
