"""采样与开关决策。

采样粒度为 **trace**：同一个 trace 下的所有 span 共用一次哈希决策，避免出现
「一次对话只上报了一半 span」的残链。因此在导出钩子里按 trace_id 判定，而不是按 span 判定。
"""

import hashlib
import math
from typing import Optional

# 用 64 位无符号空间做比较，分母固定，便于跨节点复现同一决策。
_SCALE = float(1 << 64)


def _normalize_rate(sample_rate) -> Optional[float]:
    """把配置值规整成 [0, 1] 的浮点；非法值返回 None（视为关闭）。"""
    try:
        rate = float(sample_rate)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(rate):
        return None
    if rate < 0.0 or rate > 1.0:
        return None
    return rate


def _bucket(trace_id: Optional[str]) -> float:
    """把 trace_id 稳定映射到 [0, 1) 的确定值。"""
    raw = str(trace_id).encode("utf-8") if trace_id is not None else b""
    digest = hashlib.sha256(raw).digest()[:8]
    return int.from_bytes(digest, "big") / _SCALE


def is_sampled(trace_id: Optional[str], sample_rate) -> bool:
    """按 trace 粒度决定该 trace 是否上报。

    - 采样率非法（非数字、NaN、越界）→ 关闭，避免误开全量上报；
    - ``>= 1`` → 全采样；``<= 0`` → 全丢弃；
    - 其余按 sha256(trace_id) 的稳定分桶比较。
    """
    rate = _normalize_rate(sample_rate)
    if rate is None:
        return False
    if rate >= 1.0:
        return True
    if rate <= 0.0:
        return False
    return _bucket(trace_id) < rate
