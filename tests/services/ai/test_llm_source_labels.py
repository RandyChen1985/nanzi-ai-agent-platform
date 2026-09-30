"""锁定 5 类高频直调入口的 source 标签。

这些标签让 Langfuse 里能一眼分辨"这次直调是谁发起的"；它们只是给
``get_llm`` / ``get_llm_async`` / ``get_configured_llm`` 多传一个关键字参数，
不影响模型调用本身。用源码断言锁住，防止后续重构悄悄丢掉标签。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

REPO_ROOT = Path(__file__).resolve().parents[3]

# (文件, source 标签, 说明)
EXPECTED_LABELS = [
    ("app/services/ai/intent_service.py", "intent", "意图识别"),
    ("app/services/ai/router_service.py", "router", "路由/分发"),
    (
        "app/services/ai/runners/chatbi/clarification.py",
        "chatbi.clarification",
        "ChatBI 澄清",
    ),
    ("app/services/saved_report_digest_service.py", "report.digest", "报告摘要"),
    ("app/services/saved_report_analysis_service.py", "report.analysis", "报告分析"),
    ("app/api/portal/endpoints/models.py", "model.test", "模型测试连接"),
]


@pytest.mark.parametrize(
    "rel_path,label,desc",
    EXPECTED_LABELS,
    ids=[f"{label}" for _, label, _ in EXPECTED_LABELS],
)
def test_call_site_declares_source_label(rel_path, label, desc):
    source = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
    assert f'source="{label}"' in source, f"{desc}（{rel_path}）缺少 source=\"{label}\""


def test_config_provider_forwards_source_to_llm_factory():
    """get_configured_llm 是 37 处调用点的主入口，必须把 source 透传给 get_llm。"""
    source = (REPO_ROOT / "app/services/ai/config.py").read_text(encoding="utf-8")
    assert "source: Optional[str] = None" in source
    assert 'llm_kwargs["source"] = source' in source
