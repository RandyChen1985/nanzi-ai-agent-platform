"""工作空间压缩包解压的纯函数行为。

安全边界是本模块的重点：zip slip、绝对路径、符号链接、压缩炸弹、失败回滚。
这些都必须用真实压缩包跑出来，源码字符串断言看不出来。
"""

from __future__ import annotations

import io
import tarfile
import zipfile

import pytest


pytestmark = pytest.mark.no_infrastructure


def _make_zip(path, members: dict[str, bytes]):
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


def _make_tar_gz(path, files: dict[str, bytes], *, symlinks: dict[str, str] | None = None):
    with tarfile.open(path, "w:gz") as tf:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        for name, link_target in (symlinks or {}).items():
            info = tarfile.TarInfo(name)
            info.type = tarfile.SYMTYPE
            info.linkname = link_target
            tf.addfile(info)
    return path


def test_supported_archive_suffix_matrix():
    from app.services.workspace_archive import is_supported_archive

    assert is_supported_archive("a.zip")
    assert is_supported_archive("a.tar")
    assert is_supported_archive("a.tar.gz")
    assert is_supported_archive("a.tgz")
    assert is_supported_archive("A.ZIP")  # 大小写不敏感：用户上传的大写后缀也要能解
    assert not is_supported_archive("a.rar")
    assert not is_supported_archive("a.7z")
    assert not is_supported_archive("a.txt")


def test_extract_zip_into_named_subdir(tmp_path):
    from app.services.workspace_archive import extract_archive

    archive = _make_zip(tmp_path / "pkg.zip", {"docs/a.md": b"hello", "b.txt": b"world"})
    target = tmp_path / "out"
    target.mkdir()

    result = extract_archive(str(archive), str(target), subdir="pkg")

    assert result.path == str(target / "pkg")
    assert (target / "pkg" / "docs" / "a.md").read_bytes() == b"hello"
    assert (target / "pkg" / "b.txt").read_bytes() == b"world"
    assert result.file_count == 2
    assert result.total_bytes == 10
    assert result.skipped == []


def test_extract_defaults_subdir_to_archive_stem(tmp_path):
    from app.services.workspace_archive import extract_archive

    archive = _make_tar_gz(tmp_path / "report.tar.gz", {"a.md": b"x"})
    # 注意 .tar.gz 的 stem 必须去掉两层后缀
    target = tmp_path / "out"
    target.mkdir()

    result = extract_archive(str(archive), str(target))

    assert result.path == str(target / "report")


def test_extract_into_target_when_subdir_blank(tmp_path):
    from app.services.workspace_archive import extract_archive

    archive = _make_zip(tmp_path / "pkg.zip", {"a.md": b"x"})
    target = tmp_path / "out"
    target.mkdir()

    result = extract_archive(str(archive), str(target), subdir="")

    assert result.path == str(target)
    assert (target / "a.md").read_bytes() == b"x"


def test_extract_rejects_path_traversal_member(tmp_path):
    from app.services.workspace_archive import ArchiveExtractError, extract_archive

    archive = _make_zip(tmp_path / "evil.zip", {"../evil.txt": b"pwned"})
    target = tmp_path / "out"
    target.mkdir()

    with pytest.raises(ArchiveExtractError) as exc:
        extract_archive(str(archive), str(target), subdir="evil")

    assert exc.value.status_code == 400
    assert not (tmp_path / "evil.txt").exists()
    assert not (target / "evil.txt").exists()


def test_extract_rejects_absolute_member_path(tmp_path):
    from app.services.workspace_archive import ArchiveExtractError, extract_archive

    archive = _make_zip(tmp_path / "abs.zip", {"/tmp/pwned.txt": b"x"})
    target = tmp_path / "out"
    target.mkdir()

    with pytest.raises(ArchiveExtractError) as exc:
        extract_archive(str(archive), str(target), subdir="abs")

    assert exc.value.status_code == 400
    assert not (target / "abs").exists()


def test_extract_tar_gz(tmp_path):
    from app.services.workspace_archive import extract_archive

    archive = _make_tar_gz(tmp_path / "bundle.tar.gz", {"dir/x.txt": b"tarball"})
    target = tmp_path / "out"
    target.mkdir()

    result = extract_archive(str(archive), str(target), subdir="bundle")

    assert (target / "bundle" / "dir" / "x.txt").read_bytes() == b"tarball"
    assert result.file_count == 1


def test_extract_rejects_unsupported_format(tmp_path):
    from app.services.workspace_archive import ArchiveExtractError, extract_archive

    archive = tmp_path / "a.rar"
    archive.write_bytes(b"Rar!")
    target = tmp_path / "out"
    target.mkdir()

    with pytest.raises(ArchiveExtractError) as exc:
        extract_archive(str(archive), str(target))

    assert exc.value.status_code == 400
    assert "rar" in str(exc.value).lower()


def test_extract_renames_when_target_subdir_exists(tmp_path):
    from app.services.workspace_archive import extract_archive

    archive = _make_zip(tmp_path / "pkg.zip", {"a.md": b"x"})
    target = tmp_path / "out"
    (target / "pkg").mkdir(parents=True)  # 已存在

    result = extract_archive(str(archive), str(target), subdir="pkg")

    assert result.path == str(target / "pkg_1")
    assert (target / "pkg_1" / "a.md").exists()


def test_extract_skips_existing_member_when_extracting_into_target(tmp_path):
    from app.services.workspace_archive import extract_archive

    archive = _make_zip(tmp_path / "pkg.zip", {"a.md": b"new"})
    target = tmp_path / "out"
    target.mkdir()
    (target / "a.md").write_text("old", encoding="utf-8")

    result = extract_archive(str(archive), str(target), subdir="")

    assert (target / "a.md").read_text(encoding="utf-8") == "old"
    assert result.skipped == [{"name": "a.md", "reason": "exists"}]


def test_extract_rolls_back_when_total_size_exceeded(tmp_path):
    from app.services.workspace_archive import ArchiveExtractError, extract_archive

    archive = _make_zip(tmp_path / "bomb.zip", {"big.bin": b"x" * 4096})
    target = tmp_path / "out"
    target.mkdir()

    with pytest.raises(ArchiveExtractError) as exc:
        extract_archive(str(archive), str(target), subdir="bomb", max_total_bytes=1024)

    assert exc.value.status_code == 413
    # 回滚：不留半成品目录
    assert not (target / "bomb").exists()


def test_extract_rolls_back_when_entry_count_exceeded(tmp_path):
    from app.services.workspace_archive import ArchiveExtractError, extract_archive

    archive = _make_zip(tmp_path / "many.zip", {f"f{i}.txt": b"x" for i in range(5)})
    target = tmp_path / "out"
    target.mkdir()

    with pytest.raises(ArchiveExtractError) as exc:
        extract_archive(str(archive), str(target), subdir="many", max_entries=2)

    assert exc.value.status_code == 413
    assert not (target / "many").exists()


def test_extract_skips_symlink_tar_member(tmp_path):
    from app.services.workspace_archive import extract_archive

    archive = _make_tar_gz(
        tmp_path / "links.tar.gz",
        {"ok.txt": b"fine"},
        symlinks={"evil-link": "/etc/passwd"},
    )
    target = tmp_path / "out"
    target.mkdir()

    result = extract_archive(str(archive), str(target), subdir="links")

    assert (target / "links" / "ok.txt").read_bytes() == b"fine"
    assert not (target / "links" / "evil-link").exists()
    assert result.skipped == [{"name": "evil-link", "reason": "not_regular_file"}]


def test_zip_member_name_falls_back_to_gbk_without_utf8_flag(tmp_path):
    from app.services.workspace_archive import _zip_member_filename

    info = zipfile.ZipInfo("placeholder")
    info.flag_bits = 0
    info.filename = "中文目录/说明.txt".encode("gbk").decode("cp437")

    assert _zip_member_filename(info) == "中文目录/说明.txt"


def test_zip_member_name_keeps_utf8_flag_names(tmp_path):
    from app.services.workspace_archive import _zip_member_filename

    info = zipfile.ZipInfo("中文.txt")
    info.flag_bits = 0x800

    assert _zip_member_filename(info) == "中文.txt"


def test_extract_zip_keeps_chinese_names(tmp_path):
    from app.services.workspace_archive import extract_archive

    archive = _make_zip(tmp_path / "cn.zip", {"中文.txt": b"hi"})
    target = tmp_path / "out"
    target.mkdir()

    extract_archive(str(archive), str(target), subdir="cn")

    assert (target / "cn" / "中文.txt").read_bytes() == b"hi"


# --- 路由层：错误映射与权限语义 -------------------------------------------------


@pytest.mark.asyncio
async def test_extract_route_returns_result_and_maps_archive_errors(tmp_path, monkeypatch):
    from fastapi import HTTPException

    from app.api.v1.endpoints import fs

    archive = _make_zip(tmp_path / "pkg.zip", {"a.md": b"hi"})
    out = tmp_path / "out"
    out.mkdir()

    monkeypatch.setattr(fs, "assert_path_allowed", lambda path, user: str(archive))
    monkeypatch.setattr(fs, "_resolve_writable_entry_path", lambda path, user: str(out))
    monkeypatch.setattr(fs, "is_fs_admin", lambda user: True)
    monkeypatch.setattr(
        "app.services.workspace_archive.extract_limits_from_settings",
        lambda: (1024 * 1024, 100),
    )

    body = fs.FileArchiveExtractRequest(
        archive_path=str(archive), target_dir=str(out), subdir="pkg"
    )
    response = await fs.extract_fs_archive(body, {"user_id": 1})

    assert response.data.file_count == 1
    assert (out / "pkg" / "a.md").read_bytes() == b"hi"

    # 不支持的格式：ArchiveExtractError → HTTPException(400)，而不是 500
    bad = tmp_path / "bad.rar"
    bad.write_bytes(b"Rar!")
    monkeypatch.setattr(fs, "assert_path_allowed", lambda path, user: str(bad))
    with pytest.raises(HTTPException) as exc:
        await fs.extract_fs_archive(
            fs.FileArchiveExtractRequest(archive_path=str(bad), target_dir=str(out)),
            {"user_id": 1},
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_extract_route_propagates_writable_target_denial(tmp_path, monkeypatch):
    from fastapi import HTTPException

    from app.api.v1.endpoints import fs

    archive = _make_zip(tmp_path / "pkg.zip", {"a.md": b"x"})
    monkeypatch.setattr(fs, "assert_path_allowed", lambda path, user: str(archive))

    def _deny(path, user):
        raise HTTPException(status_code=403, detail="仅允许写入本人 AI 工作目录内的文件。")

    monkeypatch.setattr(fs, "_resolve_writable_entry_path", _deny)

    with pytest.raises(HTTPException) as exc:
        await fs.extract_fs_archive(
            fs.FileArchiveExtractRequest(
                archive_path=str(archive), target_dir=str(tmp_path)
            ),
            {"user_id": 1},
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_extract_route_rejects_missing_archive(tmp_path, monkeypatch):
    from fastapi import HTTPException

    from app.api.v1.endpoints import fs

    missing = tmp_path / "nope.zip"
    monkeypatch.setattr(fs, "assert_path_allowed", lambda path, user: str(missing))

    with pytest.raises(HTTPException) as exc:
        await fs.extract_fs_archive(
            fs.FileArchiveExtractRequest(
                archive_path=str(missing), target_dir=str(tmp_path)
            ),
            {"user_id": 1},
        )
    assert exc.value.status_code == 404


def test_extract_refuses_to_write_through_symlinked_subdir(tmp_path):
    """目标目录里已存在的符号链接不能把解压结果引到包外。

    包里构造链接会被跳过，但用户可能早就在工作区里建过 `link -> 别处`；
    这时 `link/x.txt` 的字符串路径仍然「在最终目录内」，必须用 realpath 再校验。
    """
    from app.services.workspace_archive import ArchiveExtractError, extract_archive

    outside = tmp_path / "outside"
    outside.mkdir()
    target = tmp_path / "out"
    target.mkdir()
    (target / "link").symlink_to(outside, target_is_directory=True)

    archive = _make_zip(tmp_path / "pkg.zip", {"link/pwned.txt": b"x"})

    with pytest.raises(ArchiveExtractError):
        extract_archive(str(archive), str(target), subdir="")

    assert not (outside / "pwned.txt").exists()


def test_extract_endpoint_over_http_keeps_request_contract(tmp_path, monkeypatch):
    """真实 HTTP 层再走一遍：锁住前端用的 snake_case 请求字段与响应结构。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.v1.endpoints import fs
    from app.core.dependencies import require_api_key

    archive = _make_zip(tmp_path / "pkg.zip", {"a.md": b"hi"})
    out = tmp_path / "out"
    out.mkdir()

    app = FastAPI()
    app.include_router(fs.router, prefix="/api/v1/chat/fs")
    app.dependency_overrides[require_api_key] = lambda: {"user_id": 1}
    app.dependency_overrides[fs._prime_forbidden_skill_cache] = lambda: None

    monkeypatch.setattr(fs, "assert_path_allowed", lambda path, user: str(archive))
    monkeypatch.setattr(fs, "_resolve_writable_entry_path", lambda path, user: str(out))
    monkeypatch.setattr(fs, "is_fs_admin", lambda user: True)
    monkeypatch.setattr(
        "app.services.workspace_archive.extract_limits_from_settings",
        lambda: (1024 * 1024, 100),
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/chat/fs/extract",
            json={
                "archive_path": str(archive),
                "target_dir": str(out),
                "subdir": "pkg",
            },
        )

    assert response.status_code == 200, response.text
    payload = response.json()["data"]
    assert payload["file_count"] == 1
    assert payload["path"] == str(out / "pkg")
    assert payload["skipped"] == []
    assert (out / "pkg" / "a.md").read_bytes() == b"hi"
