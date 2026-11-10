"""使用记录的统计视图。

明细回答「刚才那次为什么失败」，统计回答「这周正常吗」——两个问题一屏放不下，
所以分开。统计只做 GROUP BY，不逐条捞：流水表只会越积越大。
"""

from __future__ import annotations

import inspect

import pytest


pytestmark = pytest.mark.no_infrastructure


def test_stats_endpoint_exists():
    from app.api.portal.endpoints import secrets as secrets_endpoints

    routes = [r.path for r in secrets_endpoints.router.routes]
    assert "/usage/stats" in routes


def test_stats_endpoint_is_not_swallowed_by_secret_id_route():
    """`/usage/stats` 必须排在 `/{secret_id}` 之前，否则会被当成 id。"""
    from app.api.portal.endpoints import secrets as secrets_endpoints

    paths = [r.path for r in secrets_endpoints.router.routes]
    assert paths.index("/usage/stats") < paths.index("/{secret_id}")


def test_stats_counts_denials_too():
    """被拒绝的次数本身就是「配了但用不了」的信号，不能只统计 success。"""
    from app.services.secrets import center

    source = inspect.getsource(center.usage_stats)
    assert 'result != "success"' in source


def test_stats_scoped_like_usage_detail():
    """管理员看全局，普通用户只看自己——与 /usage 同口径。"""
    from app.api.portal.endpoints import secrets as secrets_endpoints

    source = inspect.getsource(secrets_endpoints.get_usage_stats)
    # 管理员判定统一走 _is_admin（原来散着写 5 次字符串比较）
    assert "_is_admin(user_info)" in source or 'user_info.get("role")' in source


def test_stats_accepts_secret_filter():
    from app.api.portal.endpoints import secrets as secrets_endpoints

    source = inspect.getsource(secrets_endpoints.get_usage_stats)
    assert "secret_id" in source
