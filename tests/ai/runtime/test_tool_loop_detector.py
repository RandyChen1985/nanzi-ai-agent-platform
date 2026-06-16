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


def test_tool_loop_detector_disabled_never_fuses():
    detector = ToolLoopDetector(threshold=1, enabled=False)
    for _ in range(5):
        assert detector.record("tool", {"x": 1}).fused is False
