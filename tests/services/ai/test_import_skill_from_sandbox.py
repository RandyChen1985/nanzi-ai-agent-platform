"""``import_skill_from_sandbox`` 的行为契约。

## 为什么需要这个工具

用户在沙箱里让智能体跑 ``npx skills add <包>`` 装技能，产物落在
``/workspace/scratch/.agents/skills/<id>/``。这份目录在**宿主磁盘上真实存在**
（``/workspace`` 是用户工作区的 rw 挂载），但它不在平台技能库里：
``list_available_skills`` 看不到、``read_skill_instruction`` 读不到，会话结束清理后
还可能消失。本工具把整份目录收录进个人技能库，让第三方现成技能包（含脚本/图片等
二进制资源）能一次性纳入平台治理。

## 契约要点

* 源路径只接受 ``/workspace/...`` 与相对路径；``realpath`` 后必须仍在该用户工作区内；
* 源不能是技能库本体（``/workspace/skills``、``/workspace/personal-skills``）；
* 符号链接**不跟随**（``npx skills`` 给各工具建软链，跟随会收重且可能越界）；
* ``.seed`` / ``.git`` / ``node_modules`` / ``__pycache__`` 跳过；
* 目标已存在且未显式 ``overwrite=True`` → **不碰磁盘**，返回 ``NANZI_SKILL_IMPORT_EXISTS``；
* 落盘先暂存到 ``.import-<uuid>`` 再 ``os.replace``，失败不留半成品；
* 批量：源可以是技能根目录，收录其下所有含 SKILL.md 的子目录；
* 缺 frontmatter 只告警（``missing_name`` / ``missing_description``），不拒绝。
"""
from __future__ import annotations

import json
import os

import pytest

from app.services.ai.tools import system_executive_tools as exec_tools
from app.utils.context import current_user_info

pytestmark = pytest.mark.no_infrastructure

SKILL_MD = """---
name: 天气查询
description: 查询城市天气
---

读 scripts/run.py。
"""


class _ContextStub:
    def __init__(self, value):
        self._value = value

    def get(self, default=None):
        return self._value


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """构造「工作区 + 个人技能库」结构，并把工具依赖全部指向临时目录。

    ``<tmp>/personal`` 即用户工作区根（沙箱里的 ``/workspace``），
    ``<tmp>/personal/skills`` 是个人技能库。
    """
    workdir = tmp_path / "personal"
    personal_root = workdir / "skills"
    personal_root.mkdir(parents=True)

    installed = workdir / "scratch" / ".agents" / "skills" / "weather-query"
    installed.mkdir(parents=True)
    (installed / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
    (installed / "scripts").mkdir()
    (installed / "scripts" / "run.py").write_text("print('hi')\n", encoding="utf-8")
    (installed / "assets").mkdir()
    (installed / "assets" / "blob.bin").write_bytes(b"\x00\x01\x02\xff")

    monkeypatch.setattr(exec_tools, "validate_safe_path", lambda path: path)
    monkeypatch.setattr(
        "app.services.ai.skill_resolver.get_user_personal_skills_dir",
        lambda user_info: str(personal_root),
    )
    monkeypatch.setattr(
        "app.services.ai.skill_resolver.resolve_personal_skill_dir",
        lambda skills_dir, skill_id: (
            str(personal_root / skill_id) if (personal_root / skill_id).is_dir() else None
        ),
    )
    monkeypatch.setattr(
        "app.utils.context.current_user_info",
        _ContextStub({"id": 1, "name": "tester", "role": "user"}),
    )
    return type(
        "Workspace",
        (),
        {
            "workdir": workdir,
            "personal_root": personal_root,
            "installed": installed,
            "skills_root": installed.parent,
        },
    )


def _import(source_path: str, **kwargs) -> str:
    return exec_tools.import_skill_from_sandbox.func(source_path=source_path, **kwargs)


def _marker(output: str) -> dict:
    line = next(
        (ln for ln in output.splitlines() if ln.startswith("NANZI_SKILL_IMPORTED:")), None
    )
    assert line, f"输出里没有标记行：{output}"
    return json.loads(line.split("NANZI_SKILL_IMPORTED:", 1)[1])


# --- 基本收录 ---------------------------------------------------------------


def test_imports_skill_with_scripts_and_binary(workspace):
    out = _import("/workspace/scratch/.agents/skills/weather-query")

    target = workspace.personal_root / "weather-query"
    marker = _marker(out)
    assert marker["imported"][0]["skill_id"] == "weather-query"
    assert (target / "SKILL.md").read_text(encoding="utf-8") == SKILL_MD
    assert (target / "scripts" / "run.py").read_text(encoding="utf-8") == "print('hi')\n"
    # 二进制必须按字节原样带过来（这是「只写文本」的 create_skills 做不到的）
    assert (target / "assets" / "blob.bin").read_bytes() == b"\x00\x01\x02\xff"


def test_accepts_relative_path(workspace):
    out = _import("scratch/.agents/skills/weather-query")
    assert _marker(out)["imported"][0]["skill_id"] == "weather-query"
    assert (workspace.personal_root / "weather-query" / "SKILL.md").exists()


def test_explicit_skill_id_overrides_directory_name(workspace):
    out = _import("scratch/.agents/skills/weather-query", skill_id="my-weather")
    assert _marker(out)["imported"][0]["skill_id"] == "my-weather"
    assert (workspace.personal_root / "my-weather" / "SKILL.md").exists()
    assert not (workspace.personal_root / "weather-query").exists()


def test_marker_reports_counts(workspace):
    marker = _marker(_import("scratch/.agents/skills/weather-query"))
    item = marker["imported"][0]
    assert item["files"] == 3
    assert item["bytes"] > 0
    assert item["overwritten"] is False


# --- 符号链接与忽略目录 ------------------------------------------------------


def test_symbolic_links_are_skipped(workspace):
    """npx skills 会给各工具建软链；跟随复制会把同一技能收两遍，也可能越界。"""
    installed = workspace.installed
    os.symlink(installed / "SKILL.md", installed / "linked.md")
    os.symlink(workspace.workdir, installed / "outbound")

    marker = _marker(_import("scratch/.agents/skills/weather-query"))
    target = workspace.personal_root / "weather-query"

    assert marker["imported"][0]["skipped_links"] == 2
    assert not (target / "linked.md").exists()
    assert not (target / "outbound").exists()


def test_vcs_and_cache_directories_are_ignored(workspace):
    for name in (".git", "node_modules", "__pycache__", ".seed"):
        directory = workspace.installed / name
        directory.mkdir()
        (directory / "junk.txt").write_text("x", encoding="utf-8")

    _import("scratch/.agents/skills/weather-query")
    target = workspace.personal_root / "weather-query"
    for name in (".git", "node_modules", "__pycache__", ".seed"):
        assert not (target / name).exists(), f"{name} 不该被收录"


# --- 拒绝路径 ---------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/etc/passwd",
        "/workspace",
        "../escape",
        "scratch/../../escape",
        "skills",
        "personal-skills",
        "/workspace/personal-skills",
        "/workspace/skills",
    ],
)
def test_rejects_unsafe_source_paths(workspace, path):
    out = _import(path)
    assert out.startswith("错误："), out
    assert "NANZI_SKILL_IMPORTED:" not in out


def test_rejects_missing_directory(workspace):
    out = _import("scratch/.agents/skills/not-there")
    assert out.startswith("错误：")
    assert "不存在" in out


def test_rejects_directory_without_any_skill(workspace):
    empty = workspace.workdir / "scratch" / "empty"
    empty.mkdir(parents=True)
    out = _import("scratch/empty")
    assert out.startswith("错误：")
    assert "SKILL.md" in out


# --- 覆盖语义 ---------------------------------------------------------------


def test_existing_skill_is_not_touched_by_default(workspace):
    target = workspace.personal_root / "weather-query"
    target.mkdir()
    (target / "SKILL.md").write_text("OLD", encoding="utf-8")
    (target / "keep.txt").write_text("keep", encoding="utf-8")

    out = _import("scratch/.agents/skills/weather-query")

    assert _marker(out)["skipped"][0]["reason"] == "exists"
    assert (target / "SKILL.md").read_text(encoding="utf-8") == "OLD"
    assert (target / "keep.txt").exists()


def test_overwrite_replaces_whole_tree(workspace):
    target = workspace.personal_root / "weather-query"
    target.mkdir()
    (target / "SKILL.md").write_text("OLD", encoding="utf-8")
    (target / "stale.txt").write_text("stale", encoding="utf-8")

    marker = _marker(_import("scratch/.agents/skills/weather-query", overwrite=True))

    assert marker["imported"][0]["overwritten"] is True
    assert (target / "SKILL.md").read_text(encoding="utf-8") == SKILL_MD
    # 整份替换：旧技能里多出来的文件不该残留
    assert not (target / "stale.txt").exists()


# --- 批量 -------------------------------------------------------------------


def test_batch_import_from_skill_root(workspace):
    for extra in ("alpha", "beta"):
        directory = workspace.skills_root / extra
        directory.mkdir()
        (directory / "SKILL.md").write_text(
            f"---\nname: {extra}\ndescription: d\n---\n", encoding="utf-8"
        )
    # 没有 SKILL.md 的目录应被忽略
    (workspace.skills_root / "no-skill").mkdir()

    marker = _marker(_import("scratch/.agents/skills"))
    ids = sorted(item["skill_id"] for item in marker["imported"])

    assert ids == ["alpha", "beta", "weather-query"]
    assert not (workspace.personal_root / "no-skill").exists()


def test_batch_reports_invalid_directory_name(workspace):
    bad = workspace.skills_root / "Bad Name"
    bad.mkdir()
    (bad / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")

    marker = _marker(_import("scratch/.agents/skills"))

    assert [item["skill_id"] for item in marker["failed"]] == ["Bad Name"]
    assert not (workspace.personal_root / "Bad Name").exists()


# --- frontmatter 告警 -------------------------------------------------------


def test_missing_frontmatter_only_warns(workspace):
    (workspace.installed / "SKILL.md").write_text("no frontmatter here\n", encoding="utf-8")
    marker = _marker(_import("scratch/.agents/skills/weather-query"))
    assert marker["imported"][0]["warnings"] == ["missing_frontmatter"]


def test_missing_name_and_description_warn_individually(workspace):
    (workspace.installed / "SKILL.md").write_text(
        "---\nallowed-tools: Bash\n---\nbody\n", encoding="utf-8"
    )
    marker = _marker(_import("scratch/.agents/skills/weather-query"))
    assert sorted(marker["imported"][0]["warnings"]) == [
        "missing_description",
        "missing_name",
    ]


# --- 预算 -------------------------------------------------------------------


def test_file_count_budget_is_enforced(workspace, monkeypatch):
    monkeypatch.setattr("app.core.upload_limits.SKILL_DIR_MAX_FILES", 1)

    out = _import("scratch/.agents/skills/weather-query")

    marker = _marker(out)
    assert marker["imported"] == []
    assert "文件数上限" in marker["failed"][0]["reason"]
    assert not (workspace.personal_root / "weather-query").exists()


def test_byte_budget_is_enforced(workspace, monkeypatch):
    monkeypatch.setattr("app.core.upload_limits.SKILL_DIR_MAX_BYTES", 1)

    marker = _marker(_import("scratch/.agents/skills/weather-query"))

    assert marker["imported"] == []
    assert "容量上限" in marker["failed"][0]["reason"]


# --- 清理与身份 -------------------------------------------------------------


def test_failed_import_leaves_no_staging_directory(workspace, monkeypatch):
    monkeypatch.setattr("app.core.upload_limits.SKILL_DIR_MAX_FILES", 1)

    _import("scratch/.agents/skills/weather-query")

    leftovers = [name for name in os.listdir(workspace.personal_root) if name.startswith(".")]
    assert leftovers == [], f"暂存目录未清理：{leftovers}"


def test_requires_user_identity(workspace, monkeypatch):
    monkeypatch.setattr("app.utils.context.current_user_info", _ContextStub(None))
    out = _import("scratch/.agents/skills/weather-query")
    assert out.startswith("错误：")
    assert "用户身份" in out


# --- 注册契约 ---------------------------------------------------------------


def test_tool_is_registered_and_implicit():
    from app.services.ai.tools.registry import ToolRegistry

    names = [
        getattr(tool, "name", None)
        or getattr(getattr(tool, "__wrapped__", None), "name", str(tool))
        for tool in ToolRegistry.get_system_implicit_tools()
    ]
    assert "import_skill_from_sandbox" in names


def test_tool_policy_declares_write_with_confirmation():
    from app.services.ai.tool_policy import _KNOWN_TOOL_METADATA

    meta = _KNOWN_TOOL_METADATA["import_skill_from_sandbox"]
    assert meta.capability == "skill_management"
    assert meta.side_effect == "write"
    assert meta.confirmation == "ask"
    # overwrite=false 的第二次调用返回 EXISTS（不是成功），不能算幂等
    assert meta.idempotent == "no"


def test_tool_is_excluded_from_nudges_and_artifacts():
    from app.services.ai.session_tool_artifact import _EXCLUDED_TOOL_NAMES
    from app.services.ai.tool_nudge_policy import _NUDGE_EXCLUDED_TOOLS

    assert "import_skill_from_sandbox" in _NUDGE_EXCLUDED_TOOLS
    assert "import_skill_from_sandbox" in _EXCLUDED_TOOL_NAMES


# --- 与技能目录扫描口径对齐 --------------------------------------------------


def test_imported_skill_is_visible_to_list_skill_metas(workspace, monkeypatch, tmp_path):
    """收录完必须能立刻被 ``list_available_skills`` 背后的扫描看到。

    这里锁的是「导入落点 == 扫描读点」：一旦有人把导入目标改到 ``.seed`` 或别的
    子目录，技能文件写得再对，模型侧也永远看不到它。
    """
    from app.services.ai import skill_resolver

    global_dir = tmp_path / "global_skills"
    global_dir.mkdir()
    monkeypatch.setattr(
        type(__import__("app.core.config", fromlist=["settings"]).settings),
        "SKILLS_DIR",
        property(lambda self: str(global_dir)),
    )

    _import("scratch/.agents/skills/weather-query")

    metas = skill_resolver.list_skill_metas(
        user_info={"id": 1, "name": "tester", "role": "user"}
    )
    by_id = {meta["id"]: meta for meta in metas}
    assert "weather-query" in by_id
    assert by_id["weather-query"]["scope"] == "personal"
    assert by_id["weather-query"]["name"] == "天气查询"
