"""技能广场目录（代码资产）的加载与校验。

目录有两条硬约束值得单测盯着：

1. **不复制第三方正文**。``anthropics/skills`` 等来源是 Proprietary 许可，明令禁止
   把材料复制到服务之外再分发。所以 catalog 只存元数据 + 我们自己撰写的 ``docs/*.md``。
   这里断言详情接口返回的 ``intro`` 就是本地文档内容，且元数据里没有任何 SKILL.md 原文。
2. **目录坏了不能拖垮服务**。手写 JSON 谁都会写错，单条坏掉只跳过该条。

顺带锁住两个容易回归的点：``install_command`` 必须带两个 ``-y``（否则沙箱里
``npx skills`` 会停在等待输入），``install_prompt_global`` 缺省要回退到个人版。
"""

import json
import os
from pathlib import Path

import pytest

from app.services.ai.skill_gallery import catalog as gallery_catalog

CATALOG_DIR = Path(gallery_catalog.CATALOG_DIR)


@pytest.fixture()
def fresh_catalog(monkeypatch):
    """绕过进程内缓存，按当前磁盘内容重新加载。"""

    def _reload():
        monkeypatch.setattr(gallery_catalog, "_CACHE", None)
        return gallery_catalog._load_catalog(use_cache=False)

    return _reload


def test_catalog_passes_strict_validation():
    assert gallery_catalog.validate_catalog() == []


def test_list_skills_returns_cards_without_intro():
    skills = gallery_catalog.list_skills()
    assert skills, "技能广场目录不应为空"
    for skill in skills:
        # 列表接口不该带正文：8 份介绍拼起来会把列表响应撑大好几倍
        assert "intro" not in skill
        assert skill["id"]
        assert skill["summary"]
        assert skill["category"]
        assert skill["install_command"]
        assert skill["install_prompt"]


def test_categories_follow_first_appearance_order():
    skills = gallery_catalog.list_skills()
    categories = gallery_catalog.list_categories()
    expected = []
    for skill in skills:
        if skill["category"] not in expected:
            expected.append(skill["category"])
    assert categories == expected


def test_intro_is_our_own_doc_not_upstream_skill_md():
    detail = gallery_catalog.get_skill("pdf")
    assert detail is not None
    doc_path = CATALOG_DIR / detail["intro_doc"]
    assert doc_path.is_file()
    assert detail["intro"] == doc_path.read_text(encoding="utf-8")
    # 第三方 SKILL.md 的 frontmatter 不该出现在我们的介绍里——出现就说明有人
    # 把上游正文复制进来了
    assert not detail["intro"].startswith("---")
    assert "license: Proprietary" not in detail["intro"]


def test_install_command_has_two_yes_flags():
    """两个 -y 缺一不可：npx 的装包确认 + skills 的安装确认。

    少任何一个，命令会在沙箱里等 stdin，把会话拖成僵尸进程（用户踩过）。
    """
    for skill in gallery_catalog.list_skills():
        command = skill["install_command"]
        assert command.count("npx -y skills add") == 1, skill["id"]
        assert command.rstrip().endswith("-y"), skill["id"]
        assert "--skill " in command, skill["id"]
        # 固定装到可预测的目录，导入路径才不会随 agent 的 cwd 漂移
        assert "/workspace/.skill-tmp" in command, skill["id"]
        assert "DISABLE_TELEMETRY=1" in command, skill["id"]


def test_install_prompt_warns_about_interactive_hang():
    for skill in gallery_catalog.list_skills():
        prompt = skill["install_prompt"]
        assert "交互" in prompt, skill["id"]
        assert "2 分钟" in prompt or "中止" in prompt, skill["id"]
        assert "import_skill_from_sandbox" in prompt, skill["id"]


def test_install_prompt_global_falls_back_to_personal(monkeypatch):
    """缺 install_prompt_global 时回退到个人版，不给管理员一段空提示词。"""
    raw = json.loads((CATALOG_DIR / "catalog.json").read_text(encoding="utf-8"))
    for item in raw["skills"]:
        item.pop("install_prompt_global", None)
    monkeypatch.setattr(gallery_catalog, "_read_raw", lambda: raw)
    monkeypatch.setattr(gallery_catalog, "_CACHE", None)

    skills = gallery_catalog.list_skills()
    assert skills
    for skill in skills:
        assert skill["install_prompt_global"] == skill["install_prompt"]


def test_get_skill_unknown_returns_none():
    assert gallery_catalog.get_skill("no-such-skill") is None
    assert gallery_catalog.get_skill("") is None


def test_intro_doc_cannot_escape_catalog_dir(monkeypatch, tmp_path):
    """手写 intro_doc 写成 ../../etc/passwd 也只能读不到，不能真的读出来。"""
    raw = json.loads((CATALOG_DIR / "catalog.json").read_text(encoding="utf-8"))
    secret = tmp_path / "secret.md"
    secret.write_text("TOP-SECRET", encoding="utf-8")
    raw["skills"][0]["intro_doc"] = os.path.relpath(secret, CATALOG_DIR)
    monkeypatch.setattr(gallery_catalog, "_read_raw", lambda: raw)
    monkeypatch.setattr(gallery_catalog, "_CACHE", None)

    detail = gallery_catalog.get_skill(raw["skills"][0]["id"])
    assert detail is not None
    assert detail["intro"] == ""


def test_validate_catalog_reports_duplicate_and_missing_fields(monkeypatch):
    raw = {
        "version": 1,
        "skills": [
            {"id": "dup", "slug": "dup", "name": "A", "summary": "s", "category": "c"},
            {"id": "dup", "slug": "dup", "name": "B", "summary": "s", "category": "c"},
        ],
    }
    monkeypatch.setattr(gallery_catalog, "_read_raw", lambda: raw)
    problems = gallery_catalog.validate_catalog()
    assert any("重复" in item for item in problems)
    assert any("install_command" in item for item in problems)


def test_validate_catalog_rejects_illegal_id(monkeypatch):
    raw = {
        "version": 1,
        "skills": [
            {
                "id": "../evil",
                "slug": "evil",
                "name": "X",
                "summary": "s",
                "category": "c",
                "install_command": "cmd",
                "install_prompt": "prompt",
            }
        ],
    }
    monkeypatch.setattr(gallery_catalog, "_read_raw", lambda: raw)
    problems = gallery_catalog.validate_catalog()
    assert any("只能包含" in item for item in problems)


def test_broken_entry_is_skipped_not_fatal(monkeypatch):
    raw = json.loads((CATALOG_DIR / "catalog.json").read_text(encoding="utf-8"))
    raw["skills"].append({"id": "bad id with spaces", "name": "坏条目"})
    monkeypatch.setattr(gallery_catalog, "_read_raw", lambda: raw)
    monkeypatch.setattr(gallery_catalog, "_CACHE", None)

    skills = gallery_catalog.list_skills()
    assert "bad id with spaces" not in [item["id"] for item in skills]
    # 其余条目照常可用
    assert len(skills) == len(raw["skills"]) - 1


def test_corrupt_json_degrades_to_empty(monkeypatch):
    def _boom():
        raise json.JSONDecodeError("Expecting ',' delimiter", "", 0)

    monkeypatch.setattr(gallery_catalog, "_read_raw", _boom)
    monkeypatch.setattr(gallery_catalog, "_CACHE", None)

    assert gallery_catalog.list_skills() == []
    assert gallery_catalog.list_categories() == []
    assert gallery_catalog.get_skill("pdf") is None
