"""快捷结果追问的内部路由上下文。

该上下文只参与服务端路由，不拼接到用户消息，也不作为事实凭证。
本轮仍必须由数据查询执行器重新获取实时结果。
"""

from dataclasses import dataclass
from typing import Any, Mapping, Optional


MAX_RESULT_ID_LENGTH = 128


@dataclass(frozen=True)
class QuickResultContext:
    source: str
    result_id: Optional[str]
    requires_fresh_data: bool


def normalize_quick_result_context(raw: Any) -> Optional[QuickResultContext]:
    """仅接受明确声明为 ChatBI 结果追问的上下文。"""
    if raw is None:
        return None

    if not isinstance(raw, Mapping):
        model_dump = getattr(raw, "model_dump", None)
        if callable(model_dump):
            try:
                raw = model_dump(exclude_none=True)
            except Exception:
                return None
    if not isinstance(raw, Mapping):
        return None

    if raw.get("source") != "chatbi_result":
        return None
    if raw.get("requires_fresh_data") is not True:
        return None

    raw_result_id = raw.get("result_id")
    result_id = str(raw_result_id).strip() if raw_result_id is not None else ""
    if len(result_id) > MAX_RESULT_ID_LENGTH:
        return None

    return QuickResultContext(
        source="chatbi_result",
        result_id=result_id or None,
        requires_fresh_data=True,
    )
