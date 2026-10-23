"""失效工具引用判定：纯函数部分，不依赖数据库/Redis。"""
import pytest

from app.services.mcp.stale_tool_cleanup import prune_dead_tool_refs
from app.services.mcp.tool_ref_health import (
    REASON_LABELS,
    classify_stale_mcp_ref,
    configured_tool_names,
    split_mcp_tool_name,
)

pytestmark = pytest.mark.no_infrastructure


def test_configured_tool_names_handles_mixed_shapes():
    assert configured_tool_names(
        ["get_dataset_schema", {"name": "mcp-public-u-demo:query"}, {"x": 1}, "", None]
    ) == {"get_dataset_schema", "mcp-public-u-demo:query"}
    assert configured_tool_names('["mcp-public-u-demo:query"]') == {"mcp-public-u-demo:query"}
    assert configured_tool_names("plain-name") == {"plain-name"}
    assert configured_tool_names("") == set()
    assert configured_tool_names(None) == set()


def test_split_mcp_tool_name_requires_server_and_tool():
    assert split_mcp_tool_name("mcp-public-u-demo:query") == ("mcp-public-u-demo", "query")
    assert split_mcp_tool_name("  mcp-public-u-demo : query  ") == ("mcp-public-u-demo", "query")
    assert split_mcp_tool_name("get_dataset_schema") is None
    assert split_mcp_tool_name(":query") is None
    assert split_mcp_tool_name("mcp-public-u-demo:") is None
    assert split_mcp_tool_name("") is None


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        # 服务没了：最高优先级，不再追究工具状态
        (
            dict(
                server_exists=False,
                server_visible=False,
                server_enabled=False,
                cache_exists=False,
                cache_published=False,
                cache_available=False,
            ),
            "server_deleted",
        ),
        # 别人的个人服务：不泄露启用/发布状态
        (
            dict(
                server_exists=True,
                server_visible=False,
                server_enabled=True,
                cache_exists=True,
                cache_published=True,
                cache_available=True,
            ),
            "server_forbidden",
        ),
        (
            dict(
                server_exists=True,
                server_visible=True,
                server_enabled=False,
                cache_exists=True,
                cache_published=True,
                cache_available=True,
            ),
            "server_disabled",
        ),
        (
            dict(
                server_exists=True,
                server_visible=True,
                server_enabled=True,
                cache_exists=False,
                cache_published=False,
                cache_available=False,
            ),
            "tool_missing",
        ),
        (
            dict(
                server_exists=True,
                server_visible=True,
                server_enabled=True,
                cache_exists=True,
                cache_published=True,
                cache_available=False,
            ),
            "tool_removed_remote",
        ),
        (
            dict(
                server_exists=True,
                server_visible=True,
                server_enabled=True,
                cache_exists=True,
                cache_published=False,
                cache_available=True,
            ),
            "tool_unpublished",
        ),
        # 服务可见、启用、工具已发布且可用：不应被判为失效，兜底 unknown
        (
            dict(
                server_exists=True,
                server_visible=True,
                server_enabled=True,
                cache_exists=True,
                cache_published=True,
                cache_available=True,
            ),
            "unknown",
        ),
    ],
)
def test_classify_stale_mcp_ref_priority(state, expected):
    assert classify_stale_mcp_ref(**state) == expected


def test_every_classified_reason_has_user_facing_label():
    classified = {
        classify_stale_mcp_ref(
            server_exists=exists,
            server_visible=visible,
            server_enabled=enabled,
            cache_exists=cache,
            cache_published=published,
            cache_available=available,
        )
        for exists in (True, False)
        for visible in (True, False)
        for enabled in (True, False)
        for cache in (True, False)
        for published in (True, False)
        for available in (True, False)
    }
    assert classified <= set(REASON_LABELS)
    assert all(REASON_LABELS[reason].strip() for reason in classified)


def test_prune_dead_tool_refs_removes_string_and_object_refs():
    tools = [
        "get_dataset_schema",
        "mcp-public-u-demo:query",
        {"name": "mcp-public-u-demo:write", "metadata_dataset_ids": ["d1"]},
    ]
    assert prune_dead_tool_refs(tools, {"mcp-public-u-demo:query"}) == [
        "get_dataset_schema",
        {"name": "mcp-public-u-demo:write", "metadata_dataset_ids": ["d1"]},
    ]
    # 只挂失效工具的版本应写回空数组（而不是 None，前端按数组遍历）
    assert prune_dead_tool_refs(["mcp-public-u-demo:query"], {"mcp-public-u-demo:query"}) == []
    # 没有命中就不改动，返回 None 让调用方跳过写库
    assert prune_dead_tool_refs(["get_dataset_schema"], {"mcp-public-u-demo:query"}) is None
    assert prune_dead_tool_refs(None, {"x"}) is None
