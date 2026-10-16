import pytest

from app.utils.skill_metadata import (
    build_skill_attachment_hint,
    enrich_messages_with_skill_meta,
    format_skill_meta_text,
    parse_skill_frontmatter,
    upgrade_legacy_skill_mount_hints,
)

pytestmark = pytest.mark.no_infrastructure


def test_parse_skill_frontmatter(tmp_path):
    skill_dir = tmp_path / "demo-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        '---\nname: 演示技能\ndescription: "用于测试的技能说明"\n---\n# Body\n',
        encoding="utf-8",
    )
    meta = parse_skill_frontmatter("demo-skill", str(skill_dir / "SKILL.md"))
    assert meta["name"] == "演示技能"
    assert meta["description"] == "用于测试的技能说明"


def test_format_skill_meta_text():
    text = format_skill_meta_text({"name": "foo", "description": "bar"})
    assert text == "name: foo, description: bar"


def test_build_skill_attachment_hint_with_override():
    hint = build_skill_attachment_hint(
        "my-skill",
        skill_name="展示名",
        meta_override={"name": "my-skill", "description": "技能描述"},
    )
    assert "展示名" in hint
    assert "技能 ID：my-skill" in hint
    assert "已随本轮上下文预载" in hint
    # 关键回归：提示里不能再出现任何文件系统路径。曾经写死的
    # /app/data/skills/<id>/SKILL.md 对个人技能与 Docker/K8s 沙箱都不成立。
    assert "/app/data/skills" not in hint
    assert "skills meta 为：name: my-skill, description: 技能描述" in hint


def test_build_skill_attachment_hint_prefers_explicit_skill_dir():
    """调用方确知技能真实目录时给目录；否则退回 read_skill_instruction 资源读取指引。"""
    hint = build_skill_attachment_hint(
        "p1",
        meta_override={"name": "个人技能"},
        skill_dir="/app/data/agent_workspaces/admin__1/skills/p1",
    )
    assert "/app/data/agent_workspaces/admin__1/skills/p1" in hint
    assert "read_skill_instruction" not in hint

    fallback = build_skill_attachment_hint("p1", meta_override={"name": "个人技能"})
    assert "read_skill_instruction" in fallback
    assert 'path="references/rules.md"' in fallback
    assert "/app/data/agent_workspaces" not in fallback


def test_enrich_messages_with_skill_meta(tmp_path, monkeypatch):
    skills_dir = tmp_path / "skills"
    skill_dir = skills_dir / "auto-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: 自动技能\ndescription: 从磁盘读取\n---\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "app.core.config.settings",
        type("S", (), {"SKILLS_DIR": str(skills_dir)})(),
    )

    messages = [
        {
            "role": "user",
            "content": (
                "\n\n---\n\n"
                "用户本轮已调用生态技能工作流：自动技能（技能 ID：auto-skill）。"
                "其 SKILL.md 正文已随本轮上下文预载，本轮直接按预载指令执行，无需再读取技能文件。"
            ),
            "files": [{"type": "skill", "url": "auto-skill", "filename": "自动技能 (技能)"}],
        }
    ]
    enrich_messages_with_skill_meta(messages)
    assert "skills meta 为：name: 自动技能, description: 从磁盘读取" in messages[0]["content"]
    assert "/app/data/skills" not in messages[0]["content"]
    assert "技能 ID：auto-skill" in messages[0]["content"]


LEGACY_MOUNT_HINT = (
    "用户本轮已调用生态技能工作流：Alpha，对应的物理描述文件绝对路径是："
    "/app/data/skills/alpha/SKILL.md。\nskills meta 为：name: Alpha, description: 演示"
)


def test_upgrade_legacy_skill_mount_hints_rewrites_dead_path():
    """老会话里的死路径提示就地升级：换掉那半句，技能名 / meta 行 / 用户正文都不动。"""
    messages = [
        {"role": "user", "content": "帮我看看\n\n---\n\n" + LEGACY_MOUNT_HINT},
        {"role": "assistant", "content": "好的"},
    ]

    assert upgrade_legacy_skill_mount_hints(messages) == 1

    content = messages[0]["content"]
    assert "/app/data/skills" not in content
    assert "Alpha（技能 ID：alpha）" in content, "显示名要保留，并换成 ID 标记"
    assert "已随本轮上下文预载" in content
    assert "read_skill_instruction" in content
    assert "skills meta 为：name: Alpha, description: 演示" in content, "meta 行必须原样保留"
    assert "帮我看看" in content
    assert messages[1]["content"] == "好的", "不含旧提示的消息逐字节不动"


def test_upgrade_legacy_skill_mount_hints_is_a_noop_for_new_format():
    """新格式里没有可升级的东西：不能重复改写，也不能碰用户正文。"""
    content = "问题\n\n---\n\n" + build_skill_attachment_hint(
        "alpha", meta_override={"name": "Alpha"}
    )
    messages = [{"role": "user", "content": content}]

    assert upgrade_legacy_skill_mount_hints(messages) == 0
    assert messages[0]["content"] == content


def test_upgrade_legacy_skill_mount_hints_leaves_user_text_alone():
    """只有「挂载提示说明行」才升级：用户自己提到这条路径时不能被改写。"""
    messages = [
        {"role": "user", "content": ""},
        {"role": "assistant", "content": "见 /app/data/skills/alpha/SKILL.md 这份文档"},
        "not-a-dict",
        {"role": "user", "content": "提问：/app/data/skills/alpha/SKILL.md 这个路径是什么"},
    ]

    assert upgrade_legacy_skill_mount_hints(messages) == 0
    assert messages[1]["content"] == "见 /app/data/skills/alpha/SKILL.md 这份文档"
    assert messages[3]["content"] == "提问：/app/data/skills/alpha/SKILL.md 这个路径是什么"
    assert upgrade_legacy_skill_mount_hints([]) == 0


# ---------------------------------------------------------------------------
# 技能来源口径（scope）：全平台唯一的解析与判定
# ---------------------------------------------------------------------------

def test_normalize_skill_scope_accepts_every_known_spelling():
    """前端写 global、历史载荷写 public，个人侧只认 personal——必须全部收敛到同一套取值。"""
    from app.utils.skill_metadata import (
        SKILL_SCOPE_PERSONAL,
        SKILL_SCOPE_PUBLIC,
        normalize_skill_scope,
    )

    assert normalize_skill_scope("personal") == SKILL_SCOPE_PERSONAL
    assert normalize_skill_scope(" PERSONAL ") == SKILL_SCOPE_PERSONAL
    assert normalize_skill_scope("Personal") == SKILL_SCOPE_PERSONAL
    assert normalize_skill_scope("global") == SKILL_SCOPE_PUBLIC
    assert normalize_skill_scope("public") == SKILL_SCOPE_PUBLIC
    assert normalize_skill_scope("PUBLIC") == SKILL_SCOPE_PUBLIC


def test_normalize_skill_scope_treats_absent_and_unknown_as_unspecified(caplog):
    """未知取值按"未指定"处理并留 warning：它来自客户端载荷，不能 500、也不能当成个人技能。"""
    import logging

    from app.utils.skill_metadata import normalize_skill_scope

    with caplog.at_level(logging.WARNING):
        assert normalize_skill_scope(None) is None
        assert normalize_skill_scope("") is None
        assert normalize_skill_scope("   ") is None
        assert normalize_skill_scope("weird") is None
        assert normalize_skill_scope({"scope": "personal"}) is None
        assert normalize_skill_scope(123) is None

    warnings = [record for record in caplog.records if "未知技能 scope" in record.getMessage()]
    # 缺省/空串是正常形态，不该刷日志；"weird"/dict/123 三条必须留下可诊断的信号
    assert len(warnings) == 3, [record.getMessage() for record in warnings]


def test_is_personal_skill_scope_only_trusts_personal():
    """方向必须偏保守：认不出的取值一律当公共技能（既有行为），不能误放行个人口径。"""
    from app.utils.skill_metadata import is_personal_skill_scope

    assert is_personal_skill_scope("personal") is True
    assert is_personal_skill_scope("Personal") is True
    for other in ("global", "public", "weird", None, "", 0, {"x": 1}):
        assert is_personal_skill_scope(other) is False


def test_is_personal_skill_attachment_reads_both_layers():
    """scope 可能写在附件顶层或 skillMeta/skill_meta 里，两处都要认（前端两处都发）。"""
    from app.utils.skill_metadata import is_personal_skill_attachment

    assert is_personal_skill_attachment({"type": "skill", "url": "a", "scope": "personal"})
    assert is_personal_skill_attachment(
        {"type": "skill", "url": "a", "skillMeta": {"scope": "personal"}}
    )
    assert is_personal_skill_attachment(
        {"type": "skill", "url": "a", "skill_meta": {"scope": "PERSONAL"}}
    )
    # 顶层是个人、skillMeta 缺省（或反之）都不能被判成公共
    assert is_personal_skill_attachment(
        {"type": "skill", "url": "a", "scope": "personal", "skillMeta": {"name": "a"}}
    )
    assert is_personal_skill_attachment(
        {"type": "skill", "url": "a", "scope": "global", "skillMeta": {"scope": "personal"}}
    )
    # 公共 / 未知 / 非字典
    assert not is_personal_skill_attachment({"type": "skill", "url": "a", "scope": "global"})
    assert not is_personal_skill_attachment({"type": "skill", "url": "a", "scope": "weird"})
    assert not is_personal_skill_attachment(None)
    assert not is_personal_skill_attachment("personal")
    # 本函数只判"来源口径"这一件事，不判类型：调用方必须先按 type == "skill" 过滤
    # （drop_forbidden_skill_items 与 sanitize_forbidden_skill_context 都是这么用的）。
    assert is_personal_skill_attachment({"type": "file", "url": "a", "scope": "personal"})

