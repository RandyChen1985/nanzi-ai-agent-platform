import assert from "node:assert/strict";
import {
  applyChartViewMode,
  buildChartTableRows,
  createSseLineParser,
  getAvailableChartViewModes,
  mergeChartDefaults,
  parseChartOptions,
  resolveActiveChartViewMode,
} from "../src/utils/chartRenderer.ts";

const parsed = parseChartOptions(`
{
  xAxis: { type: "category", data: ["A"] },
  yAxis: { type: "value" },
  series: [{ type: "bar", data: [1] }]
}
`);
assert.equal(parsed.ok, true);
if (parsed.ok) {
  assert.equal(parsed.option.series[0].type, "bar");
}

const legacySeriesObject = parseChartOptions(`
{
  series: { type: "pie", data: [{ name: "A", value: 1 }] }
}
`);
assert.equal(legacySeriesObject.ok, true);
if (legacySeriesObject.ok) {
  assert.equal(Array.isArray(legacySeriesObject.option.series), true);
  assert.equal(legacySeriesObject.option.series[0].type, "pie");
}

const legacyChartJsOption = parseChartOptions(`
{
  type: "bar",
  data: {
    labels: ["2026-05", "2026-06"],
    datasets: [{
      label: "变化次数",
      data: [46, 97],
      backgroundColor: ["#111111", "#222222"]
    }]
  }
}
`);
assert.equal(legacyChartJsOption.ok, true);
if (legacyChartJsOption.ok) {
  assert.deepEqual(legacyChartJsOption.option.xAxis.data, ["2026-05", "2026-06"]);
  assert.equal(legacyChartJsOption.option.series[0].name, "变化次数");
  assert.equal(legacyChartJsOption.option.series[0].type, "bar");
  assert.deepEqual(legacyChartJsOption.option.series[0].data[0], {
    value: 46,
    itemStyle: { color: "#111111" },
  });
}

const unsupportedType = parseChartOptions(`
{
  series: [{ type: "sankey", data: [] }]
}
`);
assert.equal(unsupportedType.ok, false);
if (!unsupportedType.ok) {
  assert.equal(unsupportedType.error.code, "unsupported_series_type");
}

const candlestickMissingAxis = parseChartOptions(`
{
  series: [{ type: "candlestick", data: [[20, 34, 10, 38]] }]
}
`);
assert.equal(candlestickMissingAxis.ok, false);
if (!candlestickMissingAxis.ok) {
  assert.equal(candlestickMissingAxis.error.code, "invalid_option");
}

const candlestick = parseChartOptions(`
{
  xAxis: { type: "category", data: ["2026-08-01"] },
  yAxis: { type: "value", scale: true },
  series: [{ type: "candlestick", data: [[20, 34, 10, 38]] }]
}
`);
assert.equal(candlestick.ok, true);
if (candlestick.ok) {
  assert.equal(candlestick.option.series[0].type, "candlestick");
}

const candlestickDefaults = mergeChartDefaults({
  xAxis: { type: "category", data: ["2026-08-01"] },
  yAxis: { type: "value", scale: true },
  series: [{ type: "candlestick", data: [[20, 34, 10, 38]] }],
});
assert.equal("grid" in candlestickDefaults, true);
assert.equal(candlestickDefaults.tooltip.trigger, "axis");

const dualGridStock = mergeChartDefaults({
  grid: [
    { left: "10%", right: "8%", height: "50%" },
    { left: "10%", right: "8%", top: "68%", height: "16%" },
  ],
  xAxis: [
    { type: "category", data: ["08-01", "08-02"], gridIndex: 0 },
    { type: "category", data: ["08-01", "08-02"], gridIndex: 1 },
  ],
  yAxis: [
    { type: "value", scale: true, gridIndex: 0 },
    { type: "value", gridIndex: 1 },
  ],
  series: [
    { type: "candlestick", data: [[20, 34, 10, 38], [40, 35, 30, 50]], xAxisIndex: 0, yAxisIndex: 0 },
    { type: "bar", data: [120, 90], xAxisIndex: 1, yAxisIndex: 1 },
  ],
});
assert.equal(Array.isArray(dualGridStock.grid), true);
assert.equal(dualGridStock.grid.length, 2);
assert.equal(dualGridStock.grid[0].height, "50%");
assert.equal(dualGridStock.grid[1].top, "68%");
assert.equal(dualGridStock.grid[0].containLabel, true);
assert.equal(Array.isArray(dualGridStock.xAxis), true);
assert.equal(Array.isArray(dualGridStock.yAxis), true);

assert.deepEqual(
  getAvailableChartViewModes({
    series: [{ type: "bar", data: [1, 2] }],
  }),
  ["line", "bar", "pie", "table"],
);

assert.deepEqual(
  getAvailableChartViewModes({
    series: [
      { type: "candlestick", data: [[20, 34, 10, 38]] },
      { type: "bar", data: [100] },
    ],
  }),
  ["candlestick", "table"],
);

assert.deepEqual(
  getAvailableChartViewModes({
    series: [{ type: "candlestick", data: [[20, 34, 10, 38], [40, 35, 30, 50]] }],
  }),
  ["candlestick", "line", "bar", "table"],
);

assert.deepEqual(
  getAvailableChartViewModes({
    series: [{ type: "radar", data: [{ value: [1, 2] }] }],
  }),
  ["table"],
);

assert.equal(
  resolveActiveChartViewMode(
    { series: [{ type: "candlestick", data: [[1, 2, 0, 3]] }, { type: "bar", data: [1] }] },
    "line",
  ),
  "candlestick",
);

const klineToClose = applyChartViewMode(
  {
    xAxis: { data: ["A"] },
    yAxis: {},
    series: [{ type: "candlestick", data: [[20, 34, 10, 38]] }],
  },
  "line",
);
assert.equal(klineToClose.series[0].type, "line");
assert.deepEqual(klineToClose.series[0].data, [34]);

const restoreK = applyChartViewMode(
  {
    series: [
      { type: "candlestick", data: [[20, 34, 10, 38]] },
      { type: "bar", data: [9] },
    ],
  },
  "candlestick",
);
assert.equal(restoreK.series[0].type, "candlestick");
assert.equal(restoreK.series[1].type, "bar");

const missingSeries = parseChartOptions(`
{
  title: { text: "不是图表" }
}
`);
assert.equal(missingSeries.ok, false);
if (!missingSeries.ok) {
  assert.equal(missingSeries.error.code, "invalid_option");
}

const echartsFencePattern = /(?:<thought>([\s\S]*?)<\/thought>)|(?:<chart>([\s\S]*?)<\/chart>)|(?:```\s*(?:chart|echarts)\s*([\s\S]*?)```)|(?:```\s*mermaid\s*([\s\S]*?)```)|(?::::analysis\s*([^\n]*)\n([\s\S]*?)\n:::)/gi;
const echartsFence = '```echarts\n{"series":[{"type":"bar","data":[1]}]}\n```';
const echartsMatch = echartsFencePattern.exec(echartsFence);
assert.equal(echartsMatch?.[3]?.includes('"series"'), true);
assert.equal(echartsFencePattern.exec('```json\n{"rows":[1]}\n```'), null);

const executable = parseChartOptions(`
{
  series: [{
    type: "bar",
    data: [globalThis.__chartRendererExecuted = true]
  }]
}
`);
assert.equal(executable.ok, false);
assert.equal((globalThis as any).__chartRendererExecuted, undefined);

const pie = mergeChartDefaults({
  series: [{ type: "pie", data: [{ name: "A", value: 1 }] }],
});
assert.equal("xAxis" in pie, false);
assert.equal("yAxis" in pie, false);
assert.equal("grid" in pie, false);
assert.equal(pie.tooltip.trigger, "item");

const bar = mergeChartDefaults({
  xAxis: { data: ["A"] },
  series: [{ type: "bar", data: [1] }],
});
assert.equal("xAxis" in bar, true);
assert.equal("yAxis" in bar, true);
assert.equal("grid" in bar, true);
assert.equal(bar.xAxis.axisLabel.color, "#6b7280");

const lightAxis = mergeChartDefaults({
  xAxis: {
    data: ["2026-06-11"],
    axisLabel: { color: "#f3f4f6" },
  },
  series: [{ type: "line", data: [1] }],
});
assert.equal(lightAxis.xAxis.axisLabel.color, "#6b7280");

const nestedTextStyle = mergeChartDefaults({
  xAxis: {
    data: ["A"],
    axisLabel: { textStyle: { color: "#eeeeee" } },
  },
  series: [{ type: "line", data: [1] }],
});
assert.equal(nestedTextStyle.xAxis.axisLabel.textStyle.color, "#6b7280");

const cartesianTable = buildChartTableRows({
  xAxis: { data: ["06-01", "06-02"] },
  series: [
    { name: "新增用户", type: "line", data: [3, 5] },
    { name: "活跃用户", type: "bar", data: [{ value: 7 }, 9] },
  ],
});
assert.deepEqual(cartesianTable.columns, ["维度", "新增用户", "活跃用户"]);
assert.deepEqual(cartesianTable.rows, [
  ["06-01", 3, 7],
  ["06-02", 5, 9],
]);

const pieTable = buildChartTableRows({
  series: [
    {
      type: "pie",
      data: [
        { name: "华东", value: 12 },
        { name: "华南", value: 8 },
      ],
    },
  ],
});
assert.deepEqual(pieTable.columns, ["名称", "数值"]);
assert.deepEqual(pieTable.rows, [
  ["华东", 12],
  ["华南", 8],
]);

const parseSse = createSseLineParser();
assert.deepEqual(parseSse.feed("data: {\"content\":\"hel"), []);
assert.deepEqual(parseSse.feed("lo\"}\n\ndata: [DONE]\n"), [
  '{"content":"hello"}',
  "[DONE]",
]);
assert.deepEqual(parseSse.flush(), []);

// 防遮挡测试：默认直角坐标系应为底部图例预留安全高度
const defaultCartesian = mergeChartDefaults({
  xAxis: { type: "category", data: ["09-27", "09-28", "09-29"] },
  yAxis: { type: "value" },
  series: [{ name: "正常数", type: "bar", data: [18, 18, 18] }],
});
assert.equal(defaultCartesian.grid.bottom, 36);
assert.equal(defaultCartesian.xAxis.axisLabel.hideOverlap, true);
assert.equal(defaultCartesian.legend.bottom, 4);

// 防遮挡测试：模型输出过小的 bottom (如 3%) 需智能抬高到安全距离 36
const modelTinyGrid = mergeChartDefaults({
  grid: { left: "3%", right: "4%", bottom: "3%", containLabel: true },
  xAxis: { type: "category", data: ["09-27", "09-28"] },
  yAxis: { type: "value" },
  series: [{ name: "异常率", type: "line", data: [0, 0] }],
});
assert.equal(modelTinyGrid.grid.bottom, 36);

// 图例在顶部或显式关闭时，应保留模型指定的合理布局
const topLegendChart = mergeChartDefaults({
  legend: { top: 0 },
  grid: { bottom: "5%" },
  xAxis: { type: "category", data: ["A"] },
  yAxis: { type: "value" },
  series: [{ type: "bar", data: [1] }],
});
assert.equal(topLegendChart.grid.bottom, "5%");

// 标题默认居中测试：避免遮挡左轴单位名称（如“台”）
const centeredTitleChart = mergeChartDefaults({
  title: { text: "设备巡检正常/异常统计" },
  xAxis: { type: "category", data: ["A"] },
  yAxis: { type: "value", name: "台" },
  series: [{ type: "bar", data: [1] }],
});
assert.equal(centeredTitleChart.title.left, "center");
assert.equal(centeredTitleChart.title.top, 4);

// 显式指定 left 的标题应保留原设置
const customTitleChart = mergeChartDefaults({
  title: { text: "自定义标题", left: "right" },
  xAxis: { type: "category", data: ["A"] },
  yAxis: { type: "value" },
  series: [{ type: "bar", data: [1] }],
});
assert.equal(customTitleChart.title.left, "right");

