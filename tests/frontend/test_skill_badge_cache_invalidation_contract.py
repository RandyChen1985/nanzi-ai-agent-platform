"""技能流程徽章缓存失效契约。

背景：`getSkillFlowBadgesForMessage` 用 `WeakMap` 缓存每个消息的徽章结果，命中条件是
「用户附件没变 + 日志数组引用没变 + 长度没变 + 最后一条日志没变 + logVersion 没变」。
`markStalePendingStreamLogs` 会**原地**把 `log.status` 改成 error 并追加 `log.details`，
既不新增条目也不换数组引用：

- 被标记的恰好是最后一条日志 → `lastLogDetails` 变化，缓存自然失效；
- 被标记的是较早的一条 → 上面的信号全都没变，缓存命中，超时的技能流程会一直显示「运行中」。

所以「原地改日志」的调用点必须显式 `bumpSkillFlowBadgeVersion`。这两个断言就是防这条
路径被删掉——它没有类型错误、也不抛异常，只会在超时场景下安静地显示过期状态。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
EMBED_CHAT = ROOT / "frontend/src/views/EmbedChat.vue"
AGENT_DEBUG = ROOT / "frontend/src/views/AgentDebug.vue"


def _source(path: Path) -> str:
    assert path.is_file(), f"缺少文件：{path}"
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("vue_file", [EMBED_CHAT, AGENT_DEBUG], ids=["embed-chat", "agent-debug"])
def test_stale_pending_logs_bumps_badge_cache_version(vue_file: Path):
    source = _source(vue_file)

    marker = "markStalePendingStreamLogs("
    assert marker in source, "缺少 markStalePendingStreamLogs 调用点"
    call_at = source.index(marker)
    window = source[call_at:call_at + 600]
    assert "bumpSkillFlowBadgeVersion(" in window, (
        "markStalePendingStreamLogs 原地改日志后必须 bump 徽章版本，"
        "否则非末条日志超时时徽章会停留在「运行中」"
    )


def test_badge_cache_reuse_checks_log_version():
    """失效信号本身不能被删：checks 里少了 logVersion，缓存在流式过程中就不会刷新。"""
    source = _source(EMBED_CHAT)
    assert "cached.logVersion === logVersion" in source
    assert "skillFlowBadgeLogVersion" in source
