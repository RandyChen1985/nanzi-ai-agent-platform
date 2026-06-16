from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

DEFAULT_FUSE_THRESHOLD = 3
# 全局熔断：单轮内所有工具调用总次数上限，防止"无限换参数"绕过同参重复检测。
DEFAULT_GLOBAL_LIMIT = 30
# ping-pong：两个工具交替调用（A→B→A→B…）达到该长度即熔断。
DEFAULT_PING_PONG_THRESHOLD = 6
# 仅保留最近若干次调用用于序列模式识别，避免内存无界增长。
_MAX_SEQUENCE_LEN = 64


@dataclass
class ToolLoopVerdict:
    fused: bool = False
    count: int = 0
    message: str = ""
    # 熔断原因码，便于上层区分处理/埋点：repeat / ping_pong / circuit_breaker
    reason_code: str = ""


@dataclass
class ToolLoopDetector:
    """检测无效的工具调用循环。

    支持三类检测（任一触发即熔断，后续 record 直接返回已熔断结果）：

    - ``repeat``：同一工具 + 相同归一化参数重复调用达到 ``threshold`` 次。
    - ``ping_pong``：两个工具严格交替调用（A→B→A→B…）达到 ``ping_pong_threshold`` 次，
      用于捕捉"取 schema → 执行 SQL → 又取 schema → 又执行"这类来回拉锯。
    - ``circuit_breaker``：单轮内工具调用总次数达到 ``global_limit``，作为最后兜底，
      防止模型不断变换参数绕过同参重复检测而空转。
    """

    threshold: int = DEFAULT_FUSE_THRESHOLD
    enabled: bool = True
    ping_pong_threshold: int = DEFAULT_PING_PONG_THRESHOLD
    global_limit: int = DEFAULT_GLOBAL_LIMIT
    _signatures: dict[str, int] = field(default_factory=dict)
    _sequence: list[str] = field(default_factory=list)
    total_calls: int = 0
    fused: bool = False
    fuse_reason: str = ""
    fuse_reason_code: str = ""

    @staticmethod
    def normalize_arg_value(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                str(key): ToolLoopDetector.normalize_arg_value(value[key])
                for key in sorted(value.keys(), key=str)
            }
        if isinstance(value, list):
            return [ToolLoopDetector.normalize_arg_value(item) for item in value]
        if isinstance(value, str):
            return " ".join(value.strip().split())
        return value

    @classmethod
    def tool_call_signature(cls, tool_name: str, tool_args: dict[str, Any] | None) -> str:
        normalized_args = cls.normalize_arg_value(tool_args or {})
        try:
            args_text = json.dumps(normalized_args, ensure_ascii=False, sort_keys=True, default=str)
        except Exception:
            args_text = str(normalized_args)
        return f"{tool_name}:{args_text}"

    def _trailing_ping_pong_length(self) -> int:
        """返回序列尾部"两工具严格交替"的最长长度（要求恰好 2 个不同工具）。"""
        seq = self._sequence
        n = len(seq)
        if n < 2:
            return 0
        length = 1
        for i in range(n - 1, 0, -1):
            # 交替意味着相邻不相等，且与隔一位相等
            if seq[i] == seq[i - 1]:
                break
            if i + 1 < n and seq[i - 1] != seq[i + 1]:
                break
            length += 1
        # 必须恰好由两个不同工具构成，纯重复（同名）不算 ping-pong
        if len({seq[n - length:][j] for j in range(length)}) != 2:
            return 0
        return length

    def record(self, tool_name: str, tool_args: dict[str, Any] | None) -> ToolLoopVerdict:
        if not self.enabled or self.fused or not tool_name:
            return ToolLoopVerdict(fused=False, count=0)

        self.total_calls += 1
        self._sequence.append(tool_name)
        if len(self._sequence) > _MAX_SEQUENCE_LEN:
            self._sequence = self._sequence[-_MAX_SEQUENCE_LEN:]

        signature = self.tool_call_signature(tool_name, tool_args)
        count = self._signatures.get(signature, 0) + 1
        self._signatures[signature] = count

        # 1) 同参重复
        if count >= max(1, self.threshold):
            return self._fuse(
                "repeat",
                count,
                (
                    f"工具 `{tool_name}` 使用相同参数连续/重复调用 {count} 次，"
                    "系统判断继续执行大概率只会消耗步数。"
                ),
            )

        # 2) 全局熔断（最后兜底，优先于 ping-pong 给出更明确的"总量超限"信号）
        if self.global_limit > 0 and self.total_calls >= self.global_limit:
            return self._fuse(
                "circuit_breaker",
                self.total_calls,
                (
                    f"本轮工具调用总数已达 {self.total_calls} 次（全局熔断阈值 {self.global_limit}），"
                    "系统中止以避免无意义空转。"
                ),
            )

        # 3) ping-pong 交替
        if self.ping_pong_threshold > 0:
            pp_len = self._trailing_ping_pong_length()
            if pp_len >= self.ping_pong_threshold:
                pair = sorted(set(self._sequence[-pp_len:]))
                return self._fuse(
                    "ping_pong",
                    pp_len,
                    (
                        f"工具 `{pair[0]}` 与 `{pair[1]}` 交替调用 {pp_len} 次仍无进展，"
                        "系统判断已陷入拉锯循环并中止。"
                    ),
                )

        return ToolLoopVerdict(fused=False, count=count)

    def _fuse(self, reason_code: str, count: int, message: str) -> ToolLoopVerdict:
        self.fused = True
        self.fuse_reason = message
        self.fuse_reason_code = reason_code
        return ToolLoopVerdict(fused=True, count=count, message=message, reason_code=reason_code)
