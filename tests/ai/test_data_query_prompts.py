from app.services.ai.executors.prompts import DataQueryPrompts


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
    assert "数据对象、指标和时间范围" in content
    assert DataQueryPrompts.has_quick_suggestions(content)
    assert "PUE" not in content


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
