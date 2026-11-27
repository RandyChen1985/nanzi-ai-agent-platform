"""Contract: personal center embeds TaskCenter as 我的任务 without menu:task_center / element:task:manage."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PERSONAL_CENTER = ROOT / "frontend" / "src" / "views" / "PersonalCenter.vue"
MY_TASKS = ROOT / "frontend" / "src" / "views" / "MyTasks.vue"
TASK_CENTER = ROOT / "frontend" / "src" / "views" / "TaskCenter.vue"


def test_personal_center_exposes_tasks_tab_after_mcp():
    text = PERSONAL_CENTER.read_text(encoding="utf-8")
    assert "'tasks'" in text or '"tasks"' in text
    assert "我的任务" in text
    assert "TaskCenter" in text
    assert "personal-only" in text or "personalOnly" in text or "personal-only" in text.replace("_", "-")
    mcp_at = text.index("我的 MCP")
    tasks_at = text.index("我的任务")
    assert mcp_at < tasks_at


def test_my_tasks_route_provides_mobile_padding():
    text = MY_TASKS.read_text(encoding="utf-8")
    assert "TaskCenter personal-only" in text
    assert "px-4" in text and "sm:px-0" in text


def test_task_center_supports_personal_only_owner_manage():
    text = TASK_CENTER.read_text(encoding="utf-8")
    assert "personalOnly" in text
    assert "我的任务" in text
    # personal center must not gate create/manage on element:task:manage
    assert "props.personalOnly" in text
    assert "element:task:manage" in text  # still used for full TaskCenter
    assert "isTaskOwner" in text
    assert "scopedTasks" in text
    assert "visibleTaskTypeTabs" in text


def test_task_center_guards_notification_race_loading_and_save_submit():
    text = TASK_CENTER.read_text(encoding="utf-8")
    # 首屏默认 loading=true 且挂载非静默加载，避免闪空态与吞错
    assert "const loading = ref(true)" in text
    assert "fetchTasks(false)" in text
    # 任务列表与报表订阅隔离拉取
    assert "Promise.allSettled([" in text
    # 通知渠道未检测完成/全失败时不得静默清空原有渠道
    assert "notificationAvailabilityLoaded" in text
    assert ":disabled=\"saving || personalNotificationLoading\"" in text
    # 保存防重、必填校验与统一后端错误提取
    assert "const saving = ref(false)" in text
    assert "请选择执行智能体" in text
    assert 'maxlength="100"' in text
    assert "extractApiError(" in text
    # 解析 user_info 带 try/catch 且兼容 user_id / id
    assert "parseCachedUserInfo" in text
    assert "userInfo.value?.user_id ?? userInfo.value?.id" in text
    # 执行记录摘要清洗 TaskCenter 规则前缀
    assert "parseChatLogQuery" in text
    assert "formatHistorySummary" in text
    # 响应同页路由 query 变化
    assert "applyRouteDeepLink" in text