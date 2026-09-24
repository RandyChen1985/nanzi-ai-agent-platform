<script setup lang="ts">
import { computed } from 'vue';
import MermaidRenderer from '@/components/MermaidRenderer.vue';
import { renderMarkdownPreview } from '@/utils/markdown';
import { applyChartDarkTheme, mergeChartDefaults, parseChartOptions } from '@/utils/chartRenderer';
import { useDarkThemeFlag } from '@/composables/useDarkThemeFlag';
import VChart from 'vue-echarts';
import { use } from 'echarts/core';
import { CanvasRenderer } from 'echarts/renderers';
import {
  BarChart,
  CandlestickChart,
  FunnelChart,
  GaugeChart,
  HeatmapChart,
  LineChart,
  PieChart,
  RadarChart,
  ScatterChart,
  TreemapChart,
} from 'echarts/charts';
import {
  DataZoomComponent,
  DatasetComponent,
  GridComponent,
  LegendComponent,
  PolarComponent,
  TitleComponent,
  ToolboxComponent,
  TooltipComponent,
  VisualMapComponent,
} from 'echarts/components';

use([
  CanvasRenderer,
  PieChart,
  BarChart,
  LineChart,
  ScatterChart,
  GaugeChart,
  RadarChart,
  FunnelChart,
  HeatmapChart,
  TreemapChart,
  CandlestickChart,
  TitleComponent,
  TooltipComponent,
  LegendComponent,
  GridComponent,
  DataZoomComponent,
  DatasetComponent,
  VisualMapComponent,
  ToolboxComponent,
  PolarComponent,
]);

type MarkdownSegment =
  | { kind: 'markdown'; key: string; html: string }
  | { kind: 'mermaid'; key: string; content: string }
  | { kind: 'chart'; key: string; option: Record<string, any> };

// Only explicit chart/echarts fences and valid ECharts options in json fences
// become charts. Ordinary JSON code blocks remain ordinary Markdown code.
// Mermaid：显式 ```mermaid，或未标注语言但正文以常见 diagram 关键字开头。
const RICH_BLOCK_PATTERN =
  /(?:<chart>([\s\S]*?)<\/chart>)|(?:```[ \t]*(chart|echarts|json)[ \t]*\r?\n([\s\S]*?)```)|(?:```[ \t]*mermaid[ \t]*\r?\n([\s\S]*?)```)|(?:```[ \t]*\r?\n((?:graph|flowchart|sequenceDiagram|classDiagram|stateDiagram(?:-v2)?|erDiagram|journey|gantt|pie|mindmap|timeline|quadrantChart)\b[\s\S]*?)```)/gi;

const MERMAID_BARE_START_RE =
  /^(?:graph|flowchart|sequenceDiagram|classDiagram|stateDiagram(?:-v2)?|erDiagram|journey|gantt|pie|mindmap|timeline|quadrantChart)\b/;

const renderParseError = (rawBlock: string, message: string) =>
  renderMarkdownPreview(
    `> ⚠️ **图表配置解析失败**\n> 原因：${message}\n\n${rawBlock}`,
  );

const parseSegments = (content: string): MarkdownSegment[] => {
  const segments: MarkdownSegment[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  let segmentIndex = 0;

  const appendMarkdown = (markdown: string) => {
    if (!markdown) return;
    segments.push({
      kind: 'markdown',
      key: `markdown-${segmentIndex++}`,
      html: renderMarkdownPreview(markdown),
    });
  };

  RICH_BLOCK_PATTERN.lastIndex = 0;
  while ((match = RICH_BLOCK_PATTERN.exec(content)) !== null) {
    appendMarkdown(content.slice(lastIndex, match.index));

    const rawChartContent = match[1] ?? match[3];
    const language = (match[2] || '').toLowerCase();
    const mermaidContent = match[4] ?? match[5];
    const rawBlock = match[0];

    if (mermaidContent !== undefined) {
      const trimmed = mermaidContent.trim();
      // bare fence 已由正则约束关键字；显式 mermaid fence 再兜底校验一次
      if (match[4] !== undefined || MERMAID_BARE_START_RE.test(trimmed)) {
        segments.push({
          kind: 'mermaid',
          key: `mermaid-${segmentIndex++}`,
          content: trimmed,
        });
      } else {
        appendMarkdown(rawBlock);
      }
    } else if (rawChartContent !== undefined) {
      const parsed = parseChartOptions(rawChartContent.trim());
      if (parsed.ok) {
        segments.push({
          kind: 'chart',
          key: `chart-${segmentIndex++}`,
          option: mergeChartDefaults(parsed.option),
        });
      } else if (language === 'json') {
        // A normal JSON fence must not disappear when it is not an ECharts option.
        appendMarkdown(rawBlock);
      } else {
        segments.push({
          kind: 'markdown',
          key: `chart-error-${segmentIndex++}`,
          html: renderParseError(rawBlock, parsed.error.message),
        });
      }
    }

    lastIndex = RICH_BLOCK_PATTERN.lastIndex;
  }

  appendMarkdown(content.slice(lastIndex));
  return segments;
};

const props = defineProps<{
  content: string;
}>();

const segments = computed(() => parseSegments(props.content || ''));

/** 主题是直接改 `<html>` 的 class，Vue 感知不到；图表必须显式跟随。 */
const isDarkTheme = useDarkThemeFlag();

/** 深色下换一套 ECharts 文字/轴线配色（canvas 吃不到 Tailwind 的 dark: 变体）。 */
const chartOption = (option: Record<string, any>) =>
  isDarkTheme.value ? applyChartDarkTheme(option) : option;
</script>

<template>
  <div class="canvas-markdown-renderer markdown-body">
    <template v-for="segment in segments" :key="segment.key">
      <div v-if="segment.kind === 'markdown'" v-html="segment.html" />
      <MermaidRenderer
        v-else-if="segment.kind === 'mermaid'"
        :content="segment.content"
        class="canvas-markdown-mermaid"
      />
      <div
        v-else
        class="canvas-markdown-chart my-4 w-full rounded-xl border border-gray-100 dark:border-gray-700 bg-white dark:bg-gray-800 p-2 shadow-sm"
      >
        <!-- notMerge：切换主题时配色是整组替换的，默认 merge 会让旧主题的颜色残留 -->
        <VChart
          class="h-[360px] w-full"
          :option="chartOption(segment.option)"
          :update-options="{ notMerge: true }"
          autoresize
        />
      </div>
    </template>
  </div>
</template>

<style>
.canvas-markdown-renderer .canvas-markdown-mermaid {
  margin: 1rem 0;
}

.canvas-markdown-renderer .canvas-markdown-chart {
  min-height: 360px;
}
</style>
