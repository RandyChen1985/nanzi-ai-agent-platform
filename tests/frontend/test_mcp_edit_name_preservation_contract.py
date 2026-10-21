"""契约测试：编辑已有 MCP 服务不得静默改名（审查发现的 P0）。

行为断言在 `mcp_edit_name_preservation.mjs`（真实执行命名工具函数 + 组件接线）；
这里锁住"测试真的在跑"，以及组件确实接上了这两个工具函数。
"""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = "frontend/src/components/system/McpServerRegistry.vue"


def test_edit_name_preservation_behaviour_via_node():
    node = subprocess.run(
        ["node", "tests/frontend/mcp_edit_name_preservation.mjs"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert node.returncode == 0, f"stdout={node.stdout}\nstderr={node.stderr}"
    assert "MCP 编辑命名行为测试通过" in node.stdout


def test_component_keeps_original_name_on_edit():
    source = (ROOT / REGISTRY).read_text(encoding="utf-8")

    assert "const editingOriginalName = ref('')" in source
    # 编辑回填时记住原名，保存前交给纯函数决定是否重组
    open_edit = source.index("const openEditModal")
    assert "editingOriginalName.value = String(server.server_name || '')" in source[
        open_edit:open_edit + 1500
    ]
    assert "resolveEditedServerName(" in source
