from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# 默认连续相同有效句子熔断阈值（连续 20 次即熔断）
DEFAULT_REPETITION_THRESHOLD = 20
# 默认参与重复判定的最小有效句子长度（字符数），避免误杀“好的”、“然后”等简短连词
DEFAULT_MIN_PHRASE_LEN = 6
# 相同正文块连续出现的周期数。正文块需要至少包含两个句子。
DEFAULT_BLOCK_REPETITION_THRESHOLD = 15
# 参与正文块重复判定的最小有效字符数。
DEFAULT_MIN_BLOCK_LEN = 40
# 无句末标点的短文本片段连续重复阈值。
DEFAULT_COMPACT_REPETITION_THRESHOLD = 20
# 短文本重复检测的长度范围，排除“好的”“嗯嗯”等过短插话。
DEFAULT_MIN_COMPACT_LEN = 4
DEFAULT_MAX_COMPACT_LEN = 32
# 未完成句子的最大缓存大小
DEFAULT_WINDOW_SIZE = 1000

# 句末标点正则。换行、逗号和 Markdown/代码符号不作为句子边界。
_SENTENCE_DELIMITERS_PATTERN = re.compile(r"[。！？；!?;.]+")
_FENCE_LINE_PATTERN = re.compile(r"^\s*(?:```|~~~)")
_MARKDOWN_FORMAT_CHARS = frozenset("-|: \t")


@dataclass
class RepetitionVerdict:
    """重复检测裁决结果。"""
    fused: bool = False
    repeated_phrase: str = ""
    repeat_count: int = 0
    message: str = ""


@dataclass
class StreamRepetitionDetector:
    """流式文本生成重复死循环熔断检测器 (Stream Repetition Detector)。

    用于在 SSE 流式生成或 Agent 思考旁白链路中，实时拦截大语言模型（LLM）
    陷入自回归局部死循环（Repetition Degeneration）。

    检测策略：
    仅对句末标点分隔出的完整有效句子进行连续重复检测。达到 ``threshold``（默认 20）次的
    相同句子，或相同正文块达到 ``block_threshold``（默认 15）个周期，会触发熔断。
    长度达到 4 个字符的短句连续重复 20 次，或无标点短文本片段连续重复
    ``compact_threshold``（默认 20）次，也会触发熔断。Markdown 代码块和纯格式行不参与判断。
    """
    threshold: int = DEFAULT_REPETITION_THRESHOLD
    min_phrase_len: int = DEFAULT_MIN_PHRASE_LEN
    window_size: int = DEFAULT_WINDOW_SIZE
    block_threshold: int = DEFAULT_BLOCK_REPETITION_THRESHOLD
    min_block_len: int = DEFAULT_MIN_BLOCK_LEN
    compact_threshold: int = DEFAULT_COMPACT_REPETITION_THRESHOLD
    min_compact_len: int = DEFAULT_MIN_COMPACT_LEN
    max_compact_len: int = DEFAULT_MAX_COMPACT_LEN

    _buffer: str = field(default="", init=False)
    _last_phrase: str = field(default="", init=False)
    _repeat_count: int = field(default=0, init=False)
    _last_compact_phrase: str = field(default="", init=False)
    _compact_repeat_count: int = field(default=0, init=False)
    _compact_stream_buffer: str = field(default="", init=False)
    _phrase_history: list[str] = field(default_factory=list, init=False)
    _in_fenced_code: bool = field(default=False, init=False)
    _line_prefix: str = field(default="", init=False)
    _skip_current_line: bool = field(default=False, init=False)
    _fused: bool = field(default=False, init=False)
    _fused_verdict: Optional[RepetitionVerdict] = field(default=None, init=False)

    @property
    def is_fused(self) -> bool:
        return self._fused

    def reset(self) -> None:
        """重置检测器状态（用于新一轮调用或工具执行后）。"""
        self._buffer = ""
        self._last_phrase = ""
        self._repeat_count = 0
        self._last_compact_phrase = ""
        self._compact_repeat_count = 0
        self._compact_stream_buffer = ""
        self._phrase_history.clear()
        self._in_fenced_code = False
        self._line_prefix = ""
        self._skip_current_line = False
        self._fused = False
        self._fused_verdict = None

    def _filter_markdown(self, text: str) -> str:
        """过滤代码围栏内容，避免代码中的标点参与正文重复检测。"""
        filtered: list[str] = []
        for char in text:
            if char == "\n":
                if not self._in_fenced_code and not self._skip_current_line:
                    filtered.append(char)
                self._line_prefix = ""
                self._skip_current_line = False
                continue

            self._line_prefix += char
            if self._skip_current_line:
                continue
            if _FENCE_LINE_PATTERN.match(self._line_prefix):
                self._in_fenced_code = not self._in_fenced_code
                self._skip_current_line = True
                continue

            if self._in_fenced_code:
                continue

            # 含有列分隔符的 Markdown 表格行整体跳过，兼容有/无外层竖线的写法。
            if "|" in self._line_prefix:
                self._skip_current_line = True
                continue

            # 纯 Markdown 分隔线/表格分隔符没有有效正文，不送入句子检测。
            if len(self._line_prefix) >= 3 and all(
                item in _MARKDOWN_FORMAT_CHARS for item in self._line_prefix
            ):
                self._skip_current_line = True
                continue
            filtered.append(char)
        return "".join(filtered)

    def _block_verdict(self) -> Optional[RepetitionVerdict]:
        """识别至少两个句子组成的、连续重复十五周期的正文块。"""
        history = self._phrase_history
        max_block_size = min(8, len(history) // self.block_threshold)
        for block_size in range(2, max_block_size + 1):
            required = block_size * self.block_threshold
            if len(history) < required:
                continue
            cycles = [
                history[-block_size * (index + 1): -block_size * index or None]
                for index in range(self.block_threshold)
            ]
            if not all(cycle == cycles[0] for cycle in cycles[1:]):
                continue
            # 单句重复交给句子阈值处理，避免重复块规则把句子阈值意外降到 3 次。
            if len(set(cycles[0])) < 2:
                continue
            repeated_text = " ".join(cycles[0])
            if len(repeated_text) < self.min_block_len:
                continue
            return RepetitionVerdict(
                fused=True,
                repeated_phrase=repeated_text,
                repeat_count=self.block_threshold,
                message=(
                    f"检测到模型连续重复输出相同内容块「{repeated_text[:30]}」"
                    f"达 {self.block_threshold} 个周期，已触发防刷屏流式截断。"
                ),
            )
        return None

    def _is_compact_candidate(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", text)
        if not self.min_compact_len <= len(compact) <= self.max_compact_len:
            return False
        return re.fullmatch(r"[\u4e00-\u9fffA-Za-z0-9]+", compact) is not None

    def _compact_phrase_verdict(self, phrase: str) -> Optional[RepetitionVerdict]:
        if not self._is_compact_candidate(phrase):
            self._last_compact_phrase = ""
            self._compact_repeat_count = 0
            return None

        if phrase == self._last_compact_phrase:
            self._compact_repeat_count += 1
        else:
            self._last_compact_phrase = phrase
            self._compact_repeat_count = 1

        if self._compact_repeat_count < self.threshold:
            return None
        return RepetitionVerdict(
            fused=True,
                repeated_phrase=phrase,
                repeat_count=self._compact_repeat_count,
                message=(
                    f"检测到模型连续重复输出短文本片段「{phrase[:30]}」"
                f"达 {self._compact_repeat_count} 次，已触发防刷屏流式截断。"
            ),
        )

    def _compact_stream_verdict(self) -> Optional[RepetitionVerdict]:
        text = self._compact_stream_buffer
        max_unit_len = min(self.max_compact_len, len(text) // self.compact_threshold)
        for unit_len in range(self.min_compact_len, max_unit_len + 1):
            unit = text[-unit_len:]
            if not self._is_compact_candidate(unit):
                continue
            count = 0
            while len(text) >= (count + 1) * unit_len:
                start = len(text) - (count + 1) * unit_len
                if text[start: start + unit_len] != unit:
                    break
                count += 1
            if count >= self.compact_threshold:
                return RepetitionVerdict(
                    fused=True,
                    repeated_phrase=unit,
                    repeat_count=count,
                    message=(
                        f"检测到模型连续重复输出短文本片段「{unit[:30]}」"
                        f"达 {count} 次，已触发防刷屏流式截断。"
                    ),
                )
        return None

    def _normalize_phrase(self, text: str) -> str:
        """归一化句子：去除两端空白与常见标点，折叠连续空白。"""
        cleaned = re.sub(r"\s+", " ", text).strip()
        cleaned = cleaned.strip(" \t\n\r。，！？；：\"'“”‘’(),.!?;:[]{}（）")
        return cleaned

    def feed(self, delta: str) -> RepetitionVerdict:
        """接收流式文本增量，只评估其中已经结束的完整句子。"""
        if self._fused and self._fused_verdict is not None:
            return self._fused_verdict

        if not delta:
            return RepetitionVerdict(fused=False)

        filtered_delta = self._filter_markdown(str(delta))
        if filtered_delta:
            self._compact_stream_buffer += re.sub(r"\s+", "", filtered_delta)
            compact_stream_verdict = self._compact_stream_verdict()
            if compact_stream_verdict is not None:
                self._fused = True
                self._fused_verdict = compact_stream_verdict
                logger.warning(
                    "[StreamRepetitionDetector] Compact stream fused: phrase='%s' count=%d",
                    compact_stream_verdict.repeated_phrase,
                    compact_stream_verdict.repeat_count,
                )
                return compact_stream_verdict
            if len(self._compact_stream_buffer) > self.window_size:
                self._compact_stream_buffer = self._compact_stream_buffer[-self.window_size:]

        self._buffer += filtered_delta
        if not self._buffer:
            return RepetitionVerdict(fused=False, repeat_count=self._repeat_count)

        # 只切出已经遇到句末标点的完整句子，保留末尾未完成句子等待下一次增量。
        pieces = _SENTENCE_DELIMITERS_PATTERN.split(self._buffer)
        if len(pieces) == 1:
            if len(self._buffer) > self.window_size:
                self._buffer = self._buffer[-self.window_size:]
            return RepetitionVerdict(fused=False, repeat_count=self._repeat_count)

        complete_pieces = pieces[:-1]
        self._buffer = pieces[-1]

        for piece in complete_pieces:
            normalized = self._normalize_phrase(piece)
            if len(normalized) < self.min_phrase_len:
                compact_verdict = self._compact_phrase_verdict(normalized)
                if compact_verdict is not None:
                    self._fused = True
                    self._fused_verdict = compact_verdict
                    logger.warning(
                        "[StreamRepetitionDetector] Compact phrase fused: phrase='%s' count=%d",
                        compact_verdict.repeated_phrase,
                        compact_verdict.repeat_count,
                    )
                    return compact_verdict
                # 短插话/连接词是连续输出序列中的边界，不能被忽略。
                self._last_phrase = ""
                self._repeat_count = 0
                continue

            self._last_compact_phrase = ""
            self._compact_repeat_count = 0

            if normalized == self._last_phrase:
                self._repeat_count += 1
            else:
                self._last_phrase = normalized
                self._repeat_count = 1

            self._phrase_history.append(normalized)
            max_history = max(24, self.block_threshold * 8)
            if len(self._phrase_history) > max_history:
                del self._phrase_history[:-max_history]

            block_verdict = self._block_verdict()
            if block_verdict is not None:
                self._fused = True
                self._fused_verdict = block_verdict
                logger.warning(
                    "[StreamRepetitionDetector] Body block fused: phrase='%s' cycles=%d",
                    block_verdict.repeated_phrase,
                    block_verdict.repeat_count,
                )
                return block_verdict

            if self._repeat_count >= self.threshold:
                self._fused = True
                verdict = RepetitionVerdict(
                    fused=True,
                    repeated_phrase=self._last_phrase,
                    repeat_count=self._repeat_count,
                    message=(
                        f"检测到模型连续重复输出相同句子「{self._last_phrase[:30]}」"
                        f"达 {self._repeat_count} 次，已触发防刷屏流式截断。"
                    ),
                )
                self._fused_verdict = verdict
                logger.warning(
                    "[StreamRepetitionDetector] Sentence fused: phrase='%s' count=%d",
                    verdict.repeated_phrase,
                    verdict.repeat_count,
                )
                return verdict

        if len(self._buffer) > self.window_size:
            self._buffer = self._buffer[-self.window_size:]
        return RepetitionVerdict(fused=False, repeat_count=self._repeat_count)
