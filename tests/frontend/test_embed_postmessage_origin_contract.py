"""嵌入挂件 postMessage 来源校验契约。

背景：`/embed/*` 以 `frame-ancestors *` 支持被任意第三方站点 iframe 引用，而消息协议里
带着 `INIT_CONFIG`（token / ticket / business_context）、`CLEAR_SESSION`、`SEND_COMMAND`。
旧实现在 `handlePostMessage` 里只有一句 `// Security check logic here in production`，
**没有任何 origin / source 校验**，等于把「切换账号 / 注入上下文 / 重置会话」开放给任何
把页面嵌进去的站点。

行为验证见 `tests/frontend/embed_host_trust.mjs`；这里锁定接线不被回退。
"""

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
EMBED_CHAT = ROOT / "frontend/src/views/EmbedChat.vue"
EMBED_PERSONAL = ROOT / "frontend/src/views/EmbedPersonal.vue"
CHAT_SHELL = ROOT / "frontend/src/views/Chat.vue"
DEBUGGER = ROOT / "frontend/src/views/WidgetDebugger.vue"
TRUST_UTIL = ROOT / "frontend/src/utils/embedHostTrust.ts"


def _src(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _run_node(script: str) -> str:
    proc = subprocess.run(
        ["node", "--experimental-strip-types", "--no-warnings", script],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能执行纯函数行为测试")
def test_host_trust_matrix_via_node():
    assert "embed_host_trust: all assertions passed" in _run_node(
        "tests/frontend/embed_host_trust.mjs"
    )


def test_embed_chat_rejects_messages_before_reading_payload():
    src = _src(EMBED_CHAT)
    assert "from '@/utils/embedHostTrust'" in src
    assert "evaluateHostMessageTrust({" in src
    assert "resolveHostTargetOrigin({" in src

    # 校验必须早于任何字段读取：`const data = event.data` 之后就不再是「入口守卫」
    handle_tail = src.split("const handlePostMessage = (event: MessageEvent) => {", 1)[1]
    guard_pos = handle_tail.index("isTrustedHostMessage(event)")
    payload_pos = handle_tail.index("const data = event.data")
    assert guard_pos < payload_pos, "来源校验必须在读取 event.data 之前"

    # 发送侧不得再用通配 origin：INIT_SUCCESS 里带着用户名/用户 ID
    assert '"*"' not in src, "EmbedChat 不应再出现通配 postMessage 目标"
    assert "},\n    resolveHostTargetOrigin({" in src


def test_embed_personal_rejects_messages_and_targets_known_origin():
    src = _src(EMBED_PERSONAL)
    assert "evaluateHostMessageTrust({" in src
    assert "resolveHostTargetOrigin({" in src

    # 守卫抽成了独立函数，但调用必须先于任何字段读取
    handle_tail = src.split("const handleHostMessage = (event: MessageEvent) => {", 1)[1]
    guard_pos = handle_tail.index("if (!isTrustedHostMessage(event)) return;")
    payload_pos = handle_tail.index("const data = event.data")
    assert guard_pos < payload_pos, "来源校验必须在读取 event.data 之前"

    guard_fn = src.split("const isTrustedHostMessage = (event: MessageEvent): boolean => {", 1)[1]
    assert "verdict.trusted" in guard_fn
    assert "hostOriginUnverifiedWarned.value" in guard_fn
    assert '"*"' not in src


def test_chat_shell_only_trusts_its_own_iframe():
    src = _src(CHAT_SHELL)
    assert "event.source !== frame" in src, "父页必须校验消息来自自己那颗 iframe"
    assert "event.origin !== window.location.origin" in src
    assert ", '*')" not in src, "父页发往同源 iframe 不需要通配 origin"


def test_debugger_targets_same_origin_and_keeps_its_check():
    src = _src(DEBUGGER)
    assert "window.location.origin);" in src
    assert "event.origin !== targetOrigin" in src, "调试页既有的 origin 校验不得被删"


def test_trust_util_documents_the_unverified_fallback():
    """未配置白名单时是「兼容放行 + 标记 unverified」，不是静默信任。"""
    src = _src(TRUST_UTIL)
    assert "unverified: true" in src
    assert "embed_require_allowed_origins" in src, "收紧开关的出口要写在注释里，避免被误认为已完成"
