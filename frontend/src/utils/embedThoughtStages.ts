/** EmbedChat-only: aggregate engineering turn logs into business-facing thought stages. */

export type EmbedThoughtStageId = "understand" | "route" | "tools" | "answer";

export type EmbedThoughtLogLike = {
  title?: string;
  category?: string;
  status?: string;
  durationMs?: number | null;
  details?: string;
  isExpanded?: boolean;
};

export type EmbedThoughtStage = {
  id: EmbedThoughtStageId;
  title: string;
  status: "pending" | "success" | "error";
  logs: EmbedThoughtLogLike[];
  durationMs: number | null;
};

const STAGE_META: Record<
  EmbedThoughtStageId,
  { title: string; progress: string }
> = {
  understand: { title: "理解意图", progress: "正在理解问题…" },
  route: { title: "选择能力", progress: "正在选择处理方式…" },
  tools: { title: "执行工具", progress: "正在调用工具…" },
  answer: { title: "整理回答", progress: "正在生成回答…" },
};

function titleText(log: EmbedThoughtLogLike): string {
  return String(log.title || "");
}

function classifyLog(log: EmbedThoughtLogLike): EmbedThoughtStageId {
  const category = String(log.category || "").toLowerCase();
  const title = titleText(log).toLowerCase();

  if (category === "router" || title.includes("路由") || title.includes("多智能体")) {
    return "route";
  }
  if (category === "intent" || title.includes("意图")) {
    return "understand";
  }
  if (
    category === "tool" ||
    category === "sql" ||
    title.includes("工具") ||
    title.includes("schema") ||
    title.includes("sql") ||
    title.includes("检索") ||
    title.includes("经验库") ||
    title.includes("write") ||
    title.includes("汇总工具")
  ) {
    return "tools";
  }
  if (
    category === "llm" ||
    title.includes("模型调用") ||
    title.includes("生成回复") ||
    title.includes("agent 回复") ||
    title.includes("准备回答") ||
    title.includes("汇总")
  ) {
    return "answer";
  }
  return "answer";
}

function stageStatus(logs: EmbedThoughtLogLike[]): EmbedThoughtStage["status"] {
  if (logs.some((log) => log.status === "error")) return "error";
  if (logs.some((log) => log.status === "pending")) return "pending";
  return "success";
}

function stageDurationMs(logs: EmbedThoughtLogLike[]): number | null {
  let total = 0;
  let hasValue = false;
  for (const log of logs) {
    if (typeof log.durationMs === "number" && !Number.isNaN(log.durationMs)) {
      total += log.durationMs;
      hasValue = true;
    }
  }
  return hasValue ? total : null;
}

export function buildEmbedThoughtStages(
  logs: EmbedThoughtLogLike[] | undefined | null,
): EmbedThoughtStage[] {
  if (!logs?.length) return [];

  const order: EmbedThoughtStageId[] = [];
  const buckets = new Map<EmbedThoughtStageId, EmbedThoughtLogLike[]>();

  for (const log of logs) {
    const id = classifyLog(log);
    if (!buckets.has(id)) {
      order.push(id);
      buckets.set(id, []);
    }
    buckets.get(id)!.push(log);
  }

  return order.map((id) => {
    const stageLogs = buckets.get(id) || [];
    return {
      id,
      title: STAGE_META[id].title,
      status: stageStatus(stageLogs),
      logs: stageLogs,
      durationMs: stageDurationMs(stageLogs),
    };
  });
}

export function getEmbedThoughtProgressLabel(
  logs: EmbedThoughtLogLike[] | undefined | null,
): string {
  const stages = buildEmbedThoughtStages(logs);
  const active = stages.find((stage) => stage.status === "pending");
  if (active) return STAGE_META[active.id].progress;
  if (stages.length) return STAGE_META[stages[stages.length - 1].id].progress;
  return "思考中…";
}

export function getEmbedThoughtSummaryTitle(input: {
  logs?: EmbedThoughtLogLike[] | null;
  isThinking?: boolean;
  thinkingText?: string;
  turnType?: string | null;
}): string {
  if (input.isThinking) {
    const progress = getEmbedThoughtProgressLabel(input.logs);
    const custom = String(input.thinkingText || "").trim();
    // Prefer stage progress over engineering rotating slogans when logs exist.
    if (input.logs?.length) return progress;
    return custom || progress;
  }
  return "思考完成";
}

export function formatEmbedStageDuration(durationMs: number | null | undefined): string {
  if (durationMs === undefined || durationMs === null || Number.isNaN(durationMs)) return "";
  if (durationMs < 1000) return `${Math.max(1, Math.round(durationMs))}ms`;
  return `${(durationMs / 1000).toFixed(1)}s`;
}
