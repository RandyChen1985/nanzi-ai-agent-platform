"""Personal skill SKILL.md preload resolution."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from app.services.ai.skill_resolver import load_skill_md_content


def test_load_skill_md_content_reads_personal_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    personal_root = tmp_path / "workspaces" / "alice__1" / "skills" / "my-helper"
    personal_root.mkdir(parents=True)
    skill_md = personal_root / "SKILL.md"
    skill_md.write_text("---\nname: Helper\n---\n# Personal body\n", encoding="utf-8")
    global_dir = tmp_path / "global_skills"
    global_dir.mkdir()

    monkeypatch.setattr(
        "app.services.ai.skill_resolver.get_user_personal_skills_dir",
        lambda user_info: str(tmp_path / "workspaces" / "alice__1" / "skills"),
    )

    with patch("app.core.config.Settings.SKILLS_DIR", str(global_dir)):
        content = load_skill_md_content(
            "my-helper",
            user_info={"user_id": 1, "user_name": "alice"},
            scope="personal",
            skill_md_path=str(skill_md),
        )
    assert content is not None
    assert "Personal body" in content


def test_load_skill_md_content_rejects_path_escape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    outside = tmp_path / "outside" / "SKILL.md"
    outside.parent.mkdir(parents=True)
    outside.write_text("# leaked\n", encoding="utf-8")
    (tmp_path / "global_skills").mkdir()
    (tmp_path / "skills").mkdir()

    monkeypatch.setattr(
        "app.services.ai.skill_resolver.get_user_personal_skills_dir",
        lambda user_info: str(tmp_path / "skills"),
    )

    with patch("app.core.config.Settings.SKILLS_DIR", str(tmp_path / "global_skills")):
        assert (
            load_skill_md_content(
                "my-helper",
                user_info={"user_id": 1},
                skill_md_path=str(outside),
            )
            is None
        )


def test_load_skill_md_content_falls_back_to_global(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    global_dir = tmp_path / "global_skills" / "platform-skill"
    global_dir.mkdir(parents=True)
    (global_dir / "SKILL.md").write_text("# Global body\n", encoding="utf-8")

    monkeypatch.setattr(
        "app.services.ai.skill_resolver.get_user_personal_skills_dir",
        lambda user_info: None,
    )

    with patch("app.core.config.Settings.SKILLS_DIR", str(tmp_path / "global_skills")):
        content = load_skill_md_content("platform-skill")
    assert content is not None
    assert "Global body" in content
