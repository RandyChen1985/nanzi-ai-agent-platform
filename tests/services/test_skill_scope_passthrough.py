"""技能来源口径（scope）治理链路：来源不同就不许互相连坐，缓存也必须分维。

病根：聊天接口的 ``SkillMeta``/``ChatFile`` 没声明 ``scope``，Pydantic 2 默认 ``extra='ignore'``
把它静默丢掉；而历史消息是 ``[msg.model_dump() for msg in ...]`` 还原的——丢一次就再也回不来。
下游（挂载正文解析、禁用过滤）只能看到"没有来源标记"，于是个人技能被当成公共技能：按公共库
解析 SKILL.md，还被公共技能的禁用状态连坐。

这里钉住四件事：
1. 治理层按来源分开：同名公共/个人技能不互相连坐（两个方向都测）；
2. 禁用判定的缓存键分"个人 / 公共（含未指定）"两维；
3. 个人口径不查库、恒为空集（个人技能不能用公共技能 id 去查禁用集合）；
4. 注入层按来源解析：个人技能读个人库正文，绝不落到公共库的同名副本。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, PropertyMock, patch

import pytest

from app.api.v1.endpoints.chat import ChatCompletionRequest

pytestmark = pytest.mark.no_infrastructure


# ---------------------------------------------------------------------------
# 夹具：公共库与个人库各有一份同名技能 alpha
# ---------------------------------------------------------------------------

def _write_skill(root: Path, skill_id: str, *, name: str, body: str, enabled: bool = True) -> None:
    skill_dir = root / skill_id
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: 演示 {skill_id} 的能力\n"
        f"enabled: {str(enabled).lower()}\n---\n\n{body}\n",
        encoding="utf-8",
    )


@pytest.fixture()
def skills_dirs(tmp_path, monkeypatch):
    """公共库与个人库各一份 alpha：正文与显示名都不同，用来证明读的是哪一份。"""
    global_root = tmp_path / "global"
    personal_root = tmp_path / "personal"
    _write_skill(global_root, "alpha", name="公共 alpha", body="PUBLIC-BODY-MARKER")
    _write_skill(personal_root, "alpha", name="个人 alpha", body="PERSONAL-BODY-MARKER")

    monkeypatch.setattr(
        "app.services.ai.skill_resolver.get_user_personal_skills_dir",
        lambda _user: str(personal_root),
    )

    with patch("app.core.config.Settings.SKILLS_DIR", new_callable=PropertyMock) as mock_dir:
        mock_dir.return_value = str(global_root)
        yield SimpleNamespace(global_root=global_root, personal_root=personal_root)


@pytest.fixture(autouse=True)
def _clean_sync_cache():
    """同步读缓存是进程内状态：用例之间不能互相污染。"""
    from app.services.ai.skills import policy

    policy.clear_forbidden_skill_cache()
    yield
    policy.clear_forbidden_skill_cache()


def _personal_skill_file() -> dict:
    """前端 handleSelectSkill 发出的附件形态（顶层与 skillMeta 都带 scope）。"""
    return {
        "type": "skill",
        "url": "alpha",
        "filename": "个人 alpha (技能)",
        "size": 0,
        "ext": "skill",
        "scope": "personal",
        "skillMeta": {
            "id": "alpha",
            "name": "个人 alpha",
            "description": "",
            "scope": "personal",
        },
    }


# ---------------------------------------------------------------------------
# 1. 缓存键必须带来源口径
# ---------------------------------------------------------------------------

def test_scoped_cache_key_separates_personal_from_public():
    """个人维与公共维不能命中同一条缓存：同名技能的禁用状态否则会互相覆盖。"""
    from app.services.ai.skills.policy import _scoped_cache_key

    public_key = _scoped_cache_key("42", "agent-a")
    personal_key = _scoped_cache_key("42", "agent-a", "personal")

    assert public_key == "42|agent-a", "公共形态保持既有键格式"
    assert personal_key == "42|agent-a|personal"
    assert personal_key != public_key


def test_scoped_cache_key_keeps_public_spellings_on_one_dimension():
    """``global``/``public``/未指定同属公共维（前者是同一个口径的不同写法）。

    刻意**不**把 ``global`` 单列一维：既有预热调用点都不传 scope，把 global 拆开会造成
    "预热写未指定键、判定读 global 键"，永远未命中 → fail-open 漏禁公共技能。
    """
    from app.services.ai.skills.policy import _scoped_cache_key

    assert _scoped_cache_key("42", None, "global") == _scoped_cache_key("42", None)
    assert _scoped_cache_key("42", None, "public") == _scoped_cache_key("42", None)
    assert _scoped_cache_key("42", None, "personal") != _scoped_cache_key("42", None, "global")


def test_cache_entries_do_not_leak_across_scopes():
    """公共维写了 alpha 的禁用集合，个人维必须仍然查不到（恒为空，且不是同一条）。"""
    from app.services.ai.skills import policy

    policy.remember_forbidden_skill_ids("42", {"alpha"})
    assert policy.get_cached_forbidden_skill_ids("42") == frozenset({"alpha"})
    assert policy.get_cached_forbidden_skill_ids("42", None, "personal") is None

    # 反向：个人维写入（即便调用方传了 id 集合）也必须落成空集，且不污染公共维
    policy.remember_forbidden_skill_ids("42", {"alpha"}, None, "personal")
    assert policy.get_cached_forbidden_skill_ids("42", None, "personal") == frozenset()
    assert policy.get_cached_forbidden_skill_ids("42") == frozenset({"alpha"})

    user_info = {"u_id_val": "42", "id": "42", "user_id": "42"}
    assert policy.is_platform_skill_id_forbidden("alpha", user_info) is True
    assert policy.is_platform_skill_id_forbidden("alpha", user_info, None, "personal") is False


@pytest.mark.asyncio
async def test_prime_personal_scope_does_not_touch_public_cache(skills_dirs):
    """预热也必须按 scope 分维：个人口径预热写空集，公共口径的集合原样保留。"""
    from app.services.ai.skills import policy

    policy.remember_forbidden_skill_ids("42", {"alpha"})
    await policy.prime_forbidden_skill_cache("42", max_age_seconds=0, scope="personal")

    assert policy.get_cached_forbidden_skill_ids("42", None, "personal") == frozenset()
    assert policy.get_cached_forbidden_skill_ids("42") == frozenset({"alpha"})


# ---------------------------------------------------------------------------
# 2. 个人口径不查库、恒为空集
# ---------------------------------------------------------------------------

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
    return service, patch(
        "app.services.permission_service.PermissionService", return_value=service
    )


@pytest.mark.asyncio
async def test_personal_scope_never_queries_the_public_forbidden_set():
    """个人技能不能用公共技能 id 去查禁用集合/白名单——连库都不该查。"""
    from app.services.ai.skills.policy import get_forbidden_skill_ids

    service, permission_patch = _permission_patch(["alpha"])
    with patch("app.core.orm.AsyncSessionLocal", return_value=_FakeSession()), permission_patch:
        assert await get_forbidden_skill_ids(1, None, scope="personal") == set()

    assert service.get_user_permissions.await_count == 0, "个人口径不应触发权限查询"


@pytest.mark.asyncio
async def test_public_scope_still_reads_the_forbidden_set():
    """对照：公共口径照旧查库，返回真实禁用集合（修复不能把公共禁用放松掉）。"""
    from app.services.ai.skills.policy import get_forbidden_skill_ids

    service, permission_patch = _permission_patch(["alpha"])
    with patch("app.core.orm.AsyncSessionLocal", return_value=_FakeSession()), permission_patch:
        assert await get_forbidden_skill_ids(1) == {"alpha"}

    assert service.get_user_permissions.await_count == 1


# ---------------------------------------------------------------------------
# 3. 显式挂载过滤：同名公共/个人技能互不连坐（两个方向）
# ---------------------------------------------------------------------------

def test_forbidden_public_skill_does_not_take_down_its_personal_namesake():
    """公共 alpha 被抓取禁用 → 个人 alpha 照旧可挂载。"""
    from app.services.ai.skills.policy import drop_forbidden_skill_items

    personal = {
        "type": "skill",
        "url": "alpha",
        "filename": "个人 alpha (技能)",
        "scope": "personal",
    }
    public = {"type": "skill", "url": "alpha", "filename": "公共 alpha (技能)", "scope": "global"}

    kept = drop_forbidden_skill_items([personal, public], {"alpha"})

    assert kept == [personal], "只有公共那一份该被丢掉"
    assert drop_forbidden_skill_items([personal], {"alpha"}) == [personal]


def test_forbidden_public_skill_does_not_take_down_a_personal_namesake_with_nested_scope_only():
    """scope 只写在 skillMeta 里（前端/历史都可能）时同样不能连坐。"""
    from app.services.ai.skills.policy import drop_forbidden_skill_items

    nested_personal = {
        "type": "skill",
        "url": "alpha",
        "skillMeta": {"id": "alpha", "name": "个人 alpha", "scope": "personal"},
    }

    assert drop_forbidden_skill_items([nested_personal], {"alpha"}) == [nested_personal]


def test_personal_scope_judgement_does_not_weaken_the_public_filter():
    """反向用例：没有来源标记（或标记为公共）的挂载项必须照旧被拦，不能因为放宽而漏禁。"""
    from app.services.ai.skills.policy import drop_forbidden_skill_items

    without_scope = {"type": "skill", "url": "alpha", "filename": "alpha (技能)"}
    tagged_public = {"type": "skill", "url": "alpha", "scope": "public"}
    unknown_scope = {"type": "skill", "url": "alpha", "scope": "who-knows"}

    assert drop_forbidden_skill_items([without_scope], {"alpha"}) == []
    assert drop_forbidden_skill_items([tagged_public], {"alpha"}) == []
    assert drop_forbidden_skill_items([unknown_scope], {"alpha"}) == []
    # 其他类型附件与未被禁的技能都不受影响
    assert drop_forbidden_skill_items([{"type": "file", "url": "alpha"}], {"alpha"}) == [
        {"type": "file", "url": "alpha"}
    ]


# ---------------------------------------------------------------------------
# 4. 注入层：个人技能按个人库解析元数据与正文
# ---------------------------------------------------------------------------

def test_mount_metadata_is_read_from_the_personal_library(skills_dirs):
    """个人技能的 frontmatter 必须来自个人库，而不是公共库的同名副本。"""
    from app.services.ai.skills.injector import SkillInjector

    personal_meta = SkillInjector._read_skill_meta_for_mount(
        {"type": "skill", "url": "alpha", "scope": "personal"}, "alpha", {"id": 1}
    )
    nested_meta = SkillInjector._read_skill_meta_for_mount(
        {"type": "skill", "url": "alpha", "skillMeta": {"scope": "personal"}}, "alpha", {"id": 1}
    )
    public_meta = SkillInjector._read_skill_meta_for_mount(
        {"type": "skill", "url": "alpha"}, "alpha", {"id": 1}
    )

    assert personal_meta["name"] == "个人 alpha"
    assert nested_meta["name"] == "个人 alpha", "scope 只在 skillMeta 里时也要认"
    assert public_meta["name"] == "公共 alpha"


def test_personal_mount_metadata_does_not_depend_on_request_context(skills_dirs):
    """``user_info`` 显式传入时就不该依赖请求上下文。

    此前这里从 ``app.core.context`` 导入 ``current_user_info``（该模块没有这个名字），
    ImportError 被 except 吞掉 → 个人技能永远读不到自己的 frontmatter（返回空字典）。
    """
    from app.utils.context import current_user_info

    from app.services.ai.skills.injector import SkillInjector

    token = current_user_info.set(None)  # 明确清空上下文：只能靠 user_info 参数
    try:
        meta = SkillInjector._read_skill_meta_for_mount(
            {"type": "skill", "url": "alpha", "scope": "personal"}, "alpha", {"id": 1}
        )
    finally:
        current_user_info.reset(token)

    assert meta.get("name") == "个人 alpha", "个人技能元数据读取不应依赖 contextvar"


@pytest.mark.asyncio
async def test_inject_skills_uses_personal_body_and_is_not_forbidden(skills_dirs):
    """端到端：聊天接口 → 注入器，个人技能既不被公共禁用连坐，也读的是个人库正文。"""
    from app.services.ai.skills.injector import SkillInjector

    request = ChatCompletionRequest(
        messages=[
            {"role": "user", "content": "先问一句"},
            {"role": "assistant", "content": "好的"},
            {"role": "user", "content": "", "files": [_personal_skill_file()]},
        ]
    )
    # 与 chat.py 的 history 还原路径完全一致
    history = [msg.model_dump() for msg in request.messages]
    assert history[-1]["files"][0]["scope"] == "personal"

    policy = {
        "enabled": True,
        "min_score": 0.75,
        "max_count": 1,
        "max_bytes": 65536,
    }
    with patch(
        "app.services.ai.skills.policy.get_forbidden_skill_ids",
        AsyncMock(return_value={"alpha"}),  # 同名公共技能被禁用
    ), patch.object(
        SkillInjector, "resolve_skill_full_load_policy", AsyncMock(return_value=policy)
    ), patch(
        "app.services.ai.skills_stats_service.skills_stats_service.record_activations",
        AsyncMock(),
    ):
        injection = await SkillInjector.inject_skills(
            messages=history,
            user_query="",
            agent_config=SimpleNamespace(agent_id=1),
            user_info={"id": 1},
        )

    joined = "\n".join(injection)
    assert "PERSONAL-BODY-MARKER" in joined, "个人技能正文必须从个人库读到"
    assert "PUBLIC-BODY-MARKER" not in joined, "绝不能落到公共库的同名副本"
    assert "个人 alpha" in joined


@pytest.mark.asyncio
async def test_inject_skills_drops_the_public_namesake_when_forbidden(skills_dirs):
    """反向端到端：被禁的是公共那份（无 scope 标记）时，必须整条不注入。"""
    from app.services.ai.skills.injector import SkillInjector

    request = ChatCompletionRequest(
        messages=[
            {"role": "user", "content": "先问一句"},
            {"role": "assistant", "content": "好的"},
            {
                "role": "user",
                "content": "",
                "files": [
                    {
                        "type": "skill",
                        "url": "alpha",
                        "filename": "公共 alpha (技能)",
                        "size": 0,
                        "ext": "skill",
                        "scope": "global",
                    }
                ],
            },
        ]
    )
    history = [msg.model_dump() for msg in request.messages]

    policy = {"enabled": True, "min_score": 0.75, "max_count": 1, "max_bytes": 65536}
    with patch(
        "app.services.ai.skills.policy.get_forbidden_skill_ids", AsyncMock(return_value={"alpha"})
    ), patch.object(
        SkillInjector, "resolve_skill_full_load_policy", AsyncMock(return_value=policy)
    ), patch(
        "app.services.ai.skills_stats_service.skills_stats_service.record_activations",
        AsyncMock(),
    ):
        injection = await SkillInjector.inject_skills(
            messages=history,
            user_query="",
            agent_config=SimpleNamespace(agent_id=1),
            user_info={"id": 1},
        )

    assert injection == []


def test_scope_loss_would_silently_drop_the_personal_skill():
    """因果链回归：附件一旦没有来源标记（修复前 Pydantic 就是这样丢的），个人技能被误禁。"""
    from app.services.ai.skills.policy import drop_forbidden_skill_items

    lossy_file = {
        "type": "skill",
        "url": "alpha",
        "filename": "个人 alpha (技能)",
        "size": 0,
        "ext": "skill",
    }

    assert drop_forbidden_skill_items([lossy_file], {"alpha"}) == [], (
        "没有 scope 时下游只能当公共技能处理——这正是修复前个人技能被静默丢弃的原因"
    )


@pytest.mark.asyncio
async def test_inject_skills_survives_unresolvable_personal_dir(monkeypatch, caplog):
    """个人目录解析失败时只跳过这一条，不能让整轮注入崩掉。

    旧实现用 ``candidate_paths[-1]`` 取兜底路径：当挂载项是个人技能、但解析不出个人目录
    （无身份 / 工作区根异常）且没有显式路径时，候选链是空列表 → ``IndexError``。而该异常
    会把本轮**所有**技能（含公共技能与自动匹配）一起带走，只剩一条 warning。
    """
    from app.services.ai.skills.injector import SkillInjector

    monkeypatch.setattr(
        "app.services.ai.skill_resolver.get_user_personal_skills_dir",
        lambda user_info: None,
    )

    messages = [
        {
            "role": "user",
            "content": "",
            "files": [
                {
                    "type": "skill",
                    "url": "ghost",
                    "filename": "幽灵技能 (技能)",
                    "size": 0,
                    "ext": "skill",
                    "scope": "personal",
                }
            ],
        }
    ]

    with patch.object(
        SkillInjector,
        "resolve_skill_full_load_policy",
        AsyncMock(return_value={"enabled": False, "min_score": 0.75, "max_count": 0, "max_bytes": 1024}),
    ), patch(
        "app.services.ai.skills_stats_service.skills_stats_service.record_activations",
        AsyncMock(),
    ), patch(
        "app.services.ai.skills.policy.get_forbidden_skill_ids",
        AsyncMock(return_value=set()),
    ):
        injection = await SkillInjector.inject_skills(
            messages=messages,
            user_query="",
            agent_config=SimpleNamespace(agent_id=1),
            user_info=None,
        )

    assert isinstance(injection, list)  # 不抛 IndexError
    # 确认真的走到了那一道守卫（否则用例会因为前置早退而「空过」）
    assert any("personal skills dir unresolved" in r.getMessage() for r in caplog.records)
