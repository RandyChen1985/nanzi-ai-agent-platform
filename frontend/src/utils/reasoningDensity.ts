/**
 * 思考密度（详细 / 标准 / 精简 / 关闭）与精简档的轮次合并。
 *
 * 为什么单独一个模块：
 * - 合并要同时用到 `reasoningCharCount`（字数口径）与 `stripInternalContextBlocks`（剥内部上下文块），
 *   而 `processTimeline.ts` 有「只允许 import type」的硬约束（一旦引入运行时 import，
 *   `tests/frontend` 里那批 node 探针会在加载阶段整批失败），所以不能写进那里；
 * - 放在组件里又无法单测——它是纯数据变换，值得被断言钉住。
 */

import { reasoningCharCount, resolveReasoningPreview } from "./reasoningPreview";
import { stripInternalContextBlocks } from "./streamContentSanitize";
import type { ProcessTimelineItem, ProcessTimelineTextItem } from "./processTimeline";

/**
 * 思考密度四档，按「展示程度」从多到少排列。
 *
 * - `detailed`：每轮思考正文都展开，适合复盘排查；
 * - `standard`：生成中的那轮展开、落定的收成一行摘要（默认，与改造前的行为一致）；
 * - `compact`：多轮思考合并成一行「N 轮 · 合计 Xs · Y 字」，工具调用仍然可见；
 * - `off`：整张链路默认收成标题行，需要时手动点开。
 *
 * 前三个管的都是「卡片里面的思考怎么排」，`off` 管的是「整张卡片开不开」——
 * 它不是第四种排法，而是恢复改造前 `expandThoughts=false` 的语义：
 * 生成中也不展开，只保留标题行。少了它，原本把思考关掉的用户升级后会被迫看思考
 * （或最多只能选到「精简」——那仍然会把整条链路摊开，只是思考收成了一行）。
 *
 * `compact` 才是「一次回答有大量思考」的正解：它不是把思考变成卡片，而是把多轮思考压成一行，
 * 同时**不隐藏工具**——`off` 做不到这个区分（收起时工具也一起收了）。
 */
export type ReasoningDensity = "detailed" | "standard" | "compact" | "off";

export const DEFAULT_REASONING_DENSITY: ReasoningDensity = "standard";

/** 合并后挂在文本项上的汇总信息。 */
export type MergedReasoningSummary = {
  /** 被合并的轮次数。 */
  rounds: number;
  /** 各轮正文字数之和（与标题栏的字数胶囊同口径）。 */
  charCount: number;
  /** 各轮耗时之和（毫秒）；全为 0 时不展示耗时。 */
  durationMs: number;
  /** 每轮的一句话摘要，展开前也能扫到结论。 */
  previews: string[];
  /**
   * 被合并的原始轮次（仅内存引用，不落库）。
   *
   * 合并行要能「点开再收起」：若靠把 items 还原成未合并状态来实现，合并行本身就不在了，
   * 用户再想收回去没有任何入口。保留引用后，合并行始终在，正文挂在它下面渲染。
   */
  sources?: ProcessTimelineTextItem[];
};

/** 兼容旧设置：布尔开关映射到四档。`false` 旧语义是「生成中也不展开，只保留标题行」，对应 `off`。 */
export function normalizeReasoningDensity(value: unknown): ReasoningDensity {
  if (value === "detailed" || value === "compact" || value === "standard" || value === "off") {
    return value;
  }
  // 旧布尔开关关掉时是「整张链路收起」，不是「把思考合并成一行」，必须映射到 off。
  if (value === false || value === "0" || value === "false") return "off";
  return DEFAULT_REASONING_DENSITY;
}

export function reasoningDensityLabel(density: ReasoningDensity): string {
  if (density === "detailed") return "详细";
  if (density === "compact") return "精简";
  if (density === "off") return "关闭";
  return "标准";
}

/**
 * 设置页的一行说明。
 *
 * 每一档都必须写清「回答完成后」的行为——只描述生成中的样子，
 * 用户会以为这个设置只在流式期间生效，回答一出来思考全没了也不知道该调哪。
 */
export function reasoningDensityHint(density: ReasoningDensity): string {
  if (density === "detailed") {
    return "每轮思考都摊开，适合复盘排查；回答完成后整张链路仍会收成标题行";
  }
  if (density === "compact") {
    return "多轮思考合并成一行，工具调用仍然可见；回答完成后同样折叠";
  }
  if (density === "off") {
    return "整条执行链路只留一行标题，思考与工具都不展开，可随时点开；回答完成后同样折叠";
  }
  return "生成中展开当前这轮的推理链；回答完成后自动折叠成一行摘要";
}

function isReasoningItem(item: ProcessTimelineItem): item is ProcessTimelineTextItem {
  return item.kind === "text" && item.textKind === "reasoning";
}

function visibleContentOf(item: ProcessTimelineTextItem): string {
  return stripInternalContextBlocks(item.content || "");
}

/**
 * 精简档：把链路上的多轮思考合并成一条汇总项。
 *
 * 规则与理由：
 * - **跨工具合并**，不做「只合并相邻项」。真实链路是「思考 → 工具 → 思考 → 工具」交替，
 *   严格相邻几乎永远只有一轮，等于没合并；
 * - 汇总项放在**第一轮出现的位置**，这样「先想、再干」的因果顺序不被破坏；
 * - 只处理顶层项。子代理容器内部的步骤属于别人的链路，合并进来会让责任边界更糊；
 * - 少于两轮时原样返回（一轮思考本来就只有一行，没有合并的意义）；
 * - `pending` 取「任一仍在生成」，否则合并项会在流式过程中被判成已落定，标题栏的
 *   「执行中」状态会闪掉。
 */
export function mergeReasoningRounds(items: ProcessTimelineItem[]): ProcessTimelineItem[] {
  const indices: number[] = [];
  items.forEach((item, index) => {
    if (isReasoningItem(item)) indices.push(index);
  });
  if (indices.length < 2) return items;

  const rounds = indices.map((index) => items[index] as ProcessTimelineTextItem);
  const previews: string[] = [];
  let charCount = 0;
  let durationMs = 0;
  for (const round of rounds) {
    const content = visibleContentOf(round);
    charCount += reasoningCharCount(content);
    durationMs += Number(round.execution_time_ms || 0);
    const preview = resolveReasoningPreview(content).text;
    if (preview) previews.push(preview);
  }

  // `rounds[0]` 在 noUncheckedIndexedAccess 下是 `T | undefined`，直接展开会让
  // 整个对象的所有字段都退化成可选、不再满足 ProcessTimelineTextItem。
  const merged: ProcessTimelineTextItem = {
    ...(rounds[0] as ProcessTimelineTextItem),
    content: "",
    pending: rounds.some((round) => round.pending),
    interrupted: rounds.some((round) => round.interrupted),
    contentExpanded: false,
    mergedReasoning: {
      rounds: rounds.length,
      charCount,
      durationMs,
      previews,
      sources: rounds,
    },
  };

  const mergedSet = new Set(indices);
  const output: ProcessTimelineItem[] = [];
  items.forEach((item, index) => {
    if (index === indices[0]) {
      output.push(merged);
      return;
    }
    if (mergedSet.has(index)) return;
    output.push(item);
  });
  return output;
}

/** 汇总行文案：`5 轮 · 合计 29.1s · 2973 字`。 */
export function mergedReasoningLabel(summary: MergedReasoningSummary): string {
  const parts = [`${summary.rounds} 轮`];
  if (summary.durationMs > 0) {
    parts.push(`合计 ${(summary.durationMs / 1000).toFixed(1)}s`);
  }
  if (summary.charCount > 0) {
    parts.push(`${summary.charCount} 字`);
  }
  return parts.join(" · ");
}
