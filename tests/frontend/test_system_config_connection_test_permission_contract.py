"""系统配置页「连接测试」按钮的权限门控契约。

后端 `POST /api/portal/system/test-connection/{component}` 统一要求
`element:system:config_save`（app/api/portal/endpoints/system.py）。前端凡是调用它的
按钮都必须在无该权限时隐藏或禁用，否则只读用户点下去只会拿到 403：

    Permission required: element:system:config_save

历史缺陷：`测试 Embedding 连接`（global_embed）与 ChatBI 知识库的 `测试`（chatbi_kb）
两个按钮漏了门控，只读用户仍可点击。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
SYSTEM_CONFIG = ROOT / "frontend/src/views/SystemConfig.vue"


def _button_block(source: str, handler: str) -> str:
    """返回带有指定 @click 处理函数的 <button> ... </button> 片段。"""
    click_marker = f'@click="{handler}"'
    index = source.index(click_marker)
    start = source.rindex("<button", 0, index)
    end = source.index("</button>", index) + len("</button>")
    return source[start:end]


def test_global_embed_test_button_is_hidden_without_config_save():
    source = SYSTEM_CONFIG.read_text(encoding="utf-8")
    block = _button_block(source, "testGlobalEmbed")

    assert 'v-if="canSave"' in block, (
        "「测试 Embedding 连接」按钮必须在无 element:system:config_save 时隐藏，"
        "否则后端 test-connection 会返回 403"
    )


def test_chatbi_kb_test_button_is_hidden_without_config_save():
    source = SYSTEM_CONFIG.read_text(encoding="utf-8")
    block = _button_block(source, "testChatBiKb(item)")

    assert 'v-if="canSave"' in block, (
        "ChatBI 知识库「测试」按钮走同一个 test-connection 端点，"
        "无 element:system:config_save 时同样必须隐藏"
    )


def test_connection_tests_that_stay_visible_are_disabled_without_config_save():
    """RAGFlow / 远端 SQL 的测试按钮保留展示，但必须凭 canSave 禁用。"""
    source = SYSTEM_CONFIG.read_text(encoding="utf-8")

    for handler in ("testRagflowMetadataConnection", "testRemoteSqlConnection"):
        block = _button_block(source, handler)
        assert ":disabled=" in block

    for computed_name in ("ragflowTestDisabled", "remoteSqlTestDisabled"):
        start = source.index(f"const {computed_name} = computed(")
        end = source.index("})", start)
        assert "!canSave" in source[start:end], (
            f"{computed_name} 必须包含 !canSave，保证只读用户点不动测试按钮"
        )
