"""智能体单请求执行耗时观测。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable


_MODEL_TRACE_EVENTS = frozenset({"thought", "synthesis", "model_call"})
_ANSWER_CHUNK_TYPES = frozenset({"", "answer_delta", "process_narration_promote"})
_VISIBLE_ACTIVITY_CHUNK_TYPES = _ANSWER_CHUNK_TYPES | frozenset({"process_narration"})


def _elapsed_ms(started_at: float, now: float) -> float:
    return round(max(0.0, now - started_at) * 1000, 2)


def _is_visible_answer_chunk(chunk: Any) -> bool:
    if not isinstance(chunk, dict):
        return False
    chunk_type = str(chunk.get("type") or "")
    if chunk_type not in _ANSWER_CHUNK_TYPES:
        return False
    return bool(str(chunk.get("content") or "").strip())


def _is_visible_activity_chunk(chunk: Any) -> bool:
    if not isinstance(chunk, dict):
        return False
    chunk_type = str(chunk.get("type") or "")
    if chunk_type not in _VISIBLE_ACTIVITY_CHUNK_TYPES:
        return False
    return bool(str(chunk.get("content") or "").strip())


def _count_trace_events(
    trace_buffer: Iterable[Any] | None,
    event_types: frozenset[str],
) -> int:
    return sum(
        1
        for step in trace_buffer or ()
        if str(getattr(step, "event_type", "") or "") in event_types
    )


@dataclass
class ExecutionPerformanceTracker:
    """记录单次智能体执行的阶段耗时和低 cardinality 计数。"""

    clock: Callable[[], float] = time.perf_counter
    started_at: float | None = None
    _stage_marks: dict[str, float] = field(default_factory=dict, init=False)
    _ttft_ms: float | None = field(default=None, init=False)
    _first_visible_activity_ms: float | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        if self.started_at is None:
            self.started_at = self.clock()

    def mark(self, stage: str) -> None:
        """记录阶段结束时相对于本次请求开始的累计耗时。"""
        if not stage:
            return
        self._stage_marks[str(stage)] = _elapsed_ms(
            self.started_at or 0.0,
            self.clock(),
        )

    def observe_chunk(self, chunk: Any) -> None:
        """记录首个可见活动与首个正文片段，不保存正文内容。

        retraction 会把已展示的正文从气泡清空（候选正文被撤回），因此复位
        正文首见时间，让后续确认正文重建测量窗口；可见活动度量不受影响。
        """
        if isinstance(chunk, dict) and str(chunk.get("type") or "") == "retraction":
            self._ttft_ms = None
            return
        is_activity = _is_visible_activity_chunk(chunk)
        is_answer = _is_visible_answer_chunk(chunk)
        if not is_activity and not is_answer:
            return
        elapsed = _elapsed_ms(self.started_at or 0.0, self.clock())
        if is_activity and self._first_visible_activity_ms is None:
            self._first_visible_activity_ms = elapsed
        if is_answer and self._ttft_ms is None:
            self._ttft_ms = elapsed

    def snapshot(
        self,
        *,
        trace_buffer: Iterable[Any] | None,
        status: str | None,
    ) -> dict[str, Any]:
        """生成不包含用户正文、prompt 或工具参数的性能快照。"""
        return {
            "total_elapsed_ms": _elapsed_ms(
                self.started_at or 0.0,
                self.clock(),
            ),
            "stages_ms": dict(self._stage_marks),
            "first_visible_activity_ms": self._first_visible_activity_ms,
            "ttft_ms": self._ttft_ms,
            "model_call_count": _count_trace_events(
                trace_buffer,
                _MODEL_TRACE_EVENTS,
            ),
            "tool_call_count": _count_trace_events(
                trace_buffer,
                frozenset({"tool_call"}),
            ),
            "status": status,
        }
