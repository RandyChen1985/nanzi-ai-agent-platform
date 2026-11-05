"""手选数据集必须是硬约束：检索注入 + 执行期校验。

背景（真实事故）：用户在手选数据集 1 后提问「看看用户列表」，模型却检索到了
数据集 54 的同名表并以 ``dataset_name=znt2_test_ds`` 执行成功——因为手选只拼进了
prompt 文案，工具侧只认「智能体绑定」，没有绑定就等于没有范围。同一张物理表
注册在多个数据集时，这条路径能把「限定在 1 里查」变成一句空话。
"""
from __future__ import annotations

import pathlib

import pytest

from app.services.ai.tools import data_api

_REPO = pathlib.Path(__file__).resolve().parents[2]
_DATA_API = (_REPO / "app/services/ai/tools/data_api.py").read_text(encoding="utf-8")
_REGISTRY = (_REPO / "app/services/ai/tools/registry.py").read_text(encoding="utf-8")
_SCHEMA_SERVICE = (_REPO / "app/services/chatbi_dataset_schema_service.py").read_text(encoding="utf-8")
_DELEGATE = (_REPO / "app/services/ai/tools/agent_delegate_tool.py").read_text(encoding="utf-8")


def test_schema_tool_falls_back_to_mounted_dataset_scope() -> None:
    """智能体没绑定元数据集时，get_dataset_schema 必须回退到本轮手选范围。"""
    source = _REGISTRY
    assert "effective_ids = metadata_dataset_ids" in source
    assert "resolve_mounted_dataset_scope_ids" in source
    # 回退只在「智能体没绑定」时发生，绑定仍然优先
    assert "if not effective_ids:" in source


def test_schema_retrieval_narrows_by_session_scope() -> None:
    """会话固定了数据集时，检索也必须按它收窄。

    真实事故：日志里「会话固定：1(智能体数据集)」清清楚楚，但「最终生效」还是
    1、53、54——因为这层只进日志、从没参与过滤，模型于是检索到 znt2_test_ds 的同名表。
    """
    source = _SCHEMA_SERVICE
    assert "resolve_mounted_dataset_scope_ids" in source
    assert "configured_dataset_ids is None" in source
    # 必须走同一条多字段匹配，不能只比 ID
    assert "_normalize_authorized_dataset_ids(mounted_ids)" in source


def test_mounted_scope_reads_request_then_session() -> None:
    """范围解析：本轮请求优先，其次会话固定；session 里是字典，request 里是裸 ID。"""
    from app.services.ai.metadata_dataset_scope import resolve_mounted_dataset_scope_ids

    assert resolve_mounted_dataset_scope_ids() == []  # 没有执行上下文 → 未限定


def test_schema_tool_keeps_numeric_normalization_but_not_only_numeric() -> None:
    """手选可能以名称传入，纯数字归一化会把名称全部丢掉，范围又变成全量。"""
    source = _DATA_API
    assert "authorized_dataset_ids is None and metadata_dataset_ids" in source
    assert "多字段匹配" in source


def test_execute_tool_rejects_dataset_outside_mounted_scope() -> None:
    """执行期兜底：模型硬写范围外的 dataset_name 也必须被拒。"""
    source = _DATA_API
    assert "_ensure_dataset_in_mounted_scope" in source
    assert "mounted_dataset_ids" in source


@pytest.mark.asyncio
async def test_scope_check_is_noop_without_mounted_scope() -> None:
    """用户没选数据集时不做限制，保持历史行为（否则会误伤所有普通提问）。"""
    err = await data_api._ensure_dataset_in_mounted_scope(
        session=None, dataset_name="any_ds", mounted=[]
    )
    assert err is None


@pytest.mark.asyncio
async def test_scope_check_reads_ids_and_names() -> None:
    """挂载值可能混着 ID 和名称，两种都要能解析。"""

    class _Rows:
        def all(self):  # noqa: D401 - 简化 Result 接口
            return [(1, "ai_agent_meta", "智能体数据集"), (54, "znt2_test_ds", "智能体2")]

    class _Session:
        async def execute(self, *_args, **_kwargs):  # noqa: D401
            return _Rows()

    # 名称命中 → 放行
    assert await data_api._ensure_dataset_in_mounted_scope(
        session=_Session(), dataset_name="ai_agent_meta", mounted=["1"]
    ) is None
    # 大小写不一致也放行（模型常见输出）
    assert await data_api._ensure_dataset_in_mounted_scope(
        session=_Session(), dataset_name="AI_AGENT_META", mounted=["1"]
    ) is None
    # display_name 命中 → 放行
    assert await data_api._ensure_dataset_in_mounted_scope(
        session=_Session(), dataset_name="智能体数据集", mounted=["1"]
    ) is None
    # 名称挂载同样生效
    assert await data_api._ensure_dataset_in_mounted_scope(
        session=_Session(), dataset_name="znt2_test_ds", mounted=["znt2_test_ds"]
    ) is None


@pytest.mark.asyncio
async def test_scope_check_blocks_other_dataset_with_clear_message() -> None:
    """范围外的数据集要给出可执行的中文提示，而不是笼统的拒绝。"""

    class _Rows:
        def all(self):  # noqa: D401
            return [(1, "ai_agent_meta", "智能体数据集")]

    class _Session:
        async def execute(self, *_args, **_kwargs):  # noqa: D401
            return _Rows()

    err = await data_api._ensure_dataset_in_mounted_scope(
        session=_Session(), dataset_name="znt2_test_ds", mounted=["1"]
    )
    assert err is not None
    assert "[Permission Denied]" in err
    assert "znt2_test_ds" in err
    assert "智能体数据集" in err  # 把允许范围说清楚


@pytest.mark.asyncio
async def test_scope_check_fails_closed_when_mounted_dataset_missing() -> None:
    """挂载的 ID 解析不出数据集（刚被删）时按不放行处理。"""

    class _Rows:
        def all(self):  # noqa: D401
            return []

    class _Session:
        async def execute(self, *_args, **_kwargs):  # noqa: D401
            return _Rows()

    err = await data_api._ensure_dataset_in_mounted_scope(
        session=_Session(), dataset_name="ai_agent_meta", mounted=["999999"]
    )
    assert err is not None
    assert "[Permission Denied]" in err


def test_mounted_scope_merges_request_and_session_shapes() -> None:
    """request_ids 是裸 ID，session 是 {id, name} 字典，两种都要读出来。"""
    from app.core.context import set_execution_options_context
    from app.services.ai.metadata_dataset_scope import resolve_mounted_dataset_scope_ids

    set_execution_options_context(
        {
            "metadata_dataset_scope": {
                "request_ids": ["7"],
                "session": [{"id": 1, "name": "ai_agent_meta", "display_name": "智能体数据集"}],
            }
        }
    )
    try:
        assert resolve_mounted_dataset_scope_ids() == ["7", "1"]
    finally:
        set_execution_options_context({})


def test_mounted_scope_ignores_blank_entries() -> None:
    from app.core.context import set_execution_options_context
    from app.services.ai.metadata_dataset_scope import resolve_mounted_dataset_scope_ids

    set_execution_options_context(
        {"metadata_dataset_scope": {"request_ids": [None, "", " "], "session": [{"name": "no-id"}]}}
    )
    try:
        assert resolve_mounted_dataset_scope_ids() == []
    finally:
        set_execution_options_context({})


def test_delegation_inherits_mounted_dataset_scope() -> None:
    """委派给 ChatBI 专家时，本轮范围必须继承，否则子 agent 完全不受限。

    真实事故：用户在主助手界面上选定了数据集 1，主助手委派给「数据智能助手」，
    子 context 是新建的 AgentContext、子 execution_options 只有
    {"delegated_sub_agent": True} —— 两条渠道都断了，于是又能检索到 53、54。
    """
    source = _DELEGATE
    # 子上下文显式继承
    assert "metadata_dataset_ids=list(main_ctx.metadata_dataset_ids or [])" in source
    # 子 execution_options 透传范围
    assert 'sub_execution_options: Dict[str, Any] = {"delegated_sub_agent": True}' in source
    assert 'sub_execution_options["metadata_dataset_scope"] = parent_scope' in source
    assert "execution_options=sub_execution_options" in source
