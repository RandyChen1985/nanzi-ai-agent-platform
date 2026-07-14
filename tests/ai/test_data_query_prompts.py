import pytest

from app.services.ai.executors.prompts import DataQueryPrompts

pytestmark = pytest.mark.no_infrastructure


@pytest.mark.parametrize(
    ("question", "reasoning", "expected"),
    [
        ("你好", "当前请求不是明确的 ChatBI 查数请求", DataQueryPrompts.CLARIFICATION_SCENARIO_NON_DATA),
        (
            "可视化一下",
            "检测到结果追问但没有可信的近期可复用结构化查询结果",
            DataQueryPrompts.CLARIFICATION_SCENARIO_MISSING_REUSE,
        ),
        (
            "分析一下",
            "最近对话上下文不足",
            DataQueryPrompts.CLARIFICATION_SCENARIO_MISSING_CONTEXT,
        ),
        (
            "还是数据查询需求",
            "用户强调仍是查数",
            DataQueryPrompts.CLARIFICATION_SCENARIO_INTENT_CALIBRATION,
        ),
        (
            "统计 PUE",
            "需要用户补充查数信息",
            DataQueryPrompts.CLARIFICATION_SCENARIO_VAGUE_QUERY,
        ),
    ],
)
def test_resolve_clarification_scenario(question, reasoning, expected):
    assert DataQueryPrompts.resolve_clarification_scenario(question, reasoning) == expected


def test_should_skip_clarification_llm_for_stable_scenarios():
    assert DataQueryPrompts.should_skip_clarification_llm(
        DataQueryPrompts.CLARIFICATION_SCENARIO_NON_DATA
    )
    assert DataQueryPrompts.should_skip_clarification_llm(
        DataQueryPrompts.CLARIFICATION_SCENARIO_MISSING_REUSE
    )
    assert not DataQueryPrompts.should_skip_clarification_llm(
        DataQueryPrompts.CLARIFICATION_SCENARIO_VAGUE_QUERY
    )


def test_build_clarification_response_uses_rule_lead_and_quick():
    content = DataQueryPrompts.build_clarification_response(
        "统计各机房上周 PUE 排名",
        "需要用户补充查数信息",
        "",
    )
    assert "### ℹ️ 为什么需要补充信息" in content
    assert "### 💬 您可以这样继续" in content
    assert "(quick:" in content
    assert "PUE" in content


def test_build_non_data_response_only_guides_agent_switch():
    content = DataQueryPrompts.build_non_data_response("现在是什么模型")

    assert "切换智能体" in content
    assert "/switch_to_auto" in content
    assert "补充时间" not in content
    assert "指标口径" not in content
    assert "统计对象" not in content


def test_structured_clarification_only_builds_requested_gap_buttons():
    content = DataQueryPrompts.build_clarification_response(
        "统计各机房 PUE",
        "查数对象明确但缺少时间范围",
        "",
        missing_fields=("time_range",),
    )

    assert "补充时间" in content
    assert "指标口径" not in content
    assert "统计对象" not in content
    assert "### ℹ️ 为什么需要补充信息" in content
    assert "### 💬 您可以这样继续" in content
    assert "(quick:" in content
    assert "PUE" in content


def test_semantic_clarification_recommendations_replace_mechanical_gap_buttons():
    content = DataQueryPrompts.build_clarification_response(
        "统计各机房 PUE",
        "查数对象明确但缺少时间范围",
        "",
        missing_fields=("time_range",),
        suggested_queries=(
            "查询本月各机房平均 PUE",
            "查询最近 30 天各机房 PUE 趋势",
            "对比本月与上月各机房 PUE",
        ),
    )

    assert "查询本月各机房平均 PUE" in content
    assert "最近 30 天各机房 PUE 趋势" in content
    assert "（时间范围：本月）" not in content


def test_validate_semantic_recommendations_rejects_unrelated_topics():
    candidates = DataQueryPrompts.validate_clarification_recommendations(
        "统计各机房 PUE",
        ("time_range",),
        [
            "查询本月各机房平均 PUE",
            "查询本月销售收入",
            "现在是什么模型",
        ],
    )

    assert candidates == ("查询本月各机房平均 PUE",)


def test_sanitize_clarification_lead_strips_reason_and_quick():
    raw = (
        "### ℹ️ 为什么需要补充信息\n- **触发原因：** x\n"
        "请先补充时间范围。\n\n"
        "### 💬 您可以这样继续\n"
        "- [🙋 test](quick:查询 PUE)"
    )
    assert DataQueryPrompts.sanitize_clarification_lead(raw) == "请先补充时间范围。"


def test_is_valid_clarification_lead_rejects_pseudo_data_query():
    assert not DataQueryPrompts.is_valid_clarification_lead(
        "建议您查询当前用户信息。",
        "我是谁",
        "身份确认",
    )


def test_clarification_lead_prompt_does_not_require_reason_block():
    prompt = DataQueryPrompts.clarification_lead_generation_prompt(
        DataQueryPrompts.CLARIFICATION_SCENARIO_VAGUE_QUERY,
        "统计 PUE",
        "需要用户补充查数信息",
        "无",
    )
    assert "已识别缺口" in prompt
    assert "不要输出标题、列表、quick 按钮" in prompt
    assert "必须先输出" not in prompt


def test_build_clarification_fallback_for_followup_missing_context():
    content = DataQueryPrompts.build_clarification_fallback(
        "可视化分析一下",
        "请求类别 LLM 未返回有效结果；检测到结果追问但没有可信的近期可复用结构化查询结果",
        "用户: 查询算力SU 1-6月回款率\n助手: | 月份 | 回款率 |",
    )
    assert "### ℹ️ 为什么需要补充信息" in content
    assert "**触发原因：**" in content
    assert "**您可以这样改：**" in content
    assert "可复用的查询结果" in content
    assert "### 💬 您可以这样继续" in content
    assert "(quick:" in content
    assert "算力SU" in content
    assert "PUE" not in content


def test_build_clarification_fallback_for_general_chat():
    content = DataQueryPrompts.build_clarification_fallback(
        "你好，你是谁",
        "当前请求不是明确的 ChatBI 查数请求，需要用户补充想查询的业务数据、指标、维度或时间范围",
        "",
    )
    assert "### ℹ️ 为什么需要补充信息" in content
    assert "尚未识别为明确的数据查询" in content
    assert "切换智能体" in content
    assert "若不是查数需求" in content
    assert DataQueryPrompts.has_quick_suggestions(content)
    assert "PUE" not in content


def test_build_clarification_fallback_for_identity_question():
    content = DataQueryPrompts.build_clarification_fallback(
        "我是谁",
        "用户问题是身份确认或闲聊，不涉及具体业务数据查询",
        "",
    )
    assert "切换智能体" in content
    assert "身份确认" in content or "通用问答" in content
    assert "查询当前用户" not in content
    assert "(quick:" in content


def test_build_clarification_fallback_anchors_to_user_question():
    question = "最近一次对话中用户提问和 AI 响应的 Token 消耗是多少？"
    content = DataQueryPrompts.build_clarification_fallback(
        question,
        "需要用户补充查数信息",
        "",
    )
    assert "### ℹ️ 为什么需要补充信息" in content
    assert "**触发原因：**" in content
    assert "Token" in content
    assert question in content
    assert "PUE" not in content
    assert "告警" not in content
    assert "(quick:" in content


def test_append_contextual_quick_suggestions_when_llm_body_only():
    question = "统计各机房上周 PUE 排名"
    body = "还需要您补充具体统计口径后才能继续。"
    merged = DataQueryPrompts.append_contextual_quick_suggestions(
        body,
        question,
        "需要用户补充查数信息",
        "",
    )
    assert merged.startswith(body)
    assert "### 💬 您可以这样继续" in merged
    assert "PUE" in merged
    assert "(quick:" in merged


def test_build_missing_reusable_result_fallback_includes_quick_buttons():
    content = DataQueryPrompts.build_missing_reusable_result_fallback(
        "用户: 查询算力SU回款趋势",
        user_question="可视化分析一下",
    )
    assert "### ℹ️ 为什么需要补充信息" in content
    assert "结构化查询结果" in content
    assert "(quick:对刚刚的查询结果做可视化分析)" in content
    assert "(quick:查询算力SU回款趋势)" in content


def test_build_federated_synthesis_prompt_includes_quick_format_and_data_source_order():
    content = DataQueryPrompts.build_federated_synthesis_prompt(
        "上个月各销售拜访次数",
        "| 销售 | 次数 |\n| --- | ---: |\n| A | 10 |",
        dataset_names=["HR_ds", "meta_yes_crm_ds"],
    )
    assert "HR_ds、meta_yes_crm_ds" in content
    assert "（* 数据来源：" in content
    assert "禁止输出裸文本 `quick:" in content
    assert "### 💬 您可能还想了解" in content
    assert "- [🙋 {简短标签}](quick:{完整可发送提问文本})" in content
    assert "quick 区块之前" in content
    assert "禁止把 quick 建议与数据来源写在同一行" in content
