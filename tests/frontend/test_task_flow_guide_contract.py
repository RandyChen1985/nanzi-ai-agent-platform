from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_task_flow_guide_banner_contract():
    banner = _source("frontend/src/components/task/TaskFlowGuideBanner.vue")
    strip = _source("frontend/src/components/common/FlowGuideStrip.vue")
    view = _source("frontend/src/views/TaskCenter.vue")

    # 1. 验证 5 步流程定义与核心业务规则
    assert "任务创建与周期编排" in banner
    assert "资源限定与安全审批" in banner
    assert "渠道分发与触达订阅" in banner
    assert "手动试跑与时序观测" in banner
    assert "健康监控与异常处置" in banner

    # 2. 验证关键动作与路由跳转
    assert "/dashboard/agent-management" in banner
    assert "新建任务" in banner
    assert "执行记录" in banner
    assert "不再提示" in strip

    # 3. 验证主视图中的组件引入与持久化机制
    assert "TaskFlowGuideBanner" in view
    assert "nanzi_task_flow_guide_dismissed" in view
    assert "showTaskFlowGuide" in view
    assert "restoreTaskFlowGuide" in view
    assert "显示指引" in view
    assert "whitespace-nowrap" in view

    # 4. 验证 ? 号规范弹窗中的全流程指引与恢复按钮
    assert "showSpecsModal" in view
    assert "activeSpecsTab" in view
    assert "任务调度设计规范与全流程指引" in view
    assert "恢复顶部流程提示" in view
