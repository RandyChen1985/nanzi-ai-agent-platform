"""参数位的凭据绑定。

header 是配置里的固定行，绑定天然；参数不行——它的值由模型运行时填，平台分不清
「模型没填」和「模型编了一个值」。所以只有显式声明 ``source: credential`` 的参数
才走凭据中心，同时它已从模型可见的 properties 里消失。
"""

import pytest

from app.services.ai.tools.system_params import (
    SystemParamConfigError,
    apply_system_params,
    credential_param_names,
)

PARAM = "api_key"


def schema_with(*, source="credential", type_name="string", value=None):
    entry = {"name": PARAM, "type": type_name, "source": source}
    if value is not None:
        entry["value"] = value
    return {"x-nanzi-system-params": [entry]}


def test_credential_param_is_listed():
    assert credential_param_names(schema_with()) == [PARAM]


def test_literal_param_is_not_listed():
    assert credential_param_names(schema_with(source="literal", value="v")) == []


def test_model_supplied_value_is_discarded():
    """模型就算自己编了一个值，也必须被丢掉——否则「模型不可见」形同虚设。"""
    result = apply_system_params(schema_with(), {PARAM: "model-made-this-up"}, {})
    assert PARAM not in result


def test_credential_param_is_removed_not_emptied():
    """不能留一个空串：那会变成「带着空 key 的请求」，对方返回 401 而看不出原因。"""
    result = apply_system_params(schema_with(), {}, {})
    assert PARAM not in result


def test_literal_param_still_written():
    schema = {
        "x-nanzi-system-params": [
            {"name": "tenant", "type": "string", "value": "t-1"},
            {"name": PARAM, "type": "string", "source": "credential"},
        ]
    }
    assert apply_system_params(schema, {}, {}) == {"tenant": "t-1"}


def test_credential_param_requires_string_type():
    with pytest.raises(SystemParamConfigError):
        credential_param_names(schema_with(type_name="integer"))


def test_unknown_source_is_rejected():
    with pytest.raises(SystemParamConfigError):
        credential_param_names(schema_with(source="env"))


def test_slot_prefix_follows_http_method():
    """GET 的参数在 URL 上、其余方法在 JSON body 里，前缀写错就永远匹配不到绑定。"""
    import inspect

    from app.services.secrets import injection

    source = inspect.getsource(injection.apply_api_tool_param_bindings)
    assert '"query" if (method or "GET").upper() == "GET" else "body"' in source
