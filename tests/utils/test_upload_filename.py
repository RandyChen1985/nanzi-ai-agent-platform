import os
import re
import uuid

import pytest

from app.utils import fs_access
from app.utils.fs_access import (
    build_upload_storage_name,
    open_upload_storage_file,
    sanitize_upload_storage_name,
)

pytestmark = pytest.mark.no_infrastructure


def test_build_upload_storage_name_keeps_chinese_and_uses_short_suffix():
    name = build_upload_storage_name("销售日报 2025.xlsx", suffix="a1B2")

    assert name == "销售日报 2025_a1B2.xlsx"


def test_build_upload_storage_name_removes_path_separators_without_losing_name():
    name = build_upload_storage_name("../报表\\月度.xlsx", suffix="c3D4")

    assert name == ".._报表_月度_c3D4.xlsx"
    assert "/" not in name
    assert "\\" not in name


def test_build_upload_storage_name_generates_four_hex_characters_by_default():
    name = build_upload_storage_name("report.csv")

    assert re.fullmatch(r"report_[0-9a-f]{4}\.csv", name)


def test_sanitize_upload_storage_name_keeps_original_name_verbatim():
    """默认落盘名就是清洗后的原始文件名，不带任何后缀。"""
    assert sanitize_upload_storage_name("销售日报 2025.xlsx") == "销售日报 2025.xlsx"
    assert sanitize_upload_storage_name("季度报告.pdf") == "季度报告.pdf"


def test_sanitize_upload_storage_name_rewrites_path_separators_and_control_chars():
    name = sanitize_upload_storage_name("../报表\\月度\x00.xlsx")

    assert name == ".._报表_月度_.xlsx"
    assert os.path.basename(name) == name


def test_sanitize_upload_storage_name_falls_back_to_upload_when_empty():
    assert sanitize_upload_storage_name(None) == "upload"
    assert sanitize_upload_storage_name("   ") == "upload"


def test_open_upload_storage_file_keeps_the_original_name_when_free(tmp_path):
    path, handle = open_upload_storage_file(str(tmp_path), "季度报告.pdf")
    with handle:
        handle.write(b"first")

    assert path == str(tmp_path / "季度报告.pdf")
    assert (tmp_path / "季度报告.pdf").read_bytes() == b"first"
    # 没有多余的混淆副本
    assert sorted(os.listdir(tmp_path)) == ["季度报告.pdf"]


def test_open_upload_storage_file_only_adds_suffix_on_collision(tmp_path, monkeypatch):
    suffixes = iter(
        (
            uuid.UUID("a1b20000-0000-0000-0000-000000000000"),
            uuid.UUID("c3d40000-0000-0000-0000-000000000000"),
        )
    )
    monkeypatch.setattr(fs_access.uuid, "uuid4", lambda: next(suffixes))

    first_path, first_handle = open_upload_storage_file(str(tmp_path), "report.csv")
    with first_handle:
        first_handle.write(b"first")

    second_path, second_handle = open_upload_storage_file(str(tmp_path), "report.csv")
    with second_handle:
        second_handle.write(b"second")

    third_path, third_handle = open_upload_storage_file(str(tmp_path), "report.csv")
    with third_handle:
        third_handle.write(b"third")

    assert first_path == str(tmp_path / "report.csv")
    assert second_path == str(tmp_path / "report_a1b2.csv")
    assert third_path == str(tmp_path / "report_c3d4.csv")
    # 原名文件内容不被后来的同名上传覆盖
    assert (tmp_path / "report.csv").read_bytes() == b"first"
    assert (tmp_path / "report_a1b2.csv").read_bytes() == b"second"
    assert (tmp_path / "report_c3d4.csv").read_bytes() == b"third"


def test_open_upload_storage_file_treats_directory_occupying_the_name_as_collision(
    tmp_path, monkeypatch
):
    """同名目录占位时应改名写入，而不是把 IsADirectoryError 抛成 500。"""
    (tmp_path / "report.csv").mkdir()
    monkeypatch.setattr(
        fs_access.uuid, "uuid4", lambda: uuid.UUID("a1b20000-0000-0000-0000-000000000000")
    )

    path, handle = open_upload_storage_file(str(tmp_path), "report.csv")
    with handle:
        handle.write(b"data")

    assert path == str(tmp_path / "report_a1b2.csv")
    assert (tmp_path / "report_a1b2.csv").read_bytes() == b"data"


def test_open_upload_storage_file_sanitizes_traversal_before_writing(tmp_path):
    path, handle = open_upload_storage_file(str(tmp_path), "../报表明细.csv")
    with handle:
        handle.write(b"data")

    assert path == str(tmp_path / ".._报表明细.csv")
    assert sorted(os.listdir(tmp_path)) == [".._报表明细.csv"]
