from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_metadata_flow_guide_banner_contract():
    banner = _source("frontend/src/components/metadata/MetadataFlowGuideBanner.vue")
    strip = _source("frontend/src/components/common/FlowGuideStrip.vue")
    datasets_view = _source("frontend/src/views/MetadataDatasets.vue")

    # 1. 验证 4 步流程及对应标题与说明
    assert "连接与摸排数据源" in banner
    assert "智能导入与 AI 解析" in banner
    assert "建模指标与实体关系" in banner
    assert "向量同步与智能体挂载" in banner

    # 2. 验证关闭、不再提示（忽略）与动作派发
    assert "emit('close')" in banner
    assert "emit('dismiss')" in banner
    assert "emit('action'" in banner
    assert "不再提示" in strip
    assert "收起" in strip or "展开流程" in strip

    # 3. 验证在主视图中的引入与挂载
    assert "MetadataFlowGuideBanner" in datasets_view
    assert "nanzi_metadata_flow_guide_dismissed" in datasets_view
    assert "handleFlowGuideClose" in datasets_view
    assert "handleFlowGuideDismiss" in datasets_view
    assert "restoreFlowGuide" in datasets_view
    assert "<MetadataFlowGuideBanner" in datasets_view

    # 4. 验证规范帮助弹窗中集成了流程指引与恢复入口
    assert "'flow'" in datasets_view
    assert "恢复顶部流程提示" in datasets_view
