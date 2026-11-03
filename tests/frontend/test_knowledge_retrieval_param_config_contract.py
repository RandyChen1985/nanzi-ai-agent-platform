"""检索测试参数进配置（方案 B）的前端契约。"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "frontend/src/views/KnowledgeRetrievalTest.vue"
PARAM_GROUP = ROOT / "frontend/src/components/knowledge/RetrievalParamGroup.vue"


def test_page_consumes_config_defaults_instead_of_hardcoding():
    source = PAGE.read_text(encoding="utf-8")

    assert "retrieval_defaults" in source
    assert "RetrievalParamGroup" in source
    # 三组参数不再各自 ref 初始化成写死的默认值
    assert not re.search(r"const topK = ref\(", source)
    assert not re.search(r"const similarityThreshold = ref\(", source)

    # 兜底值仍在，但只作为 FALLBACK
    assert "FALLBACK_PARAMS" in source
    assert re.search(r"FALLBACK_PARAMS:\s*RetrievalParams\s*=\s*\{[^}]*topK:\s*5", source)


def test_param_group_exposes_source_and_reset():
    source = PARAM_GROUP.read_text(encoding="utf-8")

    # 参数来源状态与一键恢复是「默认值来自配置」的可解释性前提
    assert "系统配置" in source
    assert "已修改" in source
    assert "reset(" in source
    # 权重与全文互补，胶囊必须跟着滑块走
    assert "fullTextWeight" in source


def test_param_help_uses_custom_tooltip():
    """参数说明必须是自定义气泡：原生 title 延迟不可控、移动端不触发。"""
    source = PARAM_GROUP.read_text(encoding="utf-8")

    assert "param-bubble" in source
    assert 'role="tooltip"' in source
    # 不能退回原生 title 提示
    assert 'param-help" title=' not in source
    # 键盘可达：? 图标可聚焦，靠 :focus-within 展开
    assert 'tabindex="0"' in source
    assert "focus-within" in source
