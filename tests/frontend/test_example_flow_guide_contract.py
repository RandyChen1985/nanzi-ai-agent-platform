from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_example_flow_guide_banner_contract():
    banner = _source("frontend/src/components/example/ExampleFlowGuideBanner.vue")
    strip = _source("frontend/src/components/common/FlowGuideStrip.vue")
    view = _source("frontend/src/views/ExampleManagement.vue")

    # 1. 验证 5 步流程定义与核心业务规则
    assert "样本沉淀与录入" in banner
    assert "专家审核与打标" in banner
    assert "向量同步与索引" in banner
    assert "动态 Few-Shot 召回" in banner
    assert "引用统计与持续迭代" in banner

    # 2. 验证关键动作与路由跳转
    assert "/dashboard/chat" in banner
    assert "/dashboard/agent-management" in banner
    assert "一键全量同步" in banner
    assert "不再提示" in strip

    # 3. 验证主视图中的组件引入与持久化机制
    assert "ExampleFlowGuideBanner" in view
    assert "nanzi_example_flow_guide_dismissed" in view
    assert "showExampleFlowGuide" in view
    assert "restoreExampleFlowGuide" in view
    assert "显示指引" in view
    assert "whitespace-nowrap" in view

    # 4. 验证 ? 号规范弹窗中的全流程指引与恢复按钮
    assert "showHelp" in view
    assert "activeHelpTab" in view
    assert "案例集设计规范与全流程指引" in view
    assert "恢复顶部流程提示" in view
