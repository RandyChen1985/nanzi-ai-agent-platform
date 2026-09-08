"""准备阶段优化的行为与 I/O 次数回归；不连接真实基础设施。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_config_batch_preserves_cache_empty_and_missing_defaults(monkeypatch):
    from app.services import config_service as module

    redis = AsyncMock()
    redis.mget.return_value = ["", "0", None, None]
    pipe = MagicMock()
    pipe.execute = AsyncMock()
    redis.pipeline = MagicMock(return_value=pipe)
    db = AsyncMock()
    result = MagicMock()
    result.fetchall.return_value = [("from_db", "value")]
    db.execute.return_value = result
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=db)
    session.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(module, "get_redis", AsyncMock(return_value=redis))
    monkeypatch.setattr(module, "AsyncSessionLocal", lambda: session)

    values = await module.ConfigService.get_many({
        "empty": "fallback", "zero": "1", "from_db": "fallback", "missing": "default",
    })
    assert values == {"empty": "", "zero": "0", "from_db": "value", "missing": "default"}
    assert redis.mget.await_count == 1
    assert db.execute.await_count == 1
    assert redis.get.await_count == 0


@pytest.mark.asyncio
async def test_resource_snapshot_reuses_authorized_catalog_without_cross_user_reuse(monkeypatch):
    from app.services.ai import accessible_resource_catalog as module
    from app.services.ai.knowledge_catalog import AuthorizedKnowledgeCatalog, KnowledgeBaseCatalogItem

    datasets = AsyncMock(return_value=[SimpleNamespace(name="allowed", description="safe")])
    knowledge = AsyncMock(return_value=AuthorizedKnowledgeCatalog(status="available", items=(
        KnowledgeBaseCatalogItem(ragflow_dataset_id="kb", name="allowed-kb"),
        KnowledgeBaseCatalogItem(ragflow_dataset_id="internal", name="chatbi-example-meta"),
    )))
    monkeypatch.setattr(module.MetadataService, "list_accessible_dataset_options", datasets)
    monkeypatch.setattr(module, "fetch_authorized_knowledge_catalog", knowledge)
    snapshot = await module.fetch_accessible_resource_snapshot(
        MagicMock(), user_id=7, user_name="alice", is_admin=False,
    )
    assert snapshot.counts == {"status": "available", "datasets": 1, "knowledge_bases": 1}
    assert "allowed-kb" in snapshot.prompt
    assert "chatbi-example-meta" not in snapshot.prompt
    assert snapshot.matches(user_id=7, user_name="alice", is_admin=False)
    assert not snapshot.matches(user_id=8, user_name="alice", is_admin=False)
    assert not snapshot.matches(user_id=7, user_name="alice", is_admin=True)
    assert datasets.await_count == knowledge.await_count == 1


@pytest.mark.asyncio
async def test_workspace_cache_hit_does_not_rescan_skills(tmp_path, monkeypatch):
    from app.services.ai.runtime.agentscope import workspace as module

    module.clear_workspace_cache()
    monkeypatch.setattr(module, "resolve_workspace_root", AsyncMock(return_value=str(tmp_path)))
    monkeypatch.setattr("app.services.config_service.ConfigService.get", AsyncMock(return_value="local"))
    scan = MagicMock(return_value=[])
    monkeypatch.setattr(module, "discover_platform_skill_paths", scan)
    try:
        first = await module.get_local_workspace(user_id=7, conversation_id="one")
        second = await module.get_local_workspace(user_id=7, conversation_id="one")
        assert first is second
        assert scan.call_count == 1
        other = await module.get_local_workspace(user_id=8, conversation_id="one")
        assert other is not first
        assert scan.call_count == 2
    finally:
        module.clear_workspace_cache()


def test_performance_spans_measure_overlaps_and_failures_without_changing_stage_marks():
    from app.services.ai.runtime.execution_observability import ExecutionPerformanceTracker

    now = [10.0]
    tracker = ExecutionPerformanceTracker(clock=lambda: now[0])
    with tracker.measure("preparation"):
        now[0] += 1
        with pytest.raises(ValueError), tracker.measure("skills"):
            now[0] += 2
            raise ValueError("private error")
        now[0] += 1
    tracker.mark("ready")
    result = tracker.snapshot(trace_buffer=[], status="success")
    assert result["stages_ms"] == {"ready": 4000.0}
    assert result["spans"]["preparation"] == {"start_ms": 0.0, "duration_ms": 4000.0, "status": "success"}
    assert result["spans"]["skills"] == {"start_ms": 1000.0, "duration_ms": 2000.0, "status": "error"}
    assert "private error" not in str(result)


@pytest.mark.asyncio
async def test_delegable_agents_batch_load_versions(monkeypatch):
    from app.services.ai.tools import agent_delegate_tool as module

    agent_1 = SimpleNamespace(id="a1", name="agent_one", is_enabled=True, engine_type="LOCAL", sort_order=10)
    agent_2 = SimpleNamespace(id="a2", name="agent_two", is_enabled=True, engine_type="LOCAL", sort_order=5)
    agent_ragflow = SimpleNamespace(id="a3", name="ragflow_agent", is_enabled=True, engine_type="RAGFLOW", sort_order=1, capabilities=[], engine_config={})

    # mock filter_delegable_system_agents
    monkeypatch.setattr(module, "filter_delegable_system_agents", AsyncMock(return_value=[agent_1, agent_2, agent_ragflow]))

    # mock batch versions
    batch_mock = AsyncMock(return_value={
        "a1": SimpleNamespace(agent_id="a1", capabilities=[], engine_config={}, tools=[]),
        # a2 has no published version
    })
    monkeypatch.setattr(module.AgentManagerService, "_latest_published_versions_by_agent", batch_mock)

    session = MagicMock()
    runnable = await module.resolve_runnable_delegable_system_agents(
        session, [agent_1, agent_2, agent_ragflow], user_id=1, is_admin=True, current_agent_id=None,
    )

    # agent_1 has published version -> ready; agent_ragflow bypasses versioning -> ready; agent_2 has no version -> excluded
    assert [a.id for a in runnable] == ["a1", "a3"]
    assert batch_mock.await_count == 1
    # Check that it queried all local agent ids in ONE batch
    assert set(batch_mock.await_args[0][1]) == {"a1", "a2"}


@pytest.mark.asyncio
async def test_agent_runtime_config_batch_loads(monkeypatch):
    from app.services.ai.runtime.agentscope import agent_runtime as module

    get_many_mock = AsyncMock(return_value={
        "agentscope_context_trigger_ratio": "0.75",
        "agentscope_context_reserve_ratio": "0.15",
        "agentscope_tool_result_limit": "3000",
        "agentscope_inject_runtime_state": "true",
        "agentscope_inject_time_interval_hours": "1.0",
    })
    monkeypatch.setattr("app.services.config_service.ConfigService.get_many", get_many_mock)

    context_cfg = await module.load_context_config()
    assert context_cfg.trigger_ratio == 0.75
    assert context_cfg.reserve_ratio == 0.15
    assert context_cfg.tool_result_limit == 3000

    injection_cfg = await module.load_injection_config()
    assert injection_cfg.inject_runtime_state is True
    assert injection_cfg.time_interval == 1.0
    assert get_many_mock.await_count == 2


def test_workspace_sandbox_log_build():
    from app.services.ai.agent_service import _build_workspace_sandbox_log

    # 1. Docker sandbox cold start
    log_docker_cold = _build_workspace_sandbox_log(
        policy="docker",
        is_sandbox=True,
        cached=False,
        status="success",
        execution_time_ms=1250.5,
    )
    assert log_docker_cold["id"] == "workspace:sandbox"
    assert log_docker_cold["parent_id"] == "preparation:auth_context_capability"
    assert "Docker 容器" in log_docker_cold["details"]
    assert "已拉起隔离容器" in log_docker_cold["details"]
    assert log_docker_cold["execution_time_ms"] == 1250.5
    assert log_docker_cold["status"] == "success"

    # 2. Docker sandbox reuse
    log_docker_cached = _build_workspace_sandbox_log(
        policy="docker",
        is_sandbox=True,
        cached=True,
        status="success",
        execution_time_ms=2.0,
    )
    assert "复用存活容器" in log_docker_cached["details"]

    # 3. Local policy
    log_local = _build_workspace_sandbox_log(
        policy="local",
        is_sandbox=False,
        cached=False,
        status="success",
        execution_time_ms=5.0,
    )
    assert "本地环境" in log_local["details"]
    assert "目录与技能加载完成" in log_local["details"]

    # 4. Error state
    log_error = _build_workspace_sandbox_log(
        policy="docker",
        is_sandbox=True,
        cached=False,
        status="error",
        error_message="Docker daemon not running",
        execution_time_ms=10.0,
    )
    assert log_error["status"] == "error"
    assert "异常（Docker daemon not running）" in log_error["details"]

