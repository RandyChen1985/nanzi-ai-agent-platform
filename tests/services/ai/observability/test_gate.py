"""``gate`` 单元测试：开关语义与 trace 粒度采样稳定性。"""

import pytest

from app.services.ai.observability import gate

pytestmark = pytest.mark.no_infrastructure


def test_rate_zero_never_samples():
    assert gate.is_sampled("trace-a", 0.0) is False


def test_rate_one_always_samples():
    assert gate.is_sampled("trace-a", 1.0) is True


def test_same_trace_is_stable_across_calls():
    results = {gate.is_sampled("trace-fixed", 0.5) for _ in range(50)}
    assert len(results) == 1


def test_different_traces_spread_across_decision():
    results = {gate.is_sampled(f"trace-{i}", 0.5) for i in range(200)}
    assert results == {True, False}


def test_distribution_is_roughly_proportional():
    sampled = sum(gate.is_sampled(f"trace-{i}", 0.25) for i in range(4000))
    assert 800 < sampled < 1200


@pytest.mark.parametrize("rate", [-1.0, 2.0, float("nan"), float("inf"), None, "abc"])
def test_invalid_rate_is_treated_as_disabled(rate):
    assert gate.is_sampled("trace-a", rate) is False


def test_empty_trace_id_uses_placeholder_and_stays_stable():
    assert gate.is_sampled(None, 1.0) is True
    assert gate.is_sampled(None, 0.0) is False
