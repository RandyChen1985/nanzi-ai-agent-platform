from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_knowledge_flow_guide_banner_contract():
    banner = _source("frontend/src/components/knowledge/KnowledgeFlowGuideBanner.vue")
    strip = _source("frontend/src/components/common/FlowGuideStrip.vue")
    view = _source("frontend/src/views/KnowledgeBaseManagement.vue")

    # 1. 验证 5 步流程定义与前置环境依赖
    assert "环境连通与库创建" in banner
    assert "RAGFlow" in banner
    assert "文档上传与解析" in banner
    assert "召回测试与调优" in banner
    assert "权限授权与角色分配" in banner
    assert "智能体挂载与问答" in banner

    # 2. 验证关键动作与路由跳转
    assert "/dashboard/system?tab=configs" in banner
    assert "/dashboard/knowledge-retrieval-test" in banner
    assert "/dashboard/knowledge-metrics" in banner
    assert "/dashboard/roles" in banner
    assert "/dashboard/agent-management" in banner
    assert "新建知识库" in banner
    assert "不再提示" in strip

    # 3. 验证主视图中的组件引入与持久化机制
    assert "KnowledgeFlowGuideBanner" in view
    assert "nanzi_knowledge_flow_guide_dismissed" in view
    assert "showKnowledgeFlowGuide" in view
    assert "restoreKnowledgeFlowGuide" in view
    assert "显示指引" in view
    assert "whitespace-nowrap" in view

    # 4. 验证 ? 号规范弹窗中的全流程指引与恢复按钮
    assert "showHelp" in view
    assert "activeHelpTab" in view
    assert "知识库设计规范与全流程指引" in view
    assert "恢复顶部流程提示" in view
