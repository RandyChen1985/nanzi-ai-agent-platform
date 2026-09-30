"""锁定 known_failures 基线机制的行为。

基线机制把"长期存在的失败"与"本次改动引入的回归"区分开：命中清单的用例被标记
为 xfail，不阻塞全仓运行；任何不在清单中的失败都会正常报红。
"""

from pathlib import Path

import pytest

from known_failures import (
    CATEGORIES,
    load_known_failures,
    mark_known_failures,
    missing_from_collection,
)

pytestmark = pytest.mark.no_infrastructure


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "known_failures.txt"
    p.write_text(text, encoding="utf-8")
    return p


def test_loads_nodeid_and_reason(tmp_path):
    p = _write(tmp_path, "tests/a.py::test_x  # A-断言过时: 截断文案改过了\n")
    assert load_known_failures(p) == {"tests/a.py::test_x": "A-断言过时: 截断文案改过了"}


def test_ignores_blank_lines_and_comments(tmp_path):
    p = _write(tmp_path, "# 标题\n\n\ntests/a.py::test_x  # E-疑似真实缺陷: 待查\n")
    assert list(load_known_failures(p)) == ["tests/a.py::test_x"]


def test_entry_without_reason_is_rejected(tmp_path):
    p = _write(tmp_path, "tests/a.py::test_x\n")
    with pytest.raises(ValueError, match="缺少原因"):
        load_known_failures(p)


def test_reason_without_valid_category_is_rejected(tmp_path):
    p = _write(tmp_path, "tests/a.py::test_x  # 随手写点什么\n")
    with pytest.raises(ValueError, match="分类前缀"):
        load_known_failures(p)


def test_duplicate_nodeid_is_rejected(tmp_path):
    p = _write(
        tmp_path,
        "tests/a.py::test_x  # A-断言过时: a\ntests/a.py::test_x  # A-断言过时: b\n",
    )
    with pytest.raises(ValueError, match="重复"):
        load_known_failures(p)


def test_nodeid_containing_whitespace_is_rejected(tmp_path):
    """nodeid 不含空白；含空白说明生成/手工编辑时混入了 pytest 输出的告警文本。"""
    p = _write(
        tmp_path,
        "tests/a.py::test_x   ⚠️  [MySQL 提示] Exception ignored  # B-依赖升级适配: x\n",
    )
    with pytest.raises(ValueError, match="空白"):
        load_known_failures(p)


def test_entry_without_nodeid_separator_is_rejected(tmp_path):
    p = _write(tmp_path, "随便一行没有双冒号  # A-断言过时: x\n")
    with pytest.raises(ValueError, match="::"):
        load_known_failures(p)


def test_reports_entries_that_no_longer_exist():
    known = {
        "tests/gone.py::test_x": "A-断言过时: x",
        "tests/alive.py::test_y": "A-断言过时: y",
    }
    assert missing_from_collection(known, {"tests/alive.py::test_y"}) == [
        "tests/gone.py::test_x"
    ]


def test_real_baseline_file_is_valid_and_categorized():
    """仓库里真实的清单必须格式合法、每条都归了类。"""
    known = load_known_failures()
    assert known, "基线清单为空——若治理已完成，应当同时删除这套机制"
    for nodeid, reason in known.items():
        assert "::" in nodeid, f"清单项不像 nodeid：{nodeid}"
        assert any(reason.startswith(c) for c in CATEGORIES), f"缺少合法分类：{reason}"


class _FakeItem:
    """只实现 mark_known_failures 用到的那部分接口。"""

    def __init__(self, nodeid: str):
        self.nodeid = nodeid
        self.markers: list = []

    def add_marker(self, marker) -> None:
        self.markers.append(marker)


def test_marks_only_listed_items_and_returns_count():
    items = [_FakeItem("tests/a.py::test_x"), _FakeItem("tests/a.py::test_y")]
    known = {"tests/a.py::test_x": "A-断言过时: 文案改了"}

    marked = mark_known_failures(items, known, strict=False)

    assert marked == 1
    assert len(items[0].markers) == 1
    marker = items[0].markers[0]
    assert marker.name == "xfail"
    assert marker.kwargs["reason"] == "A-断言过时: 文案改了"
    assert marker.kwargs["strict"] is False
    assert items[1].markers == [], "未列入清单的用例不应被打标记"


def test_strict_flag_is_propagated_to_marker():
    items = [_FakeItem("tests/a.py::test_x")]

    mark_known_failures(items, {"tests/a.py::test_x": "E-疑似真实缺陷: 待查"}, strict=True)

    assert items[0].markers[0].kwargs["strict"] is True


def test_unlisted_failure_is_never_marked():
    """最关键的行为：不在清单里的失败必须保持红色，那才是回归信号。"""
    items = [_FakeItem("tests/new_regression.py::test_broken")]

    marked = mark_known_failures(
        items, {"tests/a.py::test_x": "A-断言过时: x"}, strict=False
    )

    assert marked == 0
    assert items[0].markers == []
