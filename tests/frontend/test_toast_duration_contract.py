"""Toast 停留时长：按文案长度自适应，而不是一刀切 2 秒。

全库 639 处 showToast 里，success/info 有约 52% 是「创建成功」这类 ≤6 字的纯确认，
另有约 31% 是 ≥20 字的说明。固定 2 秒两头都不合适：短确认白占视线，长说明读不完。
"""
import re
from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
TOAST = ROOT / "frontend/src/composables/useToast.ts"
TOAST_VUE = ROOT / "frontend/src/components/Toast.vue"

RULE_RE = re.compile(
    r"(success|info|warning|error):\s*\{\s*base:\s*(\d+),\s*perChar:\s*(\d+),\s*min:\s*(\d+),\s*max:\s*(\d+)\s*\}"
)


def _rules():
    parsed = {
        kind: tuple(int(x) for x in vals)
        for kind, *vals in RULE_RE.findall(TOAST.read_text(encoding="utf-8"))
    }
    assert set(parsed) == {"success", "info", "warning", "error"}, parsed
    return parsed


def _duration(length: int, kind: str) -> int:
    base, per_char, low, high = _rules()[kind]
    # 与 TS 端 Math.round 等价：round(x) 对 .5 的处理差异不影响本用例的整数取值
    return min(high, max(low, round(base + length * per_char)))


def test_rules_cover_all_types():
    rules = _rules()
    assert rules["success"] == (600, 130, 1200, 4000)
    assert rules["info"] == (600, 130, 1200, 4000)
    assert rules["warning"] == (1000, 160, 2500, 6000)
    assert rules["error"] == (1000, 160, 2500, 6000)


@pytest.mark.parametrize(
    ("length", "kind", "expected"),
    [
        (4, "success", 1200),   # 创建成功
        (5, "info", 1250),      # 会话已删除
        (7, "success", 1510),   # 中位长度
        (10, "info", 1900),     # 接近旧默认 2s
        (20, "success", 3200),  # 长说明读得完
        (40, "success", 4000),  # 封顶
        (10, "error", 2600),
        (20, "error", 4200),
        (40, "error", 6000),    # 报错封顶
        (2, "warning", 2500),   # 下限保护
    ],
)
def test_duration_scales_with_length(length, kind, expected):
    assert _duration(length, kind) == expected


def test_explicit_duration_still_wins():
    source = TOAST.read_text(encoding="utf-8")

    # 两个分支都是「显式 duration ?? 自动时长」，不能反过来
    assert source.count("messageOrOptions.duration !== undefined") == 1
    assert source.count("durationArg !== undefined") == 1
    assert source.count("resolveAutoDuration(") >= 3
    assert "DEFAULT_TOAST_DURATIONS" not in source


def test_toast_component_uses_same_rule():
    source = TOAST_VUE.read_text(encoding="utf-8")

    assert "import { resolveAutoDuration } from '@/composables/useToast'" in source
    assert "if (props.duration !== undefined) return props.duration" in source
    assert "return resolveAutoDuration(" in source
    # 旧的一刀切兜底不应残留
    assert "? 3500 : 2000" not in source


def test_critical_messages_get_extra_time():
    """权限/凭证类提示即便很短也要留够判断时间：「无导出权限」5 字不能 2.5s 就没了。"""
    source = TOAST.read_text(encoding="utf-8")

    assert "CRITICAL_TOAST_PATTERN" in source
    assert 'type === "error" ? 6000 : 4500' in source
    # 只对 error/warning 生效，success/info 不受影响
    assert '(type === "error" || type === "warning") && CRITICAL_TOAST_PATTERN.test(text)' in source

    # 词表覆盖权限/凭证/不可逆操作，且不误伤「请输入当前登录密码」这类表单校验
    pattern_line = next(line for line in source.splitlines() if "CRITICAL_TOAST_PATTERN =" in line)
    for keyword in ("权限", "授权", "无权", "凭证", "过期", "锁定", "不可恢复"):
        assert keyword in pattern_line, keyword
    assert "密码" not in pattern_line
