import JSON5 from "json5";

export type ChartParseResult =
  | { ok: true; option: Record<string, any> }
  | { ok: false; error: Error };

const colors = [
  "#6366f1",
  "#10b981",
  "#f59e0b",
  "#ef4444",
  "#8b5cf6",
  "#ec4899",
  "#06b6d4",
];

const cartesianSeriesTypes = new Set(["bar", "line", "scatter"]);

const axisLabelReadableColor = "#6b7280";
const axisNameReadableColor = "#374151";
const titleReadableColor = "#111827";

/** 过浅的颜色在白底图表上几乎不可见，强制替换为可读灰 */
function normalizeReadableTextColor(color: unknown, fallback = axisLabelReadableColor): string {
  if (typeof color !== "string" || !color.trim()) return fallback;
  const value = color.trim().toLowerCase();
  if (value === "transparent" || value === "inherit" || value === "currentcolor") return fallback;

  const hexMatch = value.match(/^#([0-9a-f]{3,8})$/i);
  if (hexMatch) {
    let hex = hexMatch[1];
    if (hex.length === 3) hex = hex.split("").map((c) => c + c).join("");
    if (hex.length === 8) hex = hex.slice(0, 6);
    const r = parseInt(hex.slice(0, 2), 16);
    const g = parseInt(hex.slice(2, 4), 16);
    const b = parseInt(hex.slice(4, 6), 16);
    const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
    return luminance > 0.72 ? fallback : color;
  }

  const rgbMatch = value.match(/^rgba?\(([^)]+)\)$/);
  if (rgbMatch) {
    const parts = rgbMatch[1].split(",").map((part) => Number(part.trim()));
    const [r = 0, g = 0, b = 0, a = 1] = parts;
    if (a <= 0.35) return fallback;
    const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
    return luminance > 0.72 ? fallback : color;
  }

  return color;
}

function mergeAxisLabelDefaults(defaultLabel: Record<string, any>, label: any): Record<string, any> {
  const merged = { ...defaultLabel, ...(label || {}) };
  const nestedTextStyle = merged.textStyle;
  if (nestedTextStyle && typeof nestedTextStyle === "object") {
    merged.textStyle = {
      ...nestedTextStyle,
      color: normalizeReadableTextColor(nestedTextStyle.color, axisLabelReadableColor),
    };
  }
  merged.color = normalizeReadableTextColor(merged.color, axisLabelReadableColor);
  return merged;
}

export function parseChartOptions(raw: string): ChartParseResult {
  const jsonStr = raw.trim().replace(/^(json|chart)\s+/i, "");

  try {
    let chartOptions = JSON.parse(jsonStr);
    if (chartOptions && chartOptions.option && !chartOptions.series) {
      chartOptions = chartOptions.option;
    }
    if (isChartOption(chartOptions)) {
      return { ok: true, option: chartOptions };
    }
    return { ok: false, error: new Error("Invalid ECharts option") };
  } catch (jsonError) {
    try {
      let chartOptions = JSON5.parse(jsonStr);
      if (chartOptions && chartOptions.option && !chartOptions.series) {
        chartOptions = chartOptions.option;
      }
      if (isChartOption(chartOptions)) {
        return { ok: true, option: chartOptions };
      }
      return { ok: false, error: new Error("Invalid ECharts option") };
    } catch (json5Error) {
      return {
        ok: false,
        error: json5Error instanceof Error ? json5Error : jsonError instanceof Error ? jsonError : new Error("Chart parse failed"),
      };
    }
  }
}

export function mergeChartDefaults(options: Record<string, any>): Record<string, any> {
  if (!options) return {};

  const series = Array.isArray(options.series) ? options.series : [];
  const shouldUseCartesianDefaults =
    Boolean(options.xAxis || options.yAxis) ||
    series.some((item: any) => cartesianSeriesTypes.has(String(item?.type || "")));
  const isPieOnly = series.length > 0 && series.every((item: any) => item?.type === "pie");

  const defaults: Record<string, any> = {
    color: colors,
    backgroundColor: "transparent",
    tooltip: {
      trigger: isPieOnly ? "item" : "axis",
      backgroundColor: "rgba(255, 255, 255, 0.95)",
      borderColor: "#e5e7eb",
      borderWidth: 1,
      padding: [10, 15],
      extraCssText: "box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1); border-radius: 8px;",
    },
    legend: { bottom: 0, icon: "circle", itemWidth: 8, itemHeight: 8 },
  };

  if (shouldUseCartesianDefaults) {
    defaults.grid = { left: "3%", right: "4%", bottom: "3%", top: "15%", containLabel: true, show: false };
    defaults.xAxis = {
      axisLine: { lineStyle: { color: "#d1d5db" } },
      axisTick: { show: false },
      axisLabel: { fontSize: 11, color: axisLabelReadableColor },
      splitLine: { show: false },
    };
    defaults.yAxis = {
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { fontSize: 11, color: axisLabelReadableColor },
      nameTextStyle: { color: axisNameReadableColor, fontSize: 11 },
      splitLine: { lineStyle: { color: "#f3f4f6", type: "dashed" } },
    };
  }

  const merged: Record<string, any> = {
    ...defaults,
    ...options,
    tooltip: { ...defaults.tooltip, ...(options.tooltip || {}) },
    legend: {
      ...defaults.legend,
      ...(options.legend || {}),
      textStyle: {
        fontSize: 11,
        ...((options.legend || {}).textStyle || {}),
        color: normalizeReadableTextColor(
          (options.legend || {}).textStyle?.color,
          axisNameReadableColor,
        ),
      },
    },
  };

  if (options.title) {
    merged.title = Array.isArray(options.title)
      ? options.title.map((item: any) => ({
          ...item,
          textStyle: {
            fontSize: 13,
            fontWeight: 600,
            ...(item?.textStyle || {}),
            color: normalizeReadableTextColor(item?.textStyle?.color, titleReadableColor),
          },
        }))
      : {
          ...options.title,
          textStyle: {
            fontSize: 13,
            fontWeight: 600,
            ...(options.title?.textStyle || {}),
            color: normalizeReadableTextColor(options.title?.textStyle?.color, titleReadableColor),
          },
        };
  }

  if (shouldUseCartesianDefaults) {
    merged.grid = { ...defaults.grid, ...(options.grid || {}) };
    merged.xAxis = mergeAxisDefaults(defaults.xAxis, options.xAxis);
    merged.yAxis = mergeAxisDefaults(defaults.yAxis, options.yAxis);
  }

  if (series.length > 0) {
    merged.series = series.map((item: any) => mergeSeriesDefaults(item));
  }

  return merged;
}

export function createSseLineParser() {
  let buffer = "";

  return {
    feed(chunk: string): string[] {
      buffer += chunk;
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      return lines
        .map((line) => line.trim())
        .filter((line) => line.startsWith("data: "))
        .map((line) => line.slice(6).trim());
    },
    flush(): string[] {
      const line = buffer.trim();
      buffer = "";
      return line.startsWith("data: ") ? [line.slice(6).trim()] : [];
    },
  };
}

function isChartOption(value: any): value is Record<string, any> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  return Array.isArray(value.series) || Boolean(value.xAxis);
}

function mergeAxisDefaults(defaultAxis: Record<string, any>, axis: any): any {
  const mergeOne = (item: any) => {
    const merged = { ...defaultAxis, ...(item || {}) };
    merged.axisLabel = mergeAxisLabelDefaults(defaultAxis.axisLabel || {}, item?.axisLabel);
    if (item?.nameTextStyle || defaultAxis.nameTextStyle) {
      merged.nameTextStyle = {
        ...(defaultAxis.nameTextStyle || {}),
        ...(item?.nameTextStyle || {}),
        color: normalizeReadableTextColor(
          item?.nameTextStyle?.color ?? defaultAxis.nameTextStyle?.color,
          axisNameReadableColor,
        ),
      };
    }
    return merged;
  };

  if (Array.isArray(axis)) {
    return axis.map((item) => mergeOne(item));
  }
  return mergeOne(axis);
}

function mergeSeriesDefaults(series: any): any {
  const base = { ...series };
  if (series.type === "line") {
    base.smooth = series.smooth ?? true;
    base.lineStyle = { width: 3, ...base.lineStyle };
  } else if (series.type === "bar") {
    base.itemStyle = { borderRadius: [4, 4, 0, 0], ...base.itemStyle };
    base.barMaxWidth = series.barMaxWidth ?? 40;
  } else if (series.type === "pie") {
    base.itemStyle = { borderRadius: 5, borderColor: "#fff", borderWidth: 2, ...base.itemStyle };
    if (!base.radius) base.radius = ["40%", "70%"];
  }
  return base;
}
