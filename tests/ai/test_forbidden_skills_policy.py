"""禁用公共技能：策略集合与各拦截点的过滤语义。

复用 ``ResourcePermission(resource_type="forbidden_skill")``，与 forbidden_tool 平行，无需迁移。

语义（产品已确认）：禁的是"平台发布的那一份"。用户自建的同名个人技能不受影响——运行时
本来就是个人覆盖公共，所以过滤必须发生在**合并之前**、且只作用于 global 技能。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, PropertyMock, patch

import pytest

from app.services.ai.runtime.agentscope.workspace import discover_platform_skill_paths
from app.services.ai.skill_resolver import (
    list_skill_metas,
    resolve_skills_from_query,
    scan_relevant_skills,
)


pytestmark = pytest.mark.no_infrastructure


def _write_skill(root: Path, skill_id: str, *, enabled: bool = True, name: str | None = None) -> None:
    skill_dir = root / skill_id
    skill_dir.mkdir(parents=True, exist_ok=True)
    display = name or skill_id
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {display}\ndescription: 演示 {skill_id} 的能力\n"
        f"enabled: {str(enabled).lower()}\n---\n\n# {display}\n",
        encoding="utf-8",
    )


@pytest.fixture()
def skills_env(tmp_path, monkeypatch):
    """平台目录：alpha（用户列表查询）、beta、gamma（已禁用）；个人目录：与公共同名的 alpha。"""
    global_root = tmp_path / "global"
    personal_root = tmp_path / "personal"
    _write_skill(global_root, "alpha", name="用户列表查询")
    _write_skill(global_root, "beta")
    _write_skill(global_root, "gamma", enabled=False)
    _write_skill(personal_root, "alpha", name="用户列表查询")

    monkeypatch.setattr(
        "app.services.ai.skill_resolver.get_user_personal_skills_dir",
        lambda _user: str(personal_root),
    )

    with patch("app.core.config.Settings.SKILLS_DIR", new_callable=PropertyMock) as mock_dir:
        mock_dir.return_value = str(global_root)
        yield SimpleNamespace(global_root=global_root, personal_root=personal_root)


def test_forbidden_global_skill_disappears_when_nothing_overrides_it(skills_env):
    metas = list_skill_metas(user_info={"id": 1}, excluded_skill_ids={"beta"})

    assert "beta" not in {meta["id"] for meta in metas}
    # 未被禁的技能照旧
    assert "alpha" in {meta["id"] for meta in metas}


def test_forbidden_global_skill_falls_back_to_the_users_own_same_named_skill(skills_env):
    """禁的是平台那一份；用户自己的同名技能仍可用（个人覆盖公共）。"""
    metas = list_skill_metas(user_info={"id": 1}, excluded_skill_ids={"alpha"})
    by_id = {meta["id"]: meta for meta in metas}

    assert "alpha" in by_id
    assert by_id["alpha"]["scope"] == "personal"


def test_scan_and_mention_paths_cannot_reach_a_forbidden_skill(skills_env):
    """关键词扫描与口头点名都走 list_skill_metas，禁用后应拿不到该技能。"""
    scan_query = "演示 beta 的能力"
    mention_query = "使用 beta 技能"

    # 对照：不禁时两条路都能命中，证明下面的"拿不到"不是因为查询本身无效
    assert any(meta["id"] == "beta" for meta in scan_relevant_skills(scan_query, user_info={"id": 1}))
    assert any(
        meta["id"] == "beta"
        for meta in resolve_skills_from_query(mention_query, user_info={"id": 1})
    )

    assert not any(
        meta["id"] == "beta"
        for meta in scan_relevant_skills(
            scan_query, user_info={"id": 1}, excluded_skill_ids={"beta"}
        )
    )
    assert not any(
        meta["id"] == "beta"
        for meta in resolve_skills_from_query(
            mention_query, user_info={"id": 1}, excluded_skill_ids={"beta"}
        )
    )


def test_workspace_materialization_skips_forbidden_global_skills(skills_env):
    paths = discover_platform_skill_paths(
        user_info={"id": 1},
        forbidden_skill_ids={"alpha", "beta"},
    )

    assert {os.path.basename(path) for path in paths} == {"alpha"}
    assert str(skills_env.personal_root) in paths[0]


def test_dropping_forbidden_skills_keeps_non_skill_attachments():
    """显式挂载过滤：被禁技能不注入，其他类型附件不受影响。"""
    from app.services.ai.skills.policy import drop_forbidden_skill_items

    items = [
        {"type": "skill", "url": "alpha"},
        {"type": "skill", "url": "beta"},
        {"type": "file", "url": "beta"},
    ]

    assert drop_forbidden_skill_items(items, {"beta"}) == [
        {"type": "skill", "url": "alpha"},
        {"type": "file", "url": "beta"},
    ]
    # 没有禁用项时原样返回，且不修改入参
    assert drop_forbidden_skill_items(items, set()) == items


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _permission_patch(forbidden_skills, *, roles=None):
    perms = SimpleNamespace(
        roles=set(roles or {"user"}),
        permissions=SimpleNamespace(forbidden_skills=list(forbidden_skills)),
    )
    service = AsyncMock()
    service.get_user_permissions = AsyncMock(return_value=perms)
    return patch("app.services.permission_service.PermissionService", return_value=service)


@pytest.mark.asyncio
async def test_policy_ids_come_from_user_permissions():
    from app.services.ai.skills.policy import get_forbidden_skill_ids

    with patch("app.core.orm.AsyncSessionLocal", return_value=_FakeSession()), _permission_patch(
        ["docx", "pdf"]
    ):
        assert await get_forbidden_skill_ids(1) == {"docx", "pdf"}


@pytest.mark.asyncio
async def test_policy_is_disabled_for_admins():
    from app.services.ai.skills.policy import get_forbidden_skill_ids

    with patch("app.core.orm.AsyncSessionLocal", return_value=_FakeSession()), _permission_patch(
        ["docx"], roles={"admin"}
    ):
        assert await get_forbidden_skill_ids(1) == set()


@pytest.mark.asyncio
async def test_policy_failure_degrades_instead_of_raising():
    """策略不可用时不抛异常：与 list_skill_metas 的降级风格一致（返回空集合）。"""
    from app.services.ai.skills.policy import get_forbidden_skill_ids

    with patch("app.core.orm.AsyncSessionLocal", side_effect=RuntimeError("db down")):
        assert await get_forbidden_skill_ids(1) == set()


@pytest.mark.asyncio
async def test_skill_tools_hide_and_reject_forbidden_skills(skills_env, monkeypatch):
    """工具侧：清单不列被禁技能，直接读取被禁技能被拒绝。"""
    from app.services.ai.tools import system_executive_tools
    from app.utils.context import current_user_info

    monkeypatch.setattr(
        "app.services.ai.skills.policy.get_forbidden_skill_ids",
        AsyncMock(return_value={"beta"}),
    )
    token = current_user_info.set({"id": 1, "u_id_val": 1})
    try:
        listing = await system_executive_tools.list_available_skills.func()
        assert '"beta"' not in listing
        assert '"alpha"' in listing

        rejected = await system_executive_tools.read_skill_instruction.func("beta")
        assert "不在当前智能体可用" in rejected
    finally:
        current_user_info.reset(token)


def _seeded_index(workdir) -> dict:
    """平台账本条目（账本在会话根，刻意不在 ``skills/`` 里）。"""
    blob = json.loads((workdir / ".session_skills.json").read_text(encoding="utf-8"))
    return blob.get("skills") or {}


def _seeded_dirs(workdir) -> set[str]:
    """快照里的技能条目（平台铺在 ``skills/.seed``）。"""
    seed_dir = workdir / "skills" / ".seed"
    if not seed_dir.is_dir():
        return set()
    return {path.name for path in seed_dir.iterdir() if path.is_dir()}


def test_preseed_prunes_skills_that_became_forbidden(tmp_path, skills_env):
    """老会话快照必须反向清理：幂等逻辑否则会让被禁技能一直躺在会话目录里，读文件即可绕过。"""
    from app.services.ai.runtime.agentscope.workspace import _preseed_session_skills

    workdir = tmp_path / "session"
    alpha = str(skills_env.global_root / "alpha")
    beta = str(skills_env.global_root / "beta")

    _preseed_session_skills(workdir, [alpha, beta])
    assert len(_seeded_index(workdir)) == 2

    # beta 之后被禁用：清理以**禁用集合**为权威，而不是"当前可用集合"
    _preseed_session_skills(workdir, [alpha], forbidden_skill_ids={"beta"})

    assert len(_seeded_index(workdir)) == 1
    assert len(_seeded_dirs(workdir)) == 1


def test_preseed_prunes_skills_deleted_from_the_platform(tmp_path, skills_env):
    """★ 平台侧被删除（或下架）的技能必须从老会话快照里消失。

    与"白名单收窄"要严格区分：删掉源目录才算"来源消失"，窄白名单不算（见下一个用例）。
    """
    from app.services.ai.runtime.agentscope.workspace import _preseed_session_skills

    workdir = tmp_path / "session"
    alpha = str(skills_env.global_root / "alpha")
    beta = str(skills_env.global_root / "beta")
    _preseed_session_skills(workdir, [alpha, beta])

    assert _seeded_dirs(workdir) == {"用户列表查询", "beta"}

    # 平台把 beta 删了（下架/删除都落到这一条路径），下次播种必须把它清出快照
    import shutil as shutil_module

    shutil_module.rmtree(skills_env.global_root / "beta")
    _preseed_session_skills(workdir, [alpha])

    assert _seeded_dirs(workdir) == {"用户列表查询"}
    ledger = json.loads((workdir / ".session_skills.json").read_text(encoding="utf-8"))
    assert "beta" not in json.dumps(ledger, ensure_ascii=False)


def test_preseed_keeps_snapshot_when_nothing_is_forbidden(tmp_path, skills_env):
    """没有被禁技能时不做任何清理：源目录临时读不到之类的抖动不能误删整份快照。"""
    from app.services.ai.runtime.agentscope.workspace import _preseed_session_skills

    workdir = tmp_path / "session"
    _preseed_session_skills(workdir, [str(skills_env.global_root / "alpha")])

    _preseed_session_skills(workdir, [], forbidden_skill_ids=set())

    assert len(_seeded_index(workdir)) == 1
    assert len(_seeded_dirs(workdir)) == 1


def test_preseed_does_not_delete_another_agents_skills_in_the_same_session(tmp_path, skills_env):
    """同一会话里不同智能体各有自己的可用集合，清理绝不能以"当前可用集合"为权威。

    曾经的实现正是那样：智能体 A 铺了 alpha+beta，随后路由到白名单只有 alpha 的智能体 B，
    B 这次播种会把 beta 目录一并删掉（workdir 是会话级共享的），A 回到会话时技能静默变少。
    """
    from app.services.ai.runtime.agentscope.workspace import _preseed_session_skills

    workdir = tmp_path / "session"
    alpha = str(skills_env.global_root / "alpha")
    beta = str(skills_env.global_root / "beta")

    _preseed_session_skills(workdir, [alpha, beta])  # 智能体 A：全量
    _preseed_session_skills(workdir, [alpha])  # 智能体 B：窄白名单，且没有人被禁

    assert len(_seeded_dirs(workdir)) == 2, "别的智能体的技能不该被删掉"
    assert len(_seeded_index(workdir)) == 2


def test_prune_targets_only_the_forbidden_skill(tmp_path, skills_env):
    """窄白名单与部分禁用同时出现时，只删被禁的那个。"""
    from app.services.ai.runtime.agentscope.workspace import _preseed_session_skills

    workdir = tmp_path / "session"
    alpha = str(skills_env.global_root / "alpha")
    beta = str(skills_env.global_root / "beta")

    _preseed_session_skills(workdir, [alpha, beta])
    _preseed_session_skills(workdir, [alpha], forbidden_skill_ids={"beta"})

    remaining = _seeded_dirs(workdir)
    assert len(remaining) == 1
    kept = workdir / "skills" / ".seed" / next(iter(remaining)) / "SKILL.md"
    assert kept.read_text(encoding="utf-8") == (
        skills_env.global_root / "alpha" / "SKILL.md"
    ).read_text(encoding="utf-8"), "留下的应当正是未被禁的 alpha"


def test_injector_wires_forbidden_skills_into_every_injection_path():
    """注入器每条路（挂载 / 点名 / 扫描）都必须带上禁用集合。

    只做源码级契约：过滤本身落在 list_skill_metas / drop_forbidden_skill_items，行为由上面的
    用例覆盖；这里守住接线——首版实现只改了挂载，把点名与扫描漏掉了。
    注：原先还有第四条「新会话首轮门禁」，该门禁已整体移除（它对所有智能体无条件预载
    using-superpowers，见 tests/services/test_forbidden_policy_consistency.py 第 9 节）。
    """
    source = Path("app/services/ai/skills/injector.py").read_text(encoding="utf-8")

    # 两处 skill_filter 定义（点名、扫描）都要补上禁用集合
    assert source.count('skill_filter["excluded_skill_ids"] =') == 2
    # 挂载路径不走 skill_filter，单独过滤
    assert "drop_forbidden_skill_items(" in source


def test_prune_also_drops_stale_partitions(tmp_path, skills_env):
    """被禁技能被清理时，AgentScope 已装备的分区副本也必须失效。

    分区是 ``.seed`` 的整份拷贝：只清 ``.seed`` 不清分区，被禁技能的副本会继续留在会话
    目录里（模型翻会话目录即可读到），与"禁用"的意图不符。
    """
    from app.services.ai.runtime.agentscope.workspace import _preseed_session_skills

    workdir = tmp_path / "session"
    alpha = str(skills_env.global_root / "alpha")
    beta = str(skills_env.global_root / "beta")
    _preseed_session_skills(workdir, [alpha, beta])

    partition = workdir / "skills" / "default"
    (partition / "beta").mkdir(parents=True)
    (partition / "beta" / "SKILL.md").write_text("partition copy", encoding="utf-8")
    (partition / ".index").write_text("{}", encoding="utf-8")

    _preseed_session_skills(workdir, [alpha], forbidden_skill_ids={"beta"})

    assert not partition.exists(), "过期分区必须一起失效，否则被禁技能副本仍可读"
