from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


AGENT_TOOL_LOOP_GLOBAL_LIMIT_KEY = "agent_tool_loop_global_limit"

# 同参重复调用熔断阈值放宽至 5 次，为通用工具重试提供更充裕的容错空间。
DEFAULT_FUSE_THRESHOLD = 5
# 全局熔断：单轮内所有工具调用总次数上限，防止"无限换参数"绕过同参重复检测。
DEFAULT_GLOBAL_LIMIT = 50
# 浏览器会话可能包含大量分步表单与快照操作，适度放宽全局上限。
DEFAULT_BROWSER_GLOBAL_LIMIT = 50
MIN_GLOBAL_LIMIT = 1
MAX_GLOBAL_LIMIT = 3600
# ping-pong：两个工具交替调用（A→B→A→B…）达到该长度即熔断。
DEFAULT_PING_PONG_THRESHOLD = 6
# 仅保留最近若干次调用用于序列模式识别，避免内存无界增长。
_MAX_SEQUENCE_LEN = 64

#: 循环检测白名单：这类工具对检测器**完全透明**——不占全局调用额度、不进调用
#: 序列、不参与同参重复与 ping-pong 的任何一类判定。
#:
#: 收录标准只有一条：**它做的是「把已有进展记下来」，不是「往外取东西」**。
#:
#: ``todo_write`` 在名单里，是因为提示词要求模型「每完成、失败或取消一个阶段都
#: 更新清单」——一个 5 步任务本来就要调五六次，这是设计要求不是空转。而它每次
#: 的 ``status`` 都在变、签名也就每次不同，同参重复根本拦不住；反过来它若留在
#: 调用序列里，``todo_write ↔ 某个业务工具`` 这种最正常的多步推进还会被凑成
#: ping-pong 把整轮误熔断。两头都不对，只能是透明。
#:
#: 放它进来的代价要认：这类工具不再受任何单轮计数约束。之所以可接受，是它每
#: 次调用都要吐一份完整清单（还有 ≤20 项、content 不重复等校验），空刷没有收益；
#: 真正的空转——反复调同一个业务工具——仍然被上面三道全部拦住。
LOOP_DETECTION_EXEMPT_TOOL_NAMES = frozenset({"todo_write"})
# 取时/相对日期解析：参数差异通常无意义，按工具名聚合并在更低阈值熔断。
_TIME_ANCHOR_TOOL_NAMES = frozenset({"get_current_time", "resolve_relative_dates"})
_TIME_ANCHOR_REPEAT_THRESHOLD = 2

# 浏览器观察类只读工具：每次动作后重新观察是合法标准范式
_BROWSER_OBSERVATION_TOOLS = frozenset({
    "browser_snapshot",
    "browser_read_visible",
    "browser_tabs",
})

# 浏览器动作类工具：触发动作后，应重置浏览器观察类工具的连续重复计数
_BROWSER_ACTION_TOOLS = frozenset({
    "browser_open",
    "browser_click",
    "browser_fill",
    "browser_press",
    "browser_scroll",
    "browser_hover",
    "browser_drag",
    "browser_slider_drag",
    "browser_select_option",
    "browser_wait_for",
    "browser_switch_tab",
    "browser_close_tab",
    "browser_upload",
    "browser_download",
    "browser_back",
    "browser_forward",
    "browser_reload",
    "browser_execute_js",
    "browser_set_cookies",
    "browser_handle_dialog",
})

# 工作区/文件观察类只读工具
_WORKSPACE_OBSERVATION_TOOLS = frozenset({
    "read_file",
    "read",
    "Read",
    "search_text",
    "grep",
    "Grep",
    "directory_tree_navigator",
    "list_process",
})

# 工作区状态变更与执行类动作工具：修改代码或执行命令后，应重置文件观察类工具的重复计数
_WORKSPACE_ACTION_TOOLS = frozenset({
    "write_file",
    "write",
    "Write",
    "exec_command",
    "bash",
    "Bash",
    "manage_process",
    "sqlite_scratchpad",
    "publish_generated_file",
    "excel_document_write",
    "word_document_write",
})


def validate_agent_tool_loop_global_limit(value: Any) -> None:
    """校验管理端保存的单轮工具调用总次数上限。"""
    if isinstance(value, bool):
        raise ValueError(
            f"{AGENT_TOOL_LOOP_GLOBAL_LIMIT_KEY} must be an integer between 1 and 3600"
        )
    if isinstance(value, int):
        candidate = value
    elif isinstance(value, str) and value.isdigit():
        candidate = int(value)
    else:
        raise ValueError(
            f"{AGENT_TOOL_LOOP_GLOBAL_LIMIT_KEY} must be an integer between 1 and 3600"
        )
    if not MIN_GLOBAL_LIMIT <= candidate <= MAX_GLOBAL_LIMIT:
        raise ValueError(
            f"{AGENT_TOOL_LOOP_GLOBAL_LIMIT_KEY} must be an integer between 1 and 3600"
        )


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

    - ``repeat``：同一工具 + 相同归一化参数重复调用达到 ``threshold``（默认 5 次）次。
      (针对浏览器观察类工具如 ``browser_snapshot`` 或文件读取工具如 ``read_file``，发生对应的动作类操作后
      会自动重置其同参计数，避免多步正常开发与操作中因每步观察而误判熔断；仅连续纯调用无动作时熔断)。
    - ``ping_pong``：两个工具严格交替调用（A→B→A→B…）达到 ``ping_pong_threshold`` 次，
      用于捕捉"取 schema → 执行 SQL → 又取 schema → 又执行"这类来回拉锯。
      (标准 ``Action ↔ Observation`` 观察与自测范式予以豁免)。
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
    fuse_count: int = 0
    _has_browser_tools: bool = False

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
        if tool_name in _TIME_ANCHOR_TOOL_NAMES:
            return f"{tool_name}:"
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
        pair_set = {seq[n - length:][j] for j in range(length)}
        if len(pair_set) != 2:
            return 0
        # 若交替对中包含观察类工具（如 动作 ↔ 观察），属于合法的多步操作观察/自测范式，豁免 ping-pong
        has_browser_pair = any(item in _BROWSER_OBSERVATION_TOOLS for item in pair_set) and any(item in _BROWSER_ACTION_TOOLS for item in pair_set)
        has_workspace_pair = any(item in _WORKSPACE_OBSERVATION_TOOLS for item in pair_set) and any(item in _WORKSPACE_ACTION_TOOLS for item in pair_set)
        if has_browser_pair or has_workspace_pair:
            return 0
        return length

    def record(self, tool_name: str, tool_args: dict[str, Any] | None) -> ToolLoopVerdict:
        if not self.enabled or not tool_name:
            return ToolLoopVerdict(fused=False, count=0)
        if self.fused:
            return ToolLoopVerdict(
                fused=True,
                count=self.fuse_count,
                message=self.fuse_reason,
                reason_code=self.fuse_reason_code,
            )

        # 白名单工具在这里直接返回：不 total_calls += 1、不进 _signatures、
        # 不进 _sequence。放在 `fused` 判断之后是有意的——整轮已经熔断时，调白名单
        # 工具也不该让调用方以为「还能继续」，那不是豁免范围。
        if tool_name in LOOP_DETECTION_EXEMPT_TOOL_NAMES:
            return ToolLoopVerdict(fused=False, count=0)

        if tool_name.startswith("browser_"):
            self._has_browser_tools = True

        # 若发生了浏览器动作类操作，重置所有浏览器观察类工具的累积计数（允许下一步重新正常观察页面）
        if tool_name in _BROWSER_ACTION_TOOLS:
            for obs_tool in _BROWSER_OBSERVATION_TOOLS:
                keys_to_remove = [k for k in self._signatures if k.startswith(f"{obs_tool}:")]
                for k in keys_to_remove:
                    self._signatures.pop(k, None)

        # 若发生了工作区写文件或执行命令动作，重置文件观察类工具的累积计数（允许修改后重新检查代码）
        if tool_name in _WORKSPACE_ACTION_TOOLS:
            for obs_tool in _WORKSPACE_OBSERVATION_TOOLS:
                keys_to_remove = [k for k in self._signatures if k.startswith(f"{obs_tool}:")]
                for k in keys_to_remove:
                    self._signatures.pop(k, None)

        self.total_calls += 1
        self._sequence.append(tool_name)
        if len(self._sequence) > _MAX_SEQUENCE_LEN:
            self._sequence = self._sequence[-_MAX_SEQUENCE_LEN:]

        signature = self.tool_call_signature(tool_name, tool_args)
        count = self._signatures.get(signature, 0) + 1
        self._signatures[signature] = count

        repeat_threshold = max(1, self.threshold)
        if tool_name in _TIME_ANCHOR_TOOL_NAMES:
            repeat_threshold = min(repeat_threshold, _TIME_ANCHOR_REPEAT_THRESHOLD)

        # 1) 同参重复
        if count >= repeat_threshold:
            return self._fuse(
                "repeat",
                count,
                (
                    f"工具 `{tool_name}` 使用相同参数连续/重复调用 {count} 次，"
                    "系统判断继续执行大概率只会消耗步数。"
                ),
            )

        # 2) 全局熔断（最后兜底，优先于 ping-pong 给出更明确的"总量超限"信号）
        effective_global_limit = self.global_limit
        if self._has_browser_tools and self.global_limit > 0:
            effective_global_limit = max(self.global_limit, DEFAULT_BROWSER_GLOBAL_LIMIT)

        if effective_global_limit > 0 and self.total_calls >= effective_global_limit:
            return self._fuse(
                "circuit_breaker",
                self.total_calls,
                (
                    f"本轮工具调用总数已达 {self.total_calls} 次（全局熔断阈值 {effective_global_limit}），"
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
        self.fuse_count = count
        return ToolLoopVerdict(fused=True, count=count, message=message, reason_code=reason_code)
