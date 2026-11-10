"""API 工具试跑：草稿必须带着真 id，否则凭据绑定查不到。

试跑和运行时走同一个 `_perform_request`，注入逻辑本身是共用的；唯一会掉的
环节是 id——绑定按 `consumer_id` 查，用 `__preview__` 的话标成「来自凭据」
的参数永远解析不到，只会报「未绑定」，而保存后运行时又是好用的。
"""

from __future__ import annotations

import inspect

import pytest


pytestmark = pytest.mark.no_infrastructure


def _preview_source() -> str:
    from app.api.portal.endpoints import tools as tools_endpoints

    return inspect.getsource(tools_endpoints.preview_tool)


def test_preview_draft_uses_real_tool_id():
    source = _preview_source()
    assert 'id="__preview__"' not in source, "草稿 id 被写死了，绑定查不到"
    assert "payload.tool_id" in source


def test_preview_returns_missing_credential_as_its_own_kind():
    """别把「没绑凭据」混进「执行失败」——用户要看的是去绑，不是重试。"""
    source = _preview_source()
    assert "UnresolvedCredentialError" in source
    assert '"missing_credential"' in source
