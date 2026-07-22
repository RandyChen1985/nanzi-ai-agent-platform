import pytest

from app.services.ai.runtime.tool_loop_detector import ToolLoopDetector

pytestmark = pytest.mark.no_infrastructure


def test_tool_loop_detector_fuses_on_repeated_identical_calls():
    detector = ToolLoopDetector(threshold=3, enabled=True)
    args = {"query": "hello world"}

    assert detector.record("search", args).fused is False
    assert detector.record("search", args).fused is False
    verdict = detector.record("search", args)
    assert verdict.fused is True
    assert verdict.count == 3
    assert "search" in verdict.message


def test_tool_loop_detector_normalizes_whitespace_and_key_order():
    detector = ToolLoopDetector(threshold=2, enabled=True)
    first = {"b": 1, "a": "  foo   bar  "}
    second = {"a": "foo bar", "b": 1}

    assert detector.record("tool", first).fused is False
    verdict = detector.record("tool", second)
    assert verdict.fused is True


def test_tool_loop_detector_fuses_on_repeated_get_current_time_regardless_of_args():
    detector = ToolLoopDetector(threshold=3, enabled=True)
    first = detector.record("get_current_time", {"timezone": "Asia/Shanghai"})
    second = detector.record("get_current_time", {"timezone": "UTC"})
    assert first.fused is False
    assert second.fused is True
    assert "get_current_time" in second.message


def test_tool_loop_detector_disabled_never_fuses():
    detector = ToolLoopDetector(threshold=1, enabled=False)
    for _ in range(5):
        assert detector.record("tool", {"x": 1}).fused is False


def test_ping_pong_fuses_on_alternating_tools():
    # threshold 高，避免同参重复先触发；ping_pong_threshold=4
    detector = ToolLoopDetector(threshold=99, ping_pong_threshold=4, global_limit=0)
    # 每次参数都不同，确保不是同参重复触发
    assert detector.record("get_schema", {"n": 1}).fused is False
    assert detector.record("run_sql", {"n": 2}).fused is False
    assert detector.record("get_schema", {"n": 3}).fused is False
    verdict = detector.record("run_sql", {"n": 4})
    assert verdict.fused is True
    assert verdict.reason_code == "ping_pong"
    assert "get_schema" in verdict.message and "run_sql" in verdict.message


def test_ping_pong_not_triggered_by_pure_repeat():
    # 同名工具连续调用属于 repeat，不应被误判为 ping_pong
    detector = ToolLoopDetector(threshold=99, ping_pong_threshold=3, global_limit=0)
    for i in range(5):
        verdict = detector.record("same_tool", {"n": i})
        assert verdict.reason_code != "ping_pong"
        assert verdict.fused is False


def test_global_circuit_breaker_fuses_on_total_calls():
    detector = ToolLoopDetector(threshold=99, ping_pong_threshold=0, global_limit=5)
    last = None
    for i in range(5):
        last = detector.record(f"tool_{i}", {"n": i})
    assert last.fused is True
    assert last.reason_code == "circuit_breaker"
    assert last.count == 5


def test_repeat_takes_precedence_over_other_detectors():
    detector = ToolLoopDetector(threshold=2, ping_pong_threshold=2, global_limit=2)
    args = {"q": "x"}
    assert detector.record("t", args).fused is False
    verdict = detector.record("t", args)
    assert verdict.fused is True
    assert verdict.reason_code == "repeat"


def test_fused_detector_stays_fused():
    detector = ToolLoopDetector(threshold=2, global_limit=0, ping_pong_threshold=0)
    args = {"q": "x"}
    detector.record("unrelated", {"q": "y"})
    detector.record("t", args)
    initial = detector.record("t", args)
    assert initial.fused is True
    assert initial.count == 2
    follow = detector.record("t", args)
    assert follow.fused is True
    assert follow.reason_code == initial.reason_code == "repeat"
    assert follow.message == initial.message
    assert follow.count == initial.count == 2
    assert detector.total_calls == 3
    assert detector.fused is True
