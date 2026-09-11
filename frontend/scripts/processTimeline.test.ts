import assert from "node:assert/strict";
import {
  hydrateHistoryProcessTimeline,
  timelineHasPending,
  upsertTimelineTodo,
  upsertTimelineLog,
  formatTimelineTitle,
  type ProcessTimelineTarget,
} from "../src/utils/processTimeline.ts";

function testTodoTimelineSiblingAndReplacement() {
  const target: ProcessTimelineTarget = {
    processTimeline: [
      {
        kind: "text",
        id: "narration_1",
        textKind: "narration",
        content: "开始执行",
        pending: false,
        children: [],
      },
    ],
  };

  upsertTimelineTodo(target, {
    todos: [
      { content: "检索知识库", status: "in_progress" },
      { content: "整理答案", status: "pending" },
    ],
  });
  assert.equal(target.processTimeline?.length, 2);
  assert.equal(target.processTimeline?.[1].kind, "todo");
  assert.equal(timelineHasPending(target.processTimeline), true);

  upsertTimelineTodo(target, {
    todos: [
      { content: "检索知识库", status: "completed" },
      { content: "整理答案", status: "in_progress" },
    ],
  });
  assert.equal(target.processTimeline?.length, 2);
  const todo = target.processTimeline?.[1];
  assert.equal(todo?.kind, "todo");
  assert.equal(todo?.todos[0].status, "completed");
  assert.equal(todo?.todos[1].status, "in_progress");
  assert.deepEqual(todo?.counts, { pending: 0, in_progress: 1, completed: 1 });

  upsertTimelineTodo(target, { todos: [] });
  assert.equal(target.processTimeline?.length, 1);
  assert.equal(target.processTimeline?.[0].kind, "text");
}

function testTodoHistoryRemainsIndependent() {
  const hydrated = hydrateHistoryProcessTimeline([
    {
      kind: "todo",
      id: "todo_current",
      title: "任务清单",
      todos: [{ content: "生成报告", status: "completed" }],
      counts: { pending: 0, in_progress: 0, completed: 1 },
    },
    {
      kind: "text",
      id: "narration_1",
      textKind: "narration",
      content: "已完成",
      pending: false,
    },
  ]);

  assert.equal(hydrated.length, 2);
  assert.equal(hydrated[0].kind, "todo");
  assert.equal(hydrated[1].kind, "text");
  assert.equal(timelineHasPending(hydrated), false);
}

function testSubagentNesting() {
  const target: ProcessTimelineTarget = {
    processTimeline: [
      {
        kind: "text",
        id: "narration_1",
        textKind: "narration",
        content: "让我委派给数据智能助手来查询商品数据。",
        pending: false,
        children: [],
        childrenExpanded: true,
      },
    ],
  };

  // 1. Subagent lifecycle start
  upsertTimelineLog(target, {
    id: "subagent_run123",
    title: "调用子代理: 数据智能助手",
    category: "agent",
    status: "pending",
    subagent: {
      run_id: "run123",
      agent_name: "chat-bi",
      display_name: "数据智能助手",
      child_trace_id: "trace_sub_1",
      parent_trace_id: "trace_main",
    },
  });

  const narration = target.processTimeline![0];
  assert.equal(narration.kind, "text");
  assert.equal(narration.children?.length, 1);
  const container = narration.children![0];
  assert.equal(container.id, "subagent_run123");
  assert.equal(container.title, "调用子代理: 数据智能助手");

  // 2. Subagent inner steps (non-tool + tool)
  upsertTimelineLog(target, {
    id: "log_classifier",
    title: "ChatBI 请求类别分析结果",
    category: "intent",
    status: "success",
    execution_time_ms: 1700,
    subagent: {
      run_id: "run123",
      agent_name: "chat-bi",
      display_name: "数据智能助手",
    },
  });

  upsertTimelineLog(target, {
    id: "log_schema",
    title: "工具完成: get_dataset_schema",
    category: "tool",
    status: "success",
    execution_time_ms: 203,
    subagent: {
      run_id: "run123",
      agent_name: "chat-bi",
      display_name: "数据智能助手",
    },
  });

  upsertTimelineLog(target, {
    id: "log_sql",
    title: "工具完成: execute_sql_query",
    category: "tool",
    status: "success",
    execution_time_ms: 982,
    subagent: {
      run_id: "run123",
      agent_name: "chat-bi",
      display_name: "数据智能助手",
    },
  });

  // Narration direct tool children should only be 1 (the subagent container)
  assert.equal(narration.children?.length, 1);
  // The subagent container should hold all 3 inner steps!
  assert.equal(container.children?.length, 3);
  assert.equal(container.children![0].title, "ChatBI 请求类别分析结果");
  assert.equal(container.children![1].title, "工具完成: get_dataset_schema");
  assert.equal(container.children![2].title, "工具完成: execute_sql_query");

  // 3. Subagent completion
  upsertTimelineLog(target, {
    id: "subagent_run123",
    title: "调用子代理: 数据智能助手",
    category: "agent",
    status: "success",
    execution_time_ms: 16865,
    subagent: {
      run_id: "run123",
      agent_name: "chat-bi",
      display_name: "数据智能助手",
    },
  });
  assert.equal(container.status, "success");
  assert.equal(container.execution_time_ms, 16865);
  assert.equal(container.children?.length, 3);

  // 4. Main runner tool completion deduplication
  upsertTimelineLog(target, {
    id: "tool_sub_agent_call_999",
    title: "工具完成: sub_agent_call (16865ms)",
    category: "tool",
    status: "success",
    execution_time_ms: 16865,
  });

  // Should NOT create another separate sibling tool
  assert.equal(narration.children?.length, 1);
  assert.equal(target.processTimeline?.length, 1);

  upsertTimelineLog(target, {
    id: "tool_resolution_0_filtered_search_knowledge_base",
    title: "工具已被当前权限范围过滤：search_knowledge_base",
    details: "tool excluded by the active allowlist",
    category: "tool_resolution",
    status: "warning",
  });
  assert.equal(narration.children?.length, 2);
  assert.equal(narration.children![1].category, "tool_resolution");

  console.log("testSubagentNesting passed successfully!");
}

function testHydrateHistoryReorganization() {
  const flatHistory: any[] = [
    {
      kind: "text",
      id: "narration_1",
      textKind: "narration",
      content: "我看到了两个可用的数据集。",
      pending: false,
      children: [
        {
          kind: "log",
          id: "tool_sub_agent_call",
          title: "工具完成: sub_agent_call (23755ms)",
          status: "success",
          category: "tool",
          execution_time_ms: 23755,
        },
        {
          kind: "log",
          id: "tool_schema_1",
          title: "[数据智能助手] 工具完成: get_dataset_schema",
          status: "success",
          category: "tool",
          execution_time_ms: 288,
        },
      ],
    },
    {
      kind: "log",
      id: "subagent_run123",
      title: "调用子代理: 数据智能助手",
      status: "success",
      category: "agent",
      execution_time_ms: 22800,
      subagent: {
        run_id: "run123",
        display_name: "数据智能助手",
      },
    },
    {
      kind: "log",
      id: "log_classifier",
      title: "[数据智能助手] ChatBI 请求类别分析结果",
      status: "success",
      execution_time_ms: 1800,
    },
    {
      kind: "log",
      id: "log_example",
      title: "[数据智能助手] ✨ 命中经验库案例 (1条, 匹配度一般)",
      status: "success",
      execution_time_ms: 258,
    },
  ];

  const hydrated = hydrateHistoryProcessTimeline(flatHistory, "思考内容");

  assert.equal(hydrated.length, 2); // narration + reasoning
  const narration = hydrated[0];
  assert.equal(narration.kind, "text");
  // The narration should now have only 1 tool (the merged subagent container)
  assert.equal(narration.children?.length, 1);
  const container = narration.children![0];
  assert.equal(container.title.includes("子代理"), true);
  // The container should hold all 3 steps: get_dataset_schema, ChatBI 请求类别分析结果, 命中经验库案例
  assert.equal(container.children?.length, 3);
  assert.equal(container.children![0].title, "[数据智能助手] 工具完成: get_dataset_schema");
  assert.equal(container.children![1].title, "[数据智能助手] ChatBI 请求类别分析结果");
  assert.equal(container.children![2].title, "[数据智能助手] ✨ 命中经验库案例 (1条, 匹配度一般)");

  console.log("testHydrateHistoryReorganization passed successfully!");
}

function testSubagentToolAndLifecycleDeduplication() {
  const target: ProcessTimelineTarget = {
    processTimeline: [
      {
        kind: "text",
        id: "narration_1",
        textKind: "narration",
        content: "好的，我来帮您识别数据中的高价值客户。",
        pending: false,
        children: [],
        childrenExpanded: true,
      },
    ],
  };

  // 1. Tool sub_agent_call arrived first
  upsertTimelineLog(target, {
    id: "call_subagent_1",
    title: "委派智能体",
    tool_name: "sub_agent_call",
    category: "tool",
    status: "pending",
  });

  // 2. Lifecycle log arrived with display_name
  upsertTimelineLog(target, {
    id: "subagent_run123",
    title: "调用子代理: 数据智能助手",
    category: "agent",
    status: "pending",
    subagent: {
      run_id: "run123",
      agent_name: "chat-bi",
      display_name: "数据智能助手",
    },
  });

  const narration = target.processTimeline![0];
  // Must be merged into exactly 1 child tool container!
  assert.equal(narration.children?.length, 1);
  const container = narration.children![0];
  assert.equal(formatTimelineTitle(container.title), "委派智能体 · 数据智能助手");

  // 3. Inner steps including a noise heartbeat step
  upsertTimelineLog(target, {
    id: "log_schema",
    title: "[数据智能助手] 工具完成: get_dataset_schema",
    status: "success",
    subagent: { run_id: "run123" },
  });

  upsertTimelineLog(target, {
    id: "log_heartbeat",
    title: "[数据智能助手] ✨ 开始生成回复",
    status: "success",
    subagent: { run_id: "run123" },
  });

  // After reorganization or hydration, noise heartbeat must be filtered
  const hydrated = hydrateHistoryProcessTimeline(target.processTimeline);
  const hydratedNarration = hydrated[0];
  assert.equal(hydratedNarration.children?.length, 1);
  const hydratedContainer = hydratedNarration.children![0];
  assert.equal(hydratedContainer.children?.length, 1);
  assert.equal(hydratedContainer.children![0].title, "[数据智能助手] 工具完成: get_dataset_schema");

  console.log("testSubagentToolAndLifecycleDeduplication passed successfully!");
}

testSubagentNesting();
testHydrateHistoryReorganization();
testSubagentToolAndLifecycleDeduplication();
testTodoTimelineSiblingAndReplacement();
testTodoHistoryRemainsIndependent();
