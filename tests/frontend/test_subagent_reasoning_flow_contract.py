"""子代理思考与并行委派的**数据链路**契约。

这一组锁的是「后端下发了，前端能不能真的用上」。改造过程中踩到的坑全在这一段：
后端把思考作为 `category="reasoning"` 的日志发出来，前端却有四道闸门各自可能把它丢掉：

1. `syncProcessTimelineLog` 是**显式字段映射**，漏一个字段后端发了也进不来；
2. `upsertTimelineLog` 更新已有条目时也是白名单，新建/更新两条路都得认；
3. `findSubagentContainerLog` 的兜底会把并行子代理的步骤塞进外层
   `sub_agent_batch_call` 工具日志（它没有 subagent 元数据，却长得像容器）；
4. `reorganizeSubagentItems` 历史回放时把连续的多个容器并成一张。

每一条单独看都不报错、不崩溃，只是「功能静默不生效」——所以必须逐条钉住。
"""

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TIMELINE_TS = str(ROOT / "frontend/src/utils/processTimeline.ts")
DENSITY_TS = str(ROOT / "frontend/src/utils/reasoningDensity.ts")
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


def _subagent_log(log_id: str, run_id: str, **extra):
    item = {
        "kind": "log",
        "id": log_id,
        "title": "调用子代理：元数据专家",
        "details": "",
        "status": "pending",
        "subagent": {"display_name": "元数据专家", "run_id": run_id},
    }
    item.update(extra)
    return item


# —— 实时流：reasoning 必须穿得过显式映射 ——


def test_reasoning_flag_survives_upsert():
    """实时流新建条目时要记住 reasoning，否则刷新前一直当普通工具输出渲染。"""
    assert _run(
        TIMELINE_TS,
        """
const target = { processTimeline: [] };
api.upsertTimelineLog(target, {
  id: "sr_1",
  title: "[元数据专家] 子代理思考",
  details: "先确认字段。",
  status: "success",
  category: "reasoning",
  reasoning: true,
});
const item = target.processTimeline[0];
return { reasoning: item.reasoning, category: item.category };
""",
    ) == {"reasoning": True, "category": "reasoning"}


def test_reasoning_flag_survives_later_updates():
    """后续事件（完成/耗时）不能把已经写上的 reasoning 洗掉。"""
    assert _run(
        TIMELINE_TS,
        """
const target = { processTimeline: [] };
api.upsertTimelineLog(target, { id: "sr_1", title: "思考", details: "a", status: "pending", reasoning: true });
api.upsertTimelineLog(target, { id: "sr_1", details: "ab", status: "success", execution_time_ms: 1200 });
return target.processTimeline[0].reasoning;
""",
    ) is True


def test_reasoning_flag_survives_log_merge():
    """`msg.logs` 与时间线合并时同样要保留，否则实时渲染会退化。"""
    assert _run(
        TIMELINE_TS,
        """
const timeline = [{
  kind: "log", id: "sr_1", title: "思考", details: "a", status: "pending",
  category: "reasoning", reasoning: true, isExpanded: false, children: [],
}];
api.mergeTimelineLogs(timeline, [{
  id: "sr_1", title: "思考", details: "ab", status: "success", reasoning: true,
}]);
return timeline[0].reasoning;
""",
    ) is True


# —— 并行委派：步骤要落到各自容器，不能被外层工具日志截胡 ——


def test_parallel_steps_land_in_their_own_container():
    """外层 `sub_agent_batch_call` 工具日志没有 subagent 元数据，不能当成容器接收步骤。"""
    assert _run(
        TIMELINE_TS,
        """
const target = {
  processTimeline: [
    { kind: "log", id: "batch_tool", title: "调用工具：sub_agent_batch_call", details: "",
      status: "pending", tool_name: "sub_agent_batch_call", isExpanded: false, children: [] },
    { kind: "log", id: "subagent_run_a", title: "调用子代理：元数据专家", details: "",
      status: "pending", subagent: { display_name: "元数据专家", run_id: "run_a" },
      isExpanded: false, children: [] },
    { kind: "log", id: "subagent_run_b", title: "调用子代理：查数专家", details: "",
      status: "pending", subagent: { display_name: "查数专家", run_id: "run_b" },
      isExpanded: false, children: [] },
  ],
};
api.upsertTimelineLog(target, {
  id: "step_a1", title: "[元数据专家] 调用工具：describe_table", details: "ok", status: "success",
  subagent: { display_name: "元数据专家", run_id: "run_a" },
});
api.upsertTimelineLog(target, {
  id: "step_b1", title: "[查数专家] 调用工具：execute_sql_query", details: "ok", status: "success",
  subagent: { display_name: "查数专家", run_id: "run_b" },
});
const byId = Object.fromEntries(target.processTimeline.map((i) => [i.id, i]));
const nested = (id) => {
  const found = [];
  const walk = (nodes) => (nodes || []).forEach((n) => { found.push(String(n.id)); walk(n.children); });
  walk(byId[id].children);
  return found;
};
return {
  batchToolSteps: nested("batch_tool"),
  containerA: nested("subagent_run_a"),
  containerB: nested("subagent_run_b"),
};
""",
    ) == {
        "batchToolSteps": [],
        "containerA": ["step_a1"],
        "containerB": ["step_b1"],
    }


def test_history_keeps_parallel_containers_apart():
    """历史回放：run_id 不同的容器各自成卡，不能被并成一张。"""
    assert _run(
        TIMELINE_TS,
        """
const stored = [
  { kind: "text", id: "narr_1", textKind: "narration", content: "同时查两路。", pending: false,
    children: [
      { kind: "log", id: "subagent_run_a", title: "调用子代理：元数据专家", details: "",
        status: "success", subagent: { display_name: "元数据专家", run_id: "run_a" },
        execution_time_ms: 8400, isExpanded: false,
        children: [{ kind: "log", id: "a_step", title: "调用工具：describe_table", details: "", status: "success" }] },
      { kind: "log", id: "subagent_run_b", title: "调用子代理：查数专家", details: "",
        status: "success", subagent: { display_name: "查数专家", run_id: "run_b" },
        execution_time_ms: 6100, isExpanded: false,
        children: [{ kind: "log", id: "b_step", title: "调用工具：execute_sql_query", details: "", status: "success" }] },
    ] },
];
const out = api.hydrateHistoryProcessTimeline(stored, undefined);
const containers = (out[0].children || []).filter((c) => String(c.id).startsWith("subagent_"));
return {
  containerIds: containers.map((c) => c.id),
  steps: containers.map((c) => (c.children || []).map((s) => s.id)),
  durations: containers.map((c) => c.execution_time_ms),
};
""",
    ) == {
        "containerIds": ["subagent_run_a", "subagent_run_b"],
        "steps": [["a_step"], ["b_step"]],
        "durations": [8400, 6100],
    }


def test_history_still_merges_duplicate_container_of_same_run():
    """同一个子代理的重复容器（工具事件 + 生命周期事件）仍要合并成一张。"""
    assert _run(
        TIMELINE_TS,
        """
const stored = [
  { kind: "text", id: "narr_1", textKind: "narration", content: "查一下。", pending: false,
    children: [
      { kind: "log", id: "subagent_run_a", title: "调用子代理：元数据专家", details: "",
        status: "pending", subagent: { display_name: "元数据专家", run_id: "run_a" },
        isExpanded: false, children: [] },
      { kind: "log", id: "subagent_run_a_done", title: "调用子代理：元数据专家", details: "完成",
        status: "success", subagent: { display_name: "元数据专家", run_id: "run_a" },
        execution_time_ms: 5200, isExpanded: false,
        children: [{ kind: "log", id: "a_step", title: "调用工具：describe_table", details: "", status: "success" }] },
    ] },
];
const out = api.hydrateHistoryProcessTimeline(stored, undefined);
const containers = (out[0].children || []).filter((c) => String(c.id).startsWith("subagent_"));
return { count: containers.length, steps: (containers[0].children || []).map((s) => s.id) };
""",
    ) == {"count": 1, "steps": ["a_step"]}


# —— 精简档：合并项要能展开回原文 ——


def test_merged_rounds_keep_sources_for_expansion():
    """合并行点开后要能逐轮看到原文，否则「展开」无从实现。"""
    assert _run(
        DENSITY_TS,
        """
const items = [
  { kind: "text", id: "r1", textKind: "reasoning", content: "第一轮。", pending: false },
  { kind: "text", id: "r2", textKind: "reasoning", content: "第二轮。", pending: false },
];
const merged = api.mergeReasoningRounds(items).find((i) => i.mergedReasoning);
return {
  sourceIds: merged.mergedReasoning.sources.map((s) => s.id),
  contents: merged.mergedReasoning.sources.map((s) => s.content),
};
""",
    ) == {"sourceIds": ["r1", "r2"], "contents": ["第一轮。", "第二轮。"]}
