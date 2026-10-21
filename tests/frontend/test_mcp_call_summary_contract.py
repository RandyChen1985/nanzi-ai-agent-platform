"""「我的 MCP」调用概览的契约。

全平台审计只给管理员；普通用户能看的是**自己服务**的聚合指标。
这里锁住两件事：组件确实请求只按本人聚合的接口，且不提供任何跨用户视图。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend" / "src"
SUMMARY = FRONTEND / "components" / "mcp" / "McpCallSummary.vue"
VIEW = FRONTEND / "views" / "McpManagement.vue"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_summary_component_requests_owner_scoped_endpoint():
    source = _read(SUMMARY)
    assert "/api/portal/mcp/outbound-summary/mine" in source
    # 绝不能退化成全平台审计接口
    assert "'/api/portal/mcp/outbound-logs'" not in source


def test_summary_component_offers_three_ranges():
    source = _read(SUMMARY)
    for value in ("24h", "7d", "30d"):
        assert f"value: '{value}'" in source


def test_summary_component_shows_empty_state():
    source = _read(SUMMARY)
    assert "还没有调用记录" in source


def test_summary_component_renders_metrics_and_breakdown():
    source = _read(SUMMARY)
    for label in ("调用", "成功率", "失败", "平均耗时"):
        assert label in source
    for section in ("调用最多的服务", "热门工具", "每日调用量"):
        assert section in source


def test_summary_component_supports_dark_theme():
    source = _read(SUMMARY)
    assert "dark:" in source


def test_view_renders_summary_only_in_personal_mine():
    source = _read(VIEW)
    assert "McpCallSummary" in source
    assert '<McpCallSummary v-if="mcpMenu === \'mine\'"' in source
