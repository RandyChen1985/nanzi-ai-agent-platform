"""绑定列表要带凭据状态。

绑定存在 ≠ 能取到值：凭据可能已被停用、过期、或撤销了授权。界面只显示「已绑定」，
用户看到的是「我配好了」，实际调用却是 DENY_DISABLED。这类「看起来对、用不了」
的状态最难排查，所以把状态一路带回前端。
"""

from __future__ import annotations

import inspect

import pytest


pytestmark = pytest.mark.no_infrastructure


def test_get_bindings_returns_secret_status():
    from app.api.portal.endpoints import secrets as secrets_endpoints

    source = inspect.getsource(secrets_endpoints.get_bindings)
    assert '"secret_status": secret.status' in source


def test_platform_targets_returns_secret_status():
    """用户侧「我的 MCP → 平台服务」也要能看出自己绑的凭据失效了。"""
    from app.api.portal.endpoints import secrets as secrets_endpoints

    source = inspect.getsource(secrets_endpoints)
    # 两处绑定投影都带上了
    assert source.count('"secret_status": secret.status') >= 2


def test_status_is_the_authoritative_field():
    """状态取的是 UserSecret.status，不是绑定自身的状态——绑定没有状态。"""
    from app.models.secret import UserSecret

    assert hasattr(UserSecret, "status")
