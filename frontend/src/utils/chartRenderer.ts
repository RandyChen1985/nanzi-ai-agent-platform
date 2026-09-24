import JSON5 from "json5";

export type ChartParseResult =
  | { ok: true; option: Record<string, any> }
  | { ok: false; error: ChartParseError };

export type ChartParseErrorCode =
  | "invalid_json"
  | "invalid_option"
  | "invalid_series"
  | "unsupported_series_type";

export interface ChartParseError extends Error {
  code: ChartParseErrorCode;
}

export interface ChartTableData {
  columns: string[];
  rows: Array<Array<string | number | null>>;
}

export type ChartViewMode = "line" | "bar" | "pie" | "candlestick" | "table";

const colors = [
  "#6366f1",
  "#10b981",
  "#f59e0b",
  "#ef4444",
  "#8b5cf6",
  "#ec4899",
  "#06b6d4",
];

const cartesianSeriesTypes = new Set(["bar", "line", "scatter", "candlestick"]);
export const supportedChartSeriesTypes = new Set([
  "bar",
  "line",
  "pie",
  "scatter",
  "gauge",
  "radar",
  "funnel",
  "heatmap",
  "treemap",
  "candlestick",
]);

const simpleSwitchableSeriesTypes = new Set(["bar", "line", "scatter"]);
const specializedSeriesTypes = new Set(["gauge", "radar", "funnel", "heatmap", "treemap"]);
const chartViewModeOrder: ChartViewMode[] = ["candlestick", "line", "bar", "pie", "table"];
const chartViewModeLabels: Record<ChartViewMode, string> = {
  candlestick: "K线",
  line: "折线",
  bar: "柱状",
  pie: "饼图",
  table: "表格",
};

const axisLabelReadableColor = "#6b7280";
const axisNameReadableColor = "#374151";
const titleReadableColor = "#111827";

/* 深色底对应的一组可读色（与 .dark 下的 gray-300/400/100、gray-600/700 对齐） */
const darkAxisLabelColor = "#9ca3af";
const darkAxisNameColor = "#d1d5db";
const darkTitleColor = "#f3f4f6";
const darkAxisLineColor = "#4b5563";
const darkSplitLineColor = "#374151";

/** 过浅的颜色在白底图表上几乎不可见，强制替换为可读灰 */
function normalizeReadableTextColor(color: unknown, fallback = axisLabelReadableColor): string {
  if (typeof color !== "string" || !color.trim()) return fallback;
  const value = color.trim().toLowerCase();
  if (value === "transparent" || value === "inherit" || value === "currentcolor") return fallback;

  const hexMatch = value.match(/^#([0-9a-f]{3,8})$/i);
  if (hexMatch) {
    let hex = hexMatch[1] || "";
    if (hex.length === 3) hex = hex.split("").map((c) => c + c).join("");
    if (hex.length === 8) hex = hex.slice(0, 6);
    if (hex.length < 6) return fallback;
    const r = parseInt(hex.slice(0, 2), 16);
    const g = parseInt(hex.slice(2, 4), 16);
    const b = parseInt(hex.slice(4, 6), 16);
    const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
    return luminance > 0.72 ? fallback : color;
  }

  const rgbMatch = value.match(/^rgba?\(([^)]+)\)$/);
  if (rgbMatch) {
    const parts = (rgbMatch[1] || "").split(",").map((part) => Number(part.trim()));
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
    return validateChartOption(JSON.parse(jsonStr));
  } catch (jsonError) {
    try {
      return validateChartOption(JSON5.parse(jsonStr));
    } catch (json5Error) {
      return {
        ok: false,
        error: chartParseError(
          "invalid_json",
          json5Error instanceof Error
            ? json5Error.message
            : jsonError instanceof Error
              ? jsonError.message
              : "Chart parse failed",
        ),
      };
    }
  }
}

function validateChartOption(value: any): ChartParseResult {
  let chartOptions = normalizeLegacyChartOption(value);
  if (chartOptions && chartOptions.option && !chartOptions.series) {
    chartOptions = chartOptions.option;
  }
  if (!chartOptions || typeof chartOptions !== "object" || Array.isArray(chartOptions)) {
    return { ok: false, error: chartParseError("invalid_option", "Invalid ECharts option") };
  }

  if (chartOptions.series && !Array.isArray(chartOptions.series) && typeof chartOptions.series === "object") {
    chartOptions = { ...chartOptions, series: [chartOptions.series] };
  }
  if (!Array.isArray(chartOptions.series)) {
    return { ok: false, error: chartParseError("invalid_option", "ECharts option must contain a series array") };
  }

  for (const series of chartOptions.series) {
    if (!series || typeof series !== "object" || Array.isArray(series) || typeof series.type !== "string" || !Array.isArray(series.data)) {
      return { ok: false, error: chartParseError("invalid_series", "Each series must contain type and array data") };
    }
    if (!supportedChartSeriesTypes.has(series.type)) {
      return {
        ok: false,
        error: chartParseError("unsupported_series_type", `Unsupported ECharts series type: ${series.type}`),
      };
    }
    if (cartesianSeriesTypes.has(series.type) && (!chartOptions.xAxis || !chartOptions.yAxis)) {
      return { ok: false, error: chartParseError("invalid_option", "Cartesian charts require xAxis and yAxis") };
    }
  }

  return { ok: true, option: chartOptions };
}

/**
 * 兼容模型偶发输出的 Chart.js 风格柱/线/散点图配置。
 * 这不是平台推荐格式，只转换结构明确且无执行内容的旧格式，最终仍走标准 ECharts 校验。
 */
function normalizeLegacyChartOption(value: any): any {
  if (!value || typeof value !== "object" || Array.isArray(value)) return value;
  if (!cartesianSeriesTypes.has(value.type)) return value;

  const legacyData = value.data;
  if (!legacyData || typeof legacyData !== "object" || Array.isArray(legacyData)) return value;
  if (!Array.isArray(legacyData.labels) || !Array.isArray(legacyData.datasets)) return value;
  if (!legacyData.datasets.every((dataset: any) => dataset && typeof dataset === "object" && !Array.isArray(dataset) && Array.isArray(dataset.data))) {
    return value;
  }

  const series = legacyData.datasets.map((dataset: any, index: number) => {
    const backgroundColor = dataset.backgroundColor;
    const borderColor = dataset.borderColor;
    const borderWidth = dataset.borderWidth;
    const pointData = dataset.data.map((point: any, pointIndex: number) => {
      const normalizedPoint = point && typeof point === "object" && !Array.isArray(point)
        ? { ...point }
        : { value: point };
      const itemStyle = {
        ...(normalizedPoint.itemStyle || {}),
      };
      const pointColor = Array.isArray(backgroundColor) ? backgroundColor[pointIndex] : undefined;
      if (typeof pointColor === "string") itemStyle.color = pointColor;
      if (typeof borderColor === "string") itemStyle.borderColor = borderColor;
      if (typeof borderWidth === "number") itemStyle.borderWidth = borderWidth;
      if (Object.keys(itemStyle).length > 0) normalizedPoint.itemStyle = itemStyle;
      return normalizedPoint;
    });

    const normalizedSeries: Record<string, any> = {
      ...dataset,
      name: dataset.label ?? dataset.name ?? `系列${index + 1}`,
      type: value.type,
      data: pointData,
    };
    delete normalizedSeries.label;
    delete normalizedSeries.backgroundColor;
    delete normalizedSeries.borderColor;
    delete normalizedSeries.borderWidth;
    if (typeof backgroundColor === "string") {
      normalizedSeries.itemStyle = {
        ...(normalizedSeries.itemStyle || {}),
        color: backgroundColor,
      };
    }
    return normalizedSeries;
  });

  const { type: _legacyType, data: _legacyData, ...rest } = value;
  return {
    ...rest,
    xAxis: rest.xAxis || { type: "category", data: legacyData.labels },
    yAxis: rest.yAxis || { type: "value" },
    series,
  };
}

function chartParseError(code: ChartParseErrorCode, message: string): ChartParseError {
  const error = new Error(message) as ChartParseError;
  error.code = code;
  return error;
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
    // K 线+成交量等常用 grid 数组；不可对数组做 object spread，否则会变成 {0,1,left,...} 导致空白图
    if (Array.isArray(options.grid)) {
      merged.grid = options.grid.map((item: any) => ({
        ...defaults.grid,
        ...(item && typeof item === "object" && !Array.isArray(item) ? item : {}),
      }));
    } else {
      merged.grid = { ...defaults.grid, ...(options.grid || {}) };
    }
    merged.xAxis = mergeAxisDefaults(defaults.xAxis, options.xAxis);
    merged.yAxis = mergeAxisDefaults(defaults.yAxis, options.yAxis);
  }

  if (series.length > 0) {
    merged.series = series.map((item: any) => mergeSeriesDefaults(item));
  }

  return merged;
}

/** 深色底上过暗的文字/线条几乎不可见，强制替换为可读浅色。 */
function normalizeDarkReadableTextColor(color: unknown, fallback: string): string {
  if (typeof color !== "string" || !color.trim()) return fallback;
  const value = color.trim().toLowerCase();
  if (value === "transparent" || value === "inherit" || value === "currentcolor") return fallback;

  // 亮色主题填进来的三个默认灰（轴标签/轴名/标题）在深色底上分别只有 ~3:1、~1.6:1、
  // ~1.2:1 对比度，靠亮度阈值判不出来（#6b7280 的亮度高达 0.45），因此按值精确抬亮。
  if (
    value === axisLabelReadableColor
    || value === axisNameReadableColor
    || value === titleReadableColor
  ) {
    return fallback;
  }

  const hexMatch = value.match(/^#([0-9a-f]{3,8})$/i);
  if (hexMatch) {
    let hex = hexMatch[1] || "";
    if (hex.length === 3) hex = hex.split("").map((c) => c + c).join("");
    if (hex.length === 8) hex = hex.slice(0, 6);
    if (hex.length < 6) return fallback;
    const r = parseInt(hex.slice(0, 2), 16);
    const g = parseInt(hex.slice(2, 4), 16);
    const b = parseInt(hex.slice(4, 6), 16);
    const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
    return luminance < 0.35 ? fallback : color;
  }

  const rgbMatch = value.match(/^rgba?\(([^)]+)\)$/);
  if (rgbMatch) {
    const parts = (rgbMatch[1] || "").split(",").map((part) => Number(part.trim()));
    const [r = 0, g = 0, b = 0] = parts;
    const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
    return luminance < 0.35 ? fallback : color;
  }

  return color;
}

function darkenSingleAxis(axis: any): any {
  const next: Record<string, any> = { ...axis };

  if (next.axisLabel && typeof next.axisLabel === "object") {
    next.axisLabel = {
      ...next.axisLabel,
      color: normalizeDarkReadableTextColor(next.axisLabel.color, darkAxisLabelColor),
    };
  } else if (next.axisLabel !== undefined) {
    next.axisLabel = { color: darkAxisLabelColor };
  }

  if (next.nameTextStyle && typeof next.nameTextStyle === "object") {
    next.nameTextStyle = {
      ...next.nameTextStyle,
      color: normalizeDarkReadableTextColor(next.nameTextStyle.color, darkAxisNameColor),
    };
  }

  if (next.axisLine && typeof next.axisLine === "object") {
    next.axisLine = {
      ...next.axisLine,
      lineStyle: { ...(next.axisLine.lineStyle || {}), color: darkAxisLineColor },
    };
  }

  if (next.splitLine && typeof next.splitLine === "object" && next.splitLine.lineStyle) {
    next.splitLine = {
      ...next.splitLine,
      lineStyle: { ...next.splitLine.lineStyle, color: darkSplitLineColor },
    };
  }

  return next;
}

/** 保持上游结构：数组进数组出、单对象进单对象出，避免改变 ECharts 的轴语义。 */
function darkenAxis(axis: any): any {
  if (Array.isArray(axis)) return axis.map(darkenSingleAxis);
  if (axis && typeof axis === "object") return darkenSingleAxis(axis);
  return axis;
}

/**
 * 把 `mergeChartDefaults` 的产物投影成「深色底可读」的版本。
 *
 * 只覆盖在深色底上会消失或刺眼的视觉字段（标题、图例、轴标签、轴线、分隔线、
 * tooltip 底色），并且对文字色做亮度判定：AI 显式指定的红/蓝等品牌色会被保留，
 * 只有默认的深色字（`#111827` / `#334155` 一类）才被抬成浅色。
 * 该函数是纯函数，便于行为测试。
 */
export function applyChartDarkTheme(option: Record<string, any> | null | undefined): Record<string, any> {
  if (!option || typeof option !== "object") return {};

  const next: Record<string, any> = { ...option };

  next.tooltip = {
    ...(option.tooltip || {}),
    backgroundColor: "rgba(31, 41, 55, 0.95)",
    borderColor: darkAxisLineColor,
    textStyle: {
      ...((option.tooltip || {}).textStyle || {}),
      color: darkTitleColor,
    },
  };

  if (option.legend && typeof option.legend === "object") {
    next.legend = {
      ...option.legend,
      textStyle: {
        ...(option.legend.textStyle || {}),
        color: normalizeDarkReadableTextColor(option.legend.textStyle?.color, darkAxisNameColor),
      },
    };
  }

  if (option.title) {
    next.title = Array.isArray(option.title)
      ? option.title.map((item: any) => ({
          ...item,
          textStyle: {
            ...(item?.textStyle || {}),
            color: normalizeDarkReadableTextColor(item?.textStyle?.color, darkTitleColor),
          },
        }))
      : {
          ...option.title,
          textStyle: {
            ...(option.title?.textStyle || {}),
            color: normalizeDarkReadableTextColor(option.title?.textStyle?.color, darkTitleColor),
          },
        };
  }

  if (option.xAxis) next.xAxis = darkenAxis(option.xAxis);
  if (option.yAxis) next.yAxis = darkenAxis(option.yAxis);

  return next;
}

function isOhlcPoint(point: any): boolean {
  return (
    Array.isArray(point) &&
    point.length >= 4 &&
    point.slice(0, 4).every((value) => typeof value === "number" && Number.isFinite(value))
  );
}

function seriesHasOhlcData(series: any): boolean {
  return normalizeSeriesData(series).some((point) => isOhlcPoint(point));
}

function orderChartViewModes(modes: Iterable<ChartViewMode>): ChartViewMode[] {
  const selected = new Set(modes);
  return chartViewModeOrder.filter((mode) => selected.has(mode));
}

/** 按数据形态决定消息卡片可用的切换视图（K 线+成交量等复杂图会收窄选项） */
export function getAvailableChartViewModes(options: Record<string, any>): ChartViewMode[] {
  const series = Array.isArray(options?.series) ? options.series : [];
  if (series.length === 0) return ["table"];

  const types = series.map((item) => String(item?.type || ""));
  const uniqueTypes = new Set(types);
  const hasCandlestick = types.includes("candlestick") || series.some((item) => seriesHasOhlcData(item));
  const hasSpecialized = types.some((type) => specializedSeriesTypes.has(type));

  if (hasCandlestick) {
    if (series.length > 1 || uniqueTypes.size > 1) {
      return orderChartViewModes(["candlestick", "table"]);
    }
    return orderChartViewModes(["candlestick", "line", "bar", "table"]);
  }

  if (hasSpecialized) {
    return ["table"];
  }

  if (types.every((type) => simpleSwitchableSeriesTypes.has(type) || type === "pie")) {
    return orderChartViewModes(["line", "bar", "pie", "table"]);
  }

  if (uniqueTypes.size > 1) {
    return ["table"];
  }

  return orderChartViewModes(["line", "bar", "pie", "table"]);
}

export function resolveActiveChartViewMode(
  options: Record<string, any>,
  override?: string | null,
): ChartViewMode {
  const available = getAvailableChartViewModes(options);
  if (override && available.includes(override as ChartViewMode)) {
    return override as ChartViewMode;
  }

  const series = Array.isArray(options?.series) ? options.series : [];
  const firstType = String(series[0]?.type || "");
  if (firstType === "candlestick" || series.some((item) => seriesHasOhlcData(item))) {
    return available.includes("candlestick") ? "candlestick" : (available[0] || "table");
  }
  if (firstType === "pie" && available.includes("pie")) return "pie";
  if (firstType === "bar" && available.includes("bar")) return "bar";
  if ((firstType === "line" || firstType === "scatter") && available.includes("line")) return "line";
  return available.find((mode) => mode !== "table") || available[0] || "table";
}

export function getChartViewModeLabel(mode: ChartViewMode): string {
  return chartViewModeLabels[mode] || mode;
}

/** 将图表配置切换到指定视图；candlestick 保持原始 series（含成交量等伴生序列） */
export function applyChartViewMode(
  options: Record<string, any>,
  mode: ChartViewMode,
): Record<string, any> {
  const option = JSON.parse(JSON.stringify(options || {}));
  if (mode === "table" || mode === "candlestick") {
    return option;
  }

  const series = Array.isArray(option.series) ? option.series : [];
  if (mode === "pie") {
    delete option.xAxis;
    delete option.yAxis;
    option.series = series.map((item: any, index: number) => ({
      ...item,
      type: "pie",
      data: normalizeSeriesData(item).map((point: any, pointIndex: number) => {
        if (isOhlcPoint(point)) {
          return { name: String(pointIndex + 1), value: point[1] };
        }
        if (point && typeof point === "object" && !Array.isArray(point)) {
          return {
            name: point.name != null ? String(point.name) : String(pointIndex + 1),
            value: datumValue(point),
          };
        }
        return {
          name: inferDimensionValue(series, pointIndex) || String(pointIndex + 1),
          value: datumValue(point),
        };
      }),
      name: item?.name || `系列 ${index + 1}`,
    }));
    return option;
  }

  option.series = series.map((item: any) => {
    const next = { ...item, type: mode };
    if (String(item?.type || "") === "candlestick" || seriesHasOhlcData(item)) {
      next.data = normalizeSeriesData(item).map((point: any) => (isOhlcPoint(point) ? point[1] : datumValue(point)));
    }
    return next;
  });
  return option;
}

export function buildChartTableRows(options: Record<string, any>): ChartTableData {
  const series = Array.isArray(options?.series) ? options.series : [];
  if (series.length === 0) return { columns: [], rows: [] };

  const pieSeries = series.filter((item: any) => item?.type === "pie");
  if (pieSeries.length === series.length) {
    if (pieSeries.length === 1) {
      return {
        columns: ["名称", "数值"],
        rows: normalizeSeriesData(pieSeries[0]).map((item: any, index: number) => [
          datumName(item, index),
          datumValue(item),
        ]),
      };
    }
    const names = new Set<string>();
    pieSeries.forEach((item: any) => {
      normalizeSeriesData(item).forEach((datum: any, index: number) => names.add(String(datumName(datum, index))));
    });
    const rows = Array.from(names).map((name) => [
      name,
      ...pieSeries.map((item: any) => {
        const match = normalizeSeriesData(item).find((datum: any, index: number) => String(datumName(datum, index)) === name);
        return match ? datumValue(match) : null;
      }),
    ]);
    return { columns: ["名称", ...pieSeries.map((item: any, index: number) => seriesName(item, index))], rows };
  }

  const xAxis = Array.isArray(options?.xAxis) ? options.xAxis[0] : options?.xAxis;
  const xAxisData = Array.isArray(xAxis?.data) ? xAxis.data : [];
  const rowCount = Math.max(
    xAxisData.length,
    ...series.map((item: any) => normalizeSeriesData(item).length),
  );
  const rows = Array.from({ length: rowCount }, (_, rowIndex) => [
    String(xAxisData[rowIndex] ?? inferDimensionValue(series, rowIndex)),
    ...series.map((item: any) => datumValue(normalizeSeriesData(item)[rowIndex])),
  ]);

  return {
    columns: ["维度", ...series.map((item: any, index: number) => seriesName(item, index))],
    rows,
  };
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

function normalizeSeriesData(series: any): any[] {
  return Array.isArray(series?.data) ? series.data : [];
}

function seriesName(series: any, index: number): string {
  return String(series?.name || `系列 ${index + 1}`);
}

function datumName(datum: any, index: number): string {
  if (datum && typeof datum === "object" && !Array.isArray(datum) && datum.name != null) {
    return String(datum.name);
  }
  if (Array.isArray(datum) && datum.length > 1) {
    return String(datum[0]);
  }
  return String(index + 1);
}

function datumValue(datum: any): string | number | null {
  if (datum == null) return null;
  if (typeof datum === "number" || typeof datum === "string") return datum;
  if (Array.isArray(datum)) {
    const value = datum.length > 1 ? datum[datum.length - 1] : datum[0];
    return typeof value === "number" || typeof value === "string" ? value : null;
  }
  if (typeof datum === "object") {
    const value = datum.value;
    if (Array.isArray(value)) {
      const nestedValue = value.length > 1 ? value[value.length - 1] : value[0];
      return typeof nestedValue === "number" || typeof nestedValue === "string" ? nestedValue : null;
    }
    return typeof value === "number" || typeof value === "string" ? value : null;
  }
  return null;
}

function inferDimensionValue(series: any[], rowIndex: number): string {
  for (const item of series) {
    const datum = normalizeSeriesData(item)[rowIndex];
    if (datum && typeof datum === "object") {
      if (!Array.isArray(datum) && datum.name != null) return String(datum.name);
      if (Array.isArray(datum) && datum.length > 1) return String(datum[0]);
    }
  }
  return String(rowIndex + 1);
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
