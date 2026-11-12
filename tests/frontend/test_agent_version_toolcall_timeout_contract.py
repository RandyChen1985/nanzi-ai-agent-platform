from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]


def test_version_tool_timeout_is_at_top_of_tools_step_and_supports_inheritance_and_numeric_input():
    source = (ROOT / "frontend/src/components/agent/AgentVersionEditorDrawer.vue").read_text(encoding="utf-8")

    tools_step = source[source.index("versionConfigStep === 'tools'"):]
    timeout_pos = tools_step.index("toolcall_timeout_seconds")
    search_pos = tools_step.index(":value=\"toolSearchQuery\"")
    assert timeout_pos < search_pos
    assert "使用智能体专属配置" in tools_step
    assert "跟随全局配置" in tools_step
    assert "启用后，以当前智能体版本配置为准，不再与全局或工具配置取最大值" in tools_step
    assert "将与全局及工具配置取最长的超时时间" not in tools_step
    assert "type=\"number\"" in tools_step[timeout_pos:search_pos]
    assert "inputmode=\"numeric\"" in tools_step[timeout_pos:search_pos]
    assert "min=\"1\"" in tools_step[timeout_pos:search_pos]
    assert "max=\"86400\"" in tools_step[timeout_pos:search_pos]
    assert "handleAgentVersionToolcallTimeoutKeydown" in source
    assert "handleAgentVersionToolcallTimeoutInput" in source
    assert "normalizeAgentVersionToolcallTimeoutInput" in source


def test_version_form_and_api_carry_nullable_tool_timeout():
    agent_api_source = (ROOT / "frontend/src/api/agent.ts").read_text(encoding="utf-8")
    management_source = (ROOT / "frontend/src/views/AgentManagement.vue").read_text(encoding="utf-8")
    editor_source = (ROOT / "frontend/src/components/agent/AgentVersionEditorDrawer.vue").read_text(encoding="utf-8")

    assert "toolcall_timeout_seconds?: number | null" in agent_api_source
    assert "toolcall_timeout_seconds: null" in management_source
    assert "getGlobalToolcallTimeout" in agent_api_source
    assert "getGlobalToolcallTimeout" in management_source
    assert ':global-agent-toolcall-timeout="globalAgentToolcallTimeout"' in management_source
    assert "globalAgentToolcallTimeout: number" in editor_source
    assert "当前全局配置：{{ globalAgentToolcallTimeout }} 秒" in editor_source


def test_version_level_timeout_warns_about_the_producer_watchdog():
    """版本级超时也要提示整轮看门狗——它比工具级更容易踩。

    版本级是「该版本下所有工具的默认值」，配得比看门狗大，整轮会在看门狗到点时被
    `producer_task.cancel()` 强制终止，预算根本用不满，而且没有「工具超时」文案。
    只提示工具级、不提示版本级，用户会以为问题出在工具上。
    """
    editor = (ROOT / "frontend/src/components/agent/AgentVersionEditorDrawer.vue").read_text(encoding="utf-8")
    management = (ROOT / "frontend/src/views/AgentManagement.vue").read_text(encoding="utf-8")

    # 父组件把看门狗值传下来
    assert ':producer-hard-timeout-seconds="producerHardTimeout"' in management
    assert "producerHardTimeoutSeconds?: number | null" in editor

    # 常驻展示整轮预算
    assert "整轮看门狗 {{ producerHardTimeout }} 秒" in editor
    # 越界时给强警告，且点明影响范围是「该版本下所有工具」
    assert "const isVersionTimeoutAboveProducer = computed" in editor
    assert "已超出整轮看门狗 {{ producerHardTimeout }} 秒" in editor
    assert "该版本下所有工具都会受影响" in editor
    # 0 = 看门狗关闭：不能拿 0 去比出假告警
    assert "value > 0 ? Math.round(value) : null" in editor


def test_version_level_over_max_is_warned_before_blur():
    """输入超过 86400 时要先提示：目前靠 blur 才截断，否则看着像能存 9999999。"""
    editor = (ROOT / "frontend/src/components/agent/AgentVersionEditorDrawer.vue").read_text(encoding="utf-8")
    assert "const isVersionTimeoutOverMax = computed" in editor
    assert "离开输入框后会被截断为" in editor
