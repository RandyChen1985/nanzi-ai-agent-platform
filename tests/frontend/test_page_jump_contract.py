"""聊天日志 / 审计日志的分页支持输入页码跳转。

两个页面都有几百上千条记录，原来只有上一页/下一页，翻到目标页要点几十次。
解析逻辑在 `utils/pagination.ts`（见 test_page_jump_behavior.py），
这里锁住接线：输入框存在、回车/失焦都会触发跳转、并且显示总页数。
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CHAT_LOGS = ROOT / "frontend/src/views/ChatLogs.vue"
AUDIT_LOGS = ROOT / "frontend/src/views/AuditLogs.vue"

pytestmark = pytest.mark.no_infrastructure


def _read(path: Path) -> str:
    assert path.exists(), f"缺少 {path}"
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("page_file", [CHAT_LOGS, AUDIT_LOGS], ids=["chat-logs", "audit-logs"])
def test_page_has_a_jump_input(page_file: Path):
    source = _read(page_file)

    assert 'v-model="pageInput"' in source, "没有页码输入框"
    assert '@keyup.enter="applyPageInput"' in source, "回车不能跳转"
    assert '@blur="applyPageInput"' in source, "失焦不能收敛非法输入"
    assert "parsePageInput(" in source, "没有复用共享的解析函数"
    assert "totalPages" in source, "没有显示总页数，用户不知道能跳到多少"


@pytest.mark.parametrize("page_file", [CHAT_LOGS, AUDIT_LOGS], ids=["chat-logs", "audit-logs"])
def test_paging_state_stays_in_sync_with_the_input(page_file: Path):
    """翻页按钮改动页码后，输入框要跟着更新，否则显示的是上一页。"""
    source = _read(page_file)

    assert "pageInput.value = String(value)" in source
