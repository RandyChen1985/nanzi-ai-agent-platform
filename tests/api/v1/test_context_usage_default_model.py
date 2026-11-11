"""上下文水位线在「跟随智能体默认模型」时必须按版本模型的窗口算。

历史陷阱：输入框没手动选模型时前端不传 `model_id`，端点只能退回
`agent_context_max_tokens`（默认 65536）；而执行期预算按版本模型（以及独立配置的
合成模型）的真实窗口算。版本模型窗口不等于 65536 时分母与实际不符——128k 模型
显示偏满、32k 模型显示偏空（以为还能装，其实已经该压缩了）。
"""

from types import SimpleNamespace

import pytest

from app.api.v1.endpoints import chat as chat_endpoint

pytestmark = pytest.mark.no_infrastructure


class _FakeResult:
    def __init__(self, *, one=None, first=None):
        self._one = one
        self._first = first

    def scalar_one_or_none(self):
        return self._one

    def first(self):
        return self._first


class _FakeSession:
    """按调用顺序返回预置结果，用来替掉两次 execute（取 agent_id、取版本模型列）。"""

    def __init__(self, results):
        self._results = list(results)
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        if not self._results:
            raise AssertionError("execute 调用次数超出预期")
        return self._results.pop(0)


@pytest.mark.asyncio
async def test_returns_published_version_models_for_conversation():
    session = _FakeSession(
        [
            _FakeResult(one="agent-1"),
            _FakeResult(first=("deepseek-v4-flash", "vision-synth")),
        ]
    )

    primary, synthesis = await chat_endpoint._load_conversation_agent_models(
        session, conversation_id="conv-1", user_id="7"
    )

    assert primary == "deepseek-v4-flash"
    assert synthesis == "vision-synth"


@pytest.mark.asyncio
async def test_returns_empty_pair_when_conversation_has_no_history():
    session = _FakeSession([_FakeResult(one=None)])

    assert await chat_endpoint._load_conversation_agent_models(
        session, conversation_id="conv-x", user_id="7"
    ) == ("", "")


@pytest.mark.asyncio
async def test_returns_empty_pair_when_agent_has_no_published_version():
    session = _FakeSession([_FakeResult(one="agent-1"), _FakeResult(first=None)])

    assert await chat_endpoint._load_conversation_agent_models(
        session, conversation_id="conv-1", user_id="7"
    ) == ("", "")


@pytest.mark.asyncio
async def test_blank_version_columns_are_normalized():
    session = _FakeSession(
        [_FakeResult(one="agent-1"), _FakeResult(first=("  ", None))]
    )

    assert await chat_endpoint._load_conversation_agent_models(
        session, conversation_id="conv-1", user_id="7"
    ) == ("", "")


def test_context_usage_endpoint_wires_version_models_into_budget():
    """护栏：端点必须经由版本模型解析预算，且把合成模型一并交给预算契约。"""
    import inspect

    endpoint_source = inspect.getsource(chat_endpoint.get_conversation_context_usage)
    assert "_resolve_context_usage_models" in endpoint_source, (
        "context-usage 必须按会话智能体的版本模型解析窗口，不能直接退回配置兜底"
    )
    assert "synthesis_runtime_model_info=" in endpoint_source, (
        "执行期预算取主模型与合成模型的较小窗口，水位线必须同口径"
    )

    resolver_source = inspect.getsource(chat_endpoint._resolve_context_usage_models)
    assert "_load_conversation_agent_models" in resolver_source, (
        "版本模型反查必须无条件执行"
    )
    assert "if not requested_model_id" not in resolver_source, (
        "反查版本配置不能只在「没传 model_id」时做：合成模型只存在于版本配置里"
    )

# —— 显式选中模型时，合成模型仍必须参与预算 ——
#
# 这是一条真实的漏修：初版只在「没传 model_id」时才反查版本配置，于是用户手动
# 选了大窗口模型时 synthesis_name 恒空、分母按主模型算，而执行期预算取的是
# min(主模型, 合成模型)。界面说还装得下，后端早已按合成模型窗口压缩。


def test_merge_keeps_version_synthesis_model_when_model_is_picked():
    primary, synthesis = chat_endpoint._merge_context_usage_model_names(
        "gpt-oss-120b", ("deepseek-v4-flash", "small-synth")
    )

    assert primary == "gpt-oss-120b"
    assert synthesis == "small-synth", "显式选模型不能把版本配置的合成模型丢掉"


def test_merge_falls_back_to_version_primary():
    assert chat_endpoint._merge_context_usage_model_names(
        None, ("deepseek-v4-flash", "small-synth")
    ) == ("deepseek-v4-flash", "small-synth")


def test_merge_drops_synthesis_when_same_as_primary():
    assert chat_endpoint._merge_context_usage_model_names(
        "same-model", ("other", "same-model")
    ) == ("same-model", "")


def test_merge_normalizes_blank_values():
    assert chat_endpoint._merge_context_usage_model_names("  ", ("", "  ")) == ("", "")
    assert chat_endpoint._merge_context_usage_model_names("", ("p", "")) == ("p", "")

class _FakeModelRow:
    def __init__(self, model_id, context_size=128000, max_output_tokens=8192):
        self.model_id = model_id
        self.context_size = context_size
        self.max_output_tokens = max_output_tokens


@pytest.mark.asyncio
async def test_resolve_models_keeps_synthesis_row_when_user_picked_a_model():
    """真实调用路径：显式选中模型时仍必须去查版本的合成模型。

    这条是本轮修掉的一个真实漏修——初版只在「没传 model_id」时才反查版本配置，
    于是用户手选大窗口模型时合成模型被丢掉，水位线分母偏大（界面说还装得下，
    后端早已按合成模型的较小窗口在压缩）。
    """
    session = _FakeSession(
        [
            _FakeResult(one="agent-1"),
            _FakeResult(first=("deepseek-v4-flash", "small-synth")),
            _FakeResult(one=_FakeModelRow("gpt-oss-120b", context_size=131072)),
            _FakeResult(one=_FakeModelRow("small-synth", context_size=32768)),
        ]
    )

    primary_row, synthesis_row = await chat_endpoint._resolve_context_usage_models(
        session, conversation_id="conv-1", user_id="7", requested_model_id="gpt-oss-120b"
    )

    assert primary_row.model_id == "gpt-oss-120b"
    assert synthesis_row is not None, "显式选模型时合成模型行不能被跳过"
    assert synthesis_row.context_size == 32768


@pytest.mark.asyncio
async def test_resolve_models_without_history_still_uses_requested_model():
    session = _FakeSession(
        [
            _FakeResult(one=None),
            _FakeResult(one=_FakeModelRow("gpt-oss-120b")),
        ]
    )

    primary_row, synthesis_row = await chat_endpoint._resolve_context_usage_models(
        session, conversation_id="conv-new", user_id="7", requested_model_id="gpt-oss-120b"
    )

    assert primary_row.model_id == "gpt-oss-120b"
    assert synthesis_row is None


@pytest.mark.asyncio
async def test_resolve_models_skips_synthesis_lookup_when_same_as_primary():
    """版本把合成模型配成和主模型同名时不必再查一次（去重后为空）。"""
    session = _FakeSession(
        [
            _FakeResult(one="agent-1"),
            _FakeResult(first=("same-model", "same-model")),
            _FakeResult(one=_FakeModelRow("same-model")),
        ]
    )

    primary_row, synthesis_row = await chat_endpoint._resolve_context_usage_models(
        session, conversation_id="conv-1", user_id="7", requested_model_id=None
    )

    assert primary_row.model_id == "same-model"
    assert synthesis_row is None
    assert not session._results, "合成模型同名时不应再发一次模型查询"
