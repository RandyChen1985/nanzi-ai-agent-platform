"""凭据策略三个参数的值校验。

界面上现在是开关和加减器，但 `PATCH /api/config` 可以被直接调用。不校验的话
`secret_policy_max_inject_per_call = "gldfjgl"` 会安静落库，读取侧 `_as_limit`
退回默认值——管理员以为改了，实际没生效，而且库里留着一个永远不该出现的值。
"""

import pytest

from app.services.config_service import validate_config_update

FLAG_KEYS = ["secret_policy_allow_external_host", "secret_policy_allow_script_env"]


@pytest.mark.parametrize("key", FLAG_KEYS)
@pytest.mark.parametrize("value", ["0", "1", " 1 "])
def test_flags_accept_only_zero_or_one(key, value):
    validate_config_update(key, value)


@pytest.mark.parametrize("key", FLAG_KEYS)
@pytest.mark.parametrize("value", ["yes", "on", "true", "", "2", "gldfjgl"])
def test_flags_reject_other_literals(key, value):
    """只收 0/1：读取侧虽然认得 yes/on，但界面上只有开和关，
    两套口径并存只会让「库里存的是什么」变得说不清。"""
    with pytest.raises(ValueError):
        validate_config_update(key, value)


@pytest.mark.parametrize("value", ["1", "3", "50", " 7 "])
def test_max_inject_accepts_range(value):
    validate_config_update("secret_policy_max_inject_per_call", value)


@pytest.mark.parametrize("value", ["gldfjgl", "", "0", "-1", "51", "3.5"])
def test_max_inject_rejects_out_of_range_or_non_numeric(value):
    with pytest.raises(ValueError):
        validate_config_update("secret_policy_max_inject_per_call", value)


def test_resolver_falls_back_when_stored_value_is_garbage():
    """库里已经有脏值（上线前存进去的）：运行时必须退回默认，不能炸。"""
    from app.services.secrets.resolver import DEFAULT_MAX_INJECT, _as_limit

    assert _as_limit("gldfjgl") == DEFAULT_MAX_INJECT
    assert _as_limit(None) == DEFAULT_MAX_INJECT
    # 越界同样收敛到合法区间，而不是把 0 条注入当成「不允许任何凭据」
    assert _as_limit("0") == 1
    assert _as_limit("999") == 50
