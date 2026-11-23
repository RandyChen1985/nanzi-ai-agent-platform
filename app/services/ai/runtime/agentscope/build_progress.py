"""会话运行时构建的分段耗时观测。

``_build_native_agent`` 是一段「只返回对象、不产出事件」的阻塞：工作区准备（技能
播种 / 沙箱建连）、工具绑定、工具集装配、模型与上下文配置、中间件装配、Agent 构造
都在里面完成。这段静默期只有 1s 一次的 keepalive 心跳续命，前端看不到任何业务帧，
用户感知就是「上一帧之后很久没有新事件」。

这里把分段耗时通过 ``AgentContext.event_queue`` 旁路投递到执行时间线（该队列此刻
正被 ``_execute_raw`` 的 merge_streams 实时消费）。一条卡在前端按 id 合并：

- 构建完成时给出各阶段耗时与合计；
- 首个模型调用开始时再更新同一条，补上「首次模型调用前」——这段覆盖 REPLY_START
  之后、真正发请求之前的上下文压缩与入参准备。压缩是一次额外的模型调用，期间没有任何
  事件，是「状态重置 / 长历史之后明显变慢」的主要嫌疑，必须能被看见。

旁路尽力而为：队列缺失（离线单测、非流式旁路）、投递失败、以及子代理场景都静默跳过，
绝不影响本轮。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Iterable, Mapping

logger = logging.getLogger(__name__)

#: 前端按 id 合并成一张卡，所以构建完成与首模型调用补记必须共用它。
BUILD_PROGRESS_LOG_ID = "runtime:build"
BUILD_PROGRESS_TITLE = "会话运行时准备"

#: 阶段 → 展示名。字典顺序即时间线里的展示顺序。
BUILD_STAGE_LABELS: dict[str, str] = {
    "workspace": "工作区准备",
    "tool_bind": "工具绑定",
    "toolkit": "工具集装配",
    "config": "模型与上下文配置",
    "middleware": "中间件装配",
    "agent": "Agent 构造",
}

#: 顶层阶段的子阶段 → 展示名，字典顺序即子项展示顺序。
#:
#: 子项由 :meth:`BuildStageTimer.record` 写入——它记的是**外部实测值**，不推进计时
#: 游标。这个区分很重要：工作区准备可能由 preflight 预热先发起，本轮构建只是接续同
#: 一次工作，于是「父行 = 本轮墙钟等待」与「子行 = 那次构建的内部成本」本来就不等，
#: 强行让两者相加相等只会编数据。
BUILD_STAGE_CHILDREN: dict[str, dict[str, str]] = {
    "workspace": {
        "workspace:config": "配置与会话目录",
        "workspace:skills": "技能发现",
        "workspace:sandbox": "沙箱获取",
        "workspace:preseed": "技能快照播种",
        "workspace:session": "会话工作区初始化",
        "workspace:joined": "接续预热中的构建",
    },
}


class BuildStageTimer:
    """按阶段累计耗时（毫秒），阶段首尾相接、互不重叠。

    ``mark(stage)`` 把「距上一条 mark 的时间」记到该阶段。计时口径刻意是「相邻 mark
    之间的增量」而不是「进入阶段的绝对时刻」：这样各阶段相加恰好等于总耗时，不会像
    绝对时刻相减那样把前面未归类的等待也算进来重复计数。

    ``clock`` 可注入，便于用例里不依赖真实时间。
    """

    def __init__(self, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or time.perf_counter
        self._started_at = self._clock()
        self._marked_at = self._started_at
        self._stages: dict[str, float] = {}

    def mark(self, stage: str) -> float:
        """结束上一阶段、开始 ``stage``，返回上一段耗时（毫秒）。"""
        now = self._clock()
        elapsed_ms = max(0.0, (now - self._marked_at) * 1000.0)
        self._marked_at = now
        if stage:
            self._stages[stage] = self._stages.get(stage, 0.0) + elapsed_ms
        return elapsed_ms

    def record(self, stage: str, elapsed_ms: float) -> None:
        """登记一个**外部实测**的阶段耗时，不推进计时游标。

        用于「这段耗时不是本计时器测的」场景（例如工作区准备在另一个调用方发起的
        构建里完成，这里只是把它的分段结果搬过来展示）。刻意不移动 ``_marked_at``：
        父阶段的墙钟区间必须继续按本计时器自己的刻度算，否则父行会被子行拖成重复计数。
        """
        if not stage:
            return
        self._stages[stage] = self._stages.get(stage, 0.0) + max(0.0, float(elapsed_ms))

    def elapsed_ms(self) -> float:
        """从开始到「此刻」的总耗时（毫秒）。"""
        return max(0.0, (self._clock() - self._started_at) * 1000.0)

    def stages(self) -> dict[str, float]:
        """已记录阶段的耗时快照（毫秒），未知阶段的顺序按插入序保留。"""
        return dict(self._stages)


def _child_lines(stages: Mapping[str, float], parent: str) -> list[str]:
    """父阶段的子项明细行（缩进），声明过的按声明顺序、其余按写入顺序。"""
    declared = BUILD_STAGE_CHILDREN.get(parent) or {}
    lines = [
        f"  · {label} {stages[key]:.0f}ms"
        for key, label in declared.items()
        if key in stages
    ]
    for key, value in stages.items():
        if key in declared:
            continue
        if key.startswith(f"{parent}:"):
            # 新增子阶段却忘了配文案：宁可露原始 key，也不能让这段时间凭空消失。
            lines.append(f"  · {key} {value:.0f}ms")
    return lines


def _is_child_stage(stage: str) -> bool:
    parent, _, rest = stage.partition(":")
    return bool(rest) and parent in BUILD_STAGE_CHILDREN


def format_build_details(
    stages: Mapping[str, float],
    total_ms: float,
    extra_lines: Iterable[str] = (),
) -> str:
    """把分段耗时排成时间线可读的多行文本（前端以 ``whitespace-pre-wrap`` 渲染）。"""
    lines: list[str] = []
    for stage, label in BUILD_STAGE_LABELS.items():
        children = _child_lines(stages, stage)
        value = stages.get(stage)
        if value is None and not children:
            continue
        lines.append(f"{label} {(value or 0.0):.0f}ms")
        lines.extend(children)
    # 未登记展示名的阶段也要露出来：新增 mark 却忘了配文案时，宁可显示原始 key，
    # 也不能让这段时间凭空消失。子阶段已在父行下面渲染过，这里跳过。
    for stage, value in stages.items():
        if stage in BUILD_STAGE_LABELS or _is_child_stage(stage):
            continue
        lines.append(f"{stage} {value:.0f}ms")
    lines.append(f"构建合计 {total_ms:.0f}ms")
    for line in extra_lines:
        text = str(line).strip()
        if text:
            lines.append(text)
    return "\n".join(lines)


def build_progress_event(
    *,
    status: str,
    details: str | None = None,
    execution_time_ms: float | None = None,
) -> dict[str, Any]:
    """构造 ``runtime:build`` 时间线事件（与 ``log`` 事件同构）。"""
    payload: dict[str, Any] = {
        "type": "log",
        "id": BUILD_PROGRESS_LOG_ID,
        "title": BUILD_PROGRESS_TITLE,
        "status": status,
        "category": "system",
    }
    if details:
        payload["details"] = details
    if execution_time_ms is not None:
        # 与 workspace:prepare 同口径：不足 1ms 也显示 1ms，避免「0ms」看起来像没测。
        payload["execution_time_ms"] = max(1.0, float(execution_time_ms))
    return payload


async def emit_build_progress(
    *,
    status: str,
    details: str | None = None,
    execution_time_ms: float | None = None,
) -> None:
    """把构建进度推进前端执行时间线（尽力而为，绝不阻断构建）。"""
    try:
        from app.core.context import get_current_agent_context

        ctx = get_current_agent_context()
        queue = getattr(ctx, "event_queue", None)
        if queue is None:
            return
        # 子代理也有自己的构建，但它们的事件已归到「委派智能体」容器；同一 id 会让
        # 多个子代理互相覆盖，父级的构建卡也会被顶掉。
        if getattr(ctx, "delegation_depth", 0) or getattr(
            ctx, "parent_conversation_id", None
        ):
            return
        await queue.put(
            build_progress_event(
                status=status,
                details=details,
                execution_time_ms=execution_time_ms,
            )
        )
    except Exception as exc:  # noqa: BLE001 - 进度日志是旁路，失败不影响本轮
        logger.debug("[build_progress] skipped (status=%s): %s", status, exc)
