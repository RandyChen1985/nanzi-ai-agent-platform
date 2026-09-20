"""本轮知识库引用编号台账。

知识库工具在提示词里用 ``[ID:n]`` 让模型标注引用来源，而引用量必须靠扫回答正文里的
``[ID:n]`` 才能统计出来。问题在于**编号的唯一性**：工具每次调用都从 1 重新编号时，
一轮对话里检索两次会让模型看到两组 ``[ID:1..]``，回答中写下的 ``[ID:2]`` 究竟属于
哪一次检索无从判断——统计出来的引用会被安到错误的文档上，比没有数据更糟。

台账负责两件事：

1. 发放**整轮唯一**的单调递增编号；
2. 登记 ``编号 → 切片元数据``（doc_id / doc_name / dataset_id），供回答收尾时反查。

台账随轮次建立，委派子智能体时与主智能体共享同一实例（与事实取证账本一致）。
"""

from typing import Any, Dict, Optional


class KnowledgeCitationLedger:
    """整轮唯一的知识库引用编号台账。"""

    def __init__(self) -> None:
        self._next_ref = 1
        self._entries: Dict[str, Dict[str, Any]] = {}

    @property
    def next_ref(self) -> int:
        return self._next_ref

    @property
    def entries(self) -> Dict[str, Dict[str, Any]]:
        """编号 → 切片元数据（只读副本，避免调用方误改台账内部状态）。"""
        return dict(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def register(self, chunk: Dict[str, Any]) -> str:
        """发放一个全局编号并登记切片，返回该编号的字符串形式。"""
        ref_id = str(self._next_ref)
        self._next_ref += 1
        self._entries[ref_id] = {
            "source_type": chunk.get("source_type") or "knowledge",
            "doc_id": chunk.get("doc_id"),
            "doc_name": chunk.get("doc_name"),
            "dataset_id": chunk.get("dataset_id"),
        }
        return ref_id


def get_or_create_knowledge_citation_ledger() -> Optional[KnowledgeCitationLedger]:
    """取当前轮次的引用台账；无运行上下文时返回 None（调用方退回局部编号）。"""
    try:
        from app.core.context import get_current_agent_context
    except Exception:  # pragma: no cover - 仅防御导入期循环依赖
        return None

    ctx = get_current_agent_context()
    if ctx is None:
        return None

    ledger = getattr(ctx, "knowledge_citation_ledger", None)
    if isinstance(ledger, KnowledgeCitationLedger):
        return ledger

    ledger = KnowledgeCitationLedger()
    try:
        ctx.knowledge_citation_ledger = ledger
    except Exception:
        # AgentContext 若禁止额外字段则此处会失败；退回局部编号即可，不能影响检索。
        return None
    return ledger


def new_turn_knowledge_citation_ledger(ctx: Any) -> Optional[KnowledgeCitationLedger]:
    """为本轮建立台账；委派子智能体沿用主智能体已有的实例，避免编号空间分裂。"""
    if ctx is None:
        return None

    if getattr(ctx, "delegation_depth", 0) > 0:
        shared = getattr(ctx, "knowledge_citation_ledger", None)
        if isinstance(shared, KnowledgeCitationLedger):
            return shared

    ledger = KnowledgeCitationLedger()
    try:
        ctx.knowledge_citation_ledger = ledger
    except Exception:
        return None
    return ledger
