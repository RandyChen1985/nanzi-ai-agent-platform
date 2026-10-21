"""删除用户时要连带清理他的个人 MCP。

个人 MCP 的孤儿行不会报错，但会带来两个安静的问题：

1. `sys_mcp_servers.server_name` 是**全局唯一**索引，用户删掉后这个名字被永久占用，
   别人再起同名直接 400「已存在，请修改名称」；
2. 工具缓存表的外键**没有** `ON DELETE CASCADE`，所以删服务前必须先删工具，
   否则会在删除用户时撞外键报 500。
"""
from pathlib import Path

import pytest

from app.models.mcp import McpToolCache

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[3]
MANAGEMENT = ROOT / "app/api/portal/endpoints/management.py"


def _delete_user_body() -> str:
    source = MANAGEMENT.read_text(encoding="utf-8")
    body = source.split("async def delete_user(", 1)[1]
    # 截到下一个路由装饰器，避免把后面函数的代码也算进来
    return body.split("\n@router.", 1)[0]


def test_delete_user_removes_owned_personal_servers():
    body = _delete_user_body()
    assert "McpServer" in body, "delete_user 必须清理个人 MCP 服务"
    assert "McpToolCache" in body, "工具缓存的外键没有级联，必须显式删除"
    assert "McpOutboundAuditLog" in body, "出站日志会随服务一起留下孤儿记录"


def test_delete_user_only_touches_owned_personal_servers():
    body = _delete_user_body()
    server_cleanup = body.split("from app.models.mcp import", 1)[1]
    assert 'McpServer.scope == "personal"' in server_cleanup
    assert "McpServer.user_id == user_id" in server_cleanup
    # 平台公共 MCP 不属于任何用户，不能被顺手删掉
    assert 'McpServer.scope == "global"' not in server_cleanup


def test_tool_cache_is_deleted_before_server_rows():
    """工具缓存的外键无 ON DELETE CASCADE，顺序反了会直接外键报错。"""
    body = _delete_user_body()
    cleanup = body.split("from app.models.mcp import", 1)[1]
    assert cleanup.index("delete(McpToolCache)") < cleanup.index("delete(McpServer)")


def test_mcp_cleanup_runs_before_deleting_user():
    body = _delete_user_body()
    assert body.index("McpServer") < body.index("await db.delete(user)")


def test_tool_cache_foreign_key_has_no_cascade():
    """这条是上面顺序断言的依据：模型里确实没有 ondelete 级联。"""
    column = McpToolCache.__table__.columns["server_id"]
    foreign_keys = list(column.foreign_keys)
    assert foreign_keys, "sys_mcp_tool_cache.server_id 应当是指向 sys_mcp_servers 的外键"
    assert foreign_keys[0].ondelete is None
