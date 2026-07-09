from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.schemas.agent import ChatConfig
from app.services.ai.context_compaction import COMPACTION_MARKER
from app.services.ai.agent_service import AgentService
from app.services.ai.turn_classifier import TurnClassification, TurnType


class _NoopExecutor:
    async def execute(self, messages):
        yield {"content": "ok"}


class _ExternalPendingExecutor:
    async def execute(self, messages):
        yield {
            "type": "external_execution_required",
            "status": "pending",
            "id": "call_ext",
            "external_execution_request_id": "ext_req_1",
        }


async def _noop_audit(*args, **kwargs):
    return None


@asynccontextmanager
async def _noop_lane_hold(*args, **kwargs):
    yield False


async def _skill_config_get(key, default=None):
    values = {
        "skill_auto_full_load_enabled": "true",
        "skill_auto_full_load_min_score": "0.75",
        "skill_auto_full_load_max_count": "1",
        "skill_auto_full_load_max_bytes": "65536",
        "skill_auto_scan_enabled": "true",
        "skill_auto_scan_min_score": "0.45",
        "skill_auto_scan_max_results": "1",
    }
    return values.get(key, default)


async def _skill_config_get_strict_full_load(key, default=None):
    values = {
        "skill_auto_full_load_enabled": "true",
        "skill_auto_full_load_min_score": "0.95",
        "skill_auto_full_load_max_count": "1",
        "skill_auto_full_load_max_bytes": "65536",
        "skill_auto_scan_enabled": "true",
        "skill_auto_scan_min_score": "0.45",
        "skill_auto_scan_max_results": "1",
    }
    return values.get(key, default)


@pytest.fixture(autouse=True)
def _disable_quota_block_message():
    with patch(
        "app.services.ai.agent_service.AgentService._quota_block_message",
        AsyncMock(return_value=None),
    ):
        yield


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_inject_skills_preloads_full_instruction_for_mounted_skill(tmp_path):
    service = AgentService()
    skill_dir = tmp_path / "weekly-report"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: 周报生成流程\n"
        "description: 根据材料生成工作周报\n"
        "---\n\n"
        "# 完整流程\n"
        "先提取本周完成事项，再整理风险与下周计划。\n",
        encoding="utf-8",
    )
    agent_config = ChatConfig(
        agent_id="agent-1",
        agent_name="helper",
        model_name="test-model",
        temperature=0,
        system_prompt="Base prompt",
        tools=[],
    )

    with (
        patch("app.core.config.Settings.SKILLS_DIR", str(tmp_path)),
        patch("app.services.config_service.ConfigService.get", side_effect=_skill_config_get),
    ):
        injections = await service._inject_skills(
            messages=[
                {
                    "role": "user",
                    "content": "帮我写周报",
                    "files": [{"type": "skill", "url": "weekly-report", "filename": "周报生成流程 (技能)"}],
                }
            ],
            user_query="帮我写周报",
            agent_config=agent_config,
        )

    joined = "\n".join(injections)
    assert "已预载完整指令" in joined
    assert "先提取本周完成事项" in joined
    assert "未预载；执行前必须调用 read_skill_instruction" not in joined


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_inject_skills_preloads_full_instruction_for_high_confidence_scan(tmp_path):
    service = AgentService()
    skill_dir = tmp_path / "weekly-report"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: 周报生成流程\n"
        "description: 周报生成流程\n"
        "---\n\n"
        "# 完整流程\n"
        "输出本周进展、问题风险、下周计划。\n",
        encoding="utf-8",
    )
    agent_config = ChatConfig(
        agent_id="sys-agent-chat",
        agent_name="assistant",
        model_name="test-model",
        temperature=0,
        system_prompt="Base prompt",
        tools=[],
    )

    with (
        patch("app.core.config.Settings.SKILLS_DIR", str(tmp_path)),
        patch("app.services.config_service.ConfigService.get", side_effect=_skill_config_get),
    ):
        injections = await service._inject_skills(
            messages=[{"role": "user", "content": "周报生成流程"}],
            user_query="周报生成流程",
            agent_config=agent_config,
        )

    joined = "\n".join(injections)
    assert "已预载完整指令" in joined
    assert "输出本周进展、问题风险、下周计划" in joined


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_inject_skills_preloads_full_instruction_for_mentioned_skill(tmp_path):
    service = AgentService()
    skill_dir = tmp_path / "meeting-minutes"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: 会议纪要整理\n"
        "description: 把会议记录整理成结论和待办\n"
        "---\n\n"
        "# 完整流程\n"
        "先提炼会议结论，再按负责人拆分待办。\n",
        encoding="utf-8",
    )
    agent_config = ChatConfig(
        agent_id="agent-1",
        agent_name="helper",
        model_name="test-model",
        temperature=0,
        system_prompt="Base prompt",
        tools=[],
    )

    with (
        patch("app.core.config.Settings.SKILLS_DIR", str(tmp_path)),
        patch("app.services.config_service.ConfigService.get", side_effect=_skill_config_get),
    ):
        injections = await service._inject_skills(
            messages=[{"role": "user", "content": "使用会议纪要整理技能处理这段记录"}],
            user_query="使用会议纪要整理技能处理这段记录",
            agent_config=agent_config,
        )

    joined = "\n".join(injections)
    assert "已预载完整指令" in joined
    assert "先提炼会议结论，再按负责人拆分待办" in joined


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_inject_skills_keeps_summary_for_lower_confidence_scan(tmp_path):
    service = AgentService()
    skill_dir = tmp_path / "weekly-report"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: 周报生成流程\n"
        "description: 周报生成 汇总 风险 计划\n"
        "---\n\n"
        "# 完整流程\n"
        "这段完整指令不应在低置信扫描时预载。\n",
        encoding="utf-8",
    )
    agent_config = ChatConfig(
        agent_id="sys-agent-chat",
        agent_name="assistant",
        model_name="test-model",
        temperature=0,
        system_prompt="Base prompt",
        tools=[],
    )

    with (
        patch("app.core.config.Settings.SKILLS_DIR", str(tmp_path)),
        patch("app.services.config_service.ConfigService.get", side_effect=_skill_config_get),
    ):
        injections = await service._inject_skills(
            messages=[{"role": "user", "content": "周报 预算"}],
            user_query="周报 预算",
            agent_config=agent_config,
        )

    joined = "\n".join(injections)
    assert "未预载；执行前必须调用 read_skill_instruction" in joined
    assert "这段完整指令不应在低置信扫描时预载" not in joined


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_inject_skills_forces_using_superpowers_on_first_turn_scan(tmp_path):
    service = AgentService()
    skill_dir = tmp_path / "using-superpowers"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: using-superpowers\n"
        "description: superpowers 流程规范\n"
        "---\n\n"
        "# 完整流程\n"
        "会话开始时先检查并使用相关技能。\n",
        encoding="utf-8",
    )
    agent_config = ChatConfig(
        agent_id="sys-agent-chat",
        agent_name="assistant",
        model_name="test-model",
        temperature=0,
        system_prompt="Base prompt",
        tools=[],
    )

    with (
        patch("app.core.config.Settings.SKILLS_DIR", str(tmp_path)),
        patch("app.services.config_service.ConfigService.get", side_effect=_skill_config_get_strict_full_load),
    ):
        injections = await service._inject_skills(
            messages=[{"role": "user", "content": "superpowers"}],
            user_query="superpowers",
            agent_config=agent_config,
        )

    joined = "\n".join(injections)
    assert "已预载完整指令" in joined
    assert "会话开始时先检查并使用相关技能" in joined


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_inject_skills_forces_using_superpowers_on_first_turn_even_without_lexical_match(tmp_path):
    service = AgentService()
    skill_dir = tmp_path / "using-superpowers"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: using-superpowers\n"
        "description: Use when starting any conversation\n"
        "---\n\n"
        "# 完整流程\n"
        "首轮普通中文问题也应该启用该流程。\n",
        encoding="utf-8",
    )
    agent_config = ChatConfig(
        agent_id="sys-agent-chat",
        agent_name="assistant",
        model_name="test-model",
        temperature=0,
        system_prompt="Base prompt",
        tools=[],
    )

    with (
        patch("app.core.config.Settings.SKILLS_DIR", str(tmp_path)),
        patch("app.services.config_service.ConfigService.get", side_effect=_skill_config_get_strict_full_load),
    ):
        injections = await service._inject_skills(
            messages=[{"role": "user", "content": "帮我看看这个问题"}],
            user_query="帮我看看这个问题",
            agent_config=agent_config,
        )

    joined = "\n".join(injections)
    assert "已预载完整指令" in joined
    assert "首轮普通中文问题也应该启用该流程" in joined


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_inject_skills_forces_using_superpowers_on_first_turn_greeting(tmp_path):
    service = AgentService()
    skill_dir = tmp_path / "using-superpowers"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: using-superpowers\n"
        "description: Use when starting any conversation\n"
        "---\n\n"
        "# 完整流程\n"
        "问候首轮也应该启用该流程。\n",
        encoding="utf-8",
    )
    agent_config = ChatConfig(
        agent_id="sys-agent-chat",
        agent_name="assistant",
        model_name="test-model",
        temperature=0,
        system_prompt="Base prompt",
        tools=[],
    )

    with (
        patch("app.core.config.Settings.SKILLS_DIR", str(tmp_path)),
        patch("app.services.config_service.ConfigService.get", side_effect=_skill_config_get_strict_full_load),
    ):
        injections = await service._inject_skills(
            messages=[{"role": "user", "content": "你好"}],
            user_query="你好",
            agent_config=agent_config,
        )

    joined = "\n".join(injections)
    assert "已预载完整指令" in joined
    assert "问候首轮也应该启用该流程" in joined


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_inject_skills_does_not_force_using_superpowers_after_first_turn(tmp_path):
    service = AgentService()
    skill_dir = tmp_path / "using-superpowers"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: using-superpowers\n"
        "description: superpowers 流程规范\n"
        "---\n\n"
        "# 完整流程\n"
        "非首轮不应因为特殊门禁被强制预载。\n",
        encoding="utf-8",
    )
    agent_config = ChatConfig(
        agent_id="sys-agent-chat",
        agent_name="assistant",
        model_name="test-model",
        temperature=0,
        system_prompt="Base prompt",
        tools=[],
    )

    with (
        patch("app.core.config.Settings.SKILLS_DIR", str(tmp_path)),
        patch("app.services.config_service.ConfigService.get", side_effect=_skill_config_get_strict_full_load),
    ):
        injections = await service._inject_skills(
            messages=[
                {"role": "user", "content": "你好"},
                {"role": "agent", "content": "您好"},
                {"role": "user", "content": "superpowers"},
            ],
            user_query="superpowers",
            agent_config=agent_config,
        )

    joined = "\n".join(injections)
    assert "未预载；执行前必须调用 read_skill_instruction" in joined
    assert "非首轮不应因为特殊门禁被强制预载" not in joined


def test_skill_log_chunk_titles_distinguish_enabled_and_candidate_flow():
    enabled = AgentService._build_skill_log_chunk(
        "weekly-report",
        "周报生成流程",
        "已预载完整 SKILL.md 指令，本轮可直接按该流程执行。",
    )
    assert enabled["id"] == "skill_enabled_weekly-report"
    assert enabled["title"] == "已启用流程: 周报生成流程"
    assert "技能" not in enabled["title"]

    candidate = AgentService._build_skill_log_chunk(
        "weekly-report",
        "周报生成流程",
        "已注入摘要；模型须调用 read_skill_instruction 读取 SKILL.md 全文后再执行。",
    )
    assert candidate["id"] == "skill_candidate_weekly-report"
    assert candidate["title"] == "已识别候选流程: 周报生成流程"

    fallback = AgentService._build_skill_log_chunk("weekly-report", "周报生成流程", "")
    assert fallback["id"] == "skill_candidate_weekly-report"
    assert fallback["title"] == "已识别候选流程: 周报生成流程"
    assert "已识别候选流程" in fallback["details"]


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_chat_stream_injects_skill_discovery_hint_into_system_prompt():
    service = AgentService()
    agent_config = ChatConfig(
        agent_id="agent-1",
        agent_name="helper",
        agent_display_name="Helper",
        model_name="test-model",
        temperature=0,
        system_prompt="Base prompt",
        tools=[],
    )

    captured = {}

    async def fake_dispatch(config, *args, **kwargs):
        captured["system_prompt"] = config.system_prompt
        return _NoopExecutor()

    with (
        patch(
            "app.services.ai.context_manager.AgentContextManager.resolve_agent_config",
            AsyncMock(return_value=(agent_config, None)),
        ),
        patch(
            "app.services.ai.context_manager.AgentContextManager.setup_context",
            AsyncMock(),
        ),
        patch(
            "app.services.memory_config_service.MemoryConfigService.get_bool",
            AsyncMock(return_value=False),
        ),
        patch(
            "app.services.ai.memory_service.ltm_service.fetch_memory",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.ai.agent_service.memory_service.get_history",
            AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.ai.agent_service.memory_service.add_message",
            AsyncMock(),
        ),
        patch(
            "app.services.config_service.ConfigService.get",
            AsyncMock(return_value="20"),
        ),
        patch(
            "app.services.ai.turn_classifier.resolve_turn_for_session",
            AsyncMock(return_value=(
                TurnClassification(turn_type=TurnType.GENERAL, reasoning="test"),
                None,
                0.0,
            )),
        ),
        patch(
            "app.services.ai.agent_service.AgentDispatcher.dispatch",
            side_effect=fake_dispatch,
        ),
        patch(
            "app.services.ai.agent_service.AuditManager.log_transaction",
            side_effect=_noop_audit,
        ),
        patch("app.core.config.Settings.SKILLS_DIR", "/app/data/skills"),
    ):
        chunks = []
        async for chunk in service.chat_completion_stream(
            [{"role": "user", "content": "帮我处理一个问题"}],
            user_info={"user_id": "1", "role": "admin", "user_name": "admin"},
            enable_multi_agent=False,
        ):
            chunks.append(chunk)

    assert any(chunk.get("content") == "ok" for chunk in chunks)
    assert "/app/data/skills" in captured["system_prompt"]
    assert "list_available_skills" in captured["system_prompt"]
    assert "read_skill_instruction" in captured["system_prompt"]
    assert "扫描该目录下各技能的 SKILL.md" not in captured["system_prompt"]
    assert captured["system_prompt"].endswith("Base prompt")


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_chatbi_agent_defers_turn_classification_to_data_executor():
    service = AgentService()
    agent_config = ChatConfig(
        agent_id="agent-data",
        agent_name="chatbi",
        agent_display_name="ChatBI",
        model_name="test-model",
        temperature=0,
        system_prompt="Data prompt",
        tools=[],
        capabilities=["data_query"],
    )

    captured = {}

    async def fake_dispatch(config, *args, **kwargs):
        captured["shared_turn"] = kwargs.get("shared_turn")
        return _NoopExecutor()

    with (
        patch(
            "app.services.ai.context_manager.AgentContextManager.resolve_agent_config",
            AsyncMock(return_value=(agent_config, None)),
        ),
        patch(
            "app.services.ai.context_manager.AgentContextManager.setup_context",
            AsyncMock(),
        ),
        patch(
            "app.services.ai.turn_classifier.resolve_turn_for_session",
            AsyncMock(side_effect=AssertionError("ChatBI turn classification must stay inside DataQueryExecutor")),
        ),
        patch(
            "app.services.ai.agent_service.AgentDispatcher.dispatch",
            side_effect=fake_dispatch,
        ),
        patch(
            "app.services.ai.agent_service.AuditManager.log_transaction",
            side_effect=_noop_audit,
        ),
        patch(
            "app.services.config_service.ConfigService.get",
            AsyncMock(return_value=None),
        ),
    ):
        chunks = []
        async for chunk in service.chat_completion_stream(
            [{"role": "user", "content": "那本月呢"}],
            user_info={"user_id": "1", "role": "admin", "user_name": "admin"},
            enable_multi_agent=False,
        ):
            chunks.append(chunk)

    meta = next(chunk for chunk in chunks if chunk.get("type") == "meta")
    assert meta["turn_type"] == "data_query_request"
    assert meta["turn_type_label"] == "ChatBI 请求类别分析"
    assert captured["shared_turn"] is None


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_chat_stream_awaits_audit_before_completion():
    service = AgentService()
    agent_config = ChatConfig(
        agent_id="agent-1",
        agent_name="helper",
        agent_display_name="Helper",
        model_name="test-model",
        temperature=0,
        system_prompt="Base prompt",
        tools=[],
    )
    audit_calls: list[tuple] = []

    async def capture_audit(*args, **kwargs):
        audit_calls.append((args, kwargs))

    async def fake_dispatch(config, *args, **kwargs):
        return _NoopExecutor()

    with (
        patch(
            "app.services.ai.context_manager.AgentContextManager.resolve_agent_config",
            AsyncMock(return_value=(agent_config, None)),
        ),
        patch(
            "app.services.ai.context_manager.AgentContextManager.setup_context",
            AsyncMock(),
        ),
        patch(
            "app.services.memory_config_service.MemoryConfigService.get_bool",
            AsyncMock(return_value=False),
        ),
        patch(
            "app.services.ai.memory_service.ltm_service.fetch_memory",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.ai.agent_service.memory_service.get_history",
            AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.ai.agent_service.memory_service.add_message",
            AsyncMock(),
        ),
        patch(
            "app.services.config_service.ConfigService.get",
            AsyncMock(return_value="20"),
        ),
        patch(
            "app.services.ai.turn_classifier.resolve_turn_for_session",
            AsyncMock(return_value=(
                TurnClassification(turn_type=TurnType.GENERAL, reasoning="test"),
                None,
                0.0,
            )),
        ),
        patch(
            "app.services.ai.agent_service.AgentDispatcher.dispatch",
            side_effect=fake_dispatch,
        ),
        patch(
            "app.services.ai.agent_service.AgentService._quota_block_message",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.ai.agent_service.conversation_run_lane.hold",
            _noop_lane_hold,
        ),
        patch(
            "app.services.ai.agent_service.AuditManager.log_transaction",
            side_effect=capture_audit,
        ),
        patch(
            "app.services.ai.session_summary_service.SessionSummaryService.merge_session_summary",
            AsyncMock(),
        ),
        patch("app.core.config.Settings.SKILLS_DIR", "/app/data/skills"),
    ):
        chunks = []
        async for chunk in service.chat_completion_stream(
            [{"role": "user", "content": "记录审计"}],
            user_info={"user_id": "1", "role": "admin", "user_name": "admin"},
            conversation_id="conv-audit",
            enable_multi_agent=False,
        ):
            chunks.append(chunk)

    assert any(chunk.get("content") == "ok" for chunk in chunks)
    assert len(audit_calls) == 1
    assert audit_calls[0][0][3] == "ok"
    assert audit_calls[0][1]["conversation_id"] == "conv-audit"


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_chat_stream_skips_audit_on_external_execution_required():
    service = AgentService()
    agent_config = ChatConfig(
        agent_id="agent-1",
        agent_name="helper",
        agent_display_name="Helper",
        model_name="test-model",
        temperature=0,
        system_prompt="Base prompt",
        tools=[],
    )
    audit_calls: list[tuple] = []

    async def capture_audit(*args, **kwargs):
        audit_calls.append(args)

    async def fake_dispatch(config, *args, **kwargs):
        return _ExternalPendingExecutor()

    with (
        patch(
            "app.services.ai.context_manager.AgentContextManager.resolve_agent_config",
            AsyncMock(return_value=(agent_config, None)),
        ),
        patch(
            "app.services.ai.context_manager.AgentContextManager.setup_context",
            AsyncMock(),
        ),
        patch(
            "app.services.memory_config_service.MemoryConfigService.get_bool",
            AsyncMock(return_value=False),
        ),
        patch(
            "app.services.ai.memory_service.ltm_service.fetch_memory",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.ai.agent_service.memory_service.get_history",
            AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.ai.agent_service.memory_service.add_message",
            AsyncMock(),
        ),
        patch(
            "app.services.config_service.ConfigService.get",
            AsyncMock(return_value="20"),
        ),
        patch(
            "app.services.ai.turn_classifier.resolve_turn_for_session",
            AsyncMock(return_value=(
                TurnClassification(turn_type=TurnType.GENERAL, reasoning="test"),
                None,
                0.0,
            )),
        ),
        patch(
            "app.services.ai.agent_service.conversation_run_lane.hold",
            _noop_lane_hold,
        ),
        patch(
            "app.services.ai.agent_service.AgentDispatcher.dispatch",
            side_effect=fake_dispatch,
        ),
        patch(
            "app.services.ai.agent_service.AuditManager.log_transaction",
            side_effect=capture_audit,
        ),
        patch("app.core.config.Settings.SKILLS_DIR", "/app/data/skills"),
    ):
        chunks = []
        async for chunk in service.chat_completion_stream(
            [{"role": "user", "content": "运行外部工具"}],
            user_info={"user_id": "1", "role": "admin", "user_name": "admin"},
            enable_multi_agent=False,
        ):
            chunks.append(chunk)

    assert any(chunk.get("type") == "external_execution_required" for chunk in chunks)
    assert audit_calls == []


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_scheduled_chat_stream_audits_external_execution_required():
    service = AgentService()
    agent_config = ChatConfig(
        agent_id="agent-1",
        agent_name="helper",
        agent_display_name="Helper",
        model_name="test-model",
        temperature=0,
        system_prompt="Base prompt",
        tools=[],
    )
    audit_calls: list[tuple] = []

    async def capture_audit(*args, **kwargs):
        audit_calls.append((args, kwargs))

    async def fake_dispatch(config, *args, **kwargs):
        return _ExternalPendingExecutor()

    with (
        patch(
            "app.services.ai.context_manager.AgentContextManager.resolve_agent_config",
            AsyncMock(return_value=(agent_config, None)),
        ),
        patch(
            "app.services.ai.context_manager.AgentContextManager.setup_context",
            AsyncMock(),
        ),
        patch(
            "app.services.memory_config_service.MemoryConfigService.get_bool",
            AsyncMock(return_value=False),
        ),
        patch(
            "app.services.ai.memory_service.ltm_service.fetch_memory",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.ai.agent_service.memory_service.get_history",
            AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.ai.agent_service.memory_service.add_message",
            AsyncMock(),
        ),
        patch(
            "app.services.config_service.ConfigService.get",
            AsyncMock(return_value="20"),
        ),
        patch(
            "app.services.ai.turn_classifier.resolve_turn_for_session",
            AsyncMock(return_value=(
                TurnClassification(turn_type=TurnType.GENERAL, reasoning="test"),
                None,
                0.0,
            )),
        ),
        patch(
            "app.services.ai.agent_service.conversation_run_lane.hold",
            _noop_lane_hold,
        ),
        patch(
            "app.services.ai.agent_service.AgentDispatcher.dispatch",
            side_effect=fake_dispatch,
        ),
        patch(
            "app.services.ai.agent_service.AuditManager.log_transaction",
            side_effect=capture_audit,
        ),
        patch("app.core.config.Settings.SKILLS_DIR", "/app/data/skills"),
    ):
        chunks = []
        async for chunk in service.chat_completion_stream(
            [{"role": "user", "content": "运行外部工具"}],
            user_info={
                "user_id": "1",
                "role": "admin",
                "user_name": "admin",
                "is_scheduled_task": True,
            },
            conversation_id="task-conv",
            enable_multi_agent=False,
        ):
            chunks.append(chunk)

    assert any(chunk.get("type") == "external_execution_required" for chunk in chunks)
    assert len(audit_calls) == 1
    assert audit_calls[0][0][5] == "awaiting_external_execution"
    assert audit_calls[0][1]["conversation_id"] == "task-conv"


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_scheduled_chat_stream_marks_no_tool_execution_as_error():
    service = AgentService()
    agent_config = ChatConfig(
        agent_id="agent-1",
        agent_name="helper",
        agent_display_name="Helper",
        model_name="test-model",
        temperature=0,
        system_prompt="Base prompt",
        tools=[],
    )
    audit_calls: list[tuple] = []

    async def capture_audit(*args, **kwargs):
        audit_calls.append((args, kwargs))

    async def fake_dispatch(config, *args, **kwargs):
        return _NoopExecutor()

    with (
        patch(
            "app.services.ai.context_manager.AgentContextManager.resolve_agent_config",
            AsyncMock(return_value=(agent_config, None)),
        ),
        patch(
            "app.services.ai.context_manager.AgentContextManager.setup_context",
            AsyncMock(),
        ),
        patch(
            "app.services.memory_config_service.MemoryConfigService.get_bool",
            AsyncMock(return_value=False),
        ),
        patch(
            "app.services.ai.memory_service.ltm_service.fetch_memory",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.ai.agent_service.memory_service.get_history",
            AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.ai.agent_service.memory_service.add_message",
            AsyncMock(),
        ),
        patch(
            "app.services.config_service.ConfigService.get",
            AsyncMock(return_value="20"),
        ),
        patch(
            "app.services.ai.turn_classifier.resolve_turn_for_session",
            AsyncMock(return_value=(
                TurnClassification(turn_type=TurnType.GENERAL, reasoning="test"),
                None,
                0.0,
            )),
        ),
        patch(
            "app.services.ai.agent_service.conversation_run_lane.hold",
            _noop_lane_hold,
        ),
        patch(
            "app.services.ai.agent_service.AgentDispatcher.dispatch",
            side_effect=fake_dispatch,
        ),
        patch(
            "app.services.ai.agent_service.AuditManager.log_transaction",
            side_effect=capture_audit,
        ),
        patch("app.core.config.Settings.SKILLS_DIR", "/app/data/skills"),
    ):
        chunks = []
        async for chunk in service.chat_completion_stream(
            [{"role": "user", "content": "运行自动任务"}],
            user_info={
                "user_id": "1",
                "role": "admin",
                "user_name": "admin",
                "is_scheduled_task": True,
                "requires_tool_execution": True,
            },
            conversation_id="task-conv",
            enable_multi_agent=False,
        ):
            chunks.append(chunk)

    error_chunks = [chunk for chunk in chunks if chunk.get("type") == "error"]
    assert error_chunks
    assert "自动任务未实际调用任何工具" in error_chunks[-1]["content"]
    assert len(audit_calls) == 1
    assert audit_calls[0][0][5] == "no_tool_execution"


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_scheduled_multi_agent_stream_audits_external_execution_status():
    service = AgentService()
    agent_config = ChatConfig(
        agent_id="agent-1",
        agent_name="helper",
        agent_display_name="Helper",
        model_name="test-model",
        temperature=0,
        system_prompt="Base prompt",
        tools=[],
    )
    route_details = SimpleNamespace(
        secondary_agents=["agent-2"],
        reasoning="multi agent route",
        confidence=1.0,
        agent_id="agent-1",
        turn_labels=[],
        relation_to_previous="new_task",
        user_action_type="task",
        intent_info=None,
    )
    audit_calls: list[tuple] = []

    async def capture_audit(*args, **kwargs):
        audit_calls.append((args, kwargs))

    async def fake_execute_multi_agent(*args, **kwargs):
        yield {
            "type": "external_execution_required",
            "status": "pending",
            "content": "需要外部执行",
        }

    with (
        patch(
            "app.services.ai.context_manager.AgentContextManager.resolve_agent_config",
            AsyncMock(return_value=(agent_config, route_details)),
        ),
        patch(
            "app.services.ai.context_manager.AgentContextManager.setup_context",
            AsyncMock(),
        ),
        patch(
            "app.services.memory_config_service.MemoryConfigService.get_bool",
            AsyncMock(return_value=False),
        ),
        patch(
            "app.services.ai.memory_service.ltm_service.fetch_memory",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.ai.agent_service.memory_service.get_history",
            AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.ai.agent_service.memory_service.add_message",
            AsyncMock(),
        ),
        patch(
            "app.services.config_service.ConfigService.get",
            AsyncMock(return_value="20"),
        ),
        patch(
            "app.services.ai.turn_classifier.resolve_turn_for_session",
            AsyncMock(return_value=(
                TurnClassification(turn_type=TurnType.GENERAL, reasoning="test"),
                None,
                0.0,
            )),
        ),
        patch(
            "app.services.ai.agent_service.conversation_run_lane.hold",
            _noop_lane_hold,
        ),
        patch.object(service, "_execute_multi_agent", fake_execute_multi_agent),
        patch(
            "app.services.ai.agent_service.AuditManager.log_transaction",
            side_effect=capture_audit,
        ),
        patch("app.core.config.Settings.SKILLS_DIR", "/app/data/skills"),
    ):
        chunks = []
        async for chunk in service.chat_completion_stream(
            [{"role": "user", "content": "运行外部工具"}],
            user_info={
                "user_id": "1",
                "role": "admin",
                "user_name": "admin",
                "is_scheduled_task": True,
            },
            conversation_id="task-conv",
            enable_multi_agent=True,
        ):
            chunks.append(chunk)

    assert any(chunk.get("type") == "external_execution_required" for chunk in chunks)
    assert len(audit_calls) == 1
    assert audit_calls[0][0][5] == "awaiting_external_execution"


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_chat_completion_preserves_error_status_from_stream():
    service = AgentService()

    async def fake_stream(*args, **kwargs):
        yield {"trace_id": "trace-error", "status": "init"}
        yield {
            "type": "error",
            "status": "error",
            "content": "当前会话正在处理中，请稍后再试。",
        }

    with patch.object(service, "chat_completion_stream", side_effect=fake_stream):
        result = await service.chat_completion(
            [{"role": "user", "content": "run"}],
            user_info={"user_id": "1", "role": "admin", "user_name": "admin"},
        )

    assert result["trace_id"] == "trace-error"
    assert result["status"] == "error"
    assert result["content"] == "当前会话正在处理中，请稍后再试。"


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_chat_completion_preserves_external_execution_status_from_stream():
    service = AgentService()

    async def fake_stream(*args, **kwargs):
        yield {"trace_id": "trace-external", "status": "init"}
        yield {
            "type": "external_execution_required",
            "status": "pending",
            "content": "需要外部执行",
        }

    with patch.object(service, "chat_completion_stream", side_effect=fake_stream):
        result = await service.chat_completion(
            [{"role": "user", "content": "run"}],
            user_info={"user_id": "1", "role": "admin", "user_name": "admin"},
        )

    assert result["trace_id"] == "trace-external"
    assert result["status"] == "awaiting_external_execution"
    assert result["content"] == "需要外部执行"


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_chat_stream_injects_compacted_overflow_without_persisting_digest():
    service = AgentService()
    agent_config = ChatConfig(
        agent_id="agent-1",
        agent_name="helper",
        agent_display_name="Helper",
        model_name="test-model",
        temperature=0,
        system_prompt="Base prompt",
        tools=[],
    )
    history = [
        {"role": "user", "content": "第一轮：查机房列表"},
        {"role": "assistant", "content": "第一轮结果：A 机房、B 机房"},
        {"role": "user", "content": "第二轮：看 A 机房"},
        {"role": "assistant", "content": "第二轮结果：A 机房正常"},
    ]
    captured = {}

    class CaptureExecutor:
        async def execute(self, messages):
            captured["messages"] = messages
            yield {"content": "ok"}

    async def fake_config_get(key, default=None):
        values = {
            "agent_max_context_messages": "2",
            "agent_context_compaction_enabled": "true",
            "agent_context_compaction_max_chars": "500",
        }
        return values.get(key, default)

    add_message = AsyncMock()

    async def fake_dispatch(config, *args, **kwargs):
        return CaptureExecutor()

    with (
        patch(
            "app.services.ai.context_manager.AgentContextManager.resolve_agent_config",
            AsyncMock(return_value=(agent_config, None)),
        ),
        patch(
            "app.services.ai.context_manager.AgentContextManager.setup_context",
            AsyncMock(),
        ),
        patch(
            "app.services.memory_config_service.MemoryConfigService.get_bool",
            AsyncMock(return_value=False),
        ),
        patch(
            "app.services.ai.memory_service.ltm_service.fetch_memory",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.config_service.ConfigService.get",
            side_effect=fake_config_get,
        ),
        patch(
            "app.services.ai.turn_classifier.resolve_turn_for_session",
            AsyncMock(return_value=(
                TurnClassification(turn_type=TurnType.GENERAL, reasoning="test"),
                None,
                0.0,
            )),
        ),
        patch(
            "app.services.ai.memory_service.memory_service.get_history",
            AsyncMock(return_value=history),
        ),
        patch(
            "app.services.ai.memory_service.memory_service.add_message",
            add_message,
        ),
        patch(
            "app.services.ai.agent_service.AgentDispatcher.dispatch",
            side_effect=fake_dispatch,
        ),
        patch(
            "app.services.ai.agent_service.AuditManager.log_transaction",
            side_effect=_noop_audit,
        ),
        patch(
            "app.services.ai.session_summary_service.SessionSummaryService.merge_session_summary",
            AsyncMock(),
        ),
        patch("app.core.redis.get_redis", AsyncMock(return_value=None)),
        patch("app.core.config.Settings.SKILLS_DIR", "/app/data/skills"),
    ):
        chunks = []
        async for chunk in service.chat_completion_stream(
            [{"role": "user", "content": "第三轮：再搜一下"}],
            user_info={"user_id": "1", "role": "admin", "user_name": "admin"},
            conversation_id="conv-compact",
            enable_multi_agent=False,
        ):
            chunks.append(chunk)

    assert any(chunk.get("content") == "ok" for chunk in chunks)
    assert any(
        message.get("role") == "system" and COMPACTION_MARKER in message.get("content", "")
        for message in captured["messages"]
    )
    assert captured["messages"][-1] == {"role": "user", "content": "第三轮：再搜一下"}
    persisted_contents = [call.args[3] for call in add_message.call_args_list if len(call.args) >= 4]
    assert all(COMPACTION_MARKER not in str(content) for content in persisted_contents)
