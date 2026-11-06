"""契约：向导第 1 步探测前必须先补齐默认服务名。

新建流程的展示名输入框在向导第 2 步（验证成功之后）才渲染，而探测接口过去把
``server_name`` 当必填字段，于是 ``payload.server_name`` 为空串时后端直接 400
「参数校验失败」——用户看到的是「连接并发现工具」永远失败，实际连出站请求都没发出。

这里锁住调用顺序：补齐默认后缀发生在 POST /verify 之前。
"""

from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure

SOURCE = Path("frontend/src/components/system/McpServerRegistry.vue").read_text()


def _function_body(name: str, next_name: str) -> str:
    start = SOURCE.index(f"const {name} = async")
    end = SOURCE.index(f"const {next_name} = async", start)
    return SOURCE[start:end]


def test_handle_verify_fills_default_name_before_requesting():
    body = _function_body("handleVerify", "addServer")

    name_fill = body.index("ensureServerNameSuffixFromUrl()")
    request = body.index("axios.post('/api/portal/mcp/verify'")

    assert name_fill < request, "补齐默认服务名必须发生在探测请求之前"
