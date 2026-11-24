"""智能体自定义 Skills：白名单过滤与 schema 校验。"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import PropertyMock, patch

import pytest

from app.schemas.agent import AIAgentVersionBase
from app.services.ai.runtime.agentscope.workspace import discover_platform_skill_paths
from app.services.ai.skill_resolver import list_skill_metas, skill_filter_kwargs_from_config


def _write_skill(root: Path, skill_id: str, *, enabled: bool = True, name: str | None = None) -> None:
    skill_dir = root / skill_id
    skill_dir.mkdir(parents=True, exist_ok=True)
    display = name or skill_id
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {display}\ndescription: test {skill_id}\nenabled: {str(enabled).lower()}\n---\n\n# {display}\n",
        encoding="utf-8",
    )


def test_version_schema_requires_skills_when_custom():
    # 请求侧：空列表在 schema 层仅归一化；强制校验由服务层负责
    data = AIAgentVersionBase(
        system_prompt="hi",
        skills_custom=True,
        skills=[],
    )
    assert data.skills_custom is True
    assert data.skills == []


def test_version_schema_clears_skills_when_not_custom():
    data = AIAgentVersionBase(
        system_prompt="hi",
        skills_custom=False,
        skills=["a", "b"],
    )
    assert data.skills == []


def test_version_response_accepts_null_skills():
    from datetime import datetime
    from app.schemas.agent import AIAgentVersionResponse

    resp = AIAgentVersionResponse.model_validate(
        {
            "id": "v1",
            "agent_id": "a1",
            "system_prompt": "hi",
            "skills_custom": False,
            "skills": None,
            "tools": [],
            "created_at": datetime.now(),
        }
    )
    assert resp.skills == []
    assert resp.skills_custom is False


def test_validate_skills_config_rejects_empty_custom():
    from app.services.ai.agent_manager import AgentManagerService

    with pytest.raises(ValueError, match="至少选择一个"):
        AgentManagerService._validate_skills_config(True, [])
    AgentManagerService._validate_skills_config(True, ["brainstorming"])
    AgentManagerService._validate_skills_config(False, [])


def test_discover_platform_skill_paths_respects_allowlist(tmp_path, monkeypatch):
    global_root = tmp_path / "global"
    personal_root = tmp_path / "personal"
    _write_skill(global_root, "alpha")
    _write_skill(global_root, "beta")
    _write_skill(global_root, "gamma", enabled=False)
    _write_skill(personal_root, "mine")

    monkeypatch.setattr(
        "app.services.ai.skill_resolver.get_user_personal_skills_dir",
        lambda _user: str(personal_root),
    )

    with patch("app.core.config.Settings.SKILLS_DIR", new_callable=PropertyMock) as mock_dir:
        mock_dir.return_value = str(global_root)
        all_paths = discover_platform_skill_paths(user_info={"id": 1})
        all_ids = {os.path.basename(p) for p in all_paths}
        assert all_ids == {"alpha", "beta", "mine"}

        custom_paths = discover_platform_skill_paths(
            user_info={"id": 1},
            skills_custom=True,
            allowed_global_skills=["beta", "gamma"],
        )
        custom_ids = {os.path.basename(p) for p in custom_paths}
        # gamma disabled → skipped; personal always kept
        assert custom_ids == {"beta", "mine"}


def test_list_skill_metas_custom_keeps_personal(tmp_path, monkeypatch):
    global_root = tmp_path / "global"
    personal_root = tmp_path / "personal"
    _write_skill(global_root, "alpha")
    _write_skill(global_root, "beta")
    _write_skill(personal_root, "mine")

    monkeypatch.setattr(
        "app.services.ai.skill_resolver.get_user_personal_skills_dir",
        lambda _user: str(personal_root),
    )

    with patch("app.core.config.Settings.SKILLS_DIR", new_callable=PropertyMock) as mock_dir:
        mock_dir.return_value = str(global_root)
        metas = list_skill_metas(
            user_info={"id": 1},
            skills_custom=True,
            allowed_global_skills=["alpha"],
        )
        ids = {m["id"] for m in metas}
        assert ids == {"alpha", "mine"}


def test_skill_filter_kwargs_from_config():
    assert skill_filter_kwargs_from_config(None) == {
        "skills_custom": False,
        "allowed_global_skills": None,
    }
    kwargs = skill_filter_kwargs_from_config(
        {"skills_custom": True, "skills": [" a ", "", "b"]}
    )
    assert kwargs == {
        "skills_custom": True,
        "allowed_global_skills": ["a", "b"],
    }


def test_discover_skips_non_skill_entries_and_missing_root(tmp_path, monkeypatch):
    """目录枚举用 scandir：非目录、无 SKILL.md 的条目都要跳过，根目录不可读不许抛。

    技能库里常有 .git、README、临时目录；老实现靠 listdir + 两次 stat，新实现靠
    dirent 类型。这里锁住「跳过规则不变」和「根目录消失时返回空列表」。
    """
    global_root = tmp_path / "global"
    _write_skill(global_root, "alpha")
    (global_root / "not-a-skill").mkdir()
    (global_root / "README.md").write_text("docs", encoding="utf-8")
    (global_root / ".git").mkdir()
    (global_root / ".git" / "config").write_text("x", encoding="utf-8")

    with patch("app.core.config.Settings.SKILLS_DIR", new_callable=PropertyMock) as mock_dir:
        mock_dir.return_value = str(global_root)
        paths = discover_platform_skill_paths(user_info=None)
        assert {os.path.basename(p) for p in paths} == {"alpha"}

        mock_dir.return_value = str(tmp_path / "does-not-exist")
        assert discover_platform_skill_paths(user_info=None) == []


def test_discover_parses_frontmatter_only_for_selected_skills(tmp_path, monkeypatch):
    """★ 白名单 / 禁用集合的过滤必须发生在读 SKILL.md 之前。

    技能库上千条时，扫描本身是 dirent 级操作（廉价），真正的成本是「每个技能 open
    一次读 8KB 解析 frontmatter」。若过滤排在解析之后，白名单只选 1 个技能也要为其余
    全部付磁盘 IO——这正是「技能发现」随技能库线性变慢、被报成十几秒的来源之一。
    """
    from unittest.mock import patch as _patch

    global_root = tmp_path / "global"
    for skill_id in ("alpha", "beta", "gamma", "delta"):
        _write_skill(global_root, skill_id)

    parsed: list[str] = []
    import app.utils.skill_metadata as skill_metadata

    real_parse = skill_metadata.parse_skill_frontmatter

    def spy(skill_id, skill_md_path):
        parsed.append(skill_id)
        return real_parse(skill_id, skill_md_path)

    monkeypatch.setattr(skill_metadata, "parse_skill_frontmatter", spy)

    with _patch("app.core.config.Settings.SKILLS_DIR", new_callable=PropertyMock) as mock_dir:
        mock_dir.return_value = str(global_root)
        paths = discover_platform_skill_paths(
            user_info=None,
            skills_custom=True,
            allowed_global_skills=["beta"],
            forbidden_skill_ids=["gamma"],
        )

    assert {os.path.basename(p) for p in paths} == {"beta"}
    assert parsed == ["beta"], "只有入选的技能才该被读进来解析"
