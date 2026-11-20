"""文件分享的纯函数契约：路径归一、工作区拆分、内容指纹、越界拒绝。

这些函数是整套「不复制文件也能安全分享」的地基，任何一条退化都会直接变成越权读文件，
因此全部按单元测试覆盖，不依赖数据库与磁盘上的真实工作区。
"""

from pathlib import Path

import pytest

from app.services import file_share_service as svc

pytestmark = pytest.mark.no_infrastructure


# ---------------------------------------------------------------------------
# 路径归一：拒绝一切可能跑出用户目录的写法
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("docs/报表.xlsx", "docs/报表.xlsx"),
        ("./docs//报表.xlsx", "docs/报表.xlsx"),
        ("docs\\报表.xlsx", "docs/报表.xlsx"),
        ("docs/./sub/报表.xlsx", "docs/sub/报表.xlsx"),
    ],
)
def test_normalize_rel_path_accepts_clean_relative(raw, expected):
    assert svc.normalize_rel_path(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "/etc/passwd",
        "../secret.txt",
        "docs/../../secret.txt",
        "docs/C:/windows/x.txt",
    ],
)
def test_normalize_rel_path_rejects_escape(raw):
    with pytest.raises(svc.FileShareTargetError):
        svc.normalize_rel_path(raw)


def test_split_workspace_path_requires_user_directory():
    assert svc.split_workspace_path("admin__1/docs/x.xlsx") == ("admin__1", "docs/x.xlsx")
    # 只有用户目录、没有文件 → 拒绝（分享的最小单位是文件）
    with pytest.raises(svc.FileShareTargetError):
        svc.split_workspace_path("admin__1")


# ---------------------------------------------------------------------------
# 越界复核：分享记录可信，但磁盘上的目录可能被换成符号链接
# ---------------------------------------------------------------------------


def test_resolve_share_abs_path_blocks_symlink_escape(tmp_path: Path):
    root = tmp_path / "ws"
    user_root = root / "admin__1"
    user_root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("top secret", encoding="utf-8")

    # 在用户目录里放一个指向外部的链接：仅靠字符串前缀校验是拦不住的
    link = user_root / "escape.txt"
    link.symlink_to(secret)

    resolved = svc.resolve_share_abs_path(
        workspace_root=str(root),
        workspace_key="admin__1",
        rel_path="escape.txt",
    )
    assert resolved is None, "符号链接指向用户目录之外必须被拒绝"


def test_resolve_share_abs_path_allows_normal_file(tmp_path: Path):
    root = tmp_path / "ws"
    (root / "admin__1" / "docs").mkdir(parents=True)
    target = root / "admin__1" / "docs" / "ok.txt"
    target.write_text("hello", encoding="utf-8")

    resolved = svc.resolve_share_abs_path(
        workspace_root=str(root),
        workspace_key="admin__1",
        rel_path="docs/ok.txt",
    )
    assert resolved == str(target.resolve())


# ---------------------------------------------------------------------------
# 内容指纹：这就是「冻结」的全部依据
# ---------------------------------------------------------------------------


def test_compute_sha256_changes_when_content_changes(tmp_path: Path):
    target = tmp_path / "a.txt"
    target.write_text("v1", encoding="utf-8")
    first = svc.compute_sha256(str(target))
    target.write_text("v2", encoding="utf-8")
    second = svc.compute_sha256(str(target))
    assert first != second
    assert len(first) == 64


def test_compute_sha256_is_stable_for_same_content(tmp_path: Path):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("same", encoding="utf-8")
    b.write_text("same", encoding="utf-8")
    assert svc.compute_sha256(str(a)) == svc.compute_sha256(str(b))


# ---------------------------------------------------------------------------
# 媒体类型与分享 ID
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, expect_prefix",
    [
        ("报表.xlsx", "application/vnd.openxmlformats"),
        ("report.pdf", "application/pdf"),
        ("note.md", "text/plain"),
        ("unknown.zzz", "application/octet-stream"),
    ],
)
def test_guess_mime_type(name, expect_prefix):
    assert svc.guess_mime_type(name, Path(name).suffix.lower()).startswith(expect_prefix)


def test_generate_share_id_is_unique_and_url_safe():
    ids = {svc.generate_share_id() for _ in range(50)}
    assert len(ids) == 50
    for value in ids:
        assert all(ch.isalnum() or ch in "-_" for ch in value)


# ---------------------------------------------------------------------------
# 呈现方式分流：后端说了算，前端不复述
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "file_ext, mime, expected",
    [
        (".html", "text/html", "html"),
        (".htm", "", "html"),
        ("", "text/html; charset=utf-8", "html"),
        (".svg", "image/svg+xml", "svg"),
        (".png", "image/png", "image"),
        (".jpeg", "", "image"),
        (".pdf", "application/pdf", "pdf"),
        (".md", "text/plain; charset=utf-8", "text"),
        (".csv", "text/csv", "text"),
        (".py", "", "text"),
        (".xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "download"),
        (".zip", "application/zip", "download"),
        (".docx", "", "download"),
    ],
)
def test_classify_preview_kind(file_ext, mime, expected):
    assert svc.classify_preview_kind(file_ext, mime) == expected


def test_svg_is_not_treated_as_text():
    """svg 能内嵌脚本：必须走图片通道（<img> 不执行脚本），不能当文本渲染。"""
    assert svc.classify_preview_kind(".svg", "image/svg+xml") == "svg"
    assert ".svg" not in svc.TEXT_EXTENSIONS


def test_html_sandbox_csp_never_allows_same_origin():
    """HTML 分享的全部安全性都系在这条上。

    ``allow-scripts`` 与 ``allow-same-origin`` 同时出现等于没有沙箱——文档会拿到应用同源，
    里面的脚本能读浏览者的登录凭证。这条断言就是防它被「顺手加上」。
    """
    csp = svc.HTML_SANDBOX_CSP
    assert "sandbox" in csp
    assert "allow-scripts" in csp
    assert "allow-same-origin" not in csp


def test_svg_sandbox_csp_forbids_scripts():
    assert "sandbox" in svc.SVG_SANDBOX_CSP
    assert "allow-scripts" not in svc.SVG_SANDBOX_CSP


def test_preview_text_has_size_cap():
    """几十 MB 的日志塞进 DOM 只会把访客浏览器卡死，必须有上限。"""
    assert 0 < svc.MAX_PREVIEW_TEXT_BYTES <= 8 * 1024 * 1024


# ---------------------------------------------------------------------------
# 预览响应头：inline + nosniff，html/svg 额外带 CSP sandbox
# ---------------------------------------------------------------------------


def test_preview_headers_mark_html_as_sandboxed_inline():
    from app.api.v1.endpoints.file_share import _preview_headers

    headers = _preview_headers({"preview_kind": "html", "file_name": "报告.html"})
    assert headers["Content-Disposition"].startswith("inline")
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Content-Security-Policy"] == svc.HTML_SANDBOX_CSP
    # 中文文件名走 RFC 5987，否则浏览器拿到乱码或直接失败
    assert "filename*=UTF-8''" in headers["Content-Disposition"]
    assert "%E6%8A%A5%E5%91%8A" in headers["Content-Disposition"]


def test_preview_headers_for_svg_use_scriptless_csp():
    from app.api.v1.endpoints.file_share import _preview_headers

    headers = _preview_headers({"preview_kind": "svg", "file_name": "图.svg"})
    assert headers["Content-Security-Policy"] == svc.SVG_SANDBOX_CSP
    assert "allow-scripts" not in headers["Content-Security-Policy"]


def test_preview_headers_for_plain_images_carry_no_csp():
    """图片/PDF 本身不能执行同源脚本，加 sandbox 反而可能影响浏览器内置 PDF 阅读器。"""
    from app.api.v1.endpoints.file_share import _preview_headers

    for kind in ("image", "pdf"):
        headers = _preview_headers({"preview_kind": kind, "file_name": "x"})
        assert "Content-Security-Policy" not in headers
        assert headers["X-Content-Type-Options"] == "nosniff"
