"""契约测试：MCP 服务卡片的发布状态分级与「去发布」动作。

事故背景：列表卡片把 `0 已发布` 和 `8 已发布` 都写成 text-green-600（绿色 = 正常），
于是截图里 `8 工具 / 0 已发布` 被当成配置完成，实际智能体连一个工具都勾不到，也
没有从列表直接去发布的入口。行为断言在 `mcp_server_publish_state.mjs`（解析模板 AST），
这里锁住测试真的在跑、以及几条不能在重构中丢掉的约束。
"""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = "frontend/src/components/system/McpServerRegistry.vue"


def _source() -> str:
    return (ROOT / REGISTRY).read_text(encoding="utf-8")


def test_publish_state_behaviour_via_node():
    node = subprocess.run(
        ["node", "tests/frontend/mcp_server_publish_state.mjs"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert node.returncode == 0, f"stdout={node.stdout}\nstderr={node.stderr}"
    assert "OK: MCP 服务卡片发布状态分级" in node.stdout


def test_state_machine_covers_zero_partial_and_full():
    """0 发布 / 部分发布 / 全部发布必须走不同分支，不能只比较 enabled_status。"""
    source = _source()
    helper = source.split("const serverPublishState", 1)[1].split("const unpublishedToolCount", 1)[0]

    assert "if (server.enabled_status !== 1) return 'disabled'" in helper
    assert "if (total <= 0) return 'empty'" in helper
    assert "if (published <= 0) return 'none'" in helper
    assert "if (published < total) return 'partial'" in helper
    assert "return 'full'" in helper
    # 0 发布必须被显式判成异常态，而不是自然落到「已发布」绿色分支
    assert "'none'" in helper


def test_go_publish_does_not_trigger_publication():
    """「去发布」只选中服务并滚动，不能替用户批量发布（一次改多个工具可见性）。"""
    source = _source()
    body = source.split("const goPublishServerTools", 1)[1].split("const clearSelectedServer", 1)[0]

    assert "selectServer(server)" in body
    assert "scrollIntoView" in body
    assert "publishAllCurrentServerTools" not in body
    assert "axios" not in body


def test_zero_publish_hint_is_gated_by_publish_permission():
    """没有发布权限的人看到说明即可，按钮不该出现（点进去也无从操作）。"""
    source = _source()
    marker = "工具已同步，但一个都没发布"

    index = source.index(marker)
    # 提示行之后紧接着「去发布」按钮；权限守卫写在按钮属性上
    window = source[index:index + 1200]
    assert "canPublishMcp" in window, window
    assert "goPublishServerTools(server)" in window, window
