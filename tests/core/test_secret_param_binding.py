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


def test_fixed_field_supports_integer_and_rule_requires_string():
    assert credential_param_names(schema_with(type_name="integer")) == [PARAM]
    with pytest.raises(SystemParamConfigError):
        credential_param_names(schema_with(source="rule", type_name="integer"))


def test_unknown_source_is_rejected():
    with pytest.raises(SystemParamConfigError):
        credential_param_names(schema_with(source="env"))


class TestParamInjectionSlot:
    """槽位算错就永远匹配不到绑定，而且**不会报错**——这才是最坏的一种。

    这些用例跑真实现（`apply_api_tool_param_bindings`），不是断言源码文本：
    文本断言只能证明「有人写过这行」，证明不了它算对了。
    """

    @staticmethod
    def _config(*, method: str, url: str):
        import json
        from types import SimpleNamespace

        schema = {
            "type": "object",
            "properties": {},
            "x-nanzi-system-params": [
                {"name": PARAM, "type": "string", "source": "credential"},
            ],
        }
        return SimpleNamespace(
            id="tool-1",
            name="退款",
            method=method,
            url_template=url,
            parameter_schema=json.dumps(schema),
        )

    @staticmethod
    def _resolved(monkeypatch, expected_slot: str, value: str = "SECRET-V"):
        """把 resolver 换成「只有 expected_slot 才取得到」，顺便断言它被问的就是这个槽位。"""
        from types import SimpleNamespace

        from app.services.secrets import injection

        asked: list[str] = []

        async def fake_resolve(**kwargs):
            asked.append(kwargs["slot"])
            if kwargs["slot"] == expected_slot:
                return SimpleNamespace(ok=True, value=SimpleNamespace(reveal=lambda: value), denial=None)
            # 用一个「真拒绝」而不是 denied_by_binding：后者表示「这个槽位没绑定」，
            # 属于正常情况，_record 按设计不把它记成 denial。
            return SimpleNamespace(ok=False, value=None, denial="denied_disabled")

        monkeypatch.setattr(injection.secret_resolver, "resolve", fake_resolve)
        return asked

    async def test_get_writes_to_query_slot(self, monkeypatch):
        from app.services.secrets.injection import apply_api_tool_param_bindings

        config = self._config(method="GET", url="https://api.test/refund")
        asked = self._resolved(monkeypatch, "query:api_key")

        out = await apply_api_tool_param_bindings(config, {}, method="GET", user_info={"user_id": 7})

        assert asked == ["query:api_key"]
        assert out == {PARAM: "SECRET-V"}

    @pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
    async def test_non_get_writes_to_body_slot(self, monkeypatch, method):
        from app.services.secrets.injection import apply_api_tool_param_bindings

        config = self._config(method=method, url="https://api.test/refund")
        asked = self._resolved(monkeypatch, "body:api_key")

        out = await apply_api_tool_param_bindings(config, {}, method=method, user_info={"user_id": 7})

        assert asked == ["body:api_key"]
        assert out == {PARAM: "SECRET-V"}

    async def test_url_placeholder_writes_to_path_slot(self, monkeypatch):
        """`POST /refund/{api_key}` 里的参数是 path，不是 body 字段。"""
        from app.services.secrets.injection import apply_api_tool_param_bindings

        config = self._config(method="POST", url="https://api.test/refund/{api_key}")
        asked = self._resolved(monkeypatch, "path:api_key")

        out = await apply_api_tool_param_bindings(config, {}, method="POST", user_info={"user_id": 7})

        assert asked == ["path:api_key"]
        assert out == {PARAM: "SECRET-V"}

    async def test_unbound_param_is_reported_not_silently_dropped(self, monkeypatch):
        """绑错槽位时解析会拒绝——这个拒绝必须被记成 denial，不能吞掉。

        吞掉的话调用方只会看到「参数缺失」，永远不知道是绑定没匹配上。
        """
        from app.services.secrets.injection import apply_api_tool_param_bindings

        config = self._config(method="POST", url="https://api.test/refund")
        self._resolved(monkeypatch, "query:api_key")  # 故意只认错的那个槽位

        denials: dict[str, str] = {}
        out = await apply_api_tool_param_bindings(
            config, {}, method="POST", user_info={"user_id": 7}, denials=denials
        )

        assert out == {}
        assert "body:api_key" in denials
