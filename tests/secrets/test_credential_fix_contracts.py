"""本轮核实出的几条缺陷的回归契约。

共同点：都是「看起来做过了、实际没生效」——界面显示已配置、代码路径也在，
但值从未到达它该去的地方，而且全程不报错。
"""
from pathlib import Path

ROOT = Path(__file__).parents[2]


def _src(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


class TestRotationDoesNotInheritExpiredDeadline:
    """过期的凭据轮换后，不能因为继承了旧的过期时间而回退到旧密文。

    `_pick_ciphertext` 先看新值是否在有效期内，不在就退到 `previous_*`。若轮换时
    没传新的 `expires_at`，继承来的时间戳往往已经过去，于是：新值刚写进去就不可用，
    整个双写窗口用的都是刚被替换掉的旧值——而 `is_live` 因为 previous 还在窗口内，
    一路放行，没有任何报错。
    """

    def test_expired_deadline_is_cleared_on_rotate(self):
        source = _src("app/services/secrets/center.py")
        body = source[source.index("row.last_rotated_at = now"):]
        body = body[:body.index("await _audit(")]
        assert "elif row.expires_at is not None and row.expires_at <= now:" in body
        assert "row.expires_at = None" in body

    def test_explicit_deadline_still_wins(self):
        source = _src("app/services/secrets/center.py")
        assert "if expires_at is not None:\n        row.expires_at = expires_at" in source


class TestEmptyHeadersStillGetBindings:
    """工具一个 header 都没配时，绑好的 header:Authorization 也要注入。

    原来整段注入被 `if headers:` 裹住，`{}` 直接跳过：界面写着「来自凭据」，
    发出去的请求里没有这个头，对方回 401。
    """

    def test_binding_call_is_outside_the_headers_guard(self):
        source = _src("app/services/ai/tools/generic_api.py")
        # 缩进 8 空格 = 与 `if headers:` 同级，即不在它里面
        assert "\n        headers = await apply_api_tool_bindings(" in source
        # 空 dict 也要能进注入：显式 or {}
        assert "headers or {}" in source
        # 16 空格的旧写法（在 if 内部）不能再出现
        assert "\n            headers = await apply_api_tool_bindings(" not in source


class TestSseResultReachesAudit:
    """SSE 出口必须先接住结果再返回，否则审计的 tool_output 恒为 None。"""

    def test_both_sse_exits_assign_first(self):
        source = _src("app/services/ai/tools/mcp_client.py")
        assert source.count("call_result = cls._normalize_sse_tool_result(") == 2
        # 不能再有裸 return（那会让 finally 里读到 None）
        assert "return cls._normalize_sse_tool_result(" not in source
        # 返回前也要脱敏：另两条出口都有，这里原来没有
        assert source.count("return redact_value(call_result,") >= 3


class TestApiToolEndpointsRefreshCache:
    """API 工具增改分删后要清注册表缓存，和脚本工具、MCP 服务保持一致。"""

    def test_create_and_update_clear_cache(self):
        source = _src("app/api/portal/endpoints/tools.py")
        assert source.count("ToolRegistry.clear_db_tool_cache()") == 4
        create = source[source.index("async def create_tool"):source.index("async def update_tool")]
        update = source[source.index("async def update_tool"):source.index("async def batch_set_tool_group")]
        batch = source[source.index("async def batch_set_tool_group"):source.index("async def delete_tool")]
        delete = source[source.index("async def delete_tool"):]
        assert "ToolRegistry.clear_db_tool_cache()" in create
        assert "ToolRegistry.clear_db_tool_cache()" in update
        assert "ToolRegistry.clear_db_tool_cache()" in batch
        assert "ToolRegistry.clear_db_tool_cache()" in delete
