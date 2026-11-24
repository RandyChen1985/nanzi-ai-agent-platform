"""个人技能源历史遗留 ``.seed`` 布局的回归测试。

背景：K8s 沙箱在补齐技能子挂载之前只挂载了整个用户工作区，AgentScope 的技能根
``${workdir}/skills`` 恰好就是个人技能源目录，它 ``initialize()`` 时的布局迁移把平铺技能
**搬移**进了 ``<user>/skills/.seed/<skill_id>/``。而平台所有主机侧枚举都会跳过点开头目录
（``skill_resolver._scan_skill_dir``、portal ``list_personal_skills``、工作台计数……），
于是技能在「技能列表 / 个人技能中心 / 工作台」里集体隐形，表现为 `list_available_skills`
返回 ``[]``。

这里锁住两条不变式：
1. 平铺与 ``.seed`` 两种布局都能读到（平铺优先，同名冲突时以平铺为准）；
2. 编辑类操作会定位到技能**真实所在**目录，而不是在平铺位置新建副本。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

# ── 工具函数 ─────────────────────────────────────────────────────────────


def _write_skill(root: Path, skill_id: str, *, seed: bool = False, body: str = "") -> Path:
    """在 ``root/<id>`` 或 ``root/.seed/<id>`` 下造一个最小可用技能。"""
    base = root / ".seed" if seed else root
    skill_dir = base / skill_id
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {skill_id}\ndescription: {skill_id} 描述\n---\n\n# {skill_id}\n{body}",
        encoding="utf-8",
    )
    return skill_dir


def _load_repair_module():
    path = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "repair_personal_skills_layout.py"
    )
    spec = importlib.util.spec_from_file_location("repair_personal_skills_layout", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


# ── 1. 目录枚举 helper ───────────────────────────────────────────────────


def test_iter_personal_skill_dirs_reads_flat_and_seed(tmp_path):
    from app.services.ai.skill_resolver import iter_personal_skill_dirs

    root = tmp_path / "skills"
    flat = _write_skill(root, "flat-skill")
    seeded = _write_skill(root, "seed-skill", seed=True)

    found = dict(iter_personal_skill_dirs(str(root)))

    assert set(found) == {"flat-skill", "seed-skill"}
    assert Path(found["flat-skill"]) == flat
    assert Path(found["seed-skill"]) == seeded


def test_iter_personal_skill_dirs_prefers_flat_on_conflict(tmp_path):
    from app.services.ai.skill_resolver import iter_personal_skill_dirs

    root = tmp_path / "skills"
    flat = _write_skill(root, "dup", body="flat")
    _write_skill(root, "dup", seed=True, body="seeded")

    found = dict(iter_personal_skill_dirs(str(root)))

    assert Path(found["dup"]) == flat, "同名时必须以规范平铺布局为准"


def test_iter_personal_skill_dirs_ignores_non_skill_entries(tmp_path):
    from app.services.ai.skill_resolver import iter_personal_skill_dirs

    root = tmp_path / "skills"
    _write_skill(root, "real-skill")
    (root / ".seed" / "no-skill-md").mkdir(parents=True)  # 缺 SKILL.md
    (root / "not-a-dir.txt").write_text("x", encoding="utf-8")
    (root / ".hidden").mkdir()

    # 判定口径与 _scan_skill_dir 一致：只看「目录 + 合法 id」，缺 SKILL.md 的实现
    # 会交给 parse_skill_frontmatter 兜底，因此这里仍会列出
    assert [name for name, _ in iter_personal_skill_dirs(str(root))] == [
        "no-skill-md",
        "real-skill",
    ]


def test_resolve_personal_skill_dir_falls_back_to_seed(tmp_path):
    from app.services.ai.skill_resolver import resolve_personal_skill_dir

    root = tmp_path / "skills"
    seeded = _write_skill(root, "seed-skill", seed=True)
    flat = _write_skill(root, "flat-skill")

    assert Path(resolve_personal_skill_dir(str(root), "seed-skill")) == seeded
    assert Path(resolve_personal_skill_dir(str(root), "flat-skill")) == flat
    assert resolve_personal_skill_dir(str(root), "missing-skill") is None


# ── 2. 技能列表（list_available_skills 的数据源）────────────────────────


def test_list_skill_metas_reads_seeded_personal_skill(tmp_path, monkeypatch):
    from app.services.ai import skill_resolver

    personal_root = tmp_path / "u1" / "skills"
    _write_skill(personal_root, "seed-skill", seed=True)
    monkeypatch.setattr(
        skill_resolver,
        "get_user_personal_skills_dir",
        lambda user_info=None: str(personal_root),
    )
    skill_resolver.clear_skill_meta_cache()

    metas = skill_resolver.list_skill_metas(user_info={"user_id": "u1"})

    personal = [m for m in metas if m.get("scope") == "personal"]
    assert [m["id"] for m in personal] == ["seed-skill"]
    assert personal[0]["skill_md_path"].endswith(".seed/seed-skill/SKILL.md")


def test_list_skill_metas_personal_merges_flat_and_seed(tmp_path, monkeypatch):
    from app.services.ai import skill_resolver

    personal_root = tmp_path / "u1" / "skills"
    _write_skill(personal_root, "flat-skill")
    _write_skill(personal_root, "seed-skill", seed=True)
    monkeypatch.setattr(
        skill_resolver,
        "get_user_personal_skills_dir",
        lambda user_info=None: str(personal_root),
    )
    skill_resolver.clear_skill_meta_cache()

    metas = skill_resolver.list_skill_metas(user_info={"user_id": "u1"})
    personal_ids = {m["id"] for m in metas if m.get("scope") == "personal"}

    assert personal_ids == {"flat-skill", "seed-skill"}


def test_public_skill_root_does_not_scan_seed(tmp_path, monkeypatch):
    """公共技能库是平台维护的规范布局，不接受 ``.seed``（避免误把沙箱副本当技能）。"""
    from app.services.ai import skill_resolver

    public_root = tmp_path / "public"
    public_root.mkdir()
    _write_skill(public_root, "seeded-in-public", seed=True)

    # allow_seed 默认为 False：公共技能根不认 .seed
    assert skill_resolver._scan_skill_dir(str(public_root), "global") == []


# ── 3. 个人技能中心（编辑类操作必须命中真实目录）─────────────────────────


def test_validate_personal_skill_path_resolves_seeded_skill(tmp_path, monkeypatch):
    from app.api.portal.endpoints import personal_skills

    personal_root = tmp_path / "u1" / "skills"
    seeded = _write_skill(personal_root, "seed-skill", seed=True)
    monkeypatch.setattr(
        personal_skills, "_get_personal_skills_root", lambda user_info: str(personal_root)
    )

    resolved = personal_skills._validate_personal_skill_path({"user_id": "u1"}, "seed-skill")

    assert Path(resolved) == seeded, "必须指向 .seed 里的真实目录，否则编辑会新建副本"


def test_validate_personal_skill_path_uses_flat_for_new_skill(tmp_path, monkeypatch):
    from app.api.portal.endpoints import personal_skills

    personal_root = tmp_path / "u1" / "skills"
    personal_root.mkdir(parents=True)
    monkeypatch.setattr(
        personal_skills, "_get_personal_skills_root", lambda user_info: str(personal_root)
    )

    resolved = personal_skills._validate_personal_skill_path({"user_id": "u1"}, "brand-new")

    assert Path(resolved) == personal_root / "brand-new"


# ── 4. 工作台个人技能计数 ────────────────────────────────────────────────


def test_count_personal_skills_counts_seeded_skill(tmp_path, monkeypatch):
    from app.services import workbench_home_service
    from app.services.ai import skill_resolver

    personal_root = tmp_path / "u1" / "skills"
    _write_skill(personal_root, "seed-skill", seed=True)
    monkeypatch.setattr(
        skill_resolver,
        "get_user_personal_skills_dir",
        lambda user_info=None: str(personal_root),
    )

    assert workbench_home_service._count_personal_skills({"user_id": "u1"}) == 1


# ── 5. 存量迁移脚本 ──────────────────────────────────────────────────────


def test_repair_layout_dry_run_then_apply(tmp_path, capsys):
    module = _load_repair_module()
    workspaces = tmp_path / "agent_workspaces"
    personal_root = workspaces / "u1" / "skills"
    _write_skill(personal_root, "seed-skill", seed=True)

    assert module.main(["--root", str(workspaces)]) == 0
    assert (personal_root / ".seed" / "seed-skill").is_dir(), "dry-run 不得改动文件"

    assert module.main(["--root", str(workspaces), "--apply"]) == 0
    capsys.readouterr()
    assert (personal_root / "seed-skill" / "SKILL.md").is_file()
    assert not (personal_root / ".seed").exists(), "搬空后的 .seed 应被清理"


def test_repair_layout_never_overwrites_conflict(tmp_path):
    module = _load_repair_module()
    workspaces = tmp_path / "agent_workspaces"
    personal_root = workspaces / "u1" / "skills"
    _write_skill(personal_root, "dup", body="flat-original")
    _write_skill(personal_root, "dup", seed=True, body="seeded-copy")

    assert module.main(["--root", str(workspaces), "--apply"]) == 0

    content = (personal_root / "dup" / "SKILL.md").read_text(encoding="utf-8")
    assert "flat-original" in content, "冲突时绝不能覆盖平铺那份"
    assert (personal_root / ".seed" / "dup").is_dir(), "冲突项应留在 .seed 等人工处理"


def test_repair_layout_is_idempotent(tmp_path):
    module = _load_repair_module()
    workspaces = tmp_path / "agent_workspaces"
    personal_root = workspaces / "u1" / "skills"
    _write_skill(personal_root, "seed-skill", seed=True)

    assert module.main(["--root", str(workspaces), "--apply"]) == 0
    assert module.main(["--root", str(workspaces), "--apply"]) == 0
    assert (personal_root / "seed-skill" / "SKILL.md").is_file()


# ── 6. 用户报告的直接症状：list_available_skills 返回 [] ────────────────


@pytest.mark.asyncio
async def test_list_available_skills_surfaces_seeded_personal_skill(tmp_path, monkeypatch):
    """技能都在 ``.seed`` 里时，``list_available_skills`` 不能返回空列表。

    这是用户在生产 K8s 环境报告的直接症状；该工具与沙箱策略无关，纯粹取决于
    ``list_skill_metas`` 能否看到历史遗留的 seed 布局。
    """
    from app.services.ai import skill_resolver
    from app.services.ai.tools.system_executive_tools import list_available_skills

    personal_root = tmp_path / "u1" / "skills"
    _write_skill(personal_root, "seed-skill", seed=True)
    monkeypatch.setattr(
        skill_resolver,
        "get_user_personal_skills_dir",
        lambda user_info=None: str(personal_root),
    )
    skill_resolver.clear_skill_meta_cache()

    async def _no_forbidden_skill_ids():
        return []

    monkeypatch.setattr(
        "app.services.ai.skills.policy.get_forbidden_skill_ids",
        _no_forbidden_skill_ids,
    )

    raw = await list_available_skills.ainvoke({})
    data = json.loads(raw)

    # 返回结构是 {total, returned, truncated, skills}：调用方（与模型）必须能区分
    # 「一共几条」与「这次给了几条」，否则截断会被误读成「该技能不存在」。
    assert data["total"] == len(data["skills"])
    assert data["truncated"] is False
    personal = [item for item in data["skills"] if item.get("scope") == "personal"]
    assert [item["id"] for item in personal] == ["seed-skill"], (
        "个人技能躺在 .seed 里时，工具必须仍然列出它，而不是返回 []"
    )


# ── 7. 技能路径发现 / 弹框清单 / 创建就地更新（第二轮补漏）──────────────


def test_discover_platform_skill_paths_includes_seeded_personal_skill(tmp_path, monkeypatch):
    """技能进入 ``skill_paths`` 才会被预铺并注入模型上下文——这比「列表为空」更致命。

    ``discover_platform_skill_paths`` 原先用 ``os.listdir`` 自己枚举个人技能源，
    因此 ``.seed`` 里的技能根本不会进入 ``LocalWorkspace(skill_paths=...)``。
    """
    from types import SimpleNamespace

    from app.services.ai import skill_resolver
    from app.services.ai.runtime.agentscope import workspace as ws_mod

    public_root = tmp_path / "public_skills"
    public_root.mkdir()
    _write_skill(public_root, "public-skill")
    personal_root = tmp_path / "u1" / "skills"
    _write_skill(personal_root, "seed-skill", seed=True)

    # settings.SKILLS_DIR 是只读 property，无法 setattr；函数内是
    # `from app.core.config import settings`，故替换模块属性即可生效
    monkeypatch.setattr(
        "app.core.config.settings", SimpleNamespace(SKILLS_DIR=str(public_root))
    )
    monkeypatch.setattr(
        skill_resolver,
        "get_user_personal_skills_dir",
        lambda user_info=None: str(personal_root),
    )

    paths = ws_mod.discover_platform_skill_paths(user_info={"user_id": "u1"})

    assert "seed-skill" in {Path(p).name for p in paths}


def test_agent_scoped_personal_skill_metas_reads_seeded_skill(tmp_path, monkeypatch):
    """智能体「个人技能」分区清单也要能看到 seed 布局。"""
    from app.services import agent_scoped_restrictions
    from app.services.ai import skill_resolver

    personal_root = tmp_path / "u1" / "skills"
    _write_skill(personal_root, "seed-skill", seed=True)
    monkeypatch.setattr(
        skill_resolver,
        "get_user_personal_skills_dir",
        lambda user_info=None: str(personal_root),
    )

    metas = agent_scoped_restrictions.list_personal_skill_metas({"user_id": "u1"})

    assert [m["id"] for m in metas] == ["seed-skill"]


def test_create_skills_refuses_silent_overwrite_of_seeded_skill(tmp_path, monkeypatch):
    """默认不得覆盖 ``.seed`` 里的同名技能，也不得在平铺处再造一份副本。

    覆盖语义在第三轮 review 后收紧：``create_skills`` 原来是静默 upsert（直接整份覆盖
    却回报「创建成功」），现在必须显式 ``overwrite=true`` 才写盘。这里验证「被拒」与
    「没有副产物」两件事同时成立——被拒之后如果跑去平铺位置新建，反而更糟。
    """
    from app.services.ai import skill_resolver
    from app.services.ai.tools.system_executive_tools import create_skills
    from app.utils.context import current_user_info

    personal_root = tmp_path / "u1" / "skills"
    seeded = _write_skill(personal_root, "seed-skill", seed=True, body="old-body")
    monkeypatch.setattr(
        skill_resolver,
        "get_user_personal_skills_dir",
        lambda user_info=None: str(personal_root),
    )
    monkeypatch.setattr(
        "app.services.ai.tools.system_executive_tools.validate_safe_path",
        lambda path: path,
    )

    token = current_user_info.set({"user_id": "u1", "user_name": "u1", "role": "user"})
    try:
        result = create_skills.invoke(
            {
                "skill_id": "seed-skill",
                "name": "Seed Skill",
                "description": "d",
                "skill_md_content": "# 新内容",
                "scope": "personal",
            }
        )
    finally:
        current_user_info.reset(token)

    assert "NANZI_SKILL_EXISTS:" in result
    assert "NANZI_SKILL_CREATED:" not in result
    # 原内容必须原样保留
    assert "old-body" in (seeded / "SKILL.md").read_text(encoding="utf-8")
    assert "新内容" not in (seeded / "SKILL.md").read_text(encoding="utf-8")
    assert not (personal_root / "seed-skill").exists(), "不得在平铺位置再造一份副本"


def test_create_skills_updates_seeded_skill_in_place(tmp_path, monkeypatch):
    """显式 ``overwrite=true`` 时，同名 ``.seed`` 技能必须就地更新，不得另造副本。"""
    from app.services.ai import skill_resolver
    from app.services.ai.tools.system_executive_tools import create_skills
    from app.utils.context import current_user_info

    personal_root = tmp_path / "u1" / "skills"
    seeded = _write_skill(personal_root, "seed-skill", seed=True, body="old-body")
    monkeypatch.setattr(
        skill_resolver,
        "get_user_personal_skills_dir",
        lambda user_info=None: str(personal_root),
    )

    # validate_safe_path 只放行 data/ 与 settings.SKILLS_DIR；生产的个人技能源位于
    # <project>/data/agent_workspaces/<key>/skills（含 .seed 子路径）本就在放行范围内，
    # 这里 tmp_path 不在 data 下，故单独放行以免测到无关的路径护栏
    monkeypatch.setattr(
        "app.services.ai.tools.system_executive_tools.validate_safe_path",
        lambda path: path,
    )

    token = current_user_info.set(
        {"user_id": "u1", "user_name": "u1", "role": "user"}
    )
    try:
        result = create_skills.invoke(
            {
                "skill_id": "seed-skill",
                "name": "Seed Skill",
                "description": "d",
                "skill_md_content": "# 新内容",
                "scope": "personal",
                "overwrite": True,
            }
        )
    finally:
        current_user_info.reset(token)

    assert "NANZI_SKILL_UPDATED:" in result
    assert "新内容" in (seeded / "SKILL.md").read_text(encoding="utf-8")
    assert not (personal_root / "seed-skill").exists(), "不得在平铺位置再造一份副本"


# ── 8. 历史遗留 .seed 里的「公共技能副本」必须排除（第二轮审计 P0）──────


def test_seed_platform_copies_are_excluded_from_personal_skills(tmp_path, monkeypatch):
    """``.seed`` 里与公共技能同 ID 的目录是打包副本，必须排除。

    否则个人技能会因「同 ID 个人优先」把公共技能冻结成用户的私有旧版。
    """
    from types import SimpleNamespace

    from app.services.ai import skill_resolver

    platform_root = tmp_path / "platform_skills"
    platform_root.mkdir()
    _write_skill(platform_root, "brainstorming")  # 公共技能
    personal_root = tmp_path / "u1" / "skills"
    _write_skill(personal_root, "my-own-skill")  # 真·个人技能（平铺）
    _write_skill(personal_root, "brainstorming", seed=True)  # 遗留的公共副本

    monkeypatch.setattr(
        "app.core.config.settings", SimpleNamespace(SKILLS_DIR=str(platform_root))
    )

    assert [n for n, _ in skill_resolver.iter_personal_skill_dirs(personal_root)] == [
        "my-own-skill"
    ]
    # 关掉排除即可看到那份副本确实躺在 .seed 里（证明前提成立）
    assert sorted(
        n
        for n, _ in skill_resolver.iter_personal_skill_dirs(
            personal_root, exclude_platform_copies=False
        )
    ) == ["brainstorming", "my-own-skill"]
    # 定位同样拒绝，避免把公共副本当个人技能来读写
    assert skill_resolver.resolve_personal_skill_dir(personal_root, "brainstorming") is None
    assert skill_resolver.resolve_personal_skill_dir(
        personal_root, "my-own-skill"
    ) == str(personal_root / "my-own-skill")


def test_flat_same_id_personal_skill_still_overrides_platform(tmp_path, monkeypatch):
    """**平铺**的同 ID 个人技能是平台支持的合法状态，不能被排除。

    ``list_skill_metas`` 明确「若 ID 冲突，个人技能优先覆盖全局同 ID 技能」，
    ``app.utils.skill_metadata`` 也说明「个人技能与公共技能可能同名」。按 ID 一概
    排除会把这个合法状态整条链路删掉（列表不显示、计数不算、不进 skill_paths）。
    """
    from types import SimpleNamespace

    from app.services.ai import skill_resolver

    platform_root = tmp_path / "platform_skills"
    platform_root.mkdir()
    _write_skill(platform_root, "brainstorming", body="平台版")
    personal_root = tmp_path / "u1" / "skills"
    _write_skill(personal_root, "brainstorming", body="我的个人版")  # 平铺，同名
    _write_skill(personal_root, "my-own-skill", seed=True)

    monkeypatch.setattr(
        "app.core.config.settings", SimpleNamespace(SKILLS_DIR=str(platform_root))
    )
    monkeypatch.setattr(
        skill_resolver,
        "get_user_personal_skills_dir",
        lambda user_info=None: str(personal_root),
    )
    skill_resolver.clear_skill_meta_cache()

    assert sorted(
        n for n, _ in skill_resolver.iter_personal_skill_dirs(personal_root)
    ) == ["brainstorming", "my-own-skill"]
    assert skill_resolver.resolve_personal_skill_dir(
        personal_root, "brainstorming"
    ) == str(personal_root / "brainstorming")

    metas = skill_resolver.list_skill_metas()
    personal = [m for m in metas if m.get("scope") == "personal"]
    assert sorted(m["id"] for m in personal) == ["brainstorming", "my-own-skill"]
    assert "brainstorming" not in [m["id"] for m in metas if m.get("scope") == "global"], (
        "个人技能必须覆盖同 ID 公共技能（平台既定语义）"
    )


def test_skill_meta_cache_invalidated_when_layout_moves(tmp_path, monkeypatch):
    """技能在平铺与 ``.seed`` 之间搬移时必须让缓存失效（指纹要含布局）。

    ``shutil.move`` 同文件系统内保留 mtime/size，若指纹只看 mtime/size，
    缓存会在 TTL 内返回指向旧路径的条目。
    """
    from app.services.ai import skill_resolver

    personal_root = tmp_path / "u1" / "skills"
    flat = _write_skill(personal_root, "moving-skill")

    first = skill_resolver._scan_skill_dir_cached(
        str(personal_root), "personal", allow_seed=True
    )
    assert [m["id"] for m in first] == ["moving-skill"]

    # 模拟 AgentScope 的搬移：平铺 -> .seed（保留 mtime/size）
    seed_dir = personal_root / ".seed"
    seed_dir.mkdir()
    (seed_dir / "moving-skill").parent.mkdir(parents=True, exist_ok=True)
    import shutil as _shutil

    _shutil.move(str(flat), str(seed_dir / "moving-skill"))

    second = skill_resolver._scan_skill_dir_cached(
        str(personal_root), "personal", allow_seed=True
    )
    assert [m["id"] for m in second] == ["moving-skill"]
    assert Path(second[0]["skill_md_path"]) == seed_dir / "moving-skill" / "SKILL.md", (
        "搬移后缓存必须失效并返回新路径，否则会报「技能缺少 SKILL.md」"
    )


# ── 9. 迁移脚本的 fail-closed 与原子性 ──────────────────────────────────


def test_repair_layout_refuses_platform_skill_ids(tmp_path):
    """与公共技能同 ID 的目录默认拒绝搬移（搬了会冻结公共技能更新）。"""
    module = _load_repair_module()
    personal_root = tmp_path / "u1" / "skills"
    _write_skill(personal_root, "brainstorming", seed=True)
    _write_skill(personal_root, "my-own-skill", seed=True)

    stats, unhandled = module.repair_user(
        str(personal_root),
        apply=True,
        platform_ids={"brainstorming"},
        allow_platform_ids=False,
        prune_remaining=False,
    )

    assert stats["moved"] == 1
    assert stats["skipped"] == 1
    assert (personal_root / "my-own-skill").is_dir()
    assert (personal_root / ".seed" / "brainstorming").is_dir(), "被拒绝的副本必须原地不动"
    assert unhandled == ["brainstorming"]


def test_repair_layout_fails_closed_when_platform_list_unknown(tmp_path):
    """读不到公共技能库时默认拒绝，而不是猜着搬（fail-closed）。"""
    module = _load_repair_module()
    personal_root = tmp_path / "u1" / "skills"
    _write_skill(personal_root, "my-own-skill", seed=True)

    stats, unhandled = module.repair_user(
        str(personal_root),
        apply=True,
        platform_ids=None,
        allow_platform_ids=False,
        prune_remaining=False,
    )

    assert stats["moved"] == 0
    assert stats["skipped"] == 1
    assert (personal_root / ".seed" / "my-own-skill").is_dir()
    assert unhandled == ["my-own-skill"]


def test_repair_layout_skips_invalid_skill_id(tmp_path):
    """平台按 ``^[a-zA-Z0-9_-]+$`` 识别技能；非法名搬过去依然不可见，故跳过。"""
    module = _load_repair_module()
    personal_root = tmp_path / "u1" / "skills"
    _write_skill(personal_root, "会议纪要助手", seed=True)
    _write_skill(personal_root, "legal-skill", seed=True)

    stats, unhandled = module.repair_user(
        str(personal_root),
        apply=True,
        platform_ids=set(),
        allow_platform_ids=False,
        prune_remaining=False,
    )

    assert stats["moved"] == 1
    assert stats["skipped"] == 1
    assert (personal_root / "legal-skill").is_dir()
    assert (personal_root / ".seed" / "会议纪要助手").is_dir()
    assert unhandled == ["会议纪要助手"]
