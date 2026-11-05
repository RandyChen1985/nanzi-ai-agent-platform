<template>
  <div ref="rootRef" class="relative">
    <!-- 触发器：主文案给快捷名/“自定义区间”，副文案给真实日期，两者都要有——
         只显示“最近 7 天”时用户不知道具体统计了哪几天。 -->
    <button
      type="button"
      class="flex h-9 items-center gap-2 rounded-xl border bg-white px-2.5 text-left shadow-sm transition-all active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-60"
      :class="open ? 'border-primary ring-2 ring-primary/15' : 'border-gray-200 hover:border-primary/40'"
      :disabled="disabled"
      aria-haspopup="dialog"
      :aria-expanded="open"
      @click="togglePanel"
    >
      <span class="grid h-6 w-6 flex-shrink-0 place-items-center rounded-md bg-primary/10 text-primary">
        <svg class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24">
          <rect x="3" y="4" width="18" height="18" rx="2" />
          <path d="M16 2v4M8 2v4M3 10h18" />
        </svg>
      </span>
      <span class="flex min-w-[96px] flex-col leading-tight">
        <b class="text-[12.5px] font-bold leading-tight text-gray-800">{{ triggerLabel }}</b>
        <small class="text-[10.5px] leading-tight tabular-nums text-gray-400">{{ triggerSubLabel }}</small>
      </span>
      <svg
        class="h-3.5 w-3.5 flex-shrink-0 text-gray-400 transition-transform"
        :class="{ 'rotate-180': open }"
        fill="none"
        stroke="currentColor"
        stroke-width="2.5"
        stroke-linecap="round"
        stroke-linejoin="round"
        viewBox="0 0 24 24"
      >
        <path d="M6 9l6 6 6-6" />
      </svg>
    </button>

    <!-- 面板：桌面端挂在按钮下方，窄屏改为底部抽屉式，避免 644px 宽被裁掉 -->
    <div
      v-if="open"
      data-date-range-panel
      role="dialog"
      aria-label="选择统计区间"
      class="absolute right-0 z-50 mt-2 w-[644px] max-w-[calc(100vw-2rem)] overflow-hidden rounded-2xl border border-gray-100 bg-white shadow-2xl max-[720px]:fixed max-[720px]:inset-x-3 max-[720px]:bottom-3 max-[720px]:mt-0 max-[720px]:w-auto"
    >
      <div class="flex items-center justify-between border-b border-gray-50 px-4 py-3">
        <span class="text-[13px] font-bold text-gray-900">选择统计区间</span>
        <button
          type="button"
          class="grid h-7 w-7 place-items-center rounded-lg text-gray-400 transition-colors hover:bg-gray-50 hover:text-gray-600"
          aria-label="关闭"
          @click="closePanel"
        >
          ✕
        </button>
      </div>

      <div class="grid grid-cols-[150px_1fr] max-sm:grid-cols-1">
        <!-- 快捷区间 -->
        <div class="flex flex-col gap-0.5 border-r border-gray-50 bg-gray-50/40 p-2 max-sm:flex-row max-sm:gap-1 max-sm:overflow-x-auto max-sm:border-b max-sm:border-r-0">
          <p class="px-2.5 pb-1.5 pt-1 text-[10px] font-bold tracking-wide text-gray-400 max-sm:hidden">快捷选择</p>
          <button
            v-for="preset in presets"
            :key="preset.key"
            type="button"
            class="flex items-center justify-between gap-2 rounded-lg px-2.5 py-1.5 text-left text-xs transition-colors max-sm:whitespace-nowrap"
            :class="activePresetKey === preset.key
              ? 'bg-primary/10 font-bold text-primary'
              : 'text-gray-600 hover:bg-primary/5 hover:text-primary'"
            @click="applyPreset(preset)"
          >
            <span>{{ preset.label }}</span>
            <span class="h-1.5 w-1.5 rounded-full bg-primary" :class="{ 'opacity-0': activePresetKey !== preset.key }"></span>
          </button>
        </div>

        <!-- 日历 -->
        <div class="min-w-0 p-3">
          <div class="mx-auto w-full max-w-[356px]">
            <div class="flex items-center justify-between px-1 pb-2">
              <button
                type="button"
                class="grid h-7 w-7 place-items-center rounded-lg text-gray-500 transition-colors hover:bg-gray-50 hover:text-gray-800 disabled:cursor-not-allowed disabled:opacity-25"
                aria-label="上个月"
                :disabled="!canPrev"
                @click="shiftMonth(-1)"
              >
                ‹
              </button>
              <span class="text-[13px] font-bold text-gray-900">{{ viewMonthLabel }}</span>
              <button
                type="button"
                class="grid h-7 w-7 place-items-center rounded-lg text-gray-500 transition-colors hover:bg-gray-50 hover:text-gray-800 disabled:cursor-not-allowed disabled:opacity-25"
                aria-label="下个月"
                :disabled="!canNext"
                @click="shiftMonth(1)"
              >
                ›
              </button>
            </div>

            <div class="grid grid-cols-7">
              <span
                v-for="(label, index) in weekLabels"
                :key="label"
                class="py-1 text-center text-[10px] font-semibold"
                :class="index >= 5 ? 'text-gray-300' : 'text-gray-400'"
              >{{ label }}</span>
            </div>

            <div class="grid grid-cols-7" @mouseleave="clearHover">
              <button
                v-for="cell in calendarCells"
                :key="cell.key"
                type="button"
                class="relative flex h-9 items-center justify-center transition-colors"
                :class="cellClass(cell)"
                :disabled="isDisabled(cell.key)"
                :aria-label="cell.key"
                :data-day="cell.key"
                @click="pickDay(cell.key)"
                @mouseenter="hoverDay(cell.key)"
              >
                <span class="grid h-7 w-7 place-items-center rounded-lg text-xs tabular-nums transition-colors" :class="dayClass(cell)">
                  {{ cell.day }}
                </span>
              </button>
            </div>

          </div>

          <div class="mt-2 flex items-center justify-between gap-3 border-t border-gray-50 pt-3">
            <div class="min-w-0 text-[11px] leading-relaxed text-gray-500">
              <span>{{ selectionText }}</span>
              <span v-if="error" class="block text-[11px] text-rose-600">{{ error }}</span>
            </div>
            <button
              type="button"
              class="flex-shrink-0 rounded-lg border border-gray-200 px-3.5 py-1.5 text-xs font-bold text-gray-700 transition-colors hover:bg-gray-50"
              @click="closePanel"
            >
              取消
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import {
  addDays,
  buildRangePresets,
  endOfMonth,
  formatDayKey,
  formatMonthDay,
  inclusiveDaySpan,
  parseDayKey,
  startOfDay,
  startOfMonth,
} from "@/utils/dateRange";

interface DateRangeValue {
  start: string;
  end: string;
}

interface DateRangePreset {
  key: string;
  label: string;
  range: DateRangeValue;
}

interface CalendarCell {
  key: string;
  day: number;
  date: Date;
  out: boolean;
}

/** 默认快捷项：Token 统计这类「看最近多久」的场景；要自然周就显式传 presetKeys */
const DEFAULT_PRESET_KEYS = [
  "today",
  "yesterday",
  "last7",
  "last14",
  "last30",
  "last90",
  "thisMonth",
  "lastMonth",
];

const props = withDefaults(
  defineProps<{
    modelValue: DateRangeValue;
    /** 单次可查的最大天数，后端硬上限是 90 */
    maxDays?: number;
    /** 日历最早可选日期（YYYY-MM-DD），默认今天往前一年 */
    minDate?: string;
    /** 展示哪些快捷项（见 buildRangePresets 的 key），默认今日/近 N 天/本月/上月 */
    presetKeys?: string[];
    disabled?: boolean;
  }>(),
  {
    maxDays: 90,
    minDate: "",
    disabled: false,
  }
);

const emit = defineEmits<{
  (event: "update:modelValue", value: DateRangeValue): void;
  (event: "change", value: DateRangeValue): void;
}>();

const WEEK_LABELS = ["一", "二", "三", "四", "五", "六", "日"];

const rootRef = ref<HTMLElement | null>(null);
const open = ref(false);
const today = ref(startOfDay(new Date()));
const viewMonth = ref(startOfMonth(today.value));

const draftStart = ref("");
const draftEnd = ref("");
const hoverKey = ref("");
const error = ref("");

const todayKey = computed(() => formatDayKey(today.value));
const minKey = computed(() => props.minDate || formatDayKey(addDays(today.value, -365)));
const weekLabels = WEEK_LABELS;
const viewMonthLabel = computed(() => `${viewMonth.value.getFullYear()} 年 ${viewMonth.value.getMonth() + 1} 月`);

const presets = computed<DateRangePreset[]>(() => {
  const allowed = new Set(props.presetKeys ?? DEFAULT_PRESET_KEYS);
  return buildRangePresets(today.value)
    .filter((preset) => allowed.has(preset.key))
    .map((preset) => ({
      key: preset.key,
      label: preset.label,
      range: { start: preset.start, end: preset.end },
    }))
    .filter((preset) => inclusiveDaySpan(preset.range.start, preset.range.end) <= props.maxDays);
});

const activePresetKey = computed(() => {
  const matched = presets.value.find(
    (preset) =>
      preset.range.start === props.modelValue?.start && preset.range.end === props.modelValue?.end
  );
  return matched?.key || "";
});

const triggerLabel = computed(() => {
  const matched = presets.value.find((preset) => preset.key === activePresetKey.value);
  return matched ? matched.label : "自定义区间";
});

const triggerSubLabel = computed(() => {
  const start = props.modelValue?.start || "";
  const end = props.modelValue?.end || "";
  if (!start || !end) return "请选择";
  if (start === end) return formatMonthDay(start);
  return `${formatMonthDay(start)} – ${formatMonthDay(end)}`;
});

/** 面板内的临时区间：hover 时用它预览，点下第二个日期才真正生效 */
const preview = computed<DateRangeValue | null>(() => {
  if (!draftStart.value) return null;
  if (draftEnd.value) return { start: draftStart.value, end: draftEnd.value };
  const hover = hoverKey.value;
  if (!hover) return { start: draftStart.value, end: draftStart.value };
  return hover < draftStart.value
    ? { start: hover, end: draftStart.value }
    : { start: draftStart.value, end: hover };
});

const calendarCells = computed<CalendarCell[]>(() => {
  const first = startOfMonth(viewMonth.value);
  const lead = (first.getDay() + 6) % 7; // 周一开头
  const gridStart = addDays(first, -lead);
  const cells: CalendarCell[] = [];
  for (let index = 0; index < 42; index += 1) {
    const date = addDays(gridStart, index);
    cells.push({
      key: formatDayKey(date),
      day: date.getDate(),
      date,
      out: date.getMonth() !== viewMonth.value.getMonth(),
    });
  }
  // 末尾整行都不是本月时收掉，避免多出一行空白日期
  if (cells.slice(35).every((cell) => cell.out)) cells.length = 35;
  return cells;
});

const canPrev = computed(() => formatDayKey(endOfMonth(addDays(startOfMonth(viewMonth.value), -1))) >= minKey.value);
const canNext = computed(() => startOfMonth(addDays(endOfMonth(viewMonth.value), 1)) <= today.value);

const selectionText = computed(() => {
  if (!draftStart.value) return "请选择开始日期";
  if (!draftEnd.value) {
    if (hoverKey.value && hoverKey.value !== draftStart.value && preview.value) {
      const span = inclusiveDaySpan(preview.value.start, preview.value.end);
      return `预览 ${preview.value.start} ~ ${preview.value.end} · 共 ${span} 天，点击确认`;
    }
    return `已选起点 ${draftStart.value}，再点一天作为结束`;
  }
  if (draftStart.value === draftEnd.value) return `${draftStart.value}（单日）`;
  const span = inclusiveDaySpan(draftStart.value, draftEnd.value);
  return `${draftStart.value} 至 ${draftEnd.value} · 共 ${span} 天`;
});

function isDisabled(key: string): boolean {
  return key > todayKey.value || key < minKey.value;
}

function isInRange(key: string): boolean {
  const range = preview.value;
  if (!range) return false;
  return key >= range.start && key <= range.end;
}

function isEdge(key: string): boolean {
  const range = preview.value;
  if (!range) return false;
  return key === range.start || key === range.end;
}

function cellClass(cell: CalendarCell): string[] {
  const classes: string[] = [];
  if (isDisabled(cell.key)) classes.push("cursor-not-allowed", "text-gray-300");
  else if (cell.out) classes.push("text-gray-300");
  else classes.push("text-gray-700");
  if (isInRange(cell.key)) classes.push("bg-primary/10");
  return classes;
}

function dayClass(cell: CalendarCell): string[] {
  const classes: string[] = [];
  if (isEdge(cell.key)) classes.push("bg-primary", "font-bold", "text-white");
  else if (!isDisabled(cell.key)) classes.push("hover:bg-primary/10");
  if (cell.key === todayKey.value && !isEdge(cell.key)) classes.push("ring-1", "ring-inset", "ring-primary/40");
  return classes;
}

function hoverDay(key: string) {
  if (!draftStart.value || draftEnd.value) return;
  if (isDisabled(key)) return;
  if (hoverKey.value === key) return;
  hoverKey.value = key;
}

function clearHover() {
  if (!draftEnd.value) hoverKey.value = "";
}

function pickDay(key: string) {
  if (isDisabled(key)) return;
  error.value = "";
  if (!draftStart.value || draftEnd.value) {
    draftStart.value = key;
    draftEnd.value = "";
    hoverKey.value = "";
    return;
  }
  if (key < draftStart.value) {
    draftEnd.value = draftStart.value;
    draftStart.value = key;
  } else {
    draftEnd.value = key;
  }
  hoverKey.value = "";

  const span = inclusiveDaySpan(draftStart.value, draftEnd.value);
  if (span > props.maxDays) {
    // 不静默截断：保留用户的选择，让 ta 自己改短
    error.value = `跨度 ${span} 天超过上限 ${props.maxDays} 天，请缩短范围`;
    return;
  }
  commit();
}

function commit() {
  if (!draftStart.value || !draftEnd.value) return;
  const value: DateRangeValue = { start: draftStart.value, end: draftEnd.value };
  emit("update:modelValue", value);
  emit("change", value);
  closePanel();
}

function applyPreset(preset: DateRangePreset) {
  draftStart.value = preset.range.start;
  draftEnd.value = preset.range.end;
  hoverKey.value = "";
  error.value = "";
  commit();
}

function shiftMonth(delta: number) {
  const next = new Date(viewMonth.value.getFullYear(), viewMonth.value.getMonth() + delta, 1);
  if (next > startOfMonth(today.value)) return;
  if (formatDayKey(endOfMonth(next)) < minKey.value) return;
  viewMonth.value = next;
}

function openPanel() {
  if (props.disabled) return;
  today.value = startOfDay(new Date());
  draftStart.value = props.modelValue?.start || "";
  draftEnd.value = props.modelValue?.end || "";
  hoverKey.value = "";
  error.value = "";
  const anchor = parseDayKey(draftEnd.value || draftStart.value || todayKey.value);
  viewMonth.value = startOfMonth(Number.isNaN(anchor.getTime()) ? today.value : anchor);
  open.value = true;
}

function closePanel() {
  open.value = false;
  error.value = "";
}

function togglePanel() {
  if (open.value) closePanel();
  else openPanel();
}

function onDocumentMouseDown(event: MouseEvent) {
  if (!open.value) return;
  const target = event.target as Node | null;
  if (target && rootRef.value?.contains(target)) return;
  closePanel();
}

function onDocumentKeydown(event: KeyboardEvent) {
  if (!open.value) return;
  if (event.key === "Escape") closePanel();
}

// 外部改了区间（比如页面重置筛选）时同步触发器；面板打开期间以草稿为准，避免选中被打断
watch(
  () => props.modelValue,
  (value) => {
    if (open.value) return;
    draftStart.value = value?.start || "";
    draftEnd.value = value?.end || "";
  },
  { deep: true }
);

onMounted(() => {
  document.addEventListener("mousedown", onDocumentMouseDown);
  document.addEventListener("keydown", onDocumentKeydown);
});

onBeforeUnmount(() => {
  document.removeEventListener("mousedown", onDocumentMouseDown);
  document.removeEventListener("keydown", onDocumentKeydown);
});

defineExpose({ openPanel, closePanel });
</script>
