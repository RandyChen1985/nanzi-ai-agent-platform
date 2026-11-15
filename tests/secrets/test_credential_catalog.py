"""`list_my_credentials`：编号稳定性、只有元数据、注册到位。

它是「模型能不能知道自己有哪些凭据」的唯一入口，所以三条都要钉住：
编号必须可重建（模型回传 c3 要能找回同一条）、出参不能有值、工具必须真的注册了。
"""
from pathlib import Path

import pytest

from app.services.ai.tools.credential_catalog_tools import _catalog_sort_key, ref_of

ROOT = Path(__file__).parents[2]
SRC = (ROOT / "app/services/ai/tools/credential_catalog_tools.py").read_text(encoding="utf-8")


class _Row:
    def __init__(self, secret_key, id):
        self.secret_key = secret_key
        self.id = id


def test_ref_is_one_based_index():
    assert ref_of(1) == "c1"
    assert ref_of(3) == "c3"


def test_order_is_reproducible_and_ref_points_at_same_row():
    """编号 = 排序后的序号。调用方用同一排序就能重建，模型回传 c3 才找得回同一条。"""
    rows = [_Row("CC", "3"), _Row("A", "1"), _Row("B", "2")]
    first = sorted(rows, key=_catalog_sort_key)
    second = sorted(list(reversed(rows)), key=_catalog_sort_key)
    assert [r.secret_key for r in first] == ["A", "B", "CC"]
    assert [r.secret_key for r in second] == ["A", "B", "CC"]
    # CC 在排序后的第 3 位 → c3
    assert ref_of([r.secret_key for r in first].index("CC") + 1) == "c3"


def test_payload_never_contains_value_or_internal_ids():
    """元数据可以给，值、内部 id、归属人都不行。"""
    assert "value_encrypted" not in SRC
    assert "previous_value" not in SRC
    assert "secret_id" not in SRC
    assert "owner_scope" not in SRC
    assert "owner_id" not in SRC
    # 明确返回的字段
    for key in (
        "ref",
        "secret_key",
        "name",
        "remark",
        "category",
        "status",
        "value_format",
        "value_template",
        "value_summary",
    ):
        assert f'"{key}"' in SRC


def test_tool_is_registered_and_default_available():
    """没注册等于没做：模型看不到这个工具。"""
    from app.services.ai.tools.registry import ToolRegistry

    assert "list_my_credentials" in ToolRegistry._registry
    implicit = ToolRegistry.get_system_implicit_tools()
    assert any("list_my_credentials" in str(getattr(t, "name", t)) for t in implicit)


def test_catalog_can_be_disabled_by_platform_switch():
    """默认开，但要留有平台关掉的口子（合规严格的租户）。"""
    assert "secret_policy_allow_catalog" in SRC
    assert "return True" in SRC  # 读不到配置时默认开


pytestmark = pytest.mark.no_infrastructure


class TestValueShapeIsVisibleToTheModel:
    """模型要分得清「一个 token」和「一台服务器的账号密码」。

    缺了形态信息时，模型对单值凭据也会建议「把 password 字段映射到这个参数」——
    用户照做只会得到一个填不出来的下拉框。

    这三项都是 ``sys_secrets`` 表上已有的列，读出来不解密任何值，所以可以放心
    放进清单出参（列表每页最多 50 条，逐条解密会把「列清单」变成重操作）。
    """

    def test_shape_fields_are_returned(self):
        for key in ("value_format", "value_template", "value_summary"):
            assert f'"{key}": ' in SRC, key

    def test_reading_them_does_not_decrypt(self):
        # 形态与摘要都来自表列；一旦这里出现解密调用，清单接口就会随条数线性变慢
        assert "decrypt" not in SRC
        assert "describe_value_fields" not in SRC

    def test_docstring_tells_the_model_what_they_mean(self):
        assert "``text`` 是单值" in SRC
        assert "``json`` 是结构化" in SRC
        # 模板名不等于字段清单：字段名后端没有定义，让模型别猜
        assert "不要凭模板名猜字段" in SRC
