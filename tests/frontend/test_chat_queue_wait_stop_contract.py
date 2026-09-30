import re
from pathlib import Path


EXECUTION_TIMELINE = Path(__file__).parents[2] / "frontend/src/components/chat/ChatExecutionTimeline.vue"
EMBED_CHAT = Path(__file__).parents[2] / "frontend/src/views/EmbedChat.vue"
AGENT_DEBUG = Path(__file__).parents[2] / "frontend/src/views/AgentDebug.vue"
CANCEL_UTIL = Path(__file__).parents[2] / "frontend/src/utils/cancelConversationRun.ts"


def test_execution_timeline_exposes_stop_event():
    """执行过程时间线必须暴露 stop 事件，供排队等待条目触发终止。"""
    source = EXECUTION_TIMELINE.read_text(encoding="utf-8")

    assert "defineEmits" in source
    assert 'stop' in source


def test_queue_wait_item_renders_stop_button():
    """“等待上一次会话任务完成”条目旁边必须有终止入口，否则用户只能干等锁释放。"""
    source = EXECUTION_TIMELINE.read_text(encoding="utf-8")

    assert "session:queue_wait" in source
    assert "终止" in source
    # 按钮不应触发展开/收起明细
    assert "@click.stop" in source


def test_parent_views_bind_timeline_stop_to_generation_stop():
    """时间线终止需要成功提示（排队时原本毫无反馈），输入框停止则保持静默成功。"""
    for path in (EMBED_CHAT, AGENT_DEBUG):
        source = path.read_text(encoding="utf-8")
        assert re.search(
            r'<ChatExecutionTimeline[\s\S]{0,1600}?@stop="stopGeneration\(\{ notifySuccess: true \}\)"',
            source,
        ), f"{path.name} 未给 ChatExecutionTimeline 绑定带成功提示的 @stop"


def test_stop_generation_notifies_success_only_when_requested():
    """成功 toast 必须由调用方显式请求；未确认释放时仍需 warning 提醒。"""
    for path in (EMBED_CHAT, AGENT_DEBUG):
        source = path.read_text(encoding="utf-8")
        assert "notifySuccess" in source
        assert "已停止本地生成，但服务端未确认释放结果" in source


def test_cancel_util_reports_result_to_caller():
    """取消接口必须把结果回传调用方，否则界面无从区分成功与失败。"""
    source = CANCEL_UTIL.read_text(encoding="utf-8")

    assert "Promise<boolean>" in source
    assert "return true" in source
    assert "return false" in source


def test_stop_generation_shows_toast_feedback():
    """点击终止必须给出可感知的 toast 反馈，不能静默完成。"""
    for path in (EMBED_CHAT, AGENT_DEBUG):
        source = path.read_text(encoding="utf-8")
        assert "已终止当前运行并释放会话锁" in source, f"{path.name} 缺少终止结果 toast 文案"


def test_queue_wait_stop_button_reflects_terminated_state():
    """点击终止后按钮必须变为「已终止」并禁止重复点击，进行中动效也需停止。"""
    source = EXECUTION_TIMELINE.read_text(encoding="utf-8")

    assert "已终止" in source
    assert "isQueueWaitStopped" in source
    assert ":disabled=\"isQueueWaitStopped(item)\"" in source
