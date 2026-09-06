import asyncio
import time
import pytest

from agentscope.message import ToolCallBlock
from app.services.ai.runtime.agentscope.tools import (
    AgentScopeNativeApprovalTool,
    AgentScopeRuntimeTool,
    RuntimeToolSpec,
)
from app.services.ai.runtime.agentscope.tool_parallel import (
    AGENTSCOPE_PARALLEL_TOOL_EXECUTION_KEY,
    AGENTSCOPE_MAX_CONCURRENT_TOOLS_KEY,
    is_tool_concurrency_safe,
    is_parallel_tool_execution_enabled,
    set_parallel_tool_execution_enabled,
    get_current_max_concurrency,
    set_max_concurrency_limit,
    execute_with_concurrency_guard,
    validate_agentscope_parallel_tool_execution,
    validate_agentscope_max_concurrent_tools,
)
from app.services.config_service import validate_config_update

pytestmark = pytest.mark.no_infrastructure


@pytest.fixture(autouse=True)
def reset_parallel_state():
    original_enabled = is_parallel_tool_execution_enabled()
    original_limit = get_current_max_concurrency()
    yield
    set_parallel_tool_execution_enabled(original_enabled)
    set_max_concurrency_limit(original_limit)


def test_read_only_tools_concurrency_safe_identified():
    """只读且允许自动运行的工具应被判定为并发安全。"""
    spec = RuntimeToolSpec(
        name="list_accessible_datasets",
        description="List datasets",
        parameters_schema={"type": "object"},
        source_type="system",
        callable=lambda **kwargs: "ok",
        permission_scope="read",
    )
    runtime_tool = AgentScopeRuntimeTool(spec, approval_mode="allow")
    assert spec.concurrency_safe is True
    assert runtime_tool.is_concurrency_safe is True

    class NativeReadOnlyTool:
        name = "Read"
        description = "Read file"
        input_schema = {"type": "object"}
        is_read_only = True
        is_concurrency_safe = True

    native_tool = AgentScopeNativeApprovalTool(NativeReadOnlyTool(), approval_mode="allow")
    assert native_tool.is_concurrency_safe is True


def test_mutation_and_ask_tools_concurrency_unsafe():
    """写操作、需审批或拒绝模式的工具，绝不可被判定为并发安全。"""
    write_spec = RuntimeToolSpec(
        name="execute_shell_command",
        description="Execute command",
        parameters_schema={"type": "object"},
        source_type="system",
        callable=lambda **kwargs: "ok",
        permission_scope="write",
    )
    write_tool = AgentScopeRuntimeTool(write_spec, approval_mode="allow")
    assert write_spec.concurrency_safe is False
    assert write_tool.is_concurrency_safe is False

    read_spec = RuntimeToolSpec(
        name="get_dataset_schema",
        description="Get schema",
        parameters_schema={"type": "object"},
        source_type="system",
        callable=lambda **kwargs: "ok",
        permission_scope="read",
    )
    # 审批模式为 ask 时必须串行确认
    ask_tool = AgentScopeRuntimeTool(read_spec, approval_mode="ask")
    assert ask_tool.is_concurrency_safe is False

    deny_tool = AgentScopeRuntimeTool(read_spec, approval_mode="deny")
    assert deny_tool.is_concurrency_safe is False


def test_browser_and_exclusive_tools_concurrency_unsafe():
    """即使是只读性质，浏览器与单用户独占交互工具也必须保持严格串行。"""
    for tool_name in ["browser_snapshot", "browser_scroll", "request_user_confirmation", "ask_user_question"]:
        spec = RuntimeToolSpec(
            name=tool_name,
            description=tool_name,
            parameters_schema={"type": "object"},
            source_type="system",
            callable=lambda **kwargs: "ok",
            permission_scope="read",
        )
        runtime_tool = AgentScopeRuntimeTool(spec, approval_mode="allow")
        assert spec.concurrency_safe is False
        assert runtime_tool.is_concurrency_safe is False


def test_config_service_parallel_validation_and_toggles():
    """系统配置服务的校验函数与动态开关切换。"""
    assert validate_agentscope_parallel_tool_execution("true") is True
    assert validate_agentscope_parallel_tool_execution("0") is False
    assert validate_agentscope_parallel_tool_execution(False) is False
    with pytest.raises(ValueError):
        validate_agentscope_parallel_tool_execution("invalid_bool")

    assert validate_agentscope_max_concurrent_tools("5") == 5
    assert validate_agentscope_max_concurrent_tools(10) == 10
    with pytest.raises(ValueError):
        validate_agentscope_max_concurrent_tools("0")
    with pytest.raises(ValueError):
        validate_agentscope_max_concurrent_tools("25")

    # 通过 validate_config_update 统一接口测试生效
    validate_config_update(AGENTSCOPE_PARALLEL_TOOL_EXECUTION_KEY, "false")
    assert is_parallel_tool_execution_enabled() is False

    validate_config_update(AGENTSCOPE_MAX_CONCURRENT_TOOLS_KEY, "8")
    assert get_current_max_concurrency() == 8


def test_runtime_tool_fallback_when_disabled():
    """当系统关闭并发工具执行时，所有只读工具平滑退化为串行 (False)。"""
    spec = RuntimeToolSpec(
        name="search_knowledge_base",
        description="Search KB",
        parameters_schema={"type": "object"},
        source_type="system",
        callable=lambda **kwargs: "ok",
        permission_scope="read",
    )
    tool = AgentScopeRuntimeTool(spec, approval_mode="allow")
    assert tool.is_concurrency_safe is True

    set_parallel_tool_execution_enabled(False)
    assert tool.is_concurrency_safe is False


@pytest.mark.asyncio
async def test_concurrency_semaphore_throttling():
    """验证并发信号量在多任务调用时的最大活跃峰值控制。"""
    set_max_concurrency_limit(2)
    active_count = 0
    max_active_observed = 0

    async def worker():
        nonlocal active_count, max_active_observed
        active_count += 1
        max_active_observed = max(max_active_observed, active_count)
        await asyncio.sleep(0.05)
        active_count -= 1
        return "done"

    tasks = [
        execute_with_concurrency_guard(worker)
        for _ in range(5)
    ]
    results = await asyncio.gather(*tasks)
    assert len(results) == 5
    assert all(r == "done" for r in results)
    assert max_active_observed <= 2


from agentscope.agent import Agent, ReActConfig
from agentscope.credential import CredentialBase
from agentscope.model import ChatModelBase
from agentscope.tool import Toolkit
from pydantic import BaseModel


class FakeCredential(CredentialBase):
    pass


class FakeModel(ChatModelBase):
    class Parameters(BaseModel):
        pass

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.formatter = type("FakeFormatter", (), {"supported_input_media_types": ["image", "text"]})()

    async def _call_api(self, *args, **kwargs):
        raise NotImplementedError


def _build_dummy_model() -> FakeModel:
    return FakeModel(
        credential=FakeCredential(),
        model="dummy-test",
        parameters=FakeModel.Parameters(),
        stream=False,
        max_retries=0,
    )


@pytest.mark.asyncio
async def test_agentscope_parallel_vs_sequential_speedup():
    """通过构造 AgentScope Agent 验证并发工具执行的耗时折半收益。"""
    class FastMockTool:
        def __init__(self, name: str, delay: float = 0.1):
            self.name = name
            self.description = name
            self.input_schema = {"type": "object", "properties": {}}
            self.is_concurrency_safe = True
            self.is_read_only = True
            self.is_external_tool = False
            self.is_state_injected = False
            self.is_mcp = False
            self.mcp_name = None
            self.delay = delay

        async def check_permissions(self, tool_input, context):
            from agentscope.permission import PermissionBehavior, PermissionDecision
            return PermissionDecision(behavior=PermissionBehavior.ALLOW)

        async def check_read_only(self, tool_input: dict) -> bool:
            return self.is_read_only

        async def __call__(self, **kwargs):
            from agentscope.message import TextBlock, ToolResultState
            from agentscope.tool import ToolChunk
            await asyncio.sleep(self.delay)
            return ToolChunk(content=[TextBlock(text=f"result of {self.name}")], state=ToolResultState.SUCCESS)

    tool1 = FastMockTool("tool_a", delay=0.1)
    tool2 = FastMockTool("tool_b", delay=0.1)
    toolkit = Toolkit(tools=[tool1, tool2])

    agent = Agent(
        name="test_agent",
        system_prompt="sys",
        model=_build_dummy_model(),
        toolkit=toolkit,
        react_config=ReActConfig(max_iters=1),
    )

    call1 = ToolCallBlock(id="call_1", name="tool_a", input="{}")
    call2 = ToolCallBlock(id="call_2", name="tool_b", input="{}")

    # 1. 验证批次切分：两个并发工具应该被划分到同一个 concurrent batch
    batches = await agent._batch_tool_calls([call1, call2])
    assert len(batches) == 1
    assert batches[0].type == "concurrent"
    assert len(batches[0].tool_calls) == 2

    # 2. 验证并发执行耗时：2 个各 0.1s 的工具，并发执行应在 ~0.15s 内完成，远低于串行的 0.2s+
    start = time.perf_counter()
    events = []
    async for evt in agent._execute_concurrent_tool_calls(batches[0].tool_calls):
        events.append(evt)
    duration = time.perf_counter() - start

    assert duration < 0.18, f"Expected parallel duration < 0.18s, got {duration:.3f}s"
    end_events = [e for e in events if getattr(e, "type", "") == "TOOL_RESULT_END"]
    assert len(end_events) == 2


@pytest.mark.asyncio
async def test_mixed_tools_batch_partitioning():
    """验证并发只读工具与串行写工具混合时，正确分为 concurrent 与 sequential 批次。"""
    spec_read1 = RuntimeToolSpec(
        name="read_a",
        description="read a",
        parameters_schema={"type": "object", "properties": {}},
        source_type="system",
        callable=lambda **kwargs: "ok",
        permission_scope="read",
    )
    spec_read2 = RuntimeToolSpec(
        name="read_b",
        description="read b",
        parameters_schema={"type": "object", "properties": {}},
        source_type="system",
        callable=lambda **kwargs: "ok",
        permission_scope="read",
    )
    spec_write = RuntimeToolSpec(
        name="write_c",
        description="write c",
        parameters_schema={"type": "object", "properties": {}},
        source_type="system",
        callable=lambda **kwargs: "ok",
        permission_scope="write",
    )

    tool_r1 = AgentScopeRuntimeTool(spec_read1, approval_mode="allow")
    tool_r2 = AgentScopeRuntimeTool(spec_read2, approval_mode="allow")
    tool_w = AgentScopeRuntimeTool(spec_write, approval_mode="allow")

    toolkit = Toolkit(tools=[tool_r1, tool_r2, tool_w])
    agent = Agent(
        name="test_agent",
        system_prompt="sys",
        model=_build_dummy_model(),
        toolkit=toolkit,
        react_config=ReActConfig(max_iters=1),
    )

    calls = [
        ToolCallBlock(id="1", name="read_a", input="{}"),
        ToolCallBlock(id="2", name="read_b", input="{}"),
        ToolCallBlock(id="3", name="write_c", input="{}"),
    ]

    batches = await agent._batch_tool_calls(calls)
    assert len(batches) == 2
    assert batches[0].type == "concurrent"
    assert [c.name for c in batches[0].tool_calls] == ["read_a", "read_b"]
    assert batches[1].type == "sequential"
    assert [c.name for c in batches[1].tool_calls] == ["write_c"]
