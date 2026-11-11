"""规则定位：纯函数 + 模型字段的最小断言（无 DB、无网络）。"""
import pytest

from app.models.secret import SecretBinding
from app.services.secrets.locator import (
    RuleError,
    build_candidates,
    parse_rule,
    pick_match,
    rule_summary,
)


def test_binding_has_strategy_and_rule_json_columns():
    cols = SecretBinding.__table__.columns
    assert "strategy" in cols
    assert "rule_json" in cols
    # 默认值是 fixed：存量行与所有未显式传 strategy 的写入都应按固定绑定处理
    assert cols["strategy"].default.arg == "fixed"


def test_binding_secret_id_is_nullable():
    assert SecretBinding.__table__.columns["secret_id"].nullable is True


class _Secret:
    """只带匹配需要的那三个字段，避免测试依赖完整模型。"""

    def __init__(self, secret_key, name, category, id="s"):
        self.secret_key, self.name, self.category, self.id = secret_key, name, category, id


def test_parse_rule_accepts_arg_key_and_normalizes():
    spec = parse_rule({
        "key_source": "arg:to",
        "transforms": ["trim", "lower"],
        "templates": ["{value}", "{value}-smtp"],
        "target": "secret_key",
        "match": "exact",
        "on_miss": "model",
    })
    assert spec.key_source == "arg:to"
    assert spec.arg_name == "to"
    assert spec.transforms == ("trim", "lower")
    assert spec.templates == ("{value}", "{value}-smtp")
    assert spec.on_miss == "model"


def test_parse_rule_rejects_unknown_field_and_bad_enum():
    with pytest.raises(RuleError, match="未知字段"):
        parse_rule({"key_source": "arg:to", "secret_id": "abc"})
    with pytest.raises(RuleError, match="on_miss"):
        parse_rule({"key_source": "arg:to", "on_miss": "retry"})


def test_parse_rule_rejects_too_many_templates_and_missing_placeholder():
    with pytest.raises(RuleError, match="最多 3 条"):
        parse_rule({"key_source": "arg:to", "templates": ["a", "b", "c", "d"]})
    with pytest.raises(RuleError, match="\\{value\\}"):
        parse_rule({"key_source": "arg:to", "templates": ["fixed-name"]})


def test_build_candidates_applies_transforms_in_order():
    spec = parse_rule({
        "key_source": "arg:to",
        "transforms": ["trim", "lower"],
        "templates": ["mail:{value}"],
    })
    assert build_candidates(spec, identity={}, args={"to": "  ZhangXM@nanzi.com  "}) == ["mail:zhangxm@nanzi.com"]


def test_build_candidates_strip_domain_is_noop_without_at():
    spec = parse_rule({"key_source": "arg:to", "transforms": ["strip_domain"], "templates": ["{value}"]})
    assert build_candidates(spec, identity={}, args={"to": "A"}) == ["A"]


def test_build_candidates_returns_empty_when_arg_missing():
    spec = parse_rule({"key_source": "arg:to", "templates": ["{value}"]})
    assert build_candidates(spec, identity={}, args={"subject": "hi"}) == []


def test_build_candidates_from_identity():
    spec = parse_rule({"key_source": "user.email", "transforms": ["strip_domain"], "templates": ["mail:{value}"]})
    assert build_candidates(spec, identity={"email": "a@b.com"}, args={}) == ["mail:a"]


def test_pick_match_tries_templates_in_order():
    spec = parse_rule({
        "key_source": "arg:to",
        "templates": ["{value}-smtp", "{value}"],
        "on_miss": "fail",
    })
    secrets = [_Secret("A", "A 的邮箱", "mail"), _Secret("A-smtp", "A 的 SMTP", "mail")]
    # 待匹配值必须由模板生成，才能验证「模板顺序」：先 {value}-smtp，再 {value}
    wanted = build_candidates(spec, identity={}, args={"to": "A"})
    assert wanted == ["A-smtp", "A"]
    hit, used = pick_match(spec, wanted, secrets)
    # 第一个模板 {value}-smtp 生成的 "A-smtp" 命中，说明模板顺序生效
    assert (hit.secret_key, used) == ("A-smtp", "A-smtp")


def test_pick_match_exact_vs_exact_ci():
    secrets = [_Secret("CC", "C 的邮箱", "mail")]
    strict = parse_rule({"key_source": "arg:to", "match": "exact", "templates": ["{value}"]})
    loose = parse_rule({"key_source": "arg:to", "match": "exact_ci", "templates": ["{value}"]})
    assert pick_match(strict, ["cc"], secrets)[0] is None
    assert pick_match(loose, ["cc"], secrets)[0].secret_key == "CC"


def test_pick_match_can_target_name_and_category():
    secrets = [_Secret("k1", "A 的邮箱账号", "mail-account")]
    by_name = parse_rule({"key_source": "arg:to", "target": "name", "templates": ["{value}"]})
    by_cat = parse_rule({"key_source": "arg:to", "target": "category", "templates": ["{value}"]})
    assert pick_match(by_name, ["A 的邮箱账号"], secrets)[0].secret_key == "k1"
    assert pick_match(by_cat, ["mail-account"], secrets)[0].secret_key == "k1"


def test_rule_summary_is_human_readable():
    spec = parse_rule({"key_source": "arg:to", "templates": ["{value}"], "on_miss": "model"})
    # 采用计划里 rule_summary 的第二版（自我更正版）实现
    assert rule_summary(spec) == "secret_key = {value}（取入参 to）"


def test_parse_rule_rejects_rule_carrying_secret_id():
    """规则只描述「怎么找」，绝不描述「找到谁」。"""
    with pytest.raises(RuleError, match="未知字段"):
        parse_rule({"key_source": "arg:to", "templates": ["{value}"], "secret_id": "x"})


def test_choice_field_sanitizes_remote_names():
    """字段名必须合法：远端参数名可能带 ``-``/``.``。"""
    from app.services.secrets.locator import choice_field

    assert choice_field("api_key") == "secret_choice_api_key"
    assert choice_field("a-b") == "secret_choice_a_b"
    assert choice_field("a.b") == "secret_choice_a_b"


def test_parse_choice_rejects_junk():
    from app.services.secrets.locator import parse_choice

    assert parse_choice("c3") == 3
    assert parse_choice(" C3 ") == 3
    assert parse_choice("c0") is None
    assert parse_choice("3") is None
    assert parse_choice("") is None
    assert parse_choice(None) is None


def test_catalog_sort_key_is_total_order():
    """排序必须是全序：两边重建才可能得到同一套编号。"""

    class R:
        def __init__(self, key, id):
            self.secret_key, self.id = key, id

    from app.services.secrets.locator import catalog_sort_key

    rows = [R("B", "2"), R("A", "9"), R("A", "1")]
    assert [(r.secret_key, r.id) for r in sorted(rows, key=catalog_sort_key)] == [
        ("A", "1"),
        ("A", "9"),
        ("B", "2"),
    ]
