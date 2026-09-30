import re
from pathlib import Path


EXECUTION_TIMELINE = Path(__file__).parents[2] / "frontend/src/components/chat/ChatExecutionTimeline.vue"
EMBED_CHAT = Path(__file__).parents[2] / "frontend/src/views/EmbedChat.vue"
AGENT_DEBUG = Path(__file__).parents[2] / "frontend/src/views/AgentDebug.vue"


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
    """父视图必须把时间线的 stop 接到既有的 stopGeneration（终止并释放会话锁）。"""
    for path in (EMBED_CHAT, AGENT_DEBUG):
        source = path.read_text(encoding="utf-8")
        assert re.search(
            r'<ChatExecutionTimeline[\s\S]{0,1500}?@stop="stopGeneration"',
            source,
        ), f"{path.name} 未给 ChatExecutionTimeline 绑定 @stop"
