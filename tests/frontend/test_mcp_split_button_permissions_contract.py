"""契约测试：MCP「添加」入口必须按创建权限收敛（审查发现的按钮级遗漏）。

行为断言在 `mcp_split_button_permissions.mjs` 里（解析模板 AST，逐个按钮断言）；
这里锁住"测试真的在跑、接线没被摘掉"。
"""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = "frontend/src/components/system/McpServerRegistry.vue"


def _source() -> str:
    return (ROOT / REGISTRY).read_text(encoding="utf-8")


def test_split_button_permissions_behaviour_via_node():
    node = subprocess.run(
        ["node", "tests/frontend/mcp_split_button_permissions.mjs"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert node.returncode == 0, f"stdout={node.stdout}\nstderr={node.stderr}"
    assert "行为测试通过" in node.stdout


def test_split_container_excludes_personal_scope():
    """个人区没有创建权限时不能掉进平台样式分支：Echo 只支持平台区，点了会静默 return。"""
    source = _source()

    assert 'v-else-if="props.scope !== \'personal\' && (canCreateMcp || canEditMcp)"' in source


def test_wizard_entry_and_submit_have_function_level_guards():
    """按钮不是唯一入口：页面级引导（市场/JSON 粘贴）直接调 openAddModal。"""
    source = _source()

    open_modal = source.index("const openAddModal")
    assert "if (!canCreateMcp.value)" in source[open_modal:open_modal + 400]

    add_server = source.index("const addServer")
    assert "if (!isEditing.value && !canCreateMcp.value)" in source[add_server:add_server + 500]


def test_submit_button_is_disabled_without_create_permission():
    source = _source()
    marker = "确认并完成添加"

    index = source.index(marker)
    # 按钮属性写在插值之前，取前面一段窗口看 :disabled
    window = source[max(0, index - 400):index]
    assert ":disabled=\"!isEditing && !canCreateMcp\"" in window, window
