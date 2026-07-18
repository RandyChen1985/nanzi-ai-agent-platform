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
