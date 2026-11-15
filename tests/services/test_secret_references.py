"""``${secret:<key>[.<field>]}`` 引用解析与结构化值摘要的单元测试。

这两块都不碰数据库：一块是纯文本解析，一块是纯 JSON 归纳。它们恰恰是最容易
出错的地方（点号歧义、字段缺失被静默吞掉、密钥漏进摘要），所以单独测。
"""

from __future__ import annotations

import json

import pytest

from app.services.secrets.center import (
    VALUE_FORMAT_JSON,
    VALUE_FORMAT_TEXT,
    SecretValidationError,
    _normalize_secret_fields,
    _validate_value_format,
    _validate_value_template,
    build_value_summary,
    parse_structured_value,
    _value_meta,
)
from app.services.secrets.references import (
    ReferenceError,
    has_secret_reference,
    iter_references,
    pick_field,
    render_references,
    split_key_and_path,
)


class TestReferenceDetection:
    def test_plain_value_is_not_a_reference(self):
        assert has_secret_reference("ghp_abcdefghijklmn") is False
        assert has_secret_reference("") is False
        assert has_secret_reference(None) is False

    def test_reference_anywhere_in_text_is_detected(self):
        # 前缀拼接（Bearer …）必须也能识别：只认「整段是占位符」会把这格当成
        # 用户手填的值原样发出去。
        assert has_secret_reference("Bearer ${secret:mail.smtp.token}") is True
        assert has_secret_reference("${secret:a.b.host}") is True

    def test_iter_returns_expressions(self):
        text = "${secret:a.b.host}:${secret:a.b.port}"
        assert iter_references(text) == ["a.b.host", "a.b.port"]


class TestSplitKeyAndPath:
    def test_whole_key_has_empty_path(self):
        assert split_key_and_path("mail.smtp", "mail.smtp") == ("mail.smtp", ())

    def test_key_with_single_field(self):
        assert split_key_and_path("mail.smtp.host", "mail.smtp") == ("mail.smtp", ("host",))

    def test_key_with_nested_path(self):
        assert split_key_and_path("kafka.prod.sasl.user", "kafka.prod") == (
            "kafka.prod",
            ("sasl", "user"),
        )

    def test_key_containing_dots_is_not_re_split(self):
        # 关键回归：secret_key 自己允许点号。以真实 key 为准消歧，
        # 不能把 jira.prod.token 读成 jira.prod + token 字段。
        assert split_key_and_path("jira.prod.token.host", "jira.prod.token") == (
            "jira.prod.token",
            ("host",),
        )

    def test_mismatched_key_is_returned_as_is(self):
        key, path = split_key_and_path("another.key.host", "mail.smtp")
        assert (key, path) == ("another.key.host", ())

    def test_array_index_segment_is_accepted(self):
        assert split_key_and_path("db.hosts.0", "db") == ("db", ("hosts", "0"))

    def test_bad_syntax_is_rejected(self):
        with pytest.raises(ReferenceError) as exc:
            split_key_and_path("db.", "db")
        assert exc.value.kind == ReferenceError.BAD_SYNTAX

    def test_illegal_path_character_is_rejected(self):
        with pytest.raises(ReferenceError) as exc:
            split_key_and_path("db.some field", "db")
        assert exc.value.kind == ReferenceError.BAD_SYNTAX


class TestPickField:
    JSON_VALUE = '{"host": "smtp.exmail.qq.com", "port": 465.0, "tls": true, "note": null, "hosts": ["a", "b"]}'

    def test_empty_path_returns_plaintext(self):
        assert pick_field("anything", ()) == "anything"

    def test_picks_string_and_normalizes_integral_float(self):
        assert pick_field(self.JSON_VALUE, ("host",)) == "smtp.exmail.qq.com"
        # 端口写成 465.0 时要给出 465，不能把 465.0 塞进 URL
        assert pick_field(self.JSON_VALUE, ("port",)) == "465"

    def test_bool_and_null(self):
        assert pick_field(self.JSON_VALUE, ("tls",)) == "true"
        assert pick_field(self.JSON_VALUE, ("note",)) == ""

    def test_array_index(self):
        assert pick_field(self.JSON_VALUE, ("hosts", "1")) == "b"

    def test_nested_dict_is_serialized_compactly(self):
        value = '{"auth": {"user": "u", "pass": "p"}}'
        assert pick_field(value, ("auth",)) == '{"user":"u","pass":"p"}'

    def test_non_json_raises_instead_of_returning_whole_value(self):
        # 这条是安全要求：绝不能「解析不了就把整段明文给出去」。
        with pytest.raises(ReferenceError) as exc:
            pick_field("plain-token-value", ("host",))
        assert exc.value.kind == ReferenceError.NOT_JSON

    def test_missing_field_raises(self):
        with pytest.raises(ReferenceError) as exc:
            pick_field(self.JSON_VALUE, ("nope",))
        assert exc.value.kind == ReferenceError.FIELD_MISSING

    def test_index_out_of_range_raises(self):
        with pytest.raises(ReferenceError) as exc:
            pick_field(self.JSON_VALUE, ("hosts", "5"))
        assert exc.value.kind == ReferenceError.FIELD_MISSING

    def test_walking_through_scalar_raises(self):
        with pytest.raises(ReferenceError) as exc:
            pick_field(self.JSON_VALUE, ("host", "nested"))
        assert exc.value.kind == ReferenceError.FIELD_MISSING


class TestRenderReferences:
    def test_prefix_text_is_preserved(self):
        rendered = render_references(
            "Bearer ${secret:mail.smtp.token}",
            known_key="mail.smtp",
            plaintext='{"token": "abc123"}',
        )
        assert rendered == "Bearer abc123"

    def test_multiple_references(self):
        rendered = render_references(
            "${secret:db.host}:${secret:db.port}",
            known_key="db",
            plaintext='{"host": "10.0.0.1", "port": 5432}',
        )
        assert rendered == "10.0.0.1:5432"

    def test_whole_key_reference_returns_whole_value(self):
        rendered = render_references(
            "${secret:db}", known_key="db", plaintext='{"host": "h"}'
        )
        assert rendered == '{"host": "h"}'

    def test_key_mismatch_raises(self):
        with pytest.raises(ReferenceError) as exc:
            render_references(
                "${secret:other.host}", known_key="db", plaintext='{"host": "h"}'
            )
        assert exc.value.kind == ReferenceError.KEY_MISMATCH

    def test_plain_text_without_reference_is_untouched(self):
        assert render_references("plain", known_key="db", plaintext="x") == "plain"


class TestValueFormatValidation:
    def test_accepts_known_formats(self):
        assert _validate_value_format("text") == VALUE_FORMAT_TEXT
        assert _validate_value_format("JSON") == VALUE_FORMAT_JSON
        assert _validate_value_format(None) == VALUE_FORMAT_TEXT

    def test_rejects_unknown_format(self):
        with pytest.raises(SecretValidationError):
            _validate_value_format("yaml")

    def test_template_only_for_json(self):
        assert _validate_value_template("smtp", VALUE_FORMAT_JSON) == "smtp"
        assert _validate_value_template("smtp", VALUE_FORMAT_TEXT) is None
        assert _validate_value_template("", VALUE_FORMAT_JSON) is None

    def test_template_pattern(self):
        with pytest.raises(SecretValidationError):
            _validate_value_template("SMTP Server", VALUE_FORMAT_JSON)


class TestStructuredValueParsing:
    def test_object_is_accepted(self):
        assert parse_structured_value('{"a": 1}') == {"a": 1}

    @pytest.mark.parametrize("bad", ["[1,2]", '"str"', "465", "not-json", "{"])
    def test_non_object_is_rejected(self, bad):
        with pytest.raises(SecretValidationError):
            parse_structured_value(bad)


class TestSecretFields:
    def test_dedupes_and_keeps_order(self):
        assert _normalize_secret_fields(["password", "password", "token"]) == [
            "password",
            "token",
        ]

    def test_rejects_illegal_name(self):
        with pytest.raises(SecretValidationError):
            _normalize_secret_fields(["pass word"])

    def test_caps_count(self):
        with pytest.raises(SecretValidationError):
            _normalize_secret_fields([f"f{i}" for i in range(60)])


class TestBuildValueSummary:
    def test_uses_non_secret_fields_in_order(self):
        value = '{"host": "smtp.exmail.qq.com", "port": 465, "username": "alert", "password": "p"}'
        summary = build_value_summary(value, ["password"])
        assert summary == "smtp.exmail.qq.com · 465 · alert"

    def test_secret_field_never_appears(self):
        value = '{"password": "Sup3rSecret", "host": "h"}'
        summary = build_value_summary(value, ["password"])
        assert "Sup3rSecret" not in (summary or "")
        assert summary == "h"

    def test_only_secret_fields_yields_none(self):
        assert build_value_summary('{"password": "x"}', ["password"]) is None

    def test_nested_structures_are_skipped(self):
        value = '{"cert": {"pem": "x"}, "host": "h"}'
        assert build_value_summary(value, []) == "h"

    def test_secret_shaped_value_is_filtered_even_without_declaration(self):
        # 前端声明漏了 token 时，兜底也不能让它进摘要。
        value = '{"api_key": "sk-abcdefghijklmnopqrstuvwxyz012345", "host": "h"}'
        assert build_value_summary(value, []) == "h"

    def test_long_token_shape_is_filtered(self):
        value = '{"blob": "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6q7r8s9t0", "host": "h"}'
        assert build_value_summary(value, []) == "h"

    def test_non_json_returns_none(self):
        assert build_value_summary("plain-token", []) is None

    def test_at_most_three_parts(self):
        value = '{"a": "1", "b": "2", "c": "3", "d": "4"}'
        summary = build_value_summary(value, [])
        assert summary.count("·") == 2

    def test_length_is_capped(self):
        value = '{"a": "%s", "b": "%s"}' % ("x" * 60, "y" * 60)
        summary = build_value_summary(value, [])
        assert len(summary) <= 150


class TestValueMeta:
    def test_structured_credential_gets_summary_and_no_preview(self):
        preview, summary = _value_meta(
            '{"host": "h", "password": "p"}',
            secret_type="credential",
            value_format=VALUE_FORMAT_JSON,
            secret_fields=["password"],
        )
        # `{"host":` 这八位没有信息量，所以结构化值不再给 preview
        assert preview is None
        assert summary == "h"

    def test_structured_variable_also_gets_summary(self):
        preview, summary = _value_meta(
            '{"tenant": "nanzi", "endpoint": "https://api"}',
            secret_type="variable",
            value_format=VALUE_FORMAT_JSON,
            secret_fields=[],
        )
        assert preview is None
        assert summary == "nanzi · https://api"

    def test_plain_variable_keeps_eight_char_preview(self):
        preview, summary = _value_meta(
            "tenant-001-abcdefg",
            secret_type="variable",
            value_format=VALUE_FORMAT_TEXT,
            secret_fields=[],
        )
        assert preview == "tenant-0"
        assert summary is None

    def test_plain_credential_gets_nothing(self):
        preview, summary = _value_meta(
            "ghp_secret", secret_type="credential", value_format=VALUE_FORMAT_TEXT, secret_fields=[]
        )
        assert preview is None and summary is None

    def test_invalid_json_is_rejected_at_write_time(self):
        with pytest.raises(SecretValidationError):
            _value_meta(
                "{not json",
                secret_type="credential",
                value_format=VALUE_FORMAT_JSON,
                secret_fields=[],
            )


class TestSummarySkipsEnums:
    def test_short_enum_is_skipped_in_favour_of_identifying_field(self):
        value = (
            '{"host": "smtp.exmail.qq.com", "port": 465,'
            ' "security": "ssl", "username": "alert-bot@nanzi.ai"}'
        )
        # ssl 占一格但不说明任何事，把它挤掉，让用户名进来
        assert build_value_summary(value, []) == "smtp.exmail.qq.com · 465 · alert-bot@nanzi.ai"

    def test_short_value_with_digits_is_kept(self):
        value = '{"db": "db1", "host": "h"}'
        assert build_value_summary(value, []) == "db1 · h"


class TestPathCaseSensitivity:
    """JSON 键名区分大小写，引用解析不能把它抹平。"""

    def test_camel_case_field_is_looked_up_as_written(self):
        assert split_key_and_path("db.accessToken", "db") == ("db", ("accessToken",))

    def test_camel_case_field_is_picked(self):
        value = '{"accessToken": "at-123", "clientId": "cid"}'
        assert pick_field(value, ("accessToken",)) == "at-123"
        assert pick_field(value, ("clientId",)) == "cid"

    def test_lowered_field_would_miss_so_we_keep_case(self):
        # 反证：如果实现把路径小写了，这一条就会抛 FIELD_MISSING
        value = '{"accessToken": "at-123"}'
        with pytest.raises(ReferenceError):
            pick_field(value, ("accesstoken",))

    def test_key_matching_stays_case_insensitive(self):
        assert split_key_and_path("MAIL.SMTP.host", "mail.smtp") == ("mail.smtp", ("host",))


class TestEmptySegments:
    @pytest.mark.parametrize("expression", ["db..host", "db.host.", "db.", ".db"])
    def test_empty_segment_is_a_syntax_error(self, expression):
        with pytest.raises(ReferenceError) as exc:
            split_key_and_path(expression, "db")
        assert exc.value.kind == ReferenceError.BAD_SYNTAX


class TestCollectForRedaction:
    def test_render_collects_each_picked_value(self):
        collected: list[str] = []
        rendered = render_references(
            "Bearer ${secret:db.token}",
            known_key="db",
            plaintext='{"token": "Sup3rSecret"}',
            collect=collected,
        )
        assert rendered == "Bearer Sup3rSecret"
        # 单个字段值也要能被脱敏池看见：远端回显的通常是这个值本身
        assert collected == ["Sup3rSecret"]


class TestSummaryServerSideFallback:
    """前端没声明敏感字段时，后端也必须兜住。"""

    def test_password_is_excluded_without_declaration(self):
        value = '{"host": "db.internal", "password": "MyPass123"}'
        assert build_value_summary(value, []) == "db.internal"

    def test_camel_case_secret_key_is_excluded(self):
        value = '{"host": "h", "apiKey": "plainvalue"}'
        assert build_value_summary(value, []) == "h"

    def test_credentialed_url_is_skipped_entirely(self):
        value = '{"url": "http://user:Secret123@proxy:3128", "port": 3128}'
        summary = build_value_summary(value, [])
        assert "Secret123" not in (summary or "")
        assert summary == "3128"

    def test_ordinary_username_still_shows(self):
        # 兜底不能过头：邮箱用户名不是密码，留在摘要里才有辨识度
        value = '{"username": "alert-bot@nanzi.ai", "host": "smtp"}'
        assert "alert-bot@nanzi.ai" in (build_value_summary(value, []) or "")

    @pytest.mark.parametrize(
        "key",
        ["auth_tokens", "credentials", "password_v2", "api_key_live", "clientSecret", "pw"],
    )
    def test_sensitive_words_are_matched_by_word_not_suffix(self, key):
        """带复数/版本号/驼峰的写法不能绕过（旧实现用 endswith，全会漏）。"""
        value = json.dumps({"host": "db.internal", key: "Sensitive123"})
        summary = build_value_summary(value, [])
        assert summary == "db.internal"

    @pytest.mark.parametrize("key", ["username", "host", "from_name", "no_proxy", "keyboard"])
    def test_ordinary_keys_are_not_over_blocked(self, key):
        """按词匹配不能过头：username / keyboard 这类不是密码。"""
        value = json.dumps({key: "visible-value", "port": 465})
        summary = build_value_summary(value, []) or ""
        assert "visible-value" in summary

    def test_raw_slash_in_url_password_still_detected(self):
        """未编码的 ``/`` 会截断 netloc，正则和 urlsplit 都会漏——按冒号+@ 兜一层。"""
        value = json.dumps({"url": "http://user:ab/cd@proxy:3128", "port": 3128})
        assert build_value_summary(value, []) == "3128"

    def test_plain_url_is_still_shown(self):
        value = json.dumps({"url": "http://proxy.nanzi.ai:3128", "port": 3128})
        assert "proxy.nanzi.ai" in (build_value_summary(value, []) or "")

    def test_name_only_keys_are_not_treated_as_secrets(self):
        """``key_name`` 是请求头名字，不是密钥——过滤掉它摘要就全空了。"""
        value = json.dumps({"key_name": "X-API-Key", "key_value": "nz_live_7Kx"})
        assert build_value_summary(value, ["key_value"]) == "X-API-Key"

    def test_credential_material_ids_are_still_filtered(self):
        # access_key_id / secret_id 是凭据材料的一半，仍然不能进摘要
        value = json.dumps({"access_key_id": "AKIA123", "endpoint": "oss.example.com"})
        assert build_value_summary(value, []) == "oss.example.com"
