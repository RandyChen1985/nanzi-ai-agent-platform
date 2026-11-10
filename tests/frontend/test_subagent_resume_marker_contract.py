"""子代理「续聊 #N」标记的前端契约。

用户场景：父智能体在同一轮里追问同一位子代理（`resume=true`）。时间线上两次委派
看起来一模一样，用户无法判断第二次是「接着上一轮查」还是「重新查了一遍」——
后者正是口径打架的来源。这里锁住三件事：

1. 子代理元数据能携带续聊标记（`resume` / `resume_index`），否则前端无从显示；
2. 标记要做数字归一化，字符串 "2" 与数字 2 都要能显示成「续聊 #2」；
3. 委派容器标题与折叠摘要都要体现续聊序号，且**非续聊时不出现该字样**。
"""
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

TRACE = Path("frontend/src/utils/subagentTrace.ts").read_text()
TIMELINE = Path("frontend/src/utils/processTimeline.ts").read_text()


def test_subagent_trace_meta_carries_resume_fields():
    assert "resume?: boolean" in TRACE
    assert "resume_index?: number" in TRACE


def test_resume_index_is_normalized_to_positive_int():
    """后端可能给 "2"、2 或 0；前端要统一成数字，非正数视为「不显示」。"""
    assert "resume_index" in TRACE
    assert "Number(" in TRACE
    assert "Math.trunc" in TRACE


def test_delegation_container_title_marks_resume_round():
    """容器标题要能区分「续聊 #N」，否则两次委派在时间线上无法分辨。

    标题不自己拼序号，而是复用 `subagentResumeLabel()`——徽标与标题各拼一套的话，
    数据源给 `"2"` 或 `resume: 1` 时很容易出现「标题写了、徽标没写」的错位。
    """
    assert "续聊 #" in TIMELINE
    assert "subagentResumeLabel(" in TIMELINE


def test_resume_suffix_is_conditional_on_resume_flag():
    """只有真的续聊才加后缀：首次委派不能显示「续聊 #0」。"""
    assert "subagentResumeLabel(mergedSubagent)" in TIMELINE, "标题未复用徽标文案"
    assert "续聊 #" in TRACE
    # 双真值 + 序号为正：任一缺失都不显示后缀
    assert "meta?.resume && meta?.resume_index" in TRACE


def test_resume_summary_appears_in_trace_summary():
    """折叠状态下的摘要行同样要可见，用户不展开也能看出是续聊。"""
    summary = TRACE.split("formatSubagentTraceSummary", 1)[1]
    assert "resume" in summary


def test_delegation_container_matching_prefers_run_id():
    """同一轮第二次委派必须能拿到自己的容器。

    查找容器时若先匹配「任意派发卡」，第二个 run 会并进第一个容器，还会把第一个
    容器的元数据与标题覆盖成「续聊 #N」——首次委派被标成续聊，两个 run 只剩一个卡片。
    """
    source = (
        Path(__file__).resolve().parents[2] / "frontend/src/utils/processTimeline.ts"
    ).read_text(encoding="utf-8")

    assert "if (targetRunId) return c.subagent?.run_id === targetRunId;" in source
    # 标题后缀要用容器上已合并的元数据，否则后续不带 subagent 的事件会抹掉后缀
    assert "const mergedSubagent = containerLog.subagent ?? data.subagent;" in source
