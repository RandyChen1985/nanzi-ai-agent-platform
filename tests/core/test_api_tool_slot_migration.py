"""工具配置变了，参数绑定的槽位要跟着走。

槽位编码的是「参数落在哪」：`POST /refund/{applicant}` 是 path、
`GET /refund?applicant=` 是 query、其余方法是 body。改配置后旧槽位成了孤儿，
而前端按**参数名**查绑定（三个前缀都试），照样显示「已绑定」——于是最难查的
那种状态出现了：界面说配好了，运行时报「未绑定」。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.services.secrets.policy import resolve_api_tool_slot


pytestmark = pytest.mark.no_infrastructure


def _tool(*, method: str, url: str, params: list[str]):
    schema = {
        "type": "object",
        "properties": {},
        "x-nanzi-system-params": [
            {"name": name, "type": "string", "source": "credential"} for name in params
        ],
    }
    return SimpleNamespace(
        id="tool-1",
        method=method,
        url_template=url,
        parameter_schema=json.dumps(schema),
    )


def test_slot_flips_when_url_gains_a_placeholder():
    """GET + 无占位 → query；改成 POST + 有占位 → path。"""
    before = _tool(method="GET", url="https://api.test/refund", params=["applicant"])
    after = _tool(method="POST", url="https://api.test/refund/{applicant}", params=["applicant"])

    old_slot = resolve_api_tool_slot(
        name="applicant", method=before.method, url_template=before.url_template
    )
    new_slot = resolve_api_tool_slot(
        name="applicant", method=after.method, url_template=after.url_template
    )

    assert old_slot == "query:applicant"
    assert new_slot == "path:applicant"
    # 迁移就是把 old → new，否则绑定留在旧槽位上永远匹配不到
    assert old_slot != new_slot


def test_slot_flips_when_method_changes():
    get = _tool(method="GET", url="https://api.test/refund", params=["tenant"])
    post = _tool(method="POST", url="https://api.test/refund", params=["tenant"])

    assert resolve_api_tool_slot(
        name="tenant", method=get.method, url_template=get.url_template
    ) == "query:tenant"
    assert resolve_api_tool_slot(
        name="tenant", method=post.method, url_template=post.url_template
    ) == "body:tenant"


def test_slot_is_stable_when_nothing_changed():
    """没改配置时迁移必须是空操作，不能把绑定来回搬。"""
    tool = _tool(method="POST", url="https://api.test/refund", params=["tenant"])
    assert resolve_api_tool_slot(
        name="tenant", method=tool.method, url_template=tool.url_template
    ) == resolve_api_tool_slot(
        name="tenant", method=tool.method, url_template=tool.url_template
    )


def test_migration_only_touches_param_slots():
    """`header:` 和 `env:` 不随 URL 变化，迁移函数只认 path/query/body。"""
    import inspect

    from app.services.secrets import center

    source = inspect.getsource(center.migrate_api_tool_param_slots)
    assert '{"path", "query", "body"}' in source
    assert "header" not in source


def test_update_endpoint_runs_migration():
    import inspect

    from app.api.portal.endpoints import tools as tools_endpoints

    source = inspect.getsource(tools_endpoints.update_tool)
    assert "migrate_api_tool_param_slots" in source
    # 迁移失败不能让保存工具整体失败——绑定还在，重选一次就行
    assert "迁移 API 工具" in source
