"""`applyChartDarkTheme` 的深色配色行为契约。

ECharts 画在 canvas 上，Tailwind 的 `dark:` 变体管不到它：表格能靠 CSS 变量换色，
图表只能换一整套 option。这里锁死「哪些字段必须被抬成浅色」「哪些必须原样保留」，
避免以后有人把深色适配做成「无差别覆盖」，把 AI 显式指定的品牌色也一起吃掉。
"""

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure

MODULE = "frontend/src/utils/chartRenderer.ts"

# chartRenderer 只依赖 json5，而依赖装在 frontend/ 下，需显式重定向
REQUIRE_SETUP = """
const requireModule = id => {
  if (id === 'json5') return require('./frontend/node_modules/json5');
  return require(id);
};
"""


def _run_typescript(expression: str):
    script = f"""
(async () => {{
const fs = require('fs');
const ts = require('./frontend/node_modules/typescript');
const source = fs.readFileSync({json.dumps(MODULE)}, 'utf8');
const code = ts.transpileModule(source, {{
  compilerOptions: {{ module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 }}
}}).outputText;
const moduleRef = {{ exports: {{}} }};
{REQUIRE_SETUP}
new Function('module', 'exports', 'require', code)(moduleRef, moduleRef.exports, requireModule);
const api = moduleRef.exports;
const firstAxis = axis => (Array.isArray(axis) ? axis[0] : axis);
const result = await (async () => {{ {expression} }})();
process.stdout.write(JSON.stringify(result));
}})().catch(error => {{ console.error(error); process.exit(1); }});
"""
    completed = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_dark_theme_lifts_default_dark_text_and_axes():
    """默认的深色字/浅色线必须被换成深色底可读的一套。"""
    result = _run_typescript(
        """
const merged = api.mergeChartDefaults({
  title: { text: '日均执行强度对比' },
  xAxis: { type: 'category', data: ['2026-08'] },
  yAxis: { type: 'value' },
  series: [{ type: 'bar', data: [36] }]
});
const dark = api.applyChartDarkTheme(merged);
const lightX = firstAxis(merged.xAxis);
const lightY = firstAxis(merged.yAxis);
const x = firstAxis(dark.xAxis);
const y = firstAxis(dark.yAxis);
return {
  lightTitle: merged.title.textStyle.color,
  darkTitle: dark.title.textStyle.color,
  lightYLabel: lightY.axisLabel.color,
  darkYLabel: y.axisLabel.color,
  lightXAxisLine: lightX.axisLine.lineStyle.color,
  darkXAxisLine: x.axisLine.lineStyle.color,
  lightYSplitLine: lightY.splitLine.lineStyle.color,
  darkYSplitLine: y.splitLine.lineStyle.color,
  lightTooltipBg: merged.tooltip.backgroundColor,
  darkTooltipBg: dark.tooltip.backgroundColor
};
"""
    )

    assert result["lightTitle"] == "#111827"
    assert result["darkTitle"] == "#f3f4f6"
    assert result["lightYLabel"] == "#6b7280"
    assert result["darkYLabel"] == "#9ca3af"
    assert result["lightXAxisLine"] == "#d1d5db"
    assert result["darkXAxisLine"] == "#4b5563"
    assert result["lightYSplitLine"] == "#f3f4f6"
    assert result["darkYSplitLine"] == "#374151"
    assert result["lightTooltipBg"] == "rgba(255, 255, 255, 0.95)"
    assert result["darkTooltipBg"] == "rgba(31, 41, 55, 0.95)"


def test_dark_theme_keeps_explicit_brand_colors():
    """AI 显式指定的高饱和品牌色不能被无差别覆盖。"""
    result = _run_typescript(
        """
const merged = api.mergeChartDefaults({
  title: { text: 'x', textStyle: { color: '#ef4444' } },
  legend: { textStyle: { color: '#10b981' } },
  series: []
});
const dark = api.applyChartDarkTheme(merged);
return { title: dark.title.textStyle.color, legend: dark.legend.textStyle.color };
"""
    )

    assert result["title"] == "#ef4444"
    assert result["legend"] == "#10b981"


def test_dark_theme_preserves_axis_shape():
    """单对象进单对象出、数组进数组出：不能把 ECharts 的轴结构改掉。"""
    result = _run_typescript(
        """
const merged = api.mergeChartDefaults({
  xAxis: [{ type: 'category', data: ['a'] }, { type: 'category', data: ['b'] }],
  yAxis: { type: 'value' },
  series: [{ type: 'bar', data: [1] }]
});
const dark = api.applyChartDarkTheme(merged);
return {
  lightXIsArray: Array.isArray(merged.xAxis),
  darkXIsArray: Array.isArray(dark.xAxis),
  darkXLength: Array.isArray(dark.xAxis) ? dark.xAxis.length : -1,
  darkYIsArray: Array.isArray(dark.yAxis)
};
"""
    )

    assert result["lightXIsArray"] is True
    assert result["darkXIsArray"] is True
    assert result["darkXLength"] == 2
    assert result["darkYIsArray"] is False


def test_dark_theme_is_pure():
    """纯函数：不能就地改写调用方持有的 option（图表会在切主题时重算）。"""
    result = _run_typescript(
        """
const merged = api.mergeChartDefaults({
  title: { text: 't' },
  xAxis: { type: 'category', data: ['a'] },
  yAxis: { type: 'value' },
  series: [{ type: 'bar', data: [1] }]
});
const before = JSON.stringify(merged);
const dark = api.applyChartDarkTheme(merged);
return {
  inputUntouched: JSON.stringify(merged) === before,
  returnsNewObject: dark !== merged,
  hidesRoot: api.applyChartDarkTheme(null),
  keepsTransparentBackground: dark.backgroundColor
};
"""
    )

    assert result["inputUntouched"] is True
    assert result["returnsNewObject"] is True
    assert result["hidesRoot"] == {}
    # 背景由容器（卡片）决定，图表本身必须保持透明
    assert result["keepsTransparentBackground"] == "transparent"
