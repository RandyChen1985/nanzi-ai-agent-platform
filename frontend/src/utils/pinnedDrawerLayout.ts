/**
 * 钉住面板（右侧 dock）的布局决策。
 *
 * 抽出纯函数的原因：面板宽度是各组件通过 v-model 实时同步的，求和、封顶、偏移
 * 三件事混在 EmbedChat 的 computed 里没法测，笔画一遍才发现两个钉住面板就会把
 * 对话区挤成 0 宽（`marginRight` 只挡住了溢出、没挡住「挤没内容」），而偏移用的
 * `right-[${rem}rem]` 是运行时拼的类名——Tailwind 静态扫描不到，构建产物里根本
 * 没有这条规则，于是第三个面板开始就叠在同一个位置。
 *
 * 规则（`panels` 按「从右到左」传入，第 0 个最贴右边）：
 * 1. 宽度 <= 0 的面板视为没钉住，直接忽略。
 * 2. 容器宽度 < SINGLE_PINNED_MAX_WIDTH_PX 时只保留一个，其余降级（dropped）。
 * 3. 并排时总宽不得超过 `availableWidthPx - MIN_CHAT_WIDTH_PX`，超了就按顺序丢后面的。
 * 4. 让位宽度封顶在预算内，保证对话区至少 MIN_CHAT_WIDTH_PX（容器太小时为 0）。
 * 5. 偏移连续累加：每个面板的 right 偏移 = 它右侧所有保留面板宽度之和。
 */

/** 对话区至少要留出的宽度，低于它就没有可读性了。 */
export const MIN_CHAT_WIDTH_PX = 320;

/** 低于这个容器宽度只允许一个右侧钉住面板，不允许并排。 */
export const SINGLE_PINNED_MAX_WIDTH_PX = 1100;

export interface PinnedDrawerPanel {
  key: string;
  widthPx: number;
}

export interface PinnedDrawerLayoutInput {
  availableWidthPx: number;
  /** 从右到左排列：第 0 个最贴右边缘。 */
  panels: PinnedDrawerPanel[];
}

export interface PinnedDrawerLayout {
  /** 仍然钉住、参与让位的面板 key（顺序同输入）。 */
  keptKeys: string[];
  /** 因空间不足被降级的面板 key。 */
  droppedKeys: string[];
  /** 对话区右侧让位宽度（px），已封顶。 */
  marginRightPx: number;
  /** 每个保留面板的 right 偏移（px）。 */
  rightOffsetPx: Record<string, number>;
}

const EMPTY_LAYOUT: PinnedDrawerLayout = {
  keptKeys: [],
  droppedKeys: [],
  marginRightPx: 0,
  rightOffsetPx: {},
};

export function computePinnedDrawerLayout(
  input: PinnedDrawerLayoutInput,
): PinnedDrawerLayout {
  const available = Math.max(0, Number(input?.availableWidthPx) || 0);
  const panels = (input?.panels ?? []).filter(
    (panel) => panel && Number(panel.widthPx) > 0,
  );
  if (panels.length === 0 || available <= 0) return { ...EMPTY_LAYOUT };

  const allowSideBySide = available >= SINGLE_PINNED_MAX_WIDTH_PX;
  const budget = Math.max(0, available - MIN_CHAT_WIDTH_PX);

  const kept: PinnedDrawerPanel[] = [];
  const droppedKeys: string[] = [];
  let total = 0;

  for (const panel of panels) {
    const width = Number(panel.widthPx) || 0;
    if (width <= 0) continue;
    // 第一个面板永远保留：否则窄容器里会出现「钉了但什么都不显示」。
    const blockedBySingle = !allowSideBySide && kept.length >= 1;
    const blockedByBudget = kept.length >= 1 && total + width > budget;
    if (blockedBySingle || blockedByBudget) {
      droppedKeys.push(panel.key);
      continue;
    }
    kept.push({ key: panel.key, widthPx: width });
    total += width;
  }

  const rightOffsetPx: Record<string, number> = {};
  let offset = 0;
  for (const panel of kept) {
    rightOffsetPx[panel.key] = offset;
    offset += panel.widthPx;
  }

  return {
    keptKeys: kept.map((panel) => panel.key),
    droppedKeys,
    marginRightPx: Math.min(total, budget),
    rightOffsetPx,
  };
}
