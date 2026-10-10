"""聊天日志审计页的跨用户数据源契约。

口径：**聊天日志审计页（ChatLogs）保留 admin 跨用户查看能力；聊天界面（EmbedChat）
任何角色都只能看自己的会话。** 这条边界靠「两套端点」落地，本文件把它钉住：

- 审计端点 `/api/v1/chat/admin/*`（后端 `Depends(require_admin)`）只允许 ChatLogs 使用；
- EmbedChat 源码里不得出现任何 `/chat/admin/` 调用——一旦出现就说明聊天面又有了跨用户入口。
"""

from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_agent_api_exposes_admin_audit_endpoints():
    api = _read("frontend/src/api/agent.ts")

    assert "getAdminChatHistory" in api
    assert "getAdminChatTrace" in api
    assert "getAdminContextCompactions" in api
    assert "'/api/v1/chat/admin/history'" in api
    assert "`/api/v1/chat/admin/logs/${traceId}`" in api
    assert "/api/v1/chat/admin/conversation/${encodeURIComponent(conversationId)}/context_compactions" in api
    # 自隔离端点必须保留：非 admin 但拥有 menu:chat_logs 的用户仍看自己的会话。
    assert "'/api/v1/chat/history'" in api
    assert "`/api/v1/chat/logs/${traceId}`" in api


def test_chat_logs_switches_to_audit_endpoints_for_admin():
    source = _read("frontend/src/views/ChatLogs.vue")

    assert "const isAdmin = computed(" in source
    assert "isAdmin.value ? agentApi.getAdminChatHistory : agentApi.getChatHistory" in source
    assert "isAdmin.value ? agentApi.getAdminChatTrace : agentApi.getChatTrace" in source
    assert (
        "isAdmin.value ? agentApi.getAdminContextCompactions : agentApi.getContextCompactions"
        in source
    )


def test_chat_logs_never_calls_the_self_scoped_api_directly():
    """所有取数都必须经过上面的开关，否则 admin 会静默退化成只看自己。"""
    source = _read("frontend/src/views/ChatLogs.vue")

    for direct_call in (
        "agentApi.getChatHistory(",
        "agentApi.getChatTrace(",
        "agentApi.getContextCompactions(",
    ):
        assert direct_call not in source, f"{direct_call} 必须改走 historyApi/traceApi/compactionsApi 开关"
    for indirect_call in ("historyApi.value(", "traceApi.value(", "compactionsApi.value("):
        assert indirect_call in source


def test_embed_chat_never_touches_admin_audit_endpoints():
    """聊天面不得出现跨用户入口：EmbedChat 源码里不允许引用 /chat/admin/。"""
    embed = _read("frontend/src/views/EmbedChat.vue")

    assert "/chat/admin/" not in embed
    assert "getAdminChatHistory" not in embed
    assert "getAdminChatTrace" not in embed


def test_agent_history_modal_uses_audit_endpoint_for_admin():
    """智能体「对话历史」弹窗：admin 恢复跨用户视图。

    该弹窗只按 `agent_id` 过滤（没有用户名筛选），此前靠 `/chat/history` 的 admin 旁路
    顺带看到所有用户的执行记录；旁路删除后必须显式切到审计端点，否则 admin 的智能体
    排障视角会静默退化为「只看自己跑过的」。
    """
    source = _read("frontend/src/components/agent/AgentHistoryModal.vue")

    assert "useUser" in source
    assert "const { isAdmin } = useUser()" in source
    assert "isAdmin.value ? agentApi.getAdminChatHistory : agentApi.getChatHistory" in source
    assert "agentApi.getChatHistory(" not in source


def test_task_center_expands_other_users_task_steps_for_admin():
    """任务中心的「展开步骤」：admin 可看他人任务的执行链路。

    admin 能打开他人任务详情，但步骤展开走 `/chat/logs/{trace_id}`；自隔离后该请求对他人
    任务返回 404，步骤会静默为空。因此 admin 必须切到审计轨迹端点。
    """
    source = _read("frontend/src/views/TaskCenter.vue")

    assert (
        "userInfo.value?.role === 'admin' ? agentApi.getAdminChatTrace : agentApi.getChatTrace"
        in source
    )
    assert "agentApi.getChatTrace(" not in source
    assert "traceApi.value(" in source
