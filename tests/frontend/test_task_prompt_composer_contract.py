"""Contract: TaskCenter prompt composer supports model/approval/resource scope."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TASK_CENTER = ROOT / "frontend" / "src" / "views" / "TaskCenter.vue"
COMPOSER = ROOT / "frontend" / "src" / "components" / "task" / "TaskPromptComposer.vue"
SCHEDULER = ROOT / "app" / "services" / "ai" / "scheduler_service.py"
OPTIONS = ROOT / "app" / "services" / "task_execution_options.py"


def test_task_prompt_composer_exposes_model_approval_and_resources():
    text = COMPOSER.read_text(encoding="utf-8")
    assert "自动批准" in text
    assert "请求批准" in text
    assert "定时任务无法弹窗审批" in text
    assert "datasets" in text
    assert "knowledge_bases" in text
    assert "skills" in text
    assert "mcp_tools" in text
    assert "默认模型" in text or "智能体默认模型" in text
    # 技能：平台 / 个人 Tab，对齐 EmbedChat
    assert "skillScopeTab" in text
    assert "平台" in text
    assert "我的" in text
    assert "skillScopeSelectedCount" in text
    # MCP：按服务分组、默认折叠、组内全选；仅个人已发布
    assert "mcpGroupsForActiveOptions" in text
    assert "collapsedMcpGroups" in text
    assert "toggleMcpGroupSelectAll" in text
    assert "取消全选" in text
    assert "mcpToolDisplayName" in text
    assert "=== 'personal'" in text or '=== "personal"' in text or "=== 'personal'" in text
    assert "仅可挂载个人已发布 MCP" in text
    assert "thinkingEnableOverride" in text
    assert "reasoningEffortOverride" in text
    assert "思考强度" in text
    assert "thinking_enable" in text
    assert "reasoning_effort" in text
    assert "openThinkingSettings" in text
    assert "showThinkingPanel" in text
    assert "scrollSelectedModelIntoView" in text
    assert "modelListScrollRef" in text
    assert 'data-model-current' in text
    assert "默认思考" in text or "已开启" in text
    assert "关闭本次任务思考" in text
    assert "({{ option.value }})" not in text
    assert "本次任务" in text


def test_task_prompt_composer_panels_escape_modal_clipping():
    """任务弹窗正文与组件根节点都有 overflow 裁剪，浮层必须挂到 body 用 fixed 定位。"""
    text = COMPOSER.read_text(encoding="utf-8")
    assert "<Teleport to=\"body\">" in text
    # 面板不能再用相对父容器的绝对定位，否则会被裁掉
    assert "absolute bottom-full" not in text
    assert "panelStyle" in text
    # 浮层水平位置：数据集/知识库相对工具栏居中；技能/MCP 相对按钮右对齐
    assert "setTriggerRef" in text
    assert "triggerRefs" in text
    assert "(barRect.width - width) / 2" in text
    assert "triggerRect.right - width" in text
    assert "getBoundingClientRect" in text
    # 需要跟随滚动/缩放重算位置，capture=true 才能捕获弹窗正文的滚动
    assert "'scroll', onViewportChange, true" in text
    assert "'resize', onViewportChange" in text
    # 浮层已脱离根节点，点击判定必须同时认面板自身
    assert "panelRef.value?.contains(target)" in text


def test_task_prompt_composer_panels_can_be_dismissed():
    """三个浮层都要能关掉：点外部 / 指针移开 / 关闭按钮 / Esc。"""
    text = COMPOSER.read_text(encoding="utf-8")
    # 「外部」按按钮栏判定，点文本框、已选标签、说明文字都应收起面板
    assert "barRef.value?.contains(target)" in text
    assert "rootRef" not in text
    # 指针移开延时关闭，进入面板或按钮栏时取消
    assert text.count("@pointerleave=\"scheduleClose\"") == 4
    assert text.count("@pointerenter=\"cancelPendingClose\"") == 4
    # 三个面板各自带标题与关闭按钮
    assert text.count("@click=\"closePanel\"") == 3
    assert text.count("{{ panelTitle }}") == 3
    assert "'Escape'" in text
    # 触摸设备抬手即 pointerleave，自动关闭只能对鼠标生效
    assert "event.pointerType !== 'mouse'" in text


def test_task_center_wires_prompt_composer_into_config():
    text = TASK_CENTER.read_text(encoding="utf-8")
    assert "TaskPromptComposer" in text
    assert "approval_mode" in text
    assert "resource_scope" in text
    assert "taskModel" in text
    assert "hydrateExecutionOptions" in text
    assert "taskThinkingEnableOverride" in text
    assert "taskReasoningEffortOverride" in text
    assert "taskTemperatureOverride" in text
    assert "update:thinking-enable-override" in text
    assert "update:reasoning-effort-override" in text
    assert "update:temperature-override" in text
    assert "baseConfig.temperature" in text


def test_task_prompt_composer_temperature_controls_contract():
    text = COMPOSER.read_text(encoding="utf-8")
    assert "temperatureOverride" in text
    assert "temperatureSummaryLabel" in text
    assert "effectiveTemperature" in text
    assert "采样温度 (Temperature)" in text
    assert "isTemperatureOverLimit" in text
    assert "严谨 0.2" in text
    assert "均衡 0.7" in text
    assert "发散 1.0" in text
    assert "跟随默认" in text
    assert "当前温度大于 1.0" in text
    assert "modelTriggerTooltip" in text


def test_task_thinking_effort_options_are_expanded():
    text = COMPOSER.read_text(encoding="utf-8")

    assert "showReasoningEffortPanel" not in text
    assert "跟随模型默认" in text
    assert "v-for=\"option in supportedReasoningEfforts\"" in text


def test_scheduler_reads_task_execution_options_from_config():
    scheduler = SCHEDULER.read_text(encoding="utf-8")
    options = OPTIONS.read_text(encoding="utf-8")
    assert "permission_options_from_task_config" in scheduler
    assert "debug_options_from_task_config" in scheduler
    assert "knowledge_dataset_ids" in scheduler
    assert "metadata_dataset_ids" in scheduler
    assert "DEFAULT_APPROVAL_MODE = \"allow\"" in options
    assert "TEMPERATURE_KEY = \"temperature\"" in options
    assert "normalize_temperature" in options
    assert "resource_scope" in options


def test_task_center_groups_agents_by_system_and_custom():
    text = TASK_CENTER.read_text(encoding="utf-8")
    assert "systemAgents" in text
    assert "customAgents" in text
    assert "isMainAgent" in text
    assert "agentTab" in text
    assert "syncAgentTab" in text
    assert "toggleAgentDropdown" in text
    assert "系统智能体" in text
    assert "自定义智能体" in text
    assert "MAIN" in text
    assert "SYSTEM" in text
    assert "CUSTOM" in text
    assert "systemAgents.value[0]?.id || agents.value[0]?.id" in text


def test_agent_manager_service_prioritizes_system_agents():
    agent_manager = (ROOT / "app" / "services" / "ai" / "agent_manager.py").read_text(encoding="utf-8")
    assert "system_first = case((AIAgent.is_system == True, 0), else_=1)" in agent_manager
    # 验证 system_first 在 sort_order 之前
    assert agent_manager.index("system_first") < agent_manager.index("AIAgent.sort_order.desc()")

