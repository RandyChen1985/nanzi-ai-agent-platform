import json
from unittest.mock import PropertyMock, patch

import pytest


pytestmark = pytest.mark.no_infrastructure


def test_skill_discovery_tools_list_and_read_skill(tmp_path):
    from app.services.ai.tools.system_executive_tools import (
        list_available_skills,
        read_skill_instruction,
    )

    skill_dir = tmp_path / "ops-helper"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: 运维辅助技能\n"
        "description: 处理运维排障流程\n"
        "---\n\n"
        "# 操作守则\n"
        "先定位影响范围，再给出处置步骤。\n",
        encoding="utf-8",
    )
    hidden_dir = tmp_path / ".draft"
    hidden_dir.mkdir()
    (hidden_dir / "SKILL.md").write_text("hidden", encoding="utf-8")

    with patch("app.core.config.Settings.SKILLS_DIR", new_callable=PropertyMock) as mock_skills_dir:
        mock_skills_dir.return_value = str(tmp_path)

        listing = list_available_skills.invoke({})
        assert "ops-helper" in listing
        assert "运维辅助技能" in listing
        assert "处理运维排障流程" in listing
        assert ".draft" not in listing

        instruction = read_skill_instruction.invoke({"skill_id": "ops-helper"})
        assert "操作守则" in instruction
        assert "先定位影响范围" in instruction

        bad = read_skill_instruction.invoke({"skill_id": "../ops-helper"})
        assert "非法技能 ID" in bad


def test_list_available_skills_respects_agent_custom_allowlist(tmp_path, monkeypatch):
    from app.core.context import AgentContext, agent_context, set_agent_context
    from app.services.ai.tools.system_executive_tools import (
        list_available_skills,
        read_skill_instruction,
    )
    from app.utils.context import current_user_info

    global_root = tmp_path / "global"
    personal = tmp_path / "personal"
    for skill_id, name in (("alpha", "Alpha"), ("beta", "Beta")):
        d = global_root / skill_id
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: desc-{skill_id}\nenabled: true\n---\n\n# {name}\n",
            encoding="utf-8",
        )

    mine = personal / "mine"
    mine.mkdir(parents=True)
    (mine / "SKILL.md").write_text(
        "---\nname: Mine\ndescription: personal\nenabled: true\n---\n\n# Mine\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "app.services.ai.skill_resolver.get_user_personal_skills_dir",
        lambda _user: str(personal),
    )

    user_token = current_user_info.set({"id": 1, "user_name": "u1"})
    set_agent_context(
        AgentContext(
            agent_id="a1",
            agent_name="agent",
            skills_custom=True,
            skills=["beta"],
        )
    )

    try:
        with patch("app.core.config.Settings.SKILLS_DIR", new_callable=PropertyMock) as mock_skills_dir:
            mock_skills_dir.return_value = str(global_root)
            listing = list_available_skills.invoke({})
            assert "beta" in listing
            assert "alpha" not in listing
            assert "mine" in listing

            ok = read_skill_instruction.invoke({"skill_id": "beta"})
            assert "Beta" in ok
            denied = read_skill_instruction.invoke({"skill_id": "alpha"})
            assert "白名单" in denied and "自定义技能" in denied
            personal_ok = read_skill_instruction.invoke({"skill_id": "mine"})
            assert "Mine" in personal_ok
    finally:
        current_user_info.reset(user_token)
        agent_context.set(None)


def test_general_chat_implicit_tools_include_safe_skill_lookup():
    from app.services.ai.tools.registry import ToolRegistry

    tool_names = {getattr(tool, "name", "") for tool in ToolRegistry.get_system_implicit_tools()}

    assert "list_available_skills" in tool_names
    assert "read_skill_instruction" in tool_names


# --------------------------------------------------------------------------- #
# 技能包内资源：清单 + 按需读取（path 参数）
# --------------------------------------------------------------------------- #


def _write_skill(skill_dir, *, name: str, body: str = "# 主体\n", extra: dict | None = None):
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: desc-{name}\nenabled: true\n---\n\n{body}",
        encoding="utf-8",
    )
    for rel, content in (extra or {}).items():
        target = skill_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")


def _patch_skills_dir(tmp_path):
    return patch("app.core.config.Settings.SKILLS_DIR", new_callable=PropertyMock)


def test_read_skill_instruction_lists_bundled_resources(tmp_path):
    from app.services.ai.tools.system_executive_tools import read_skill_instruction

    _write_skill(
        tmp_path / "ops-helper",
        name="运维辅助技能",
        body="# 操作守则\n先定位影响范围。\n",
        extra={
            "references/boundary-rules.md": "# 边界规则\n",
            "references/calc-rules.md": "# 计算规则\n只算一次。\n",
            "scripts/run.py": "print('ok')\n",
            "default-rules.json": '{"a": 1}\n',
            ".hidden.md": "不该出现\n",
            "__pycache__/x.pyc": "binary-ish\n",
        },
    )

    with _patch_skills_dir(tmp_path) as mock_skills_dir:
        mock_skills_dir.return_value = str(tmp_path)

        instruction = read_skill_instruction.invoke({"skill_id": "ops-helper"})
        assert "操作守则" in instruction
        # 资源清单：相对路径、可读提示
        assert "references/calc-rules.md" in instruction
        assert "references/boundary-rules.md" in instruction
        assert "scripts/run.py" in instruction
        assert "default-rules.json" in instruction
        assert 'path="<相对路径>"' in instruction
        # 隐藏项 / 缓存目录 / SKILL.md 自身不进清单
        assert ".hidden.md" not in instruction
        assert "__pycache__" not in instruction
        assert "- SKILL.md" not in instruction

        resource = read_skill_instruction.invoke(
            {"skill_id": "ops-helper", "path": "references/calc-rules.md"}
        )
        assert "[技能读取成功: ops-helper/references/calc-rules.md" in resource
        assert "只算一次" in resource

        script = read_skill_instruction.invoke(
            {"skill_id": "ops-helper", "path": "scripts/run.py"}
        )
        assert "print('ok')" in script


def test_read_skill_instruction_without_resources_keeps_old_shape(tmp_path):
    from app.services.ai.tools.system_executive_tools import read_skill_instruction

    _write_skill(tmp_path / "bare", name="Bare", body="# 只有正文\n")

    with _patch_skills_dir(tmp_path) as mock_skills_dir:
        mock_skills_dir.return_value = str(tmp_path)
        instruction = read_skill_instruction.invoke({"skill_id": "bare"})
        # 头部新增「共 N 字符」总量标注（截断可见性契约），其余旧形状不变
        assert instruction.startswith("[技能读取成功: bare/SKILL.md · scope=global · 共 ")
        assert "技能自带资源" not in instruction
        assert "只有正文" in instruction


def test_read_skill_instruction_rejects_path_escape(tmp_path):
    from app.services.ai.tools.system_executive_tools import read_skill_instruction

    _write_skill(
        tmp_path / "ops-helper",
        name="运维辅助技能",
        extra={"references/calc-rules.md": "# 规则\n"},
    )
    (tmp_path / "outside.md").write_text("外部文件\n", encoding="utf-8")

    with _patch_skills_dir(tmp_path) as mock_skills_dir:
        mock_skills_dir.return_value = str(tmp_path)

        for bad_path in ("../outside.md", "references/../../outside.md", ".."):
            rejected = read_skill_instruction.invoke(
                {"skill_id": "ops-helper", "path": bad_path}
            )
            assert "不允许包含 .." in rejected, bad_path
            assert "外部文件" not in rejected

        absolute = read_skill_instruction.invoke(
            {"skill_id": "ops-helper", "path": str(tmp_path / "outside.md")}
        )
        assert "相对路径" in absolute
        assert "外部文件" not in absolute

        directory = read_skill_instruction.invoke(
            {"skill_id": "ops-helper", "path": "references"}
        )
        assert "是目录" in directory

        missing = read_skill_instruction.invoke(
            {"skill_id": "ops-helper", "path": "references/nope.md"}
        )
        assert "没有资源文件" in missing

        # 控制字符 / 转义序列：用户可上传的技能包里可能出现，回显到日志与上下文即控制序列注入
        for control_path in ("references/\x01evil.sh", "references/\x1b[2Jevil.md"):
            rejected_control = read_skill_instruction.invoke(
                {"skill_id": "ops-helper", "path": control_path}
            )
            assert "控制字符" in rejected_control, repr(control_path)

        # 反向断言：中文名与带空格/括号的合法资源名必须能读（窄字符白名单会把它们误杀）
        (tmp_path / "ops-helper" / "references" / "边界规则.md").write_text(
            "中文名内容\n", encoding="utf-8"
        )
        chinese = read_skill_instruction.invoke(
            {"skill_id": "ops-helper", "path": "references/边界规则.md"}
        )
        assert "中文名内容" in chinese


def test_read_skill_instruction_rejects_symlink_escape(tmp_path):
    import os

    from app.services.ai.tools.system_executive_tools import read_skill_instruction

    skill_dir = tmp_path / "ops-helper"
    _write_skill(skill_dir, name="运维辅助技能")
    secret = tmp_path.parent / f"{tmp_path.name}-secret.md"
    secret.write_text("TOP-SECRET\n", encoding="utf-8")
    link = skill_dir / "leak.md"
    try:
        os.symlink(secret, link)
    except (OSError, NotImplementedError):  # pragma: no cover - 平台不支持软链接
        pytest.skip("当前环境不支持创建软链接")

    with _patch_skills_dir(tmp_path) as mock_skills_dir:
        mock_skills_dir.return_value = str(tmp_path)
        rejected = read_skill_instruction.invoke(
            {"skill_id": "ops-helper", "path": "leak.md"}
        )
        assert "软链接" in rejected or "路径越界" in rejected
        assert "TOP-SECRET" not in rejected


def test_read_skill_instruction_reports_binary_and_truncates_large(tmp_path):
    from app.services.ai.tools.system_executive_tools import (
        SKILL_RESOURCE_MAX_BYTES,
        read_skill_instruction,
    )

    _write_skill(
        tmp_path / "ops-helper",
        name="运维辅助技能",
        extra={
            "assets/logo.png": b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR",
            "references/huge.md": "A" * (SKILL_RESOURCE_MAX_BYTES + 5000),
        },
    )

    with _patch_skills_dir(tmp_path) as mock_skills_dir:
        mock_skills_dir.return_value = str(tmp_path)

        binary = read_skill_instruction.invoke(
            {"skill_id": "ops-helper", "path": "assets/logo.png"}
        )
        assert "不是文本文件" in binary

        huge = read_skill_instruction.invoke(
            {"skill_id": "ops-helper", "path": "references/huge.md"}
        )
        # 截断必须可见，且返回长度对齐上下文侧上限（不再只截到 262144 字符）
        assert "已截断" in huge
        assert len(huge) < SKILL_RESOURCE_MAX_BYTES + 2000


def test_read_skill_instruction_truncates_huge_resource_list(tmp_path):
    from app.services.ai.tools.system_executive_tools import (
        _SKILL_RESOURCE_LIST_MAX_ENTRIES,
        read_skill_instruction,
    )

    _write_skill(
        tmp_path / "bulk",
        name="Bulk",
        extra={
            f"references/f{index:04d}.md": "x\n"
            for index in range(_SKILL_RESOURCE_LIST_MAX_ENTRIES + 5)
        },
    )

    with _patch_skills_dir(tmp_path) as mock_skills_dir:
        mock_skills_dir.return_value = str(tmp_path)
        instruction = read_skill_instruction.invoke({"skill_id": "bulk"})
        assert "清单已截断" in instruction


def test_read_skill_resource_respects_allowlist_and_personal_scope(tmp_path, monkeypatch):
    from app.core.context import AgentContext, agent_context, set_agent_context
    from app.services.ai.tools.system_executive_tools import read_skill_instruction
    from app.utils.context import current_user_info

    global_root = tmp_path / "global"
    personal = tmp_path / "personal"
    for skill_id in ("alpha", "beta"):
        _write_skill(
            global_root / skill_id,
            name=skill_id.title(),
            extra={f"references/{skill_id}.md": f"{skill_id}-rules\n"},
        )
    _write_skill(
        personal / "mine",
        name="Mine",
        extra={"references/mine.md": "mine-rules\n"},
    )

    monkeypatch.setattr(
        "app.services.ai.skill_resolver.get_user_personal_skills_dir",
        lambda _user: str(personal),
    )
    user_token = current_user_info.set({"id": 7, "user_name": "u7"})
    set_agent_context(
        AgentContext(agent_id="a7", agent_name="agent", skills_custom=True, skills=["beta"])
    )

    try:
        with _patch_skills_dir(global_root) as mock_skills_dir:
            mock_skills_dir.return_value = str(global_root)

            # 白名单外的公共技能：读正文与读资源都必须被拒
            denied_md = read_skill_instruction.invoke({"skill_id": "alpha"})
            # 必须点明是「自定义技能白名单」问题，不能与「不存在」混成一句模糊提示
            assert "白名单" in denied_md and "自定义技能" in denied_md
            assert "不存在" not in denied_md
            denied_resource = read_skill_instruction.invoke(
                {"skill_id": "alpha", "path": "references/alpha.md"}
            )
            assert "alpha-rules" not in denied_resource

            allowed = read_skill_instruction.invoke(
                {"skill_id": "beta", "path": "references/beta.md"}
            )
            assert "beta-rules" in allowed
            assert "· scope=global" in allowed

            # 个人技能始终可用，且 scope 口径正确
            personal_resource = read_skill_instruction.invoke(
                {"skill_id": "mine", "path": "references/mine.md"}
            )
            assert "mine-rules" in personal_resource
            assert "· scope=personal" in personal_resource
    finally:
        current_user_info.reset(user_token)
        agent_context.set(None)


def test_read_skill_instruction_rejects_forbidden_skill_resource(tmp_path, monkeypatch):
    from app.services.ai.tools.system_executive_tools import read_skill_instruction

    async def _forbidden(*_args, **_kwargs):
        return {"blocked"}

    monkeypatch.setattr(
        "app.services.ai.skills.policy.get_forbidden_skill_ids", _forbidden
    )
    _write_skill(
        tmp_path / "blocked",
        name="Blocked",
        extra={"references/secret.md": "secret-rules\n"},
    )

    with _patch_skills_dir(tmp_path) as mock_skills_dir:
        mock_skills_dir.return_value = str(tmp_path)

        denied_md = read_skill_instruction.invoke({"skill_id": "blocked"})
        # 被权限禁用要单独说清，不能退化成「不存在、已禁用，或不在范围内」的三选一
        assert "被权限规则禁用" in denied_md
        assert "不存在" not in denied_md
        denied_resource = read_skill_instruction.invoke(
            {"skill_id": "blocked", "path": "references/secret.md"}
        )
        assert "secret-rules" not in denied_resource


# --------------------------------------------------------------------------- #
# 清单结构：total / returned / truncated 必须可感知
# --------------------------------------------------------------------------- #
#
# 「没看到 ≠ 不存在」这件事不能只靠提示词约束：工具的返回结构本身必须让模型看得出
# 清单是否被截断。旧实现返回裸数组，截断后与"本来就只有这几条"完全无法区分。


def test_list_available_skills_reports_total_and_truncation(tmp_path):
    from app.services.ai.tools.system_executive_tools import list_available_skills

    for index in range(6):
        _write_skill(tmp_path / f"skill-{index}", name=f"技能{index}")

    with _patch_skills_dir(tmp_path) as mock_skills_dir:
        mock_skills_dir.return_value = str(tmp_path)

        page = json.loads(list_available_skills.invoke({"limit": 2}))
        assert page["total"] == 6, "total 必须是过滤后的真实总数，不是本次返回条数"
        assert page["returned"] == 2
        assert page["truncated"] is True
        assert len(page["skills"]) == 2
        assert "未展示不等于不存在" in page["note"]

        full = json.loads(list_available_skills.invoke({}))
        assert full["total"] == full["returned"] == 6
        assert full["truncated"] is False
        # 没被截断就不该出现解释性 note，避免模型以为还有未展示条目
        assert "note" not in full


def test_list_available_skills_keyword_narrows_but_keeps_total(tmp_path):
    from app.services.ai.tools.system_executive_tools import list_available_skills

    for index in range(6):
        _write_skill(tmp_path / f"skill-{index}", name=f"技能{index}")

    with _patch_skills_dir(tmp_path) as mock_skills_dir:
        mock_skills_dir.return_value = str(tmp_path)

        narrowed = json.loads(list_available_skills.invoke({"keyword": "skill-3"}))
        assert [item["id"] for item in narrowed["skills"]] == ["skill-3"]
        assert narrowed["total"] == 1
        assert narrowed["truncated"] is False
        assert narrowed["keyword"] == "skill-3"

        # 匹配不到时也要说清是"keyword 没命中"，而不是"没有技能"
        empty = json.loads(list_available_skills.invoke({"keyword": "nope"}))
        assert empty["total"] == 0
        assert empty["skills"] == []
        assert "keyword" in empty["note"]


def test_list_available_skills_empty_catalog_is_explained(tmp_path):
    from app.services.ai.tools.system_executive_tools import list_available_skills

    empty_root = tmp_path / "empty_skills"
    empty_root.mkdir()

    with _patch_skills_dir(empty_root) as mock_skills_dir:
        mock_skills_dir.return_value = str(empty_root)

        payload = json.loads(list_available_skills.invoke({}))
        assert payload["total"] == 0
        assert payload["truncated"] is False
        assert payload["skills"] == []
        assert payload["note"] == "当前会话没有可见技能。"


def test_read_skill_instruction_suggests_closest_id_on_typo(tmp_path):
    from app.services.ai.tools.system_executive_tools import read_skill_instruction

    _write_skill(tmp_path / "dsapi-admin", name="DSAPI")

    with _patch_skills_dir(tmp_path) as mock_skills_dir:
        mock_skills_dir.return_value = str(tmp_path)

        # 下划线/中划线手误：旧文案只会说"不存在、已禁用，或不在范围内"，模型无从自查
        message = read_skill_instruction.invoke({"skill_id": "dsapi_admin"})

    assert "不存在" in message
    assert "dsapi-admin" in message, "拼写变体必须给出最接近的候选 id"
    assert "list_available_skills" in message


def test_read_skill_instruction_does_not_leak_forbidden_or_disabled_in_suggestions(
    tmp_path, monkeypatch
):
    """被权限禁用或已停用的技能不得出现在「最接近的候选 id」里，且明确拒因不再自相矛盾。"""
    from app.services.ai.tools.system_executive_tools import read_skill_instruction

    async def _forbidden(*_args, **_kwargs):
        return {"secret-ops"}

    monkeypatch.setattr(
        "app.services.ai.skills.policy.get_forbidden_skill_ids", _forbidden
    )
    _write_skill(tmp_path / "secret-ops", name="SecretOps")
    _write_skill(tmp_path / "paused-ops", name="PausedOps", enabled=False)

    with _patch_skills_dir(tmp_path) as mock_skills_dir:
        mock_skills_dir.return_value = str(tmp_path)

        # 用近似拼写探测：不得把被禁/停用的技能 id 当候选吐出来
        probe_forbidden = read_skill_instruction.invoke({"skill_id": "secret_ops"})
        assert "secret-ops" not in probe_forbidden

        probe_disabled = read_skill_instruction.invoke({"skill_id": "paused_ops"})
        assert "paused-ops" not in probe_disabled

        # 命中明确拒因时，不再追加「单次读取失败不代表该技能已被删除或禁用」的自相矛盾尾巴
        denied = read_skill_instruction.invoke({"skill_id": "secret-ops"})
        assert "被权限规则禁用" in denied
        assert "不代表该技能已被删除或禁用" not in denied

        disabled = read_skill_instruction.invoke({"skill_id": "paused-ops"})
        assert "已被停用" in disabled
        assert "不代表该技能已被删除或禁用" not in disabled


def test_list_available_skills_rejects_bool_limit(tmp_path):
    """bool 是 int 子类：limit=True 不得被当成 limit=1。"""
    from app.services.ai.tools.system_executive_tools import _coerce_skill_list_limit

    assert _coerce_skill_list_limit(True) == 50
    assert _coerce_skill_list_limit(False) == 50
