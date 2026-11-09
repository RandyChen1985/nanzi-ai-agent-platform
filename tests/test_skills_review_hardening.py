"""skills 全量 review 期间修掉的三处高危缺陷的回归测试。

每一项都对应一个真实可达的问题，而不是假设：

1. ``skill_id="."`` 曾能通过路径校验并让 ``DELETE /skills/{id}`` 把**整个公共技能库**
   删掉（校验只挡 ``..``，不挡 ``.``）。
2. 覆盖导入是「先 rmtree 旧技能、再解包」：坏包或缺 SKILL.md 会让新旧两份**同时消失**。
3. SKILL.md 带 UTF-8 BOM 时 frontmatter 匹配不到 → ``enabled`` 静默回落成 true，
   而 toggle 直接返回 False 被端点包成 500，于是该技能**永远禁不掉**。
"""
from __future__ import annotations

import os
import tempfile
import zipfile
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.api.portal.endpoints import skills
from app.utils.skill_metadata import (
    is_skill_enabled,
    parse_skill_frontmatter,
    toggle_skill_enabled_in_file,
)

pytestmark = pytest.mark.no_infrastructure


@pytest.fixture
def mock_skills_dir(tmp_path):
    test_settings = SimpleNamespace(SKILLS_DIR=str(tmp_path))
    with patch("app.api.portal.endpoints.skills.settings", test_settings):
        yield tmp_path


def _is_case_sensitive_fs() -> bool:
    """探测当前文件系统是否区分大小写。

    macOS 默认（APFS 不敏感）下 ``os.path.isfile("SKILL.md")`` 对磁盘上的 ``skill.md``
    也会返回 True，因此「只认精确大小写」的行为在那里无法被验证（生产是 Linux，可以）。
    """
    with tempfile.TemporaryDirectory() as tmp:
        probe = os.path.join(tmp, "CaseProbe")
        with open(probe, "w", encoding="utf-8") as handle:
            handle.write("x")
        return not os.path.exists(os.path.join(tmp, "caseprobe"))


def _zip_bytes(files: dict) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# 1. 路径校验必须是白名单，"." 不能指向技能库根
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "bad_id",
    [".", "..", "...", "中文技能", "foo.bar", ".hidden", "a" * 129, "with space", "a/b"],
)
def test_skill_id_whitelist_rejects_non_platform_ids(mock_skills_dir, bad_id):
    """白名单之外的 ID 一律 400。

    重点是 ``"."``：``os.path.join(SKILLS_DIR, ".")`` 就是技能库根目录本身，
    ``commonpath`` 自比也相等，旧的黑名单校验放行它之后 DELETE 会 rmtree 整库。
    """
    with pytest.raises(HTTPException) as exc:
        skills.validate_secure_skill_path(bad_id)
    assert exc.value.status_code == 400


def test_skills_root_survives_dot_skill_id(mock_skills_dir):
    """显式锁住后果：非法 ID 被拒后，库内既有技能必须完好。"""
    victim = mock_skills_dir / "real-skill"
    victim.mkdir()
    (victim / "SKILL.md").write_text("# 真技能", encoding="utf-8")

    with pytest.raises(HTTPException):
        skills.validate_secure_skill_path(".")

    assert victim.is_dir()
    assert (victim / "SKILL.md").read_text(encoding="utf-8") == "# 真技能"


def test_reserved_skill_ids_are_rejected(mock_skills_dir):
    """与 /skills 下更早注册的字面路由同名会被遮蔽，必须拒绝。"""
    for reserved in ("personal", "stats", "bindings", "publication-requests", "import"):
        with pytest.raises(HTTPException) as exc:
            skills.validate_secure_skill_path(reserved)
        assert exc.value.status_code == 400


def test_valid_skill_id_still_passes(mock_skills_dir):
    """白名单不能误伤正常 ID。"""
    resolved = skills.validate_secure_skill_path("my-skill_2")
    assert resolved == str(mock_skills_dir / "my-skill_2")


# ---------------------------------------------------------------------------
# 2. 导入必须是原子的：失败不得销毁原技能
# ---------------------------------------------------------------------------
def test_failed_import_keeps_existing_skill(mock_skills_dir):
    """坏包（缺 SKILL.md）覆盖导入后，原技能与它的脚本必须原样还在。"""
    skill_dir = mock_skills_dir / "atomic-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# 原版", encoding="utf-8")
    (skill_dir / "keep.py").write_text("print('keep')", encoding="utf-8")

    with pytest.raises(HTTPException) as exc:
        skills.atomic_import_skill_package(
            _zip_bytes({"readme.txt": "这里没有 SKILL.md"}), "bad.zip", str(skill_dir)
        )

    assert exc.value.status_code == 400
    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == "# 原版"
    assert (skill_dir / "keep.py").is_file()
    # staged 临时目录不留残留
    assert not list(mock_skills_dir.glob(".atomic-skill-import-*"))


def test_successful_import_replaces_atomically(mock_skills_dir):
    """正常导入仍然生效，并且把外层单目录平铺上来。"""
    skill_dir = mock_skills_dir / "new-skill"
    skills.atomic_import_skill_package(
        _zip_bytes({"pkg/SKILL.md": "# 新版", "pkg/run.py": "print(1)"}),
        "pkg.zip",
        str(skill_dir),
    )
    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == "# 新版"
    assert (skill_dir / "run.py").is_file()
    assert not list(mock_skills_dir.glob(".new-skill-import-*"))


@pytest.mark.skipif(
    not _is_case_sensitive_fs(),
    reason="文件系统不区分大小写（macOS 默认），无法区分 SKILL.md 与 skill.md",
)
def test_import_requires_exact_skill_md_case(mock_skills_dir):
    """小写 skill.md 必须被拒：所有消费方只认 SKILL.md，放行会造出幽灵技能。"""
    with pytest.raises(HTTPException) as exc:
        skills.atomic_import_skill_package(
            _zip_bytes({"skill.md": "# 小写"}), "lower.zip", str(mock_skills_dir / "ghost")
        )
    assert exc.value.status_code == 400
    assert not (mock_skills_dir / "ghost").exists()


# ---------------------------------------------------------------------------
# 3. UTF-8 BOM 不得让 enabled 判定与 toggle 失手
# ---------------------------------------------------------------------------
def test_frontmatter_parser_tolerates_utf8_bom(tmp_path):
    md = tmp_path / "SKILL.md"
    md.write_text("\ufeff---\nname: bom-skill\nenabled: false\n---\n\n正文\n", encoding="utf-8")

    meta = parse_skill_frontmatter("bom-skill", str(md))
    assert meta["name"] == "bom-skill"
    assert is_skill_enabled(meta) is False


def test_toggle_enabled_tolerates_utf8_bom(tmp_path):
    """BOM 文件此前 toggle 返回 False → 端点 500，技能永远禁不掉。"""
    md = tmp_path / "SKILL.md"
    md.write_text("\ufeff---\nname: bom-skill\n---\n\n正文\n", encoding="utf-8")

    assert toggle_skill_enabled_in_file(str(md), False) is True
    assert is_skill_enabled(parse_skill_frontmatter("bom-skill", str(md))) is False

    assert toggle_skill_enabled_in_file(str(md), True) is True
    assert is_skill_enabled(parse_skill_frontmatter("bom-skill", str(md))) is True


def test_toggle_inserts_frontmatter_for_missing_frontmatter(tmp_path):
    """没有 frontmatter 的文件补一份最小 frontmatter 并保留正文，而不是返回失败。

    列表解析对这类纯 Markdown 技能缺省 ``enabled=true``；若 toggle 返回 False（端点 400），
    它们就永远禁不掉、也没有别的入口能改。因此旧契约「如实返回 False」已改为补 frontmatter。
    """
    md = tmp_path / "SKILL.md"
    md.write_text("没有任何 frontmatter 的正文", encoding="utf-8")
    assert toggle_skill_enabled_in_file(str(md), False) is True
    written = md.read_text(encoding="utf-8")
    assert written.startswith("---\nenabled: false\n---\n\n没有任何 frontmatter 的正文")
