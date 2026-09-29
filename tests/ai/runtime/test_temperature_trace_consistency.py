"""审计轨迹里的温度也必须是「本次生效温度」。

工具卡片修好之后，如果其它轨迹记录点仍写智能体版本温度，
同一轮对话里就会出现两个互相矛盾的温度数字，
排查时依旧无法判断「我调的温度到底生效了没有」。
"""

from pathlib import Path

import pytest

from app.core.context import set_debug_context

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[3]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _clean_debug_context():
    set_debug_context({})
    yield
    set_debug_context({})


def test_resolve_config_temperature_follows_session_override():
    from app.services.ai.temperature import resolve_config_temperature

    config = type("Config", (), {"temperature": 0.0})()

    assert resolve_config_temperature(config) == 0.0

    set_debug_context({"temperature": 0.3})

    assert resolve_config_temperature(config) == 0.3


def test_resolve_config_temperature_treats_zero_as_a_real_value():
    from app.services.ai.temperature import resolve_config_temperature

    config = type("Config", (), {"temperature": 0.7})()
    set_debug_context({"temperature": 0.0})

    assert resolve_config_temperature(config) == 0.0


TRACE_TEMPERATURE_SITES = [
    (
        "app/services/ai/executors/base.py",
        "temperature=float(self.config.temperature or 0)",
    ),
    (
        "app/services/ai/executors/rag_executor.py",
        "temperature=self.config.temperature,",
    ),
    (
        "app/services/ai/multi_agent_orchestrator.py",
        "temperature=float(s_temp or 0)",
    ),
    (
        "app/services/ai/runners/chatbi/few_shot.py",
        "temperature=float(runner.config.temperature or 0)",
    ),
    (
        "app/services/ai/runners/chatbi/react_stream.py",
        "temperature=float(runner.config.temperature or 0)",
    ),
    (
        "app/services/ai/runners/chatbi/schema_prefetch.py",
        "temperature=float(runner.config.temperature or 0)",
    ),
    (
        "app/services/ai/runners/chatbi/synthesis.py",
        "runner.config.synthesis_temperature or runner.config.temperature or 0",
    ),
    (
        "app/services/ai/runners/assistant_agent_runner.py",
        "temperature=self.config.temperature,",
    ),
]


@pytest.mark.parametrize("path, forbidden", TRACE_TEMPERATURE_SITES)
def test_trace_sites_no_longer_write_the_raw_config_temperature(path, forbidden):
    source = _read(path)

    assert forbidden not in source
    assert (
        "resolve_config_temperature" in source
        or "_effective_temperature_for_trace" in source
    )
