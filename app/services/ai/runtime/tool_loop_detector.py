from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

DEFAULT_FUSE_THRESHOLD = 3


@dataclass
class ToolLoopVerdict:
    fused: bool = False
    count: int = 0
    message: str = ""


@dataclass
class ToolLoopDetector:
    """Detect repeated tool calls with identical normalized arguments."""

    threshold: int = DEFAULT_FUSE_THRESHOLD
    enabled: bool = True
    _signatures: dict[str, int] = field(default_factory=dict)
    fused: bool = False
    fuse_reason: str = ""

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

    def record(self, tool_name: str, tool_args: dict[str, Any] | None) -> ToolLoopVerdict:
        if not self.enabled or self.fused or not tool_name:
            return ToolLoopVerdict(fused=False, count=0)

        signature = self.tool_call_signature(tool_name, tool_args)
        count = self._signatures.get(signature, 0) + 1
        self._signatures[signature] = count

        if count >= max(1, self.threshold):
            self.fused = True
            self.fuse_reason = (
                f"工具 `{tool_name}` 使用相同参数连续/重复调用 {count} 次，"
                "系统判断继续执行大概率只会消耗步数。"
            )
            return ToolLoopVerdict(fused=True, count=count, message=self.fuse_reason)

        return ToolLoopVerdict(fused=False, count=count)
