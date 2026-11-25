"""AI 聊天触发 Skills 全链路加固与优化回归测试（无基础设施依赖）。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.schemas.agent import ChatConfig
from app.services.ai.intent_service import (
    looks_like_platform_self_service_query,
    looks_like_skill_execution,
)
from app.services.ai.pipeline.context import PipelineContext
from app.services.ai.pipeline.steps.context_step import ContextStep
from app.services.ai.pipeline.steps.route_step import RouteStep
from app.services.ai.request_decision import RequestSource, resolve_request_decision
from app.services.ai.skill_resolver import (
    clear_skill_meta_cache,
    lexical_relevance_score,
    load_skill_md_content,
    resolve_skills_from_query,
    scan_relevant_skills,
)
from app.services.ai.skills.injector import (
    SkillInjector,
    clear_recent_session_skills_cache,
)
from app.services.ai.tool_nudge_policy import (
    extract_active_skill_state,
    resolve_tool_nudge,
)
from app.utils.skill_metadata import (
    build_skill_attachment_hint,
    extract_plain_user_query,
    sanitize_disabled_skill_context,
)

pytestmark = pytest.mark.no_infrastructure


def _write_skill(
    root,
    skill_id: str,
    *,
    name: str,
    description: str,
    body: str = "# Workflow\n按步骤执行。",
    enabled: bool = True,
    extra_files: dict[str, str] | None = None,
):
    skill_dir = root / skill_id
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\nenabled: {str(enabled).lower()}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    for rel, content in (extra_files or {}).items():
        target = skill_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return skill_dir


def _fake_tool(name: str, description: str = "tool"):
    return SimpleNamespace(name=name, description=description)


async def _default_skill_cfg(key, default=None):
    return {
        "skill_auto_full_load_enabled": "true",
        "skill_auto_full_load_min_score": "0.75",
        "skill_auto_full_load_max_count": "1",
        "skill_auto_full_load_max_bytes": "65536",
        "skill_auto_scan_enabled": "true",
        "skill_auto_scan_min_score": "0.45",
        "skill_auto_scan_max_results": "1",
    }.get(key, default)


# ---------------------------------------------------------------------------
# P0-1: 纯用户问题剥离 + 平台自助误判防护
# ---------------------------------------------------------------------------


def test_extract_plain_user_query_strips_platform_attachment_block_only():
    hint = build_skill_attachment_hint(
        "weekly-report",
        meta_override={"name": "周报生成", "description": "生成周报"},
    )
    attached = f"帮我整理本周进展\n\n---\n\n{hint}"
    assert extract_plain_user_query(attached) == "帮我整理本周进展"

    # 只挂载技能未输入文字
    assert extract_plain_user_query(f"\n\n---\n\n{hint}") == ""

    # 用户自己的 Markdown 水平线不应被误截
    markdown_text = "第一段内容\n\n---\n\n第二段用户正文"
    assert extract_plain_user_query(markdown_text) == markdown_text


def test_skill_execution_not_misclassified_as_platform_self_help():
    for exec_query in (
        "使用用户列表查询技能查询一次",
        "用周报生成技能整理本周工作",
        "使用工单创建技能提一个报修单",
        "调用 pdf-helper 技能解析这份文档",
        "执行代码审查技能检查这个 PR",
        "use weekly-report skill for this week",
    ):
        assert looks_like_skill_execution(exec_query) is True, exec_query
        assert looks_like_platform_self_service_query(exec_query) is False, exec_query
        decision = resolve_request_decision(exec_query)
        assert decision.source != RequestSource.PLATFORM_SELF_HELP, exec_query

    # 真正的创建/保存技能元操作仍被识别为 meta_action
    from app.services.ai.intent_service import looks_like_meta_action

    for create_query in (
        "帮我创建一个周报技能",
        "把上面的流程保存为技能",
        "把刚才的步骤生成一个技能",
    ):
        assert looks_like_meta_action(create_query) is True, create_query
        assert looks_like_skill_execution(create_query) is False, create_query

    # 真正的平台自助咨询仍应走 PLATFORM_SELF_HELP
    for help_query in (
        "如何使用技能",
        "技能怎么挂载",
        "那如何安装 skills 技能呢",
        "技能如何启用和配置",
    ):
        assert looks_like_skill_execution(help_query) is False, help_query
        assert looks_like_platform_self_service_query(help_query) is True, help_query
        decision = resolve_request_decision(help_query)
        assert decision.source == RequestSource.PLATFORM_SELF_HELP, help_query


def test_mounted_skill_attachment_hint_does_not_trigger_platform_docs_nudge():
    hint = build_skill_attachment_hint(
        "weather-query",
        meta_override={"name": "天气查询", "description": "查询实时天气"},
    )
    raw_query = f"帮我分析一下当前数据\n\n---\n\n{hint}"
    tools = [
        _fake_tool("Grep", "搜索文本"),
        _fake_tool("Read", "读取文件"),
        _fake_tool("read_skill_instruction", "读取技能指令"),
    ]

    decision = resolve_request_decision(raw_query)
    assert decision.source != RequestSource.PLATFORM_SELF_HELP

    nudge = resolve_tool_nudge(raw_query, tools)
    assert nudge is None or nudge.tool_name != "Grep"


# ---------------------------------------------------------------------------
# P0-2: 显式挂载与会话固定必须遵守 skills_custom 白名单
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mounted_and_scoped_skills_respect_skills_custom_allowlist(tmp_path, monkeypatch):
    clear_skill_meta_cache()
    global_dir = tmp_path / "global"
    personal_dir = tmp_path / "personal"
    _write_skill(global_dir, "allowed-pub", name="白名单技能", description="允许使用")
    _write_skill(global_dir, "blocked-pub", name="非白名单技能", description="不应允许")
    _write_skill(personal_dir, "my-personal", name="个人技能", description="用户自建")

    monkeypatch.setattr(
        "app.services.ai.skill_resolver.get_user_personal_skills_dir",
        lambda _u: str(personal_dir),
    )

    agent_config = ChatConfig(
        agent_id="expert-1",
        agent_name="expert",
        model_name="test-model",
        temperature=0,
        system_prompt="base",
        tools=[],
        skills_custom=True,
        skills=["allowed-pub"],
    )

    with (
        patch("app.core.config.Settings.SKILLS_DIR", str(global_dir)),
        patch("app.services.config_service.ConfigService.get", side_effect=_default_skill_cfg),
        patch(
            "app.services.ai.skills.policy.get_forbidden_skill_ids",
            AsyncMock(return_value=set()),
        ),
        patch(
            "app.services.ai.skills_stats_service.skills_stats_service.record_activations",
            AsyncMock(),
        ),
    ):
        injections = await SkillInjector.inject_skills(
            messages=[
                {
                    "role": "user",
                    "content": "帮我处理一下",
                    "files": [
                        {"type": "skill", "url": "blocked-pub", "filename": "非白名单技能 (技能)", "scope": "global"},
                        {"type": "skill", "url": "allowed-pub", "filename": "白名单技能 (技能)", "scope": "global"},
                        {"type": "skill", "url": "my-personal", "filename": "个人技能 (技能)", "scope": "personal"},
                    ],
                }
            ],
            user_query="帮我处理一下",
            agent_config=agent_config,
            user_info={"user_id": 1, "user_name": "alice"},
        )

    joined = "\n".join(injections)
    assert "allowed-pub" in joined
    assert "my-personal" in joined
    assert "blocked-pub" not in joined


# ---------------------------------------------------------------------------
# P0-3: tool_nudge 感知已激活技能状态（摘要态引导读全文 / 预载态防抢占）
# ---------------------------------------------------------------------------


def test_tool_nudge_guides_summary_only_skill_and_protects_preloaded_skill():
    from app.services.ai.agent_prompts import AgentServicePrompts

    tools = [
        _fake_tool("todo_write", "记录多步骤任务清单"),
        _fake_tool("Grep", "搜索文件"),
        _fake_tool("web_search", "联网搜索最新新闻与公网实时资讯"),
        _fake_tool("read_skill_instruction", "读取技能完整指令"),
    ]

    # 必须使用真实 AgentServicePrompts.skills_profile() 产物：
    # 头部说明文本身含「已预载完整指令」字样，不能误把仅摘要 prompt 判成 has_full=True！
    summary_system = AgentServicePrompts.skills_profile(
        [
            AgentServicePrompts.skill_summary_injection_block(
                "周报生成", "weekly-report", "整理工作周报"
            )
        ]
    )
    has_active, has_full, summary_ids = extract_active_skill_state(summary_system)
    assert has_active is True
    assert has_full is False
    assert summary_ids == ("weekly-report",)

    # 显式点名 + 摘要态 -> 强制优先 read_skill_instruction
    nudge_exec = resolve_tool_nudge(
        "使用周报生成技能整理本周工作",
        tools,
        has_active_skills=has_active,
        has_full_preloaded_skill=has_full,
        summary_only_skill_ids=summary_ids,
    )
    assert nudge_exec is not None
    assert nudge_exec.tool_name == "read_skill_instruction"
    assert nudge_exec.force_first_call is True

    # 低分扫描 + 摘要态 -> 软提示 read_skill_instruction
    nudge_scan = resolve_tool_nudge(
        "整理一下周报和预算情况",
        tools,
        has_active_skills=has_active,
        has_full_preloaded_skill=has_full,
        summary_only_skill_ids=summary_ids,
    )
    assert nudge_scan is not None
    assert nudge_scan.tool_name == "read_skill_instruction"
    assert nudge_scan.force_first_call is False

    # 真实全文预载块 -> has_full=True
    preloaded_system = AgentServicePrompts.skills_profile(
        [
            AgentServicePrompts.skill_full_instruction_block(
                "周报生成", "weekly-report", "整理工作周报", "# Steps\n按步骤输出周报。"
            )
        ]
    )
    has_active2, has_full2, summary_ids2 = extract_active_skill_state(preloaded_system)
    assert has_active2 is True and has_full2 is True and summary_ids2 == ()

    # 全文预载 + 多步请求 -> 不被 todo_write 抢占首步
    nudge_preloaded = resolve_tool_nudge(
        "首先汇总材料，然后生成周报，最后导出 markdown 报告",
        tools,
        has_active_skills=has_active2,
        has_full_preloaded_skill=has_full2,
        summary_only_skill_ids=summary_ids2,
    )
    assert nudge_preloaded is None

    # 全文预载 + 强外部实时事实（联网搜新闻）-> 仍允许促发 web_search
    nudge_web = resolve_tool_nudge(
        "帮我联网搜索最新的 AI 新闻资讯",
        tools,
        has_active_skills=has_active2,
        has_full_preloaded_skill=has_full2,
        summary_only_skill_ids=summary_ids2,
    )
    assert nudge_web is not None
    assert nudge_web.tool_name == "web_search"

    # 全文预载 + 显式平台部署文档求助 -> 仍允许促发 Grep
    nudge_explicit_docs = resolve_tool_nudge(
        "怎么部署 nanzi 平台的 docker 部署文档在哪里",
        tools,
        has_active_skills=has_active2,
        has_full_preloaded_skill=has_full2,
        summary_only_skill_ids=summary_ids2,
    )
    assert nudge_explicit_docs is not None
    assert nudge_explicit_docs.tool_name == "Grep"


@pytest.mark.asyncio
async def test_mounted_skill_still_allows_mention_while_resource_scope_pins_it(tmp_path):
    """回归验证：挂载 A + 文本点名 B -> 两者都注入；仅会话固定 B -> 不再额外点名。"""
    clear_skill_meta_cache()
    _write_skill(tmp_path, "skill-a", name="合同审查", description="审查合同风险")
    _write_skill(tmp_path, "skill-b", name="用户列表查询", description="查询用户列表")

    cfg = ChatConfig(
        agent_id="sys-agent-chat",
        agent_name="assistant",
        model_name="test-model",
        temperature=0,
        system_prompt="base",
        tools=[],
    )

    with (
        patch("app.core.config.Settings.SKILLS_DIR", str(tmp_path)),
        patch("app.services.config_service.ConfigService.get", side_effect=_default_skill_cfg),
        patch("app.services.ai.skills.policy.get_forbidden_skill_ids", AsyncMock(return_value=set())),
        patch(
            "app.services.ai.skills_stats_service.skills_stats_service.record_activations",
            AsyncMock(),
        ),
    ):
        # 场景 1：仅显式挂载 A + 口头点名 B -> A 与 B 均应注入
        both = await SkillInjector.inject_skills(
            messages=[
                {
                    "role": "user",
                    "content": "顺便使用用户列表查询技能查一下",
                    "files": [{"type": "skill", "url": "skill-a", "filename": "合同审查 (技能)", "scope": "global"}],
                }
            ],
            user_query="顺便使用用户列表查询技能查一下",
            agent_config=cfg,
        )
        joined_both = "\n".join(both)
        assert "skill-a" in joined_both
        assert "skill-b" in joined_both

        # 场景 2：会话固定 B（resource_scope.skills）-> 范围锁定为 B，不因文本点名 A 而额外注入 A
        pinned = await SkillInjector.inject_skills(
            messages=[{"role": "user", "content": "使用合同审查技能看看"}],
            user_query="使用合同审查技能看看",
            agent_config=cfg,
            resource_scope={"skills": [{"id": "skill-b", "name": "用户列表查询", "scope": "global"}]},
        )
        joined_pinned = "\n".join(pinned)
        assert "skill-b" in joined_pinned
        assert "skill-a" not in joined_pinned


# ---------------------------------------------------------------------------
# P1-4: 中文 2-gram 长句扫描召回 + 口头点名扩展 + skills_custom 专家自动扫描
# ---------------------------------------------------------------------------


def test_lexical_scan_handles_chinese_long_sentences_and_english_mentions(tmp_path):
    clear_skill_meta_cache()
    _write_skill(
        tmp_path,
        "weather-query",
        name="天气预报",
        description="查询全国各城市实时天气预报与空气质量指数",
    )
    _write_skill(
        tmp_path,
        "weekly-report",
        name="周报生成流程",
        description="周报生成 汇总 风险 计划",
    )

    with patch("app.core.config.Settings.SKILLS_DIR", str(tmp_path)):
        # 中文无标点长句（旧算法因整句合成单 token 导致得分 0）
        results = scan_relevant_skills("帮我查一下今天北京的天气情况", max_results=1, min_score=0.45)
        assert len(results) == 1
        assert results[0]["id"] == "weather-query"

        # 保持低置信扫描落在摘要区 [0.45, 0.75)
        low_score = lexical_relevance_score(
            "周报 预算",
            {"id": "weekly-report", "name": "周报生成流程", "description": "周报生成 汇总 风险 计划"},
        )
        assert 0.45 <= low_score < 0.75

        # 泛化 2 字核心词（如「代码助手」「数据查询」）不得在普通长句中误触发扫描
        generic_false_positive = lexical_relevance_score(
            "帮我看看这段 Python 代码为什么在并发下会超时",
            {"id": "code-helper", "name": "代码助手", "description": "辅助生成项目脚手架模板"},
        )
        assert generic_false_positive < 0.45

        # 新增「调用 ... 技能」与英文「use ... skill」点名识别
        m1 = resolve_skills_from_query("调用天气预报技能查一下上海")
        assert [m["id"] for m in m1] == ["weather-query"]
        m2 = resolve_skills_from_query("use weather-query skill to check Beijing")
        assert [m["id"] for m in m2] == ["weather-query"]


@pytest.mark.asyncio
async def test_custom_skill_expert_agent_enables_scoped_auto_scan(tmp_path):
    clear_skill_meta_cache()
    _write_skill(
        tmp_path,
        "weather-query",
        name="天气预报",
        description="查询实时天气预报与空气质量",
        body="# 天气工作流\n查询目标城市实时天气。",
    )
    _write_skill(
        tmp_path,
        "other-skill",
        name="订单审计",
        description="审计订单流水",
    )
    expert_config = ChatConfig(
        agent_id="expert-weather",
        agent_name="weather-expert",
        model_name="test-model",
        temperature=0,
        system_prompt="base",
        tools=[],
        skills_custom=True,
        skills=["weather-query"],
    )

    with (
        patch("app.core.config.Settings.SKILLS_DIR", str(tmp_path)),
        patch("app.services.config_service.ConfigService.get", side_effect=_default_skill_cfg),
        patch(
            "app.services.ai.skills.policy.get_forbidden_skill_ids",
            AsyncMock(return_value=set()),
        ),
        patch(
            "app.services.ai.skills_stats_service.skills_stats_service.record_activations",
            AsyncMock(),
        ),
    ):
        injections = await SkillInjector.inject_skills(
            messages=[{"role": "user", "content": "帮我查一下今天北京天气预报"}],
            user_query="帮我查一下今天北京天气预报",
            agent_config=expert_config,
        )

    assert len(injections) == 1
    assert "weather-query" in injections[0]


# ---------------------------------------------------------------------------
# P1-5: 预载复用头尾裁剪 + 附带技能资源清单
# ---------------------------------------------------------------------------


def test_preload_clips_head_tail_and_attaches_resource_tree(tmp_path):
    clear_skill_meta_cache()
    body = "HEAD_RULE_START\n" + ("中段详述" * 2000) + "\n<HARD-GATE>TAIL_RED_LINE_RULE</HARD-GATE>"
    _write_skill(
        tmp_path,
        "rich-skill",
        name="复合技能",
        description="带参考规则与脚本",
        body=body,
        extra_files={
            "references/rules.md": "# rules",
            "scripts/calc.py": "print(1)",
        },
    )

    with patch("app.core.config.Settings.SKILLS_DIR", str(tmp_path)):
        content = load_skill_md_content(
            "rich-skill",
            max_bytes=1200,
            include_resource_tree=True,
        )

    assert content is not None
    assert "HEAD_RULE_START" in content
    assert "TAIL_RED_LINE_RULE" in content
    assert "中间省略" in content
    assert "references/rules.md" in content
    assert "scripts/calc.py" in content


# ---------------------------------------------------------------------------
# P1-6: 停用（enabled=false）技能挂载清洗
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_context_step_sanitizes_disabled_skill_mount_before_history_write(tmp_path):
    clear_skill_meta_cache()
    _write_skill(
        tmp_path,
        "paused-skill",
        name="暂停技能",
        description="已停用",
        enabled=False,
    )
    hint = build_skill_attachment_hint(
        "paused-skill",
        meta_override={"name": "暂停技能", "description": "已停用"},
    )
    ctx = PipelineContext(
        messages=[
            {
                "role": "user",
                "content": f"帮我跑一下\n\n---\n\n{hint}",
                "files": [
                    {
                        "type": "skill",
                        "url": "paused-skill",
                        "filename": "暂停技能 (技能)",
                        "scope": "global",
                    }
                ],
            }
        ],
        user_info={"user_id": 1, "user_name": "alice", "role": "user"},
        conversation_id="conv-disabled",
    )

    from app.services.ai.memory_service import memory_service

    with (
        patch("app.core.config.Settings.SKILLS_DIR", str(tmp_path)),
        patch.object(memory_service, "get_history", AsyncMock(return_value=[])),
        patch.object(memory_service, "add_message", AsyncMock()) as add_message,
        patch(
            "app.services.ai.pipeline.steps.context_step._apply_context_snapshot",
            AsyncMock(side_effect=lambda h, **_k: h),
        ),
        patch("app.services.config_service.ConfigService.get", AsyncMock(return_value="60")),
        patch(
            "app.services.ai.skills.policy.get_forbidden_skill_ids",
            AsyncMock(return_value=set()),
        ),
    ):
        async for _chunk in ContextStep(None).run(ctx):
            pass

    stored_content = add_message.await_args.args[3]
    stored_files = add_message.await_args.kwargs["files"]
    assert stored_files == []
    assert "已停用" in stored_content
    assert "已随本轮上下文预载" not in stored_content


def test_sanitize_disabled_skill_context_preserves_user_text():
    hint = build_skill_attachment_hint("s1", meta_override={"name": "技能一"})
    msgs = [
        {
            "role": "user",
            "content": f"用户原话\n\n---\n\n{hint}",
            "files": [{"type": "skill", "url": "s1", "filename": "技能一 (技能)"}],
        }
    ]
    assert sanitize_disabled_skill_context(msgs, {"s1"}) == 1
    assert msgs[0]["files"] == []
    assert "用户原话" in msgs[0]["content"]
    assert "已停用" in msgs[0]["content"]


# ---------------------------------------------------------------------------
# P1-7 & P2-8: 统计口径收口 + 权限查询去重 + 会话固定日志
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stats_skip_summary_only_scan_and_record_dynamic_read(tmp_path):
    clear_skill_meta_cache()
    _write_skill(
        tmp_path,
        "weekly-report",
        name="周报生成流程",
        description="周报生成 汇总 风险 计划",
        extra_files={"references/guide.md": "detail"},
    )
    main_config = ChatConfig(
        agent_id="sys-agent-chat",
        agent_name="assistant",
        model_name="test-model",
        temperature=0,
        system_prompt="base",
        tools=[],
    )
    record_mock = AsyncMock()
    forbidden_mock = AsyncMock(return_value=set())

    with (
        patch("app.core.config.Settings.SKILLS_DIR", str(tmp_path)),
        patch("app.services.config_service.ConfigService.get", side_effect=_default_skill_cfg),
        patch("app.services.ai.skills.policy.get_forbidden_skill_ids", forbidden_mock),
        patch(
            "app.services.ai.skills_stats_service.skills_stats_service.record_activations",
            record_mock,
        ),
    ):
        injections = await SkillInjector.inject_skills(
            messages=[{"role": "user", "content": "周报 预算"}],
            user_query="周报 预算",
            agent_config=main_config,
        )

    assert len(injections) == 1
    assert "未预载" in injections[0]
    # 仅摘要扫描候选不应计入激活统计；且单轮只查 1 次 forbidden 权限
    assert record_mock.await_count == 0
    assert forbidden_mock.await_count == 1

    # 动态调用 read_skill_instruction 读 SKILL.md 时计入统计，读子资源 path 不重复计入
    from app.services.ai.tools.system_executive_tools import read_skill_instruction

    with (
        patch("app.core.config.Settings.SKILLS_DIR", str(tmp_path)),
        patch("app.services.ai.skills.policy.get_forbidden_skill_ids", AsyncMock(return_value=set())),
        patch(
            "app.services.ai.skills_stats_service.skills_stats_service.record_activations",
            record_mock,
        ),
    ):
        res_md = await read_skill_instruction.ainvoke({"skill_id": "weekly-report"})
        assert "技能读取成功" in res_md
        assert record_mock.await_count == 1
        assert record_mock.await_args.args[0] == ["weekly-report"]

        res_ref = await read_skill_instruction.ainvoke(
            {"skill_id": "weekly-report", "path": "references/guide.md"}
        )
        assert "detail" in res_ref
        assert record_mock.await_count == 1


@pytest.mark.asyncio
async def test_resource_scope_skill_emits_log_callback(tmp_path):
    clear_skill_meta_cache()
    _write_skill(tmp_path, "fixed-skill", name="固定技能", description="会话固定")
    logs = []
    config = ChatConfig(
        agent_id="sys-agent-chat",
        agent_name="assistant",
        model_name="test-model",
        temperature=0,
        system_prompt="base",
        tools=[],
    )
    with (
        patch("app.core.config.Settings.SKILLS_DIR", str(tmp_path)),
        patch("app.services.config_service.ConfigService.get", side_effect=_default_skill_cfg),
        patch(
            "app.services.ai.skills.policy.get_forbidden_skill_ids",
            AsyncMock(return_value=set()),
        ),
        patch(
            "app.services.ai.skills_stats_service.skills_stats_service.record_activations",
            AsyncMock(),
        ),
    ):
        await SkillInjector.inject_skills(
            messages=[{"role": "user", "content": "开始处理"}],
            user_query="开始处理",
            agent_config=config,
            resource_scope={"skills": [{"id": "fixed-skill", "name": "固定技能", "scope": "global"}]},
            skills_log_callback=lambda sid, name, msg: logs.append((sid, name, msg)),
        )

    assert len(logs) == 1
    assert logs[0][0] == "fixed-skill"
    assert "已预载完整" in logs[0][2]


# ---------------------------------------------------------------------------
# P2-8: 路由落定具体 agent_id 后补做智能体级禁用二次清洗
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_route_step_sanitizes_agent_scoped_forbidden_skill_after_route():
    hint = build_skill_attachment_hint("agent-blocked", meta_override={"name": "被禁技能"})
    ctx = PipelineContext(
        messages=[
            {
                "role": "user",
                "content": f"帮我查一下\n\n---\n\n{hint}",
                "files": [
                    {
                        "type": "skill",
                        "url": "agent-blocked",
                        "filename": "被禁技能 (技能)",
                        "scope": "global",
                    }
                ],
            }
        ],
        user_info={"user_id": 7, "user_name": "bob", "role": "user"},
        lane_user_id="7",
        conversation_id="conv-route-clean",
        agent_id=None,  # 预路由前尚未确定智能体
    )
    ctx.user_query = ctx.messages[0]["content"]
    ctx.shared_state["context_source_history"] = [dict(ctx.messages[0])]
    ctx.shared_state["context_user_message"] = dict(ctx.messages[0])

    resolved_cfg = ChatConfig(
        agent_id="sys-agent-chat",
        agent_name="assistant",
        model_name="test-model",
        temperature=0,
        system_prompt="base",
        tools=[],
    )

    class _FakeAgentService:
        def _start_route_resolution(self, *, route_events, resolve_kwargs):
            import asyncio

            async def _resolve():
                return resolved_cfg, None, 5.0, None

            return asyncio.create_task(_resolve())

        async def _resolve_runtime_model_info_safe(self, **_kwargs):
            return SimpleNamespace(
                effective_model_id="gpt-4o",
                configured_model="gpt-4o",
                source="agent",
                resolution_status="resolved",
                max_context_tokens=128000,
                context_window=128000,
                max_tokens=128000,
            )

        async def _rebuild_context_for_resolved_model(self, *, shared_state, **_kwargs):
            # 模拟 ContextCompactor.rebuild_context_for_resolved_model 从
            # context_source_history + context_user_message 重建最终消息列表。
            return [shared_state["context_user_message"]]

    prime_mock = AsyncMock()
    with (
        patch("app.services.ai.skills.policy.prime_forbidden_skill_cache", prime_mock),
        patch(
            "app.services.ai.skills.policy.get_forbidden_skill_ids",
            AsyncMock(return_value={"agent-blocked"}),
        ),
    ):
        async for _chunk in RouteStep(_FakeAgentService()).run(ctx):
            pass

    prime_mock.assert_awaited_once()
    assert prime_mock.await_args.kwargs["agent_id"] == "sys-agent-chat"
    assert ctx.messages[0]["files"] == []
    assert "已被管理员禁用" in ctx.messages[0]["content"]
    assert "技能 ID：agent-blocked" not in ctx.messages[0]["content"]
    assert "已被管理员禁用" in ctx.shared_state["context_source_history"][0]["content"]
    assert "已被管理员禁用" in ctx.shared_state["context_user_message"]["content"]


# ---------------------------------------------------------------------------
# P2-9: 单次激活技能的紧邻追问轮轻量延续
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recent_session_skill_carries_over_as_summary_on_followup(tmp_path):
    clear_skill_meta_cache()
    clear_recent_session_skills_cache()
    _write_skill(
        tmp_path,
        "monthly-audit",
        name="月度对账流程",
        description="核对各渠道月度收支差异",
    )
    config = ChatConfig(
        agent_id="sys-agent-chat",
        agent_name="assistant",
        model_name="test-model",
        temperature=0,
        system_prompt="base",
        tools=[],
    )
    user_info = {"user_id": 9, "user_name": "carol"}

    with (
        patch("app.core.config.Settings.SKILLS_DIR", str(tmp_path)),
        patch("app.services.config_service.ConfigService.get", side_effect=_default_skill_cfg),
        patch(
            "app.services.ai.skills.policy.get_forbidden_skill_ids",
            AsyncMock(return_value=set()),
        ),
        patch(
            "app.services.ai.skills_stats_service.skills_stats_service.record_activations",
            AsyncMock(),
        ),
    ):
        # 第 1 轮：显式点名激活技能
        first = await SkillInjector.inject_skills(
            messages=[{"role": "user", "content": "使用月度对账流程技能核对 10 月数据"}],
            user_query="使用月度对账流程技能核对 10 月数据",
            agent_config=config,
            user_info=user_info,
            conversation_id="conv-followup",
        )
        assert any("已预载完整指令" in block for block in first)

        # 第 2 轮：紧邻追问（未重新挂载也未重复点名）-> 以摘要态轻量延续
        logs = []
        second = await SkillInjector.inject_skills(
            messages=[{"role": "user", "content": "按刚才的方式再跑一下 11 月的"}],
            user_query="按刚才的方式再跑一下 11 月的",
            agent_config=config,
            user_info=user_info,
            conversation_id="conv-followup",
            skills_log_callback=lambda sid, name, msg: logs.append((sid, name, msg)),
        )
        assert len(second) == 1
        assert "monthly-audit" in second[0]
        assert "未预载；执行前必须调用 read_skill_instruction" in second[0]
        assert any("延续上一轮技能" in item[2] for item in logs)

        # 问候/无关新话题不应误延续
        greeting = await SkillInjector.inject_skills(
            messages=[{"role": "user", "content": "你好"}],
            user_query="你好",
            agent_config=config,
            user_info=user_info,
            conversation_id="conv-followup",
        )
        assert greeting == []
