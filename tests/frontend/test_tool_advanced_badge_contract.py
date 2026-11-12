"""工具高级配置的「已配置点亮」与参数取舍契约。

覆盖三件容易回归的事：
1. 齿轮按钮必须能看出「这个工具配过高级项」，不能只有 hover 变色；
2. 弹窗里不再出现「执行模型」（运行链没有下发点），超时输入与来源提示必须在；
3. 判定口径用「值 ≠ 默认」而不是「键存在」，否则历史版本里统一写入的 temperature: 0
   会让所有工具都亮起，提示失去意义。
"""
from pathlib import Path

MODAL = Path("frontend/src/components/agent/ToolRuntimeConfigModal.vue")
DRAWER = Path("frontend/src/components/agent/AgentVersionEditorDrawer.vue")
MANAGEMENT = Path("frontend/src/views/AgentManagement.vue")
CAPABILITIES = Path("frontend/src/constants/toolCapabilities.ts")
API = Path("frontend/src/api/agent.ts")
TIMELINE = Path("frontend/src/components/chat/ChatExecutionTimeline.vue")
TIMELINE_UTIL = Path("frontend/src/utils/processTimeline.ts")
SSE = Path("frontend/src/utils/agentscopeSseHandlers.ts")
EMBED = Path("frontend/src/views/EmbedChat.vue")
DEBUG = Path("frontend/src/views/AgentDebug.vue")


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_gear_button_lights_up_with_count_badge_and_summary():
    drawer = _source(DRAWER)
    assert "getToolAdvancedConfigSummary" in drawer
    assert "tool-action-btn--active" in drawer
    assert "tool-action-count" in drawer
    assert "tool-advanced-line" in drawer
    # tooltip 要写清「配了几项、配了什么」，不能只说「配置」
    assert "已配置 ${getToolAdvancedConfigSummary(tool.name).count} 项" in drawer
    # 死 prop 不能复活：它只声明不消费，之前就是这么漂移成摆设的
    assert "getToolCustomConfig" not in drawer


def test_summary_counts_only_tool_level_parameters():
    management = _source(MANAGEMENT)
    assert "const getToolAdvancedConfigSummary" in management
    # 工具级是覆盖不是收紧：摘要照抄配置值即为真实生效值，超上层只补个标记
    assert "Math.min(timeout, ceiling)" not in management
    assert "超时 ${Math.round(timeout)}s" in management
    assert "·超上层" in management
    assert "温度 ${temperature.toFixed(1)}" in management
    assert "parts.push('描述覆盖')" in management
    # 数据集绑定与调度范围各有独立按钮点亮，不重复计入
    assert "metadata_dataset_ids" not in management.split("const getToolAdvancedConfigSummary")[1].split("const toolTimeoutCeiling")[0]
    assert "delegable_agent_ids" not in management.split("const getToolAdvancedConfigSummary")[1].split("const toolTimeoutCeiling")[0]


def test_summary_uses_value_not_key_presence():
    """温度 0 是工具默认值，不能算「配置过」。"""
    management = _source(MANAGEMENT)
    summary_block = management.split("const getToolAdvancedConfigSummary")[1].split("const toolTimeoutCeiling")[0]
    assert "temperature > 0" in summary_block
    assert "timeout > 0" in summary_block


def test_modal_drops_model_picker_and_adds_timeout():
    modal = _source(MODAL)
    assert "ModelSelect" not in modal
    assert "执行超时 (Execute Timeout)" in modal
    assert "留空 = 跟随上层" in modal
    assert "timeout_seconds" in modal
    # 工具级是「覆盖」：上层值只用于默认值与越界提示，不钳制输入
    assert "upperTimeoutSeconds" in modal
    assert "const upperTimeout = computed" in modal
    assert "const isAboveUpper = computed" in modal
    assert "该工具会独占更长运行时间" in modal
    # 表单硬上限（86400）才是真截断，且输入框 max 要跟着它
    assert "const MAX_TOOL_TIMEOUT = 86400;" in modal
    assert ':max="MAX_TOOL_TIMEOUT"' in modal
    assert "const isOverMax = computed" in modal
    # 旧语义必须消失：不能再出现「只能收紧」这种钳制文案
    assert "只能收紧" not in modal


def test_modal_disables_temperature_for_pure_function_tools():
    modal = _source(MODAL)
    assert "supportsInternalLlm" in modal
    assert "该工具执行时不调用模型" in modal
    assert 'disabled="readonly || !supportsInternalLlm"' in modal or ":disabled=\"readonly || !supportsInternalLlm\"" in modal


def test_save_only_writes_changed_keys():
    modal = _source(MODAL)
    assert "delete next.timeout_seconds" in modal
    assert "delete next.temperature" in modal
    assert "delete next.description_override" in modal
    # 历史模型字段不再写回
    assert "delete next.model_name" in modal


def test_capability_constant_matches_backend_contract():
    capabilities = _source(CAPABILITIES)
    assert "read_image" in capabilities
    assert "toolSupportsInternalLlm" in capabilities
    management = _source(MANAGEMENT)
    assert "toolSupportsInternalLlm(currentConfiguringTool)" in management


def test_frontend_type_has_timeout_seconds():
    api = _source(API)
    assert "timeout_seconds?: number | null" in api



def test_timeline_uses_per_call_timeout_before_run_snapshot():
    """时间线「已耗时 / 上限」的分母要跟着工具级走。

    分母原先只取 `run_config` 的版本级/全局级快照：工具级把 Bash 收紧到 10s 时，
    界面仍写「972ms / 300s」，用户会以为工具级配置没生效。
    """
    timeline = _source(TIMELINE)
    util = _source(TIMELINE_UTIL)

    # 事件字段要能从 SSE 一路透传到时间线条目
    assert "timeout_seconds?: number | null" in util
    assert "timeout_seconds: data.timeout_seconds" in util
    assert "existing.timeout_seconds = data.timeout_seconds" in util
    # 展示侧优先取这条调用自己的值，props 只做兜底
    assert "const own = Number(item.timeout_seconds)" in timeline
    assert "if (Number.isFinite(own) && own > 0) return own;" in timeline


def test_sse_log_mapping_forwards_timeout_field():
    """SSE log → 时间线条目是一层显式字段白名单，漏写就等于后端白下发。

    实机踩过：后端已经按工具级把 Bash 收紧到 10s（DB trace 里 meta 明确是
    `timeout_seconds: 10.0 / source: tool`），界面却仍显示 `/ 300s`，
    就因为这一层没有透传字段，条目拿不到值只能回退版本级快照。
    """
    sse = _source(SSE)
    marker = "export function syncProcessTimelineLog"
    block = sse[sse.index(marker): sse.index("subagent: normalizeSubagentTraceMeta(data.subagent)", sse.index(marker))]

    assert "timeout_seconds:" in block
    # 不能只判 undefined：Number(null) === 0 会把「没配」显示成 0 秒上限
    assert "data.timeout_seconds === null" in block


def test_modal_does_not_silently_truncate_tool_timeout():
    """工具级超时不能被上层值静默截断。

    历史坑：`clampTimeout` 里带上了上层值，用户填 600 被静默改成 180，界面一声不吭。
    现在钳制只受表单硬上限（86400）约束，且只有「超硬上限」才算真截断并提示；
    超上层值只提示、不拦。
    """
    modal = _source(MODAL)

    block = modal[modal.index("const clampTimeout"): modal.index("};", modal.index("const clampTimeout"))]
    assert "Math.min(MAX_TOOL_TIMEOUT" in block
    assert "upperTimeout" not in block  # 不再按上层值截断
    assert "timeoutCeiling" not in block

    # 两种提示分开：超上层只提醒（允许保存），超硬上限才是真截断
    assert "const isAboveUpper = computed" in modal
    assert "const isOverMax = computed" in modal
    assert "const upperTimeout = computed" in modal
    # 生效值直接取工具级，不再与上层取 min
    assert "return { value: own, source: \"tool\" };" in modal


def test_modal_warns_when_tool_timeout_exceeds_producer_watchdog():
    """整轮看门狗是真正的天花板：工具级超过它，本轮会先被取消。

    它和版本级/全局级不是一回事：后两者是「默认值」，看门狗是「整轮总时长兜底」
    （含模型调用与所有工具）。只做默认值提示而不提示看门狗，用户会以为配 1200s
    就能跑 1200s，实际 600s 时整轮被强制终止且没有任何「工具超时」文案。
    """
    modal = _source(MODAL)
    management = _source(MANAGEMENT)

    assert "producerHardTimeoutSeconds" in modal
    assert "const isAboveProducer = computed" in modal
    assert "已超出整轮看门狗" in modal
    # 常驻展示整轮预算，而不是只在实际越界时才提
    assert "整轮看门狗 {{ producerHardTimeout }}s" in modal
    # 0 表示看门狗关闭：此时不存在天花板，不能拿 0 去比
    assert "value > 0 ? Math.round(value) : null" in modal

    # 管理页要把看门狗值传下来
    assert "producerHardTimeout" in management
    assert ":producer-hard-timeout-seconds=\"producerHardTimeout\"" in management


def test_modal_temperature_hint_matches_slider():
    """温度是滑块，没有「留空」这个动作，文案不能承诺做不到的事。"""
    modal = _source(MODAL)
    assert "调回 0.0 保存即恢复默认" in modal
    assert "留空保存即恢复默认" not in modal


def test_frontend_global_timeout_default_matches_backend():
    """前端兜底默认值要跟后端 DEFAULT_AGENT_MAX_TOOLCALL_TIMEOUT 一致（180s）。"""
    management = _source(MANAGEMENT)
    assert "const globalAgentToolcallTimeout = ref(180)" in management
    assert "const globalAgentToolcallTimeout = ref(120)" not in management


def test_legacy_logs_path_also_carries_timeout():
    """降级路径读 msg.logs 时同样要有真实超时，否则降级后又是 300s。

    msg.logs 有三个写入口，缺一不可：更新已有条目、首次推入（pending 态）、
    调试页推入。只修「更新」那一处，pending 行仍然拿不到值。
    """
    embed = _source(EMBED)
    debug = _source(DEBUG)

    # 更新已有条目
    assert "timeout_seconds:\n        (data.timeout_seconds as number | undefined) ?? currentLog.timeout_seconds" in embed
    # 首次推入（pending 态，分母就在这一步）
    assert "timeout_seconds:\n      (data.timeout_seconds as number | undefined) ?? null" in embed
    assert "msg.logs.push({" in embed
    # 调试页同口径
    assert "timeout_seconds: (data.timeout_seconds as number | undefined) ?? null" in debug
