"""思考密度三档与精简档轮次合并的行为契约。

密度是纯展示派生数据、不落库，出问题只能靠这里的断言兜住：

1. 旧设置（布尔的 `expandThoughts`）必须能映射到三档，否则老用户在升级后
   会突然看到全部思考展开或全部消失；
2. 精简档要**跨工具合并**——真实链路是「思考 → 工具 → 思考 → 工具」交替，
   只合并严格相邻项等于什么都没合并；
3. 合并项要落在**第一轮的位置**，否则「先想、再干」的因果顺序会被打乱；
4. 子代理容器内部的步骤不参与合并（那是别人的链路，责任边界要清楚）；
5. `pending` 取「任一仍在生成」，否则流式过程中标题栏的「执行中」会闪掉。

直接用仓库的 ts_module_loader 跑真实源码。
"""

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DENSITY_TS = str(ROOT / "frontend/src/utils/reasoningDensity.ts")
TIMELINE_TS = str(ROOT / "frontend/src/utils/processTimeline.ts")
pytestmark = pytest.mark.no_infrastructure


def _run(module: str, expression: str):
    script = f"""
(async () => {{
const {{ loadModule }} = require('./tests/frontend/ts_module_loader.cjs');
const api = loadModule({json.dumps(module)});
const result = await (async () => {{ {expression} }})();
process.stdout.write(JSON.stringify(result));
}})().catch(error => {{ console.error(error); process.exit(1); }});
"""
    completed = subprocess.run(
        ["node", "-e", script], cwd=ROOT, check=True, capture_output=True, text=True
    )
    return json.loads(completed.stdout)


def _reasoning(rid: str, content: str, **extra):
    item = {
        "kind": "text",
        "id": rid,
        "textKind": "reasoning",
        "content": content,
        "pending": False,
    }
    item.update(extra)
    return item


def _log(log_id: str, title: str = "工具"):
    return {"kind": "log", "id": log_id, "title": title, "details": "", "status": "success"}


# —— 档位归一 ——


def test_legacy_boolean_setting_maps_to_density():
    """老用户只存过 expandThoughts：true=标准、false=关闭、缺失=标准。

    这里特意不放宽：旧的「关」是**整张链路收起**（连工具行都不展开），
    不是「把思考合并成一行」。映射到精简档等于强行改掉老用户的设置。
    """
    assert _run(
        DENSITY_TS,
        """
return {
  on: api.normalizeReasoningDensity(true),
  off: api.normalizeReasoningDensity(false),
  zero: api.normalizeReasoningDensity("0"),
  missing: api.normalizeReasoningDensity(null),
  garbage: api.normalizeReasoningDensity("whatever"),
  explicit: api.normalizeReasoningDensity("detailed"),
  explicitOff: api.normalizeReasoningDensity("off"),
};
""",
    ) == {
        "on": "standard",
        "off": "off",
        "zero": "off",
        "missing": "standard",
        "garbage": "standard",
        "explicit": "detailed",
        "explicitOff": "off",
    }


def test_density_labels_and_hints_cover_all_four():
    assert _run(
        DENSITY_TS,
        """
const levels = ["detailed", "standard", "compact", "off"];
return {
  labels: levels.map(api.reasoningDensityLabel),
  hintCount: levels.map(d => api.reasoningDensityHint(d).length > 0),
};
""",
    ) == {
        "labels": ["详细", "标准", "精简", "关闭"],
        "hintCount": [True, True, True, True],
    }


# —— 合并规则 ——


def test_single_round_is_left_untouched():
    """只有一轮时合并没有意义，返回原数组（同一引用，避免无谓重建）。"""
    assert _run(
        DENSITY_TS,
        """
const items = [
  { kind: "text", id: "r1", textKind: "reasoning", content: "只有一轮思考。", pending: false },
  { kind: "log", id: "t1", title: "工具", details: "", status: "success" },
];
const out = api.mergeReasoningRounds(items);
return { sameLength: out.length === items.length, merged: Boolean(out.some(i => i.mergedReasoning)) };
""",
    ) == {"sameLength": True, "merged": False}


def test_rounds_across_tools_are_merged_into_one_row():
    """三处思考夹着两个工具：合并成 1 项，工具仍是独立条目。"""
    assert _run(
        DENSITY_TS,
        """
const items = [
  { kind: "text", id: "r1", textKind: "reasoning", content: "第一轮。", pending: false, execution_time_ms: 3200 },
  { kind: "log", id: "t1", title: "工具", details: "", status: "success" },
  { kind: "text", id: "r2", textKind: "reasoning", content: "第二轮。", pending: false, execution_time_ms: 9800 },
  { kind: "log", id: "t2", title: "工具", details: "", status: "success" },
  { kind: "text", id: "r3", textKind: "reasoning", content: "第三轮。", pending: false, execution_time_ms: 2100 },
];
const out = api.mergeReasoningRounds(items);
const merged = out.find(i => i.mergedReasoning);
return {
  length: out.length,
  kinds: out.map(i => (i.kind === "log" ? "log" : "text")),
  rounds: merged.mergedReasoning.rounds,
  durationMs: merged.mergedReasoning.durationMs,
  previewCount: merged.mergedReasoning.previews.length,
  mergedId: merged.id,
};
""",
    ) == {
        "length": 3,
        "kinds": ["text", "log", "log"],
        "rounds": 3,
        "durationMs": 15100,
        "previewCount": 3,
        "mergedId": "r1",
    }


def test_merged_item_sits_at_first_round_position():
    """合并项必须落在第一轮的位置，否则「先想、再干」的顺序会被打乱。"""
    assert _run(
        DENSITY_TS,
        """
const items = [
  { kind: "text", id: "n1", textKind: "narration", content: "先核一下口径。", pending: false },
  { kind: "text", id: "r1", textKind: "reasoning", content: "第一轮。", pending: false },
  { kind: "log", id: "t1", title: "工具", details: "", status: "success" },
  { kind: "text", id: "r2", textKind: "reasoning", content: "第二轮。", pending: false },
];
const out = api.mergeReasoningRounds(items);
return out.map(i => (i.mergedReasoning ? "MERGED" : i.id));
""",
    ) == ["n1", "MERGED", "t1"]


def test_pending_survives_merge_so_header_keeps_running_state():
    """任一仍在生成时，合并项必须仍算进行中。"""
    assert _run(
        DENSITY_TS,
        """
const items = [
  { kind: "text", id: "r1", textKind: "reasoning", content: "已结束。", pending: false },
  { kind: "log", id: "t1", title: "工具", details: "", status: "success" },
  { kind: "text", id: "r2", textKind: "reasoning", content: "还在写。", pending: true },
];
const out = api.mergeReasoningRounds(items);
const merged = out.find(i => i.mergedReasoning);
return { pending: merged.pending, bodyEmpty: merged.content === "" };
""",
    ) == {"pending": True, "bodyEmpty": True}


def test_nested_reasoning_inside_subagent_is_not_merged():
    """子代理容器内部的步骤属于别人的链路，不参与顶层合并。"""
    assert _run(
        DENSITY_TS,
        """
const items = [
  { kind: "text", id: "r1", textKind: "reasoning", content: "主代理第一轮。", pending: false },
  {
    kind: "log", id: "subagent_1", title: "调用子代理：元数据专家", details: "", status: "success",
    children: [{ kind: "text", id: "r-child", textKind: "reasoning", content: "子代理在想。", pending: false }],
  },
  { kind: "text", id: "r2", textKind: "reasoning", content: "主代理第二轮。", pending: false },
];
const out = api.mergeReasoningRounds(items);
const merged = out.find(i => i.mergedReasoning);
const container = out.find(i => i.id === "subagent_1");
return {
  rounds: merged.mergedReasoning.rounds,
  childUntouched: container.children[0].content === "子代理在想。",
  childHasNoSummary: !container.children[0].mergedReasoning,
};
""",
    ) == {"rounds": 2, "childUntouched": True, "childHasNoSummary": True}


def test_summary_label_reads_rounds_duration_and_chars():
    assert _run(
        DENSITY_TS,
        """
return [
  api.mergedReasoningLabel({ rounds: 5, durationMs: 29100, charCount: 2973, previews: [] }),
  api.mergedReasoningLabel({ rounds: 2, durationMs: 0, charCount: 0, previews: [] }),
  api.mergedReasoningLabel({ rounds: 3, durationMs: 1500, charCount: 88, previews: [] }),
];
""",
    ) == ["5 轮 · 合计 29.1s · 2973 字", "2 轮", "3 轮 · 合计 1.5s · 88 字"]


def test_char_count_ignores_markdown_marks_like_the_capsule():
    """字数与标题栏胶囊同口径（剥标记、去空白），否则合并行和单行会自相矛盾。"""
    assert _run(
        DENSITY_TS,
        """
const items = [
  { kind: "text", id: "r1", textKind: "reasoning", content: "**甲乙** 丙", pending: false },
  { kind: "text", id: "r2", textKind: "reasoning", content: "丁戊", pending: false },
];
const merged = api.mergeReasoningRounds(items).find(i => i.mergedReasoning);
return merged.mergedReasoning.charCount;
""",
    ) == 5


# —— 展开判定接受密度 ——


def test_expand_decision_honours_density_for_unsettled_items():
    assert _run(
        TIMELINE_TS,
        """
const item = { kind: "text", id: "r1", textKind: "reasoning", content: "x", pending: false };
const pending = { kind: "text", id: "r2", textKind: "reasoning", content: "x", pending: true };
return {
  detailedSettled: api.isReasoningContentExpanded(item, { density: "detailed" }),
  compactSettled: api.isReasoningContentExpanded(item, { density: "compact" }),
  standardSettled: api.isReasoningContentExpanded(item, { density: "standard" }),
  standardPending: api.isReasoningContentExpanded(pending, { density: "standard" }),
  compactPending: api.isReasoningContentExpanded(pending, { density: "compact" }),
  legacyNoOption: api.isReasoningContentExpanded(pending),
};
""",
    ) == {
        "detailedSettled": True,
        "compactSettled": False,
        "standardSettled": False,
        "standardPending": True,
        "compactPending": False,
        "legacyNoOption": True,
    }


def test_manual_toggle_beats_density():
    """用户手动点过就以手动为准，密度只提供默认值。"""
    assert _run(
        TIMELINE_TS,
        """
const opened = { kind: "text", id: "r1", textKind: "reasoning", content: "x", pending: false, contentExpanded: true };
const closed = { kind: "text", id: "r2", textKind: "reasoning", content: "x", pending: true, contentExpanded: false };
return {
  openedInCompact: api.isReasoningContentExpanded(opened, { density: "compact" }),
  closedInDetailed: api.isReasoningContentExpanded(closed, { density: "detailed" }),
};
""",
    ) == {"openedInCompact": True, "closedInDetailed": False}
