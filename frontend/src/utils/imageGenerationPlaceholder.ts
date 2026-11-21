/**
 * 「正在出图」占位骨架的数据解析。
 *
 * 生图/改图是平台里唯一「一次工具调用可能十几秒、且要花一次钱」的操作。改造前这段
 * 时间用户能看到的只有折叠卡片里一行「调用工具 · generate_image」，消息区完全空白，
 * 于是三件事都无从判断：在做什么、要出什么比例、还要等多久。
 *
 * 撑起这块占位需要的信息全部能从时间线项推出来（工具名、入参里的尺寸与张数、状态），
 * 所以这里只做纯计算，组件只管画：**不猜百分比**——后端没有真实进度，编出来的数字
 * 会在长尾请求上打脸；只给「阶段 + 已用秒数」。
 */

import type { ProcessTimelineItem, ProcessTimelineLogItem } from "./processTimeline";

/** 会出图的两个工具。名字与后端注册的始终一致，改名前先搜这个常量。 */
export const IMAGE_TOOL_NAMES = ["generate_image", "edit_image"] as const;
export type ImageToolName = (typeof IMAGE_TOOL_NAMES)[number];

export type ImagePlaceholderSpec = {
  tool: ImageToolName;
  /** 请求尺寸（`1664x928`）。空串表示「跟随原图」或走后端默认尺寸。 */
  size: string;
  /** 请求张数，已收敛到 1..4。 */
  count: number;
  /** 后端上报的阶段文案（远端进度 / 工具进度），没有则按秒表推。 */
  progressMessage: string;
  /** 该工具项的开始时刻（毫秒时间戳）；时间线没给则为 null，由组件用挂载时刻兜底。 */
  startedAtMs: number | null;
};

/** `1024x1024` / `1024*1024` / `1024X1024` 都认，认不出返回空串。 */
const SIZE_PATTERN = /^(\d{2,5})\s*[xX*×]\s*(\d{2,5})$/;

export function normalizePlaceholderSize(raw: unknown): string {
  const text = String(raw ?? "").trim();
  const hit = SIZE_PATTERN.exec(text);
  return hit ? `${Number(hit[1])}x${Number(hit[2])}` : "";
}

/**
 * 尺寸 → CSS `aspect-ratio` 值，只给骨架里那个「幽灵框」用。
 *
 * 骨架本体是固定的矮横幅（高度与比例无关，见组件里的 `.imgph-band`），比例信息改由这个
 * 空框承载——框宽了矮了，但「这次出的是竖图还是横图」不能丢。给不出比例时用方形：
 * 最中性的「一张图」，竖图横图都不会显得离谱。
 */
export function sizeToAspectRatio(size: string): string {
  const hit = SIZE_PATTERN.exec(String(size || "").trim());
  if (!hit) return "1 / 1";
  const width = Number(hit[1]);
  const height = Number(hit[2]);
  if (!width || !height) return "1 / 1";
  return `${width} / ${height}`;
}

/** 常见出图比例，标签直接写给人看。 */
const COMMON_RATIOS: ReadonlyArray<readonly [number, number, string]> = [
  [1, 1, "1:1"],
  [4, 3, "4:3"],
  [3, 4, "3:4"],
  [3, 2, "3:2"],
  [2, 3, "2:3"],
  [16, 9, "16:9"],
  [9, 16, "9:16"],
  [21, 9, "21:9"],
  [9, 21, "9:21"],
  [5, 4, "5:4"],
  [4, 5, "4:5"],
];

function gcd(a: number, b: number): number {
  return b ? gcd(b, a % b) : a;
}

/**
 * 尺寸 → 幽灵框里的比例标签（`928x1664` → `9:16`）。
 *
 * 先按常见比例对（容差 2%）匹配，因为出图网关给的尺寸常是「近似比例」
 * （928×1664 实际是 0.5577，而 9:16 = 0.5625），直接约分会得到 `29:52` 这种没人读的标签。
 * 匹配不上再约分；约分后仍是大数就写成 `1:1.79` 这类小数比例。
 * 给不出尺寸返回空串——组件写「?」，不假装是方形。
 */
export function sizeToRatioLabel(size: string): string {
  const hit = SIZE_PATTERN.exec(String(size || "").trim());
  if (!hit) return "";
  const width = Number(hit[1]);
  const height = Number(hit[2]);
  if (!width || !height) return "";

  const actual = width / height;
  for (const [w, h, label] of COMMON_RATIOS) {
    const target = w / h;
    if (Math.abs(actual - target) / target <= 0.02) return label;
  }

  const divisor = gcd(width, height);
  const reducedWidth = width / divisor;
  const reducedHeight = height / divisor;
  if (reducedWidth <= 40 && reducedHeight <= 40) return `${reducedWidth}:${reducedHeight}`;
  return actual >= 1 ? `${actual.toFixed(2)}:1` : `1:${(1 / actual).toFixed(2)}`;
}

/**
 * 后端把入参格式化成「缩进 JSON」文本下发（见 `format_tool_args_for_display`），
 * 过长会被 `DEFAULT_TOOL_ARGS_MAX_LEN` 截断。截断后解析必然失败——这时候按「不知道」
 * 处理：比例退回方形、张数退回 1，绝不因为一个解析异常把占位整体吞掉。
 */
export function parseImageToolArgs(raw: unknown): Record<string, unknown> {
  if (!raw) return {};
  if (typeof raw === "object") return raw as Record<string, unknown>;
  const text = String(raw).trim();
  if (!text.startsWith("{")) return {};
  try {
    const parsed = JSON.parse(text);
    return parsed && typeof parsed === "object" ? (parsed as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}

function isImageToolName(name: string): name is ImageToolName {
  return (IMAGE_TOOL_NAMES as readonly string[]).includes(name);
}

/**
 * 从标题里认出工具名，形如 `调用工具: generate_image` / `调用工具 · generate_image`。
 *
 * 这不是「偷懒的兜底」，而是当前唯一的来源：后端「工具开始」事件只下发标题
 * （`event_stream.py` 的 `start_log`），`tool_name` 要到完成事件才带上——而占位
 * 恰恰只在「还没完成」这段窗口里才有意义。只认 `tool_name` 的话骨架永远不会出现。
 */
const TOOL_NAME_IN_TITLE = /调用工具\s*[:：·]?\s*([A-Za-z0-9_.-]+)/;

export function toolNameFromTitle(title: unknown): string {
  const matched = TOOL_NAME_IN_TITLE.exec(String(title ?? ""));
  // 捕获组下标访问在 noUncheckedIndexedAccess 下是 `string | undefined`；
  // 正则只有一组时它必然存在，这里显式兜一层，避免把「必有」写成非空断言。
  const name = matched?.[1];
  return name ? name.trim().toLowerCase() : "";
}

/** 时间线是树（委派子代理时工具挂在内层），所以要递归收集。 */
function collectLogs(
  items: readonly ProcessTimelineItem[] | undefined,
  out: ProcessTimelineLogItem[],
): void {
  if (!Array.isArray(items)) return;
  for (const item of items) {
    if (!item || typeof item !== "object") continue;
    if (item.kind === "log") out.push(item);
    const children = (item as { children?: ProcessTimelineItem[] }).children;
    if (Array.isArray(children) && children.length) collectLogs(children, out);
  }
}

/**
 * 开始时刻归一成毫秒。
 *
 * 后端在两个地方给时间：`tool_started_at` 用 `time.time()`（秒级浮点），另有直接给
 * 毫秒的路径。两者相差三个数量级，判错了秒表会跳到几万分钟，所以按量级区分。
 */
export function normalizeStartedAtMs(raw: unknown): number | null {
  const value = Number(raw);
  if (!Number.isFinite(value) || value <= 0) return null;
  if (value > 1e12) return value; // 已是毫秒
  if (value > 1e9) return value * 1000; // 秒级 time.time()
  return null;
}

/**
 * 找出「当前正在出图」的那次工具调用。
 *
 * 取**最后一条** pending 的图像工具：同一轮里失败重试时，前面那条已不是「当前」，
 * 拿错会把「重试中」显示成「第一次就卡住了」。
 */
export function resolveImagePlaceholder(
  timeline: readonly ProcessTimelineItem[] | undefined,
): ImagePlaceholderSpec | null {
  const logs: ProcessTimelineLogItem[] = [];
  collectLogs(timeline, logs);

  for (let index = logs.length - 1; index >= 0; index -= 1) {
    const log = logs[index];
    // index 由 length-1 递减，越界不可能发生；守卫只为让索引访问的类型成立。
    if (!log || log.status !== "pending") continue;
    // tool_name 优先（完成事件会带），进行中只能靠标题。
    const name = String(log.tool_name || "").trim().toLowerCase() || toolNameFromTitle(log.title);
    if (!isImageToolName(name)) continue;

    const args = parseImageToolArgs(log.tool_args);
    const size = normalizePlaceholderSize(args.size ?? args.image_size ?? "");
    const rawCount = Number(args.n ?? args.count ?? args.num_images ?? 1);
    const count = Number.isFinite(rawCount)
      ? Math.min(4, Math.max(1, Math.trunc(rawCount)))
      : 1;

    return {
      tool: name,
      size,
      count,
      progressMessage: String(log.progressMessage || "").trim(),
      startedAtMs: normalizeStartedAtMs(log.started_at),
    };
  }
  return null;
}

/**
 * 阶段文案。
 *
 * 优先用后端上报的真实阶段；没有就用秒表推三档。三档不是「假装知道进度」，而是把
 * 等待拆成用户能理解的三段：还没发出去 / 模型在画 / 图在往回传。超过 45 秒换成安抚
 * 文案——长尾等待最需要的是一句话，而不是继续转圈。
 */
export function imagePlaceholderStageText(
  spec: Pick<ImagePlaceholderSpec, "tool" | "progressMessage">,
  elapsedMs: number,
): string {
  if (spec.progressMessage) return spec.progressMessage;
  const seconds = Math.max(0, elapsedMs) / 1000;
  if (seconds < 3) return "正在提交请求";
  if (seconds < 45) return spec.tool === "edit_image" ? "正在按你的要求重绘" : "正在绘制";
  return "上游较慢，仍在等待";
}

/** 副标题：说明这次要出什么（尺寸 / 张数 / 是否跟随原图）。 */
export function imagePlaceholderDetailText(
  spec: Pick<ImagePlaceholderSpec, "tool" | "size" | "count">,
): string {
  const parts: string[] = [];
  if (spec.size) parts.push(spec.size);
  else parts.push(spec.tool === "edit_image" ? "跟随原图比例" : "使用默认尺寸");
  if (spec.count > 1) parts.push(`${spec.count} 张`);
  return parts.join(" · ");
}
