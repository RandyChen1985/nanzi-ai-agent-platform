"""技能元数据解析与技能 ID 校验的口径收敛。

三处解析器、三份 ID 正则各自为政是这轮 review 的核心问题：

* ``parse_skill_metadata`` 曾是第三份 frontmatter 解析器（不剥 BOM），BOM 文件在列表页
  永远显示「启用」、在运行时却是禁用；
* 技能 ID 校验散落在端点、智能体工具、发布服务三处，口径不同就会造出「管理页看得见、
  运行时永远看不到」的幽灵技能（例如叫 ``stats``/``export`` 的技能永远打不开）。

这组用例把「一份实现、所有入口复用」钉死：create / rename / delete / 导入 / 上传 /
发布快照 / 智能体工具全部走同一个校验函数。
"""
from __future__ import annotations

import re
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException, UploadFile

from app.api.portal.endpoints import personal_skills, skills
from app.services import skill_publication_service as publication_service
from app.services import skill_rename_service as rename_service
from app.utils.skill_metadata import is_skill_enabled

pytestmark = pytest.mark.no_infrastructure


class _NoopSession:
    """rename/delete 链路只把它透传给 service，用例拦在 service 边界。"""

    def __init__(self):
        self.commits = 0

    async def execute(self, *_args, **_kwargs):
        raise AssertionError("不该真的查库")

    async def commit(self):
        self.commits += 1


@pytest.fixture
def mock_skills_dir(tmp_path):
    with patch(
        "app.api.portal.endpoints.skills.settings", SimpleNamespace(SKILLS_DIR=str(tmp_path))
    ):
        yield tmp_path


def _zip_bytes(files: dict) -> bytes:
    import zipfile

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# 1. 解析器收敛
# ---------------------------------------------------------------------------
def test_parse_skill_metadata_tolerates_bom_like_the_runtime_parser(tmp_path):
    """BOM 容忍必须与 skill_metadata 一致：否则列表页永远显示「启用」。"""
    md = tmp_path / "SKILL.md"
    md.write_text(
        "\ufeff---\nname: bom-skill\ndescription: 带 BOM\nenabled: false\n---\n\n正文\n",
        encoding="utf-8",
    )

    meta = skills.parse_skill_metadata("bom-skill", str(md))

    assert meta["name"] == "bom-skill"
    assert meta["description"] == "带 BOM"
    assert meta["enabled"] == "false"
    assert is_skill_enabled(meta) is False, "BOM 不能把 enabled: false 抹成启用"


def test_parse_skill_metadata_keeps_field_defaults(tmp_path):
    """返回字段与默认值语义不变：name 回落 ID、description 回落「暂无技能描述」。

    ``group`` 是技能分组加进列表字段后新增的一项：SKILL.md 里没有 ``group:`` 时返回
    ``None``（= 未分组），前端据此把它归进「未分组」页签。这条断言是逐字段全等，
    所以新增字段必须在这里显式列出，否则这里会静默漏掉字段变更。
    """
    missing = tmp_path / "SKILL.md"

    meta = skills.parse_skill_metadata("ghost", str(missing))

    assert meta == {
        "id": "ghost",
        "name": "ghost",
        "description": "暂无技能描述",
        "path": "data/skills/ghost",
        "enabled": "true",
        "group": None,
    }


def test_parse_skill_metadata_delegates_to_the_single_parser(tmp_path):
    """列表页解析结果必须与唯一解析器逐字段一致（不再有第二套口径）。"""
    md = tmp_path / "SKILL.md"
    md.write_text("---\nname: 名称\ndescription: 描述\nenabled: true\n---\n", encoding="utf-8")

    from app.utils.skill_metadata import parse_skill_frontmatter

    source = parse_skill_frontmatter("demo", str(md))
    meta = skills.parse_skill_metadata("demo", str(md))

    assert meta["name"] == source["name"]
    assert meta["description"] == source["description"]
    assert meta["enabled"] == source["enabled"]


def test_system_executive_tool_has_no_own_frontmatter_parser(tmp_path):
    """智能体工具里的那份解析器也必须收敛（曾是完全独立的第三份实现）。"""
    from app.services.ai.tools import system_executive_tools as tool_module

    md = tmp_path / "SKILL.md"
    md.write_text("\ufeff---\nname: 工具技能\ndescription: d\n---\n", encoding="utf-8")

    meta = tool_module._parse_skill_frontmatter("tool-skill", str(md))

    assert meta["name"] == "工具技能"
    assert meta["description"] == "d"


def test_system_executive_tools_has_no_private_skill_id_regex():
    """工具侧不再自带 ID 正则（收敛到 skill_rename_service.validate_skill_id）。"""
    import inspect

    from app.services.ai.tools import system_executive_tools as tool_module

    source = inspect.getsource(tool_module._validate_skill_id)
    assert "validate_skill_id" in source
    assert re.search(r"re\.match", source) is None


# ---------------------------------------------------------------------------
# 2. ID 校验收敛：create / rename / delete / 导入 / 上传
# ---------------------------------------------------------------------------
def test_create_skill_rejects_reserved_and_overlong_ids(mock_skills_dir):
    import asyncio

    for bad in ("stats", "export", "publications", "a" * 129):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(
                skills.create_skill(
                    skills.SkillCreateRequest(id=bad, name="x", description="y"),
                    user={"role": "admin"},
                )
            )
        assert exc.value.status_code == 400
    assert list(mock_skills_dir.iterdir()) == [], "非法 ID 不该在技能库里留下目录"


def test_rename_skill_rejects_reserved_new_id(mock_skills_dir):
    (mock_skills_dir / "old-skill").mkdir()
    (mock_skills_dir / "old-skill" / "SKILL.md").write_text("# old\n", encoding="utf-8")

    import asyncio

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            skills.rename_platform_skill(
                "old-skill",
                skills.SkillRenameRequest(new_skill_id="export"),
                user={"role": "admin"},
                session=_NoopSession(),
            )
        )
    assert exc.value.status_code == 400
    assert (mock_skills_dir / "old-skill" / "SKILL.md").is_file()


def test_delete_skill_rejects_reserved_id(mock_skills_dir):
    victim = mock_skills_dir / "real-skill"
    victim.mkdir()
    (victim / "SKILL.md").write_text("# real\n", encoding="utf-8")

    import asyncio

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            skills.delete_entire_skill("stats", user={"role": "admin"}, session=_NoopSession())
        )
    assert exc.value.status_code == 400
    assert victim.is_dir(), "非法 ID 被拒后技能库必须原样"


def test_import_and_upload_reuse_the_same_validator(mock_skills_dir):
    import asyncio

    upload = UploadFile(filename="stats.zip", file=BytesIO(_zip_bytes({"SKILL.md": "# x"})))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            skills.import_skill_package(file=upload, overwrite=False, user={"role": "admin"})
        )
    assert exc.value.status_code == 400

    upload = UploadFile(filename="pkg.zip", file=BytesIO(_zip_bytes({"SKILL.md": "# x"})))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            skills.upload_skill_archive(
                "export", folder="scripts", file=upload, user={"role": "admin"}
            )
        )
    assert exc.value.status_code == 400


def test_personal_skill_path_validator_enforces_length_and_reserved_words():
    root = "/tmp/personal-skills"
    user = {"user_id": 7, "user_name": "tester"}

    with patch(
        "app.api.portal.endpoints.personal_skills.get_user_personal_skills_dir",
        return_value=root,
    ):
        for bad in ("import", "publications", "a" * 129, "with space", "a/b", "."):
            with pytest.raises(HTTPException) as exc:
                personal_skills._validate_personal_skill_path(user, bad)
            assert exc.value.status_code == 400

        assert (
            personal_skills._validate_personal_skill_path(user, "my-skill_2")
            == f"{root}/my-skill_2"
        )


def test_publication_service_reuses_the_shared_validator():
    """发布快照/物化服务里的第三份正则也必须收敛。"""
    assert publication_service._validate_skill_id("ok-skill_1") is None
    for bad in ("export", "a" * 129, "中文", ""):
        with pytest.raises(ValueError):
            publication_service._validate_skill_id(bad)


def test_shared_validator_accepts_only_platform_ids():
    assert rename_service.validate_skill_id("  good-skill  ") == "good-skill"
    for bad in ("", "   ", "中文", "with space", "a.b", "a" * 129, "stats"):
        with pytest.raises(rename_service.SkillRenameError):
            rename_service.validate_skill_id(bad)


# ---------------------------------------------------------------------------
# 3. 智能体工具（create_skills）不再绕过校验
# ---------------------------------------------------------------------------
def _admin_context():
    from app.utils.context import current_user_info

    return current_user_info.set({"user_id": 1, "user_name": "admin", "role": "admin"})


def test_create_skills_tool_rejects_reserved_and_overlong_ids(tmp_path):
    from unittest.mock import PropertyMock

    from app.services.ai.tools.system_executive_tools import create_skills
    from app.utils.context import current_user_info

    token = _admin_context()
    try:
        with patch("app.core.config.Settings.SKILLS_DIR", new_callable=PropertyMock) as mocked:
            mocked.return_value = str(tmp_path)
            for bad in ("stats", "export", "a" * 129, "../escape"):
                result = create_skills.invoke(
                    {
                        "skill_id": bad,
                        "name": "n",
                        "description": "d",
                        "skill_md_content": "# x",
                        "scope": "global",
                    }
                )
                assert result.startswith("错误："), result
            assert list(tmp_path.iterdir()) == [], "非法 ID 不该在磁盘上留下任何东西"

            ok = create_skills.invoke(
                {
                    "skill_id": "fine-skill",
                    "name": "n",
                    "description": "d",
                    "skill_md_content": "# x",
                    "scope": "global",
                }
            )
            assert "创建成功" in ok
            assert (tmp_path / "fine-skill" / "SKILL.md").is_file()
    finally:
        current_user_info.reset(token)


def test_create_skills_tool_reports_reason_for_bad_id():
    from app.services.ai.tools.system_executive_tools import create_skills

    token = _admin_context()
    try:
        result = create_skills.invoke(
            {
                "skill_id": "../bad-path",
                "name": "n",
                "description": "d",
                "skill_md_content": "# x",
                "scope": "global",
            }
        )
        assert "非法技能 ID 格式" in result
    finally:
        from app.utils.context import current_user_info

        current_user_info.reset(token)
