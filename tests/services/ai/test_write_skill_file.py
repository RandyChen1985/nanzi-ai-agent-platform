"""``write_skill_file`` 的行为契约。

## 为什么需要这个工具

``create_skills`` 只写 SKILL.md。但真实技能常常还要带脚本、模板、参考资料、图片——
说明书里写了 ``python scripts/build.py``，磁盘上却没有那个脚本，技能就是半残的。

此前模型只能靠 ``write_file`` 自己拼路径（要猜出 ``agent_workspaces/{user_key}/skills``），
且该工具只能写文本、也不校验「这个文件属于哪个技能」。本工具把这件事收敛成一次
带技能语义的写入：技能必须已存在、路径必须落在技能目录内、二进制走 Base64。

## 契约要点

* 技能不存在 → 拒绝（提示先用 create_skills），不会凭空造出「有文件没说明书」的目录；
* SKILL.md → 拒绝（归 create_skills 管，避免绕过它的 overwrite 闸门）；
* 路径 → 必须相对、禁 ``..``、禁隐藏目录，落点必须在技能目录内；
* 目标已存在且未显式 ``overwrite=True`` → **不碰磁盘**，返回 ``NANZI_SKILL_FILE_EXISTS``；
* 单文件 10MB、技能目录 500MB / 20000 文件（与解压上传共用 ``app.core.upload_limits`` 的预算）。
"""
from __future__ import annotations

import base64
import json

import pytest

from app.services.ai.tools import system_executive_tools as exec_tools
from app.utils.context import current_user_info

pytestmark = pytest.mark.no_infrastructure

SKILL_MD = """---
name: 演示技能
description: 用于回归测试
---

按 scripts/build.py 执行。
"""


class _ContextStub:
    """最小 contextvar 替身：让工具内的 ``current_user_info.get()`` 拿到测试用户。"""

    def __init__(self, value):
        self._value = value

    def get(self, default=None):
        return self._value


@pytest.fixture
def personal_root(tmp_path, monkeypatch):
    """把个人技能根指向临时目录，放行路径校验，并预置一个已存在的技能。"""
    root = tmp_path / "personal" / "skills"
    skill_dir = root / "demo-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")

    monkeypatch.setattr(exec_tools, "validate_safe_path", lambda path: path)
    monkeypatch.setattr(
        "app.services.ai.skill_resolver.get_user_personal_skills_dir",
        lambda user_info: str(root),
    )
    monkeypatch.setattr(
        "app.services.ai.skill_resolver.resolve_personal_skill_dir",
        lambda skills_dir, skill_id: (
            str(root / skill_id) if (root / skill_id).is_dir() else None
        ),
    )
    monkeypatch.setattr(
        "app.utils.context.current_user_info",
        _ContextStub({"id": 1, "name": "tester", "role": "user"}),
    )
    return root


def _write(skill_id: str = "demo-skill", path: str = "scripts/build.py", content: str = "print(1)\n", **kwargs) -> str:
    return exec_tools.write_skill_file.func(
        skill_id=skill_id,
        path=path,
        content=content,
        **kwargs,
    )


def test_writes_text_file_into_subdirectory(personal_root):
    result = _write()

    assert "NANZI_SKILL_FILE_WRITTEN:" in result
    assert "写入成功" in result
    target = personal_root / "demo-skill" / "scripts" / "build.py"
    assert target.read_text(encoding="utf-8") == "print(1)\n"


def test_marker_payload_is_parseable(personal_root):
    """标记必须是可 JSON 解析的单行载荷（与 create_skills 的标记同一风格）。"""
    result = _write()
    marker = [line for line in result.splitlines() if line.startswith("NANZI_SKILL_FILE_WRITTEN:")][0]
    payload = json.loads(marker.split(":", 1)[1])
    assert payload == {
        "skill_id": "demo-skill",
        "scope": "personal",
        "path": "scripts/build.py",
        "bytes": len("print(1)\n".encode("utf-8")),
        "encoding": "utf8",
        "overwritten": False,
    }


def test_writes_binary_via_base64(personal_root):
    """图片等二进制：Base64 解码后按字节落盘，不能走 utf-8 文本写入。"""
    raw = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 0x00, 0xFF])
    result = _write(
        path="assets/logo.png",
        content=base64.b64encode(raw).decode("ascii"),
        encoding="base64",
    )

    assert "NANZI_SKILL_FILE_WRITTEN:" in result
    assert (personal_root / "demo-skill" / "assets" / "logo.png").read_bytes() == raw


def test_base64_tolerates_line_breaks(personal_root):
    """模型常在 Base64 里插换行，解码前要先去掉空白。"""
    raw = b"hello-binary"
    encoded = base64.b64encode(raw).decode("ascii")
    wrapped = encoded[:4] + "\n" + encoded[4:]

    result = _write(path="assets/blob.bin", content=wrapped, encoding="base64")

    assert "NANZI_SKILL_FILE_WRITTEN:" in result
    assert (personal_root / "demo-skill" / "assets" / "blob.bin").read_bytes() == raw


def test_existing_file_is_not_touched_without_overwrite(personal_root):
    target = personal_root / "demo-skill" / "scripts" / "build.py"
    target.parent.mkdir(parents=True)
    target.write_text("original\n", encoding="utf-8")

    result = _write(content="replaced\n")

    assert "NANZI_SKILL_FILE_EXISTS:" in result
    assert "未做任何修改" in result
    assert target.read_text(encoding="utf-8") == "original\n"


def test_overwrite_true_replaces_file(personal_root):
    target = personal_root / "demo-skill" / "scripts" / "build.py"
    target.parent.mkdir(parents=True)
    target.write_text("original\n", encoding="utf-8")

    result = _write(content="replaced\n", overwrite=True)

    assert "NANZI_SKILL_FILE_WRITTEN:" in result
    assert "已覆盖更新" in result
    assert target.read_text(encoding="utf-8") == "replaced\n"
    payload = json.loads(
        [line for line in result.splitlines() if line.startswith("NANZI_SKILL_FILE_WRITTEN:")][0].split(":", 1)[1]
    )
    assert payload["overwritten"] is True


def test_rejects_path_traversal(personal_root):
    result = _write(path="../escape.py")

    assert "错误" in result
    assert ".." in result
    assert not (personal_root / "personal" / "escape.py").exists()


def test_rejects_absolute_path(personal_root):
    result = _write(path="/etc/passwd")

    assert "错误" in result
    assert "绝对路径" in result


def test_rejects_hidden_directory(personal_root):
    result = _write(path=".git/hooks/pre-commit")

    assert "错误" in result
    assert "隐藏" in result


def test_rejects_skill_md(personal_root):
    """SKILL.md 归 create_skills 管：否则能绕过它的 overwrite 闸门。"""
    result = _write(path="SKILL.md", content="# 越权覆盖\n")

    assert "错误" in result
    assert "create_skills" in result
    assert (personal_root / "demo-skill" / "SKILL.md").read_text(encoding="utf-8") == SKILL_MD


def test_rejects_when_skill_missing(personal_root):
    result = _write(skill_id="no-such-skill")

    assert "错误" in result
    assert "不存在" in result
    assert "create_skills" in result
    assert not (personal_root / "no-such-skill").exists()


def test_rejects_invalid_skill_id(personal_root):
    result = _write(skill_id="bad id!")

    assert "错误" in result
    assert "非法技能 ID" in result


def test_rejects_invalid_base64(personal_root):
    result = _write(path="assets/x.bin", content="not-base64!!!", encoding="base64")

    assert "错误" in result
    assert "Base64" in result


def test_rejects_unknown_encoding(personal_root):
    result = _write(encoding="utf16")

    assert "错误" in result
    assert "utf8" in result


def test_rejects_oversized_single_file(personal_root, monkeypatch):
    monkeypatch.setattr("app.core.upload_limits.SKILL_ASSET_MAX_FILE_BYTES", 4)

    result = _write(content="12345")

    assert "错误" in result
    assert "超过上限" in result


def test_enforces_directory_file_count_budget(personal_root, monkeypatch):
    monkeypatch.setattr("app.core.upload_limits.SKILL_DIR_MAX_FILES", 1)

    result = _write()

    assert "错误" in result
    assert "文件数已达上限" in result


def test_global_scope_requires_admin(personal_root):
    result = _write(scope="global")

    assert "错误" in result
    assert "管理员" in result


def test_global_scope_writes_when_admin(tmp_path, monkeypatch):
    platform_root = tmp_path / "platform-skills"
    skill_dir = platform_root / "shared-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")

    monkeypatch.setattr(exec_tools, "validate_safe_path", lambda path: path)
    # SKILLS_DIR 是 Settings 上的只读 property（没有 setter），只能在类上覆盖；
    # 实例上直接赋值会抛 AttributeError。
    from app.core.config import Settings

    monkeypatch.setattr(Settings, "SKILLS_DIR", property(lambda self: str(platform_root)))
    monkeypatch.setattr(
        "app.utils.context.current_user_info",
        _ContextStub({"id": 9, "name": "root", "role": "admin"}),
    )

    result = _write(skill_id="shared-skill", path="references/rules.md", content="# 规则\n", scope="global")

    assert "NANZI_SKILL_FILE_WRITTEN:" in result
    assert (skill_dir / "references" / "rules.md").read_text(encoding="utf-8") == "# 规则\n"


def test_directory_usage_counts_nested_files(personal_root):
    """预算统计必须递归，否则脚本放在子目录里就绕过了上限。"""
    _write(path="scripts/a.py", content="a")
    _write(path="references/b.md", content="b")

    files, total = exec_tools._skill_dir_usage(str(personal_root / "demo-skill"))

    # SKILL.md + 两个新文件
    assert files == 3
    assert total == len(SKILL_MD.encode("utf-8")) + 2


# ---------------------------------------------------------------------------
# 注册契约：漏注册会让工具「存在但模型永远看不到」，比没有更糟
# ---------------------------------------------------------------------------

def test_tool_is_registered_and_visible_to_every_agent():
    from app.services.ai.tools.registry import ToolRegistry

    names = [getattr(tool, "name", str(tool)) for tool in ToolRegistry.get_system_implicit_tools()]
    assert "write_skill_file" in names, "工具未进入 SYSTEM_IMPLICIT_TOOLS，普通智能体看不到它"


def test_tool_metadata_matches_skill_management_policy():
    from app.services.ai.tool_policy import resolve_tool_metadata

    metadata = resolve_tool_metadata("write_skill_file")
    assert metadata.capability == "skill_management"
    assert metadata.side_effect == "write"
    assert metadata.confirmation == "ask"
    assert metadata.nudge_mode == "explicit"


def test_tool_is_excluded_from_nudge_and_artifact_snapshot():
    """写盘类工具不该被系统主动促发，也不该混进「本轮产出的文件」快照。"""
    from app.services.ai.session_tool_artifact import _EXCLUDED_TOOL_NAMES
    from app.services.ai.tool_nudge_policy import _NUDGE_EXCLUDED_TOOLS

    assert "write_skill_file" in _NUDGE_EXCLUDED_TOOLS
    assert "write_skill_file" in _EXCLUDED_TOOL_NAMES


def test_skill_budget_constants_are_shared_with_upload_endpoint():
    """技能目录预算只有一份口径：端点解压与模型写入共用，防止各自漂移。"""
    from app.core.upload_limits import SKILL_DIR_MAX_BYTES, SKILL_DIR_MAX_FILES
    from app.api.portal.endpoints import skills as skills_endpoint

    assert skills_endpoint.MAX_EXTRACTED_FILES == SKILL_DIR_MAX_FILES
    assert skills_endpoint.MAX_EXTRACTED_BYTES == SKILL_DIR_MAX_BYTES
