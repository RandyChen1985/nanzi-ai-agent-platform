<script setup lang="ts">
import { computed, ref } from 'vue';
import { normalizeGeneratedFileHref } from '@/utils/generatedFileUrl';
import { renderMarkdown } from '@/utils/markdown';
import { parseQuickButtons, postProcessQuickButtonHtml } from '@/utils/quickButtons';
import { buildChartTableRows, mergeChartDefaults, parseChartOptions } from '@/utils/chartRenderer';
import { dedupeSqlPlanPayload, parseSqlPlan, type SqlPlanData } from '@/utils/sqlPlan';
import MermaidRenderer from './MermaidRenderer.vue';
import SqlPlanCard from './SqlPlanCard.vue';

// Dynamically import ECharts
import VChart from 'vue-echarts';
import { use } from 'echarts/core';
import { CanvasRenderer } from 'echarts/renderers';
import { PieChart, BarChart, LineChart, ScatterChart, GaugeChart, RadarChart, FunnelChart, HeatmapChart, TreemapChart } from 'echarts/charts';
import {
  TitleComponent,
  TooltipComponent,
  LegendComponent,
  GridComponent,
  DataZoomComponent,
  DatasetComponent,
  VisualMapComponent,
  ToolboxComponent,
  PolarComponent
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
  TitleComponent,
  TooltipComponent,
  LegendComponent,
  GridComponent,
  DataZoomComponent,
  DatasetComponent,
  VisualMapComponent,
  ToolboxComponent,
  PolarComponent
]);

const props = withDefaults(defineProps<{
  content: string;
  theme?: 'default' | 'minimal' | 'academic' | 'apple' | 'warm' | 'compact';
}>(), {
  theme: 'default'
});

const emit = defineEmits<{
  (e: 'quick-question', question: string): void;
  (e: 'show-citation', payload: { id: string; anchor: HTMLElement }): void;
  (e: 'open-canvas', payload: { type: 'html' | 'code' | 'mermaid' | 'pdf' | 'csv' | 'image' | 'compare'; title: string; content: string }): void;
}>();

const localChartTypes = ref<Record<number, string>>({});

const getChartOption = (segment: ContentSegment, idx: number) => {
  const option = JSON.parse(JSON.stringify(segment.chartData || {}));
  const overriddenType = localChartTypes.value[idx];
  if (overriddenType) {
    if (option.series) {
      if (Array.isArray(option.series)) {
        option.series = option.series.map((s: any) => ({ ...s, type: overriddenType }));
      } else if (typeof option.series === 'object') {
        option.series = { ...option.series, type: overriddenType };
      }
    }
    if (overriddenType === 'pie') {
      delete option.xAxis;
      delete option.yAxis;
    } else {
      if (!option.xAxis && segment.chartData?.xAxis) option.xAxis = segment.chartData.xAxis;
      if (!option.yAxis && segment.chartData?.yAxis) option.yAxis = segment.chartData.yAxis;
    }
  }
  return option;
};

const getChartTable = (segment: ContentSegment) => buildChartTableRows(segment.chartData || {});

interface ContentSegment {
  type: 'text' | 'chart' | 'mermaid' | 'thought' | 'analysis' | 'clarification' | 'sql_plan' | 'canvas_html' | 'canvas_code';
  content: string;
  chartData?: any;
  title?: string;
  sqlPlan?: SqlPlanData;
  langName?: string;
}

  /** 将 [ID:n] 转为可点击徽章（Markdown 渲染前保护，避免被解析器吞掉） */
  const protectCitationsInMarkdown = (text: string) => {
    if (!text) return '';
    return text.replace(/(?:[\[【]ID:(\w+)[\]】])|(?:Fig\.\s*(\d+))/gi, (match, id, figNum) => {
      const finalId = id || figNum;
      return `<span class="citation-badge" data-cite-id="${finalId}">${match}</span>`;
    });
  };

  const isInsideCitationBadge = (html: string, offset: number) => {
    const before = html.slice(0, offset);
    const lastBadge = before.lastIndexOf('citation-badge');
    if (lastBadge === -1) return false;
    const lastOpen = before.lastIndexOf('<span', lastBadge);
    const lastClose = before.lastIndexOf('</span>');
    return lastOpen > lastClose;
  };

  const FILE_PATH_EXTENSIONS =
    'md|csv|txt|py|js|ts|sh|sql|json|pdf|html|css|yaml|yml|log|env|docx?|xlsx?|xlsm|pptx?';
  const FILE_PATH_SEGMENT = String.raw`[^/#\s<>"'，。；：！？、]+`;
  const filePathRegex = new RegExp(
    String.raw`(?:\./|/)?(?:${FILE_PATH_SEGMENT}/)+${FILE_PATH_SEGMENT}\.(?:${FILE_PATH_EXTENSIONS})`,
    'giu',
  );

  const appendOpenLinkToPath = (rawPathVal: string) => {
    const pathVal = rawPathVal.replace(/###HTML_TAG_PLACEHOLDER_\d+###/g, '').trim();
    if (!pathVal) return rawPathVal;
    const canvasUrl = `canvas://file?path=${encodeURIComponent(pathVal)}`;
    return `${pathVal}<a href="${canvasUrl}" class="inline-flex items-center text-blue-600 dark:text-blue-400 hover:underline font-bold ml-1.5 text-[10.5px]" title="点击在画布中打开文件" style="cursor: pointer;">[打开]</a>`;
  };

  const injectOpenLinksForPaths = (text: string) =>
    text.replace(filePathRegex, (pathVal) => appendOpenLinkToPath(pathVal));

  /**
   * 后处理：修复被 Markdown 引擎“误杀”转义的 HTML
   */
  const postProcessHtml = (html: string) => {
    if (!html) return '';
    let res = postProcessQuickButtonHtml(html);

    // 智能将服务器物理绝对路径重映射为可加载的网络相对路径（uploads 转静态托管，其他绝对路径转 fs 预览 API）
    res = res.replace(/(src|href)=["']([^"']*)["']/gi, (match, attr, val) => {
      const normalizedGeneratedFileHref = normalizeGeneratedFileHref(val);
      if (normalizedGeneratedFileHref !== val) {
        return `${attr}="${normalizedGeneratedFileHref}"`;
      }
      if (val.startsWith('http://') || val.startsWith('https://') || val.startsWith('data:')) {
        return match;
      }
      if (val.includes('uploads/')) {
        const parts = val.split('uploads/');
        const newVal = '/static/uploads/' + parts[parts.length - 1];
        return `${attr}="${newVal}"`;
      }
      if (!val.startsWith('http://') &&
          !val.startsWith('https://') &&
          !val.startsWith('data:') &&
          !val.startsWith('quick:') &&
          !val.startsWith('canvas:') &&
          !val.startsWith('/static/') &&
          !val.startsWith('/api/') &&
          !val.startsWith('/assets/')) {
        const convId = localStorage.getItem("yovole_embed_conv_id") || "";
        const convParam = convId ? `&conversation_id=${encodeURIComponent(convId)}` : "";
        const newVal = `/api/v1/chat/fs/preview?path=${encodeURIComponent(val)}${convParam}`;
        return `${attr}="${newVal}"`;
      }
      return match;
    });

    // 兜底：仅包裹尚未在 citation-badge 内的裸 [ID:n]，避免双层徽章
    res = res.replace(
      /(?:[\[【]ID:(\w+)[\]】])|(?:Fig\.\s*(\d+))/gi,
      (match, id, figNum, offset) => {
        if (typeof offset === 'number' && isInsideCitationBadge(res, offset)) {
          return match;
        }
        const finalId = id || figNum;
        return `<span class="citation-badge" data-cite-id="${finalId}">${match}</span>`;
      },
    );

    // 安全识别本地绝对或相对路径（排除标签内部属性，如 href, src），并在后方渲染一键在画布打开的 [打开] 链接
    const tags: string[] = [];
    let textWithPlaceholders = res.replace(/<[^>]+>/g, (match) => {
      tags.push(match);
      return `###HTML_TAG_PLACEHOLDER_${tags.length - 1}###`;
    });

    textWithPlaceholders = injectOpenLinksForPaths(textWithPlaceholders);

    res = textWithPlaceholders.replace(/###HTML_TAG_PLACEHOLDER_(\d+)###/g, (_match, idx) => {
      return tags[parseInt(idx, 10)] ?? "";
    });

    // 兜底：Markdown 反引号会把路径包进 <code>，上面占位符还原后再处理一次 code 内文本
    res = res.replace(/<code>([^<]*)<\/code>/gi, (_match, inner) => `<code>${injectOpenLinksForPaths(inner)}</code>`);

    return res;
  };

  const renderMarkdownSegment = (text: string) => {
    return postProcessHtml(renderMarkdown(protectCitationsInMarkdown(text)));
  };

const handleContentClick = (event: MouseEvent) => {
  const target = event.target as HTMLElement;
  const citeEl = target.closest('[data-cite-id]');
  if (citeEl) {
    const citeId = citeEl.getAttribute('data-cite-id');
    if (citeId) {
      emit('show-citation', { id: citeId, anchor: citeEl as HTMLElement });
      event.preventDefault();
      event.stopPropagation();
      return;
    }
  }

  const linkEl = target.closest('a');
  if (linkEl) {
    const href = linkEl.getAttribute('href');
    if (href) {
      if (href.startsWith('quick:')) {
        const rawQuestion = href.replace(/^quick:/, '');
        let question = '';
        try { question = decodeURIComponent(decodeURIComponent(rawQuestion)); }
        catch { question = decodeURIComponent(rawQuestion); }
        if (question) { emit('quick-question', question.trim()); event.preventDefault(); event.stopPropagation(); return; }
      } else {
        const lowerHref = ((href.toLowerCase().split('?')[0] ?? '').split('#')[0] ?? '');
        const isPdf = lowerHref.endsWith('.pdf');
        const isCsv = lowerHref.endsWith('.csv');
        const isImage = lowerHref.endsWith('.jpg') || lowerHref.endsWith('.jpeg') || lowerHref.endsWith('.png') || lowerHref.endsWith('.gif') || lowerHref.endsWith('.webp');
        const isCompare = href.startsWith('canvas://compare');
        const isCanvasFile = href.startsWith('canvas://file');

        if (isPdf || isCsv || isImage || isCompare || isCanvasFile) {
          let type: 'html' | 'code' | 'mermaid' | 'pdf' | 'csv' | 'image' | 'compare' = 'code';
          let filename = '预览';

          if (isCompare) {
            type = 'compare';
            filename = linkEl.textContent?.trim() || '数据对比';
          } else if (isCanvasFile) {
            try {
              const urlObj = new URL(href.replace('canvas://', 'http://localhost/'));
              const filePath = urlObj.searchParams.get('path') || '';
              const lowerPath = filePath.toLowerCase();
              filename = filePath.split('/').pop() || '文件预览';

              if (lowerPath.endsWith('.csv')) type = 'csv';
              else if (lowerPath.endsWith('.pdf')) type = 'pdf';
              else if (lowerPath.endsWith('.jpg') || lowerPath.endsWith('.jpeg') || lowerPath.endsWith('.png') || lowerPath.endsWith('.gif') || lowerPath.endsWith('.webp')) type = 'image';
              else if (lowerPath.endsWith('.html') || lowerPath.endsWith('.htm')) type = 'html';
              else type = 'code';
            } catch {
              type = 'code';
            }
          } else {
            type = isPdf ? 'pdf' : (isCsv ? 'csv' : 'image');
            filename = linkEl.textContent?.trim() || (isPdf ? 'PDF 文档' : isCsv ? 'CSV 数据表' : '图片预览');
          }

          emit('open-canvas', { type, title: filename, content: href });
          event.preventDefault();
          event.stopPropagation();
          return;
        }
      }
    }
  }

  const imgEl = target.closest('img');
  if (imgEl) {
    const src = imgEl.getAttribute('src');
    if (src) {
      emit('open-canvas', { type: 'image', title: '图片预览', content: src });
      event.preventDefault();
      event.stopPropagation();
      return;
    }
  }

  const copyBtn = target.closest('.code-copy-btn');
  if (copyBtn) {
    const wrapper = copyBtn.closest('.code-block-wrapper');
    const codeEl = wrapper?.querySelector('pre code');
    if (codeEl) {
      navigator.clipboard.writeText(codeEl.textContent || '').then(() => {
        copyBtn.classList.add('copied');
        setTimeout(() => copyBtn.classList.remove('copied'), 2000);
      });
      event.preventDefault(); event.stopPropagation();
    }
  }
};

const segments = computed<ContentSegment[]>(() => {
  if (!props.content) return [];

  let cleanContent = props.content;
  // 1. 隐藏函数调用
  cleanContent = cleanContent.replace(/<function_calls>[\s\S]*?<\/function_calls>/gi, '\n> [!NOTE]\n> 🔄 *System: Internal processing steps hidden.*\n\n');

  // 2. 预处理 Quick 按钮 (核心改进)
  cleanContent = parseQuickButtons(cleanContent);

  const regex = /(?:<sql_plan>([\s\S]*?)<\/sql_plan>)|(?:<thought>([\s\S]*?)<\/thought>)|(?:<chart>([\s\S]*?)<\/chart>)|(?:```\s*(?:chart|echarts|json)\s*([\s\S]*?)```)|(?:```\s*mermaid\s*([\s\S]*?)```)|(?::::analysis\s*([^\n]*)\n([\s\S]*?)\n:::)|(?::::clarification\s*([^\n]*)\n([\s\S]*?)\n:::)|(?:```\s*([a-zA-Z0-9_\-]+)?\s*\n([\s\S]*?)```)/gi;
  const result: ContentSegment[] = [];
  let lastIndex = 0;
  let match;
  const seenSqlPlans = new Set<string>();

  while ((match = regex.exec(cleanContent)) !== null) {
    if (match.index > lastIndex) {
      result.push({ type: 'text', content: renderMarkdownSegment(cleanContent.slice(lastIndex, match.index)) });
    }
    if (match[1]) {
      const rawSqlPlan = match[1].trim();
      const dedupeKey = dedupeSqlPlanPayload(rawSqlPlan);
      if (!seenSqlPlans.has(dedupeKey)) {
        seenSqlPlans.add(dedupeKey);
        const parsed = parseSqlPlan(rawSqlPlan);
        if (parsed.ok) {
          result.push({ type: 'sql_plan', content: rawSqlPlan, sqlPlan: parsed.data });
        } else {
          result.push({
            type: 'text',
            content: renderMarkdown(`> ⚠️ **SQL Plan 解析失败**\n\n\`\`\`json\n${rawSqlPlan}\n\`\`\``),
          });
        }
      }
    } else if (match[2]) result.push({ type: 'thought', content: renderMarkdown(match[2].trim()) });
    else if (match[5]) result.push({ type: 'mermaid', content: match[5].trim() });
    else if (match[6] || match[7]) {
      result.push({
        type: 'analysis',
        title: 'AI分析推理过程 ...',
        content: renderMarkdown(match[7]?.trim() || '')
      });
    }
    else if (match[8] || match[9]) {
      result.push({
        type: 'clarification',
        title: (match[8] || '需要再确认一下').trim() || '需要再确认一下',
        content: renderMarkdownSegment(parseQuickButtons(match[9]?.trim() || '')),
      });
    }
    else if (match[3] || match[4]) {
      const rawJson = (match[3] || match[4]) as string;
      const jsonStr = rawJson.trim().replace(/^(json|javascript|js|xml|chart)\s+/i, '');
      const parsed = parseChartOptions(jsonStr);
      if (parsed.ok) {
        result.push({ type: 'chart', content: jsonStr, chartData: mergeChartDefaults(parsed.option) });
      } else {
        console.error('Failed to parse chart options:', parsed.error);
        result.push({ type: 'text', content: renderMarkdown(`> ⚠️ **Chart Render Failed**\n\n\`\`\`json\n${jsonStr}\n\`\`\``) });
      }
    }
    else if (match[11] !== undefined) {
      const lang = (match[10] || '').trim().toLowerCase();
      const codeContent = match[11];
      const isHtmlApp = lang === 'html' && (/<html/i.test(codeContent) || /<body/i.test(codeContent) || /<!doctype html/i.test(codeContent));

      if (isHtmlApp) {
        result.push({
          type: 'canvas_html',
          content: codeContent,
          title: 'HTML 交互应用'
        });
      } else {
        const lines = codeContent.split('\n').length;
        if (lang && lines > 15) {
          result.push({
            type: 'canvas_code',
            content: codeContent,
            title: `${lang.toUpperCase()} 源代码`,
            langName: lang
          });
        } else {
          const langPrefix = match[10] || '';
          const rawMarkdownCode = `\`\`\`${langPrefix}\n${codeContent}\`\`\``;
          result.push({
            type: 'text',
            content: renderMarkdownSegment(rawMarkdownCode)
          });
        }
      }
    }
    lastIndex = regex.lastIndex;
  }

  if (lastIndex < cleanContent.length) {
    result.push({ type: 'text', content: renderMarkdownSegment(cleanContent.slice(lastIndex)) });
  }
  return result;
});

</script>

<template>
  <div :class="['message-renderer', `theme-${theme}`, 'space-y-4']" @click="handleContentClick">
    <div v-for="(segment, idx) in segments" :key="idx">
      <div v-if="segment.type === 'thought'" class="thought-block group mb-6">
        <div class="flex items-center space-x-2 mb-2 text-slate-400">
          <div class="p-1 bg-slate-100 rounded"><svg class="w-3.5 h-3.5 animate-pulse" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"/></svg></div>
          <span class="text-[11px] font-black uppercase tracking-[0.2em]">Deep Reasoning</span>
        </div>
        <div class="markdown-body thought-content pl-4 py-1 border-l-2 border-slate-200 text-slate-500/80 italic text-sm" v-html="segment.content"></div>
      </div>
      <div v-if="segment.type === 'text'" class="markdown-body" v-html="segment.content"></div>
      <div v-else-if="segment.type === 'mermaid'" class="w-full my-4 relative group/mermaid">
        <button
          @click="emit('open-canvas', { type: 'mermaid', title: '架构流程图预览', content: segment.content })"
          class="absolute top-2 right-2 p-1.5 bg-white/90 dark:bg-gray-800/90 text-gray-500 hover:text-primary rounded-lg shadow border border-gray-200 dark:border-gray-700 opacity-0 group-hover/mermaid:opacity-100 transition-all z-10 text-xs flex items-center space-x-1"
          title="在画布中放大缩小平移"
        >
          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0zM10 7v6m3-3H7"/></svg>
          <span>放大查看</span>
        </button>
        <MermaidRenderer :content="segment.content" />
      </div>
      <div v-else-if="segment.type === 'chart'" class="w-full h-64 bg-white rounded-lg border border-gray-100 p-2 shadow-sm relative group/chart">
        <!-- Chart type switcher buttons overlay -->
        <div class="absolute top-2 right-2 flex items-center space-x-1 bg-white/90 dark:bg-gray-800/90 shadow-sm border border-gray-100 dark:border-gray-700 rounded-lg p-1 z-10 opacity-0 group-hover/chart:opacity-100 transition-opacity">
          <button
            @click="localChartTypes[idx] = 'line'"
            class="px-2 py-0.5 hover:bg-gray-100 dark:hover:bg-gray-700 rounded text-[10px] font-bold transition-colors"
            :class="localChartTypes[idx] === 'line' || (!localChartTypes[idx] && segment.chartData?.series?.[0]?.type === 'line') ? 'text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-900/20' : 'text-gray-400'"
            title="切换为折线图"
          >
            折线
          </button>
          <button
            @click="localChartTypes[idx] = 'bar'"
            class="px-2 py-0.5 hover:bg-gray-100 dark:hover:bg-gray-700 rounded text-[10px] font-bold transition-colors"
            :class="localChartTypes[idx] === 'bar' || (!localChartTypes[idx] && segment.chartData?.series?.[0]?.type === 'bar') ? 'text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-900/20' : 'text-gray-400'"
            title="切换为柱状图"
          >
            柱状
          </button>
          <button
            @click="localChartTypes[idx] = 'pie'"
            class="px-2 py-0.5 hover:bg-gray-100 dark:hover:bg-gray-700 rounded text-[10px] font-bold transition-colors"
            :class="localChartTypes[idx] === 'pie' || (!localChartTypes[idx] && segment.chartData?.series?.[0]?.type === 'pie') ? 'text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-900/20' : 'text-gray-400'"
            title="切换为饼图"
          >
            饼图
          </button>
          <button
            @click="localChartTypes[idx] = 'table'"
            class="px-2 py-0.5 hover:bg-gray-100 dark:hover:bg-gray-700 rounded text-[10px] font-bold transition-colors"
            :class="localChartTypes[idx] === 'table' ? 'text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-900/20' : 'text-gray-400'"
            title="切换为表格视图"
          >
            表格
          </button>
        </div>
        <div v-if="localChartTypes[idx] === 'table'" class="h-full overflow-auto pt-10 px-1 pb-1 custom-scrollbar">
          <table
            v-if="getChartTable(segment).columns.length"
            class="min-w-full border-separate border-spacing-0 text-xs text-gray-700 dark:text-gray-200"
          >
            <thead>
              <tr>
                <th
                  v-for="(column, columnIndex) in getChartTable(segment).columns"
                  :key="`${column}-${columnIndex}`"
                  class="sticky top-0 bg-gray-50 dark:bg-gray-800 px-3 py-2 text-left font-black text-gray-500 dark:text-gray-400 border-b border-gray-100 dark:border-gray-700 whitespace-nowrap"
                >
                  {{ column }}
                </th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="(row, rowIndex) in getChartTable(segment).rows"
                :key="rowIndex"
                class="odd:bg-white even:bg-gray-50/70 dark:odd:bg-gray-900 dark:even:bg-gray-800/60"
              >
                <td
                  v-for="(cell, cellIndex) in row"
                  :key="`${rowIndex}-${cellIndex}`"
                  class="px-3 py-2 border-b border-gray-100 dark:border-gray-800 whitespace-nowrap"
                >
                  {{ cell ?? '-' }}
                </td>
              </tr>
            </tbody>
          </table>
          <div v-else class="h-full flex items-center justify-center text-xs font-bold text-gray-400">
            暂无可展示的表格数据
          </div>
        </div>
        <v-chart v-else class="chart" :option="getChartOption(segment, idx)" autoresize />
      </div>

      <!-- Canvas HTML 激活卡片 -->
      <div v-else-if="segment.type === 'canvas_html'" class="my-3 p-4 rounded-xl border border-blue-200 dark:border-blue-800 bg-blue-50/50 dark:bg-blue-900/10 flex items-center justify-between shadow-sm">
        <div class="flex items-center space-x-3">
          <div class="p-2 bg-blue-100 dark:bg-blue-900/30 rounded-lg text-blue-600 dark:text-blue-400">
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"/></svg>
          </div>
          <div>
            <h4 class="text-xs font-bold text-gray-800 dark:text-gray-200">{{ segment.title }}</h4>
            <p class="text-[10px] text-gray-400 dark:text-gray-500">已为您生成可交互原型页面</p>
          </div>
        </div>
        <button
          @click="emit('open-canvas', { type: 'html', title: segment.title || '', content: segment.content })"
          class="px-3 py-1.5 text-xs font-bold text-white bg-blue-600 hover:bg-blue-700 active:scale-95 rounded-lg shadow-sm transition-all"
        >
          运行应用
        </button>
      </div>

      <!-- Canvas Code 激活卡片 -->
      <div v-else-if="segment.type === 'canvas_code'" class="my-3 p-4 rounded-xl border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/30 flex items-center justify-between shadow-sm">
        <div class="flex items-center space-x-3">
          <div class="p-2 bg-gray-100 dark:bg-gray-700 rounded-lg text-gray-600 dark:text-gray-400">
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4"/></svg>
          </div>
          <div>
            <h4 class="text-xs font-bold text-gray-800 dark:text-gray-200">{{ segment.title }}</h4>
            <p class="text-[10px] text-gray-400 dark:text-gray-500">包含大段 {{ segment.langName }} 源代码</p>
          </div>
        </div>
        <button
          @click="emit('open-canvas', { type: 'code', title: segment.title || '', content: segment.content })"
          class="px-3 py-1.5 text-xs font-bold text-gray-700 dark:text-gray-300 bg-white dark:bg-gray-800 hover:bg-gray-100 dark:hover:bg-gray-700 active:scale-95 border border-gray-200 dark:border-gray-700 rounded-lg shadow-sm transition-all"
        >
          查看代码
        </button>
      </div>

      <div v-else-if="segment.type === 'sql_plan' && segment.sqlPlan" class="w-full">
        <SqlPlanCard :plan="segment.sqlPlan" />
      </div>

      <div v-else-if="segment.type === 'analysis'" class="analysis-block mb-6">
        <details class="analysis-details group" open>
          <summary class="analysis-summary">
            <div class="flex items-center justify-between w-full">
              <div class="flex items-center space-x-2">
                <div class="p-1 bg-blue-500 rounded text-white shadow-sm shadow-blue-500/20">
                  <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"/>
                  </svg>
                </div>
                <span class="text-xs font-black text-gray-800 dark:text-gray-100 uppercase tracking-tight">{{ segment.title }}</span>
              </div>
              <div class="flex items-center space-x-2">
                <span class="text-[10px] font-bold text-gray-400 group-hover:text-blue-500 transition-colors">思维链路</span>
                <svg class="w-4 h-4 text-gray-400 transform transition-transform duration-300 group-open:rotate-180" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M19 9l-7 7-7-7"/>
                </svg>
              </div>
            </div>
          </summary>
          <div class="analysis-content">
            <div class="markdown-body text-sm text-gray-600 dark:text-gray-300 leading-relaxed" v-html="segment.content"></div>
          </div>
        </details>
      </div>

      <div v-else-if="segment.type === 'clarification'" class="clarification-card my-3">
        <div class="clarification-card__header">
          <div class="clarification-card__icon" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.4" d="M8.228 9c.549-1.165 1.956-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/>
            </svg>
          </div>
          <div class="clarification-card__heading">
            <span class="clarification-card__badge">需要你确认</span>
            <h4 class="clarification-card__title">{{ segment.title }}</h4>
          </div>
        </div>
        <div class="clarification-card__body markdown-body text-sm text-gray-700 dark:text-gray-200 leading-relaxed" v-html="segment.content"></div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chart { height: 100%; width: 100%; }
.markdown-body :deep(a[href^="http"]) { color: #2563eb !important; text-decoration: underline !important; cursor: pointer !important; }

/* Quick Action Buttons (Universal Styling) */
.markdown-body :deep(.quick-action-btn),
.markdown-body :deep(a[href^="quick:"]) {
  display: inline-flex !important;
  align-items: center !important;
  margin: 6px 8px 6px 0 !important;
  padding: 6px 16px !important;
  background-color: var(--primary-color, #1677ff) !important;
  border: 1px solid rgba(255, 255, 255, 0.15) !important;
  border-radius: 12px !important;
  color: white !important;
  font-size: 13px !important;
  font-weight: 800 !important;
  text-decoration: none !important;
  box-shadow: 0 4px 14px -2px var(--primary-color-alpha, rgba(22, 119, 255, 0.3)) !important;
  transition: all 0.3s cubic-bezier(0.34, 1.56, 0.64, 1) !important;
  cursor: pointer !important;
  position: relative !important;
  line-height: 1.2 !important;
}

.markdown-body :deep(.quick-action-btn::before),
.markdown-body :deep(a[href^="quick:"]::before) {
  content: "⚡️" !important;
  margin-right: 6px !important;
  font-size: 11px !important;
}

.markdown-body :deep(.quick-action-btn:hover),
.markdown-body :deep(a[href^="quick:"]:hover) {
  filter: brightness(1.05) !important;
  transform: translateY(-2px) scale(1.03) !important;
  box-shadow: 0 8px 20px -4px var(--primary-color-alpha, rgba(22, 119, 255, 0.45)) !important;
}

.markdown-body :deep(.citation-badge) { display: inline-flex; align-items: center; justify-content: center; font-size: 10px; font-weight: 700; color: #3b82f6; background-color: #eff6ff; border: 1px solid #dbeafe; border-radius: 4px; padding: 0 4px; margin: 0 2px; cursor: pointer; vertical-align: super; transition: all 0.2s ease; }
.markdown-body :deep(.citation-badge:hover) { background-color: #3b82f6; color: white; transform: scale(1.1); }
.markdown-body { overflow-wrap: break-word; }
.markdown-body :deep(ol) { list-style-type: decimal; padding-left: 1.5em; margin-bottom: 1em; }
.markdown-body :deep(ul) { list-style-type: disc; padding-left: 1.5em; margin-bottom: 1em; }
.markdown-body :deep(li) { margin-bottom: 0.35em; }
.markdown-body :deep(pre), .markdown-body :deep(code) { white-space: pre-wrap; overflow-x: auto; }

/* Analysis Block Styling - Refined for clarity and "thinking" vibe */
.analysis-block {
  margin: 1.5rem 0;
}

.clarification-card {
  position: relative;
  border: 1.5px solid #f6d48a;
  border-radius: 14px;
  background: linear-gradient(180deg, #fffbeb 0%, #fff7ed 100%);
  padding: 14px 16px 12px 16px;
  box-shadow: 0 2px 8px rgba(180, 83, 9, 0.08);
  overflow: hidden;
}

.clarification-card::before {
  content: "";
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 4px;
  background: linear-gradient(180deg, #f59e0b 0%, #ea580c 100%);
}

.dark .clarification-card {
  border-color: #92400e;
  background: linear-gradient(180deg, #1c1410 0%, #1a1510 100%);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.25);
}

.dark .clarification-card::before {
  background: linear-gradient(180deg, #fbbf24 0%, #f59e0b 100%);
}

.clarification-card__header {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  margin-bottom: 10px;
  padding-left: 2px;
}

.clarification-card__icon {
  flex-shrink: 0;
  width: 28px;
  height: 28px;
  border-radius: 9px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  background: linear-gradient(135deg, #f59e0b 0%, #ea580c 100%);
  box-shadow: 0 2px 6px rgba(234, 88, 12, 0.28);
}

.clarification-card__icon svg {
  width: 15px;
  height: 15px;
}

.clarification-card__heading {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
  padding-top: 1px;
}

.clarification-card__badge {
  display: inline-flex;
  align-self: flex-start;
  align-items: center;
  padding: 3px 9px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.02em;
  color: #9a3412;
  background: #ffedd5;
  border: 1px solid #fdba74;
}

.dark .clarification-card__badge {
  color: #fed7aa;
  background: rgba(234, 88, 12, 0.22);
  border-color: rgba(251, 146, 60, 0.45);
}

.clarification-card__title {
  margin: 0;
  font-size: 15px;
  font-weight: 800;
  line-height: 1.35;
  color: #7c2d12;
}

.dark .clarification-card__title {
  color: #ffedd5;
}

.clarification-card__body {
  padding-left: 2px;
}

.clarification-card__body :deep(h3) {
  margin-top: 0.85rem !important;
  margin-bottom: 0.4rem !important;
  font-size: 12px !important;
  font-weight: 800 !important;
  color: #9a3412 !important;
}

.dark .clarification-card__body :deep(h3) {
  color: #fdba74 !important;
}

.analysis-details {
  border: 1.5px solid #e2e8f0;
  border-radius: 16px;
  background-color: #f8fafc; /* Slate 50 - Solid backgound */
  box-shadow: 0 1px 3px rgba(0,0,0,0.02);
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  overflow: hidden;
}

.dark .analysis-details {
  background-color: #0f172a; /* Slate 900 */
  border-color: #1e293b;
}

.analysis-details:hover {
  border-color: #cbd5e1;
  box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);
}

.analysis-summary {
  list-style: none;
  padding: 12px 16px;
  cursor: pointer;
  user-select: none;
  outline: none;
  background-color: rgba(241, 245, 249, 0.5);
  border-bottom: 1px solid transparent;
  transition: all 0.2s ease;
}

.dark .analysis-summary {
  background-color: rgba(30, 41, 59, 0.3);
}

.analysis-details[open] .analysis-summary {
  border-bottom-color: #f1f5f9;
}

.dark .analysis-details[open] .analysis-summary {
  border-bottom-color: #1e293b;
}

.analysis-summary::-webkit-details-marker {
  display: none;
}

.analysis-content {
  padding: 16px;
  background-color: #ffffff;
  border-top: 1px solid #f1f5f9;
  animation: slideDown 0.4s cubic-bezier(0.16, 1, 0.3, 1);
}

.dark .analysis-content {
  background-color: #1e293b;
  border-top-color: #334155;
}

@keyframes slideDown {
  from {
    opacity: 0;
    transform: translateY(-8px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

/* ==================== 极简排版样式 (Minimal Theme) ==================== */
.theme-minimal :deep(.markdown-body pre):before {
  display: none !important; /* 隐藏 Mac 控制台圆点 */
}
.theme-minimal :deep(.markdown-body pre) {
  margin-top: 0.75em !important;
  margin-bottom: 0.75em !important;
  padding: 0.75em !important;
  background-color: #f8fafc !important; /* 浅淡灰蓝背景 */
  border: none !important;              /* 去除代码边框 */
  border-radius: 6px !important;
  box-shadow: none !important;
}
.theme-minimal :deep(.markdown-body code) {
  background-color: rgba(15, 23, 42, 0.06) !important;
  color: #334155 !important;
}
.theme-minimal :deep(.markdown-body p) {
  margin-bottom: 0.75em !important;
  line-height: 1.6 !important;
}
.theme-minimal :deep(.markdown-body h1, .markdown-body h2, .markdown-body h3) {
  margin-top: 1em !important;
  margin-bottom: 0.4em !important;
}

/* ==================== 学术排版样式 (Academic Theme) ==================== */
.theme-academic :deep(.markdown-body) {
  font-family: Georgia, Cambria, "Times New Roman", Times, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", serif !important;
  line-height: 1.8 !important;
  font-size: 14.5px !important;
  letter-spacing: 0.03em !important;
}
.theme-academic :deep(.markdown-body p) {
  text-indent: 2em !important; /* 普通段落首行缩进 2 字符 */
  margin-bottom: 1.2em !important;
  text-align: justify !important; /* 两端对齐 */
}
/* 防止首行缩进影响到代码块、引用、列表、表格和按钮等特殊元素中的段落 */
.theme-academic :deep(.markdown-body pre p),
.theme-academic :deep(.markdown-body blockquote p),
.theme-academic :deep(.markdown-body li p),
.theme-academic :deep(.markdown-body td p),
.theme-academic :deep(.markdown-body th p),
.theme-academic :deep(.markdown-body .quick-action-btn) {
  text-indent: 0 !important;
}
.theme-academic :deep(.markdown-body h1, .markdown-body h2, .markdown-body h3) {
  font-family: inherit !important;
  text-align: center !important; /* 标题居中 */
  margin-top: 1.8em !important;
  margin-bottom: 0.6em !important;
  border-bottom: none !important;
}
.theme-academic :deep(.markdown-body h1) {
  font-size: 1.5em !important;
}
.theme-academic :deep(.markdown-body h2) {
  font-size: 1.3em !important;
}
.theme-academic :deep(.markdown-body h3) {
  font-size: 1.15em !important;
}
.theme-academic :deep(.markdown-body pre) {
  background-color: #fafafa !important;
  border: 1px dashed #d1d5db !important; /* 虚线框 */
  border-radius: 4px !important;
  box-shadow: none !important;
  padding: 1em !important;
}
.theme-academic :deep(.markdown-body pre):before {
  display: none !important; /* 学术排版中不需要 Mac 圆点控制栏 */
}
.theme-academic :deep(.markdown-body blockquote) {
  border-left: 3px double #9ca3af !important; /* 双线边框 */
  background-color: #f9fafb !important;
  padding: 0.75em 1.25em !important;
  margin: 1.2em 0 !important;
  font-style: normal !important;
}

/* ==================== 苹果极简样式 (Apple Theme) ==================== */
.theme-apple :deep(.markdown-body) {
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro", "SF Pro Display", "SF Pro Text", "Helvetica Neue", Helvetica, Arial, sans-serif !important;
  color: #1d1d1f !important; /* 苹果深黑色字 */
  line-height: 1.65 !important;
  font-size: 14.5px !important;
  letter-spacing: -0.011em !important; /* 苹果系统字体的紧凑字间距 */
}
.dark .theme-apple :deep(.markdown-body) {
  color: #f5f5f7 !important; /* 苹果暗色明亮文字 */
}
.theme-apple :deep(.markdown-body a:not(.quick-action-btn):not([href^="quick:"])) {
  color: #0066cc !important; /* 苹果经典蓝色链接 */
  text-decoration: none !important;
  font-weight: 500 !important;
}
.dark .theme-apple :deep(.markdown-body a:not(.quick-action-btn):not([href^="quick:"])) {
  color: #2997ff !important;
}
.theme-apple :deep(.markdown-body a:hover) {
  text-decoration: underline !important;
}
.theme-apple :deep(.markdown-body p) {
  margin-bottom: 1em !important;
}
.theme-apple :deep(.markdown-body h1, .markdown-body h2, .markdown-body h3) {
  color: #1d1d1f !important;
  font-family: inherit !important;
  font-weight: 700 !important;
  letter-spacing: -0.022em !important; /* 标题更紧凑的字距 */
  border-bottom: none !important;
}
.dark .theme-apple :deep(.markdown-body h1, .markdown-body h2, .markdown-body h3) {
  color: #f5f5f7 !important;
}
.theme-apple :deep(.markdown-body h1) {
  font-size: 1.6em !important;
  margin-top: 1.2em !important;
  margin-bottom: 0.4em !important;
}
.theme-apple :deep(.markdown-body h2) {
  font-size: 1.35em !important;
  margin-top: 1.1em !important;
  margin-bottom: 0.4em !important;
}
.theme-apple :deep(.markdown-body h3) {
  font-size: 1.18em !important;
  margin-top: 1em !important;
  margin-bottom: 0.3em !important;
}
.theme-apple :deep(.markdown-body pre) {
  background-color: #1d1d1f !important; /* 苹果官方开发者代码块底色 */
  border: 1px solid rgba(255, 255, 255, 0.1) !important;
  border-radius: 12px !important;
  padding: 1.25em 1em 1em 1em !important;
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.15) !important;
}
.theme-apple :deep(.markdown-body pre):before {
  content: "" !important; /* 确保 Mac 三色圆点完美显示 */
  position: absolute !important;
  top: 10px !important;
  left: 12px !important;
  width: 8px !important;
  height: 8px !important;
  border-radius: 50% !important;
  background-color: #ff5f56 !important;
  box-shadow: 16px 0 0 #ffbd2e, 32px 0 0 #27c93f !important;
  display: block !important;
  z-index: 1 !important;
}
.theme-apple :deep(.markdown-body code) {
  background-color: rgba(0, 0, 0, 0.05) !important;
  color: #1d1d1f !important;
  font-family: SFMono-Regular, Consolas, Monaco, monospace !important;
}
.dark .theme-apple :deep(.markdown-body code) {
  background-color: rgba(255, 255, 255, 0.1) !important;
  color: #f5f5f7 !important;
}
.theme-apple :deep(.markdown-body pre code) {
  background-color: transparent !important;
  color: #f5f5f7 !important; /* 代码文本亮白 */
}
.theme-apple :deep(.markdown-body blockquote) {
  border-left: 3px solid #86868b !important; /* 苹果经典灰色边线 */
  background-color: rgba(0, 0, 0, 0.02) !important;
  color: #86868b !important;
  padding: 0.8em 1.2em !important;
  border-radius: 0 8px 8px 0 !important;
}
.dark .theme-apple :deep(.markdown-body blockquote) {
  background-color: rgba(255, 255, 255, 0.02) !important;
}

/* ==================== 护眼书香样式 (Warm Theme) ==================== */
.theme-warm :deep(.markdown-body) {
  font-family: "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Georgia", serif !important;
  color: #586e75 !important; /* 深褐茶色字 */
  line-height: 1.85 !important;
  font-size: 14.5px !important;
}
.theme-warm :deep(.markdown-body p) {
  margin-bottom: 1.1em !important;
}
.theme-warm :deep(.markdown-body h1, .markdown-body h2, .markdown-body h3) {
  color: #b58900 !important; /* 暖棕黄色标题 */
  border-bottom-color: #eee8d5 !important;
}
.theme-warm :deep(.markdown-body pre) {
  background-color: #f5ece2 !important; /* 温暖浅灰底色 */
  border: 1px solid #e4dcd3 !important;
  box-shadow: none !important;
}
.theme-warm :deep(.markdown-body pre):before {
  opacity: 0.7 !important; /* 使圆点半透明融入背景 */
}
.theme-warm :deep(.markdown-body code) {
  background-color: rgba(238, 232, 213, 0.8) !important;
  color: #b58900 !important;
}
.theme-warm :deep(.markdown-body blockquote) {
  border-left-color: #93a1a1 !important;
  background-color: rgba(238, 232, 213, 0.3) !important;
  color: #657b83 !important;
}

/* ==================== 高密度紧凑样式 (Compact Theme) ==================== */
.theme-compact :deep(.markdown-body) {
  font-size: 12.5px !important;
  line-height: 1.45 !important;
}
.theme-compact :deep(.markdown-body p) {
  margin-bottom: 0.5em !important;
}
.theme-compact :deep(.markdown-body h1, .markdown-body h2, .markdown-body h3) {
  margin-top: 0.8em !important;
  margin-bottom: 0.3em !important;
}
.theme-compact :deep(.markdown-body pre) {
  margin-top: 0.5em !important;
  margin-bottom: 0.5em !important;
  padding: 0.75em 0.5em 0.5em 0.5em !important;
}
.theme-compact :deep(.markdown-body pre):before {
  top: 6px !important;
  left: 8px !important;
  scale: 0.85 !important;
}
.theme-compact :deep(.markdown-body ul, .markdown-body ol) {
  margin-bottom: 0.5em !important;
}
.theme-compact :deep(.markdown-body li) {
  margin-bottom: 0.2em !important;
}
.theme-compact :deep(.markdown-body blockquote) {
  margin: 0.6em 0 !important;
  padding-top: 0.25em !important;
  padding-bottom: 0.25em !important;
}

/* ==================== 瑞士包豪斯样式 (Bauhaus Theme) ==================== */
.theme-bauhaus :deep(.markdown-body) {
  font-family: "Helvetica Neue", Helvetica, Arial, "PingFang SC", sans-serif !important;
  color: #111111 !important;
  line-height: 1.6 !important;
  font-size: 14px !important;
  border-radius: 0px !important;
}
.dark .theme-bauhaus :deep(.markdown-body) {
  color: #eeeeee !important;
}
.theme-bauhaus :deep(.markdown-body a:not(.quick-action-btn):not([href^="quick:"])) {
  color: #002fa7 !important; /* 克莱因蓝 */
  text-decoration: none !important;
  font-weight: bold !important;
  border-bottom: 2px solid #002fa7 !important;
}
.dark .theme-bauhaus :deep(.markdown-body a:not(.quick-action-btn):not([href^="quick:"])) {
  color: #38bdf8 !important;
  border-bottom-color: #38bdf8 !important;
}
.theme-bauhaus :deep(.markdown-body a:hover) {
  background-color: #002fa7 !important;
  color: #ffffff !important;
}
.theme-bauhaus :deep(.markdown-body h1, .markdown-body h2, .markdown-body h3) {
  font-family: inherit !important;
  font-weight: 900 !important;
  color: #000000 !important;
  border-radius: 0px !important;
  text-transform: uppercase !important;
  border-bottom: 2px solid #111111 !important;
  padding-bottom: 4px !important;
}
.dark .theme-bauhaus :deep(.markdown-body h1, .markdown-body h2, .markdown-body h3) {
  color: #ffffff !important;
  border-bottom-color: #eeeeee !important;
}
.theme-bauhaus :deep(.markdown-body h1) { font-size: 1.6em !important; margin-top: 1.2em !important; }
.theme-bauhaus :deep(.markdown-body h2) { font-size: 1.35em !important; margin-top: 1.1em !important; }
.theme-bauhaus :deep(.markdown-body h3) { font-size: 1.18em !important; margin-top: 1em !important; }
.theme-bauhaus :deep(.markdown-body pre) {
  background-color: #f3f4f6 !important;
  border: 1px solid #111111 !important;
  border-radius: 0px !important;
  box-shadow: 4px 4px 0px #002fa7 !important; /* 克莱因蓝几何阴影 */
  padding: 1em !important;
  position: relative !important;
}
.dark .theme-bauhaus :deep(.markdown-body pre) {
  background-color: #111827 !important;
  border-color: #eeeeee !important;
  box-shadow: 4px 4px 0px #38bdf8 !important;
}
.theme-bauhaus :deep(.markdown-body pre):before {
  display: none !important; /* 包豪斯不需要 Mac 控制点 */
}
.theme-bauhaus :deep(.markdown-body code) {
  background-color: #f3f4f6 !important;
  color: #e60012 !important; /* 包豪斯红单点高亮 */
  border-radius: 0px !important;
  border: 1px solid #e5e7eb !important;
  padding: 2px 4px !important;
}
.dark .theme-bauhaus :deep(.markdown-body code) {
  background-color: #1f2937 !important;
  color: #f87171 !important;
  border-color: #374151 !important;
}
.theme-bauhaus :deep(.markdown-body pre code) {
  border: none !important;
  padding: 0 !important;
  color: #111111 !important;
}
.dark .theme-bauhaus :deep(.markdown-body pre code) {
  color: #eeeeee !important;
}
.theme-bauhaus :deep(.markdown-body blockquote) {
  border-left: 4px solid #000000 !important;
  background-color: #f9fafb !important;
  color: #111111 !important;
  border-radius: 0px !important;
  padding: 0.8em 1.2em !important;
}
.dark .theme-bauhaus :deep(.markdown-body blockquote) {
  border-left-color: #eeeeee !important;
  background-color: #1f2937 !important;
  color: #eeeeee !important;
}

/* ==================== 复古日报样式 (Editorial Theme) ==================== */
.theme-editorial :deep(.markdown-body) {
  font-family: "Georgia", "Nimbus Roman No9 L", "Songti SC", "Noto Serif SC", serif !important;
  color: #2c2520 !important; /* 经典油墨棕黑 */
  line-height: 1.8 !important;
  font-size: 14.5px !important;
}
.dark .theme-editorial :deep(.markdown-body) {
  color: #e5dcd3 !important;
}
.theme-editorial :deep(.markdown-body p) {
  margin-bottom: 1.2em !important;
  text-align: justify !important;
}
.theme-editorial :deep(.markdown-body p:first-of-type):first-letter {
  float: left !important;
  font-size: 2.8em !important;
  line-height: 0.9 !important;
  margin-right: 6px !important;
  font-weight: bold !important;
  color: #8c2d19 !important; /* 复古报刊红首字下沉 */
}
.theme-editorial :deep(.markdown-body pre p:first-of-type):first-letter,
.theme-editorial :deep(.markdown-body blockquote p:first-of-type):first-letter,
.theme-editorial :deep(.markdown-body li p:first-of-type):first-letter {
  float: none !important;
  font-size: inherit !important;
  line-height: inherit !important;
  margin-right: 0 !important;
  font-weight: inherit !important;
  color: inherit !important;
}
.theme-editorial :deep(.markdown-body h1, .markdown-body h2, .markdown-body h3) {
  font-family: inherit !important;
  font-weight: 700 !important;
  color: #8c2d19 !important; /* 报刊红标题 */
  border-bottom: 1px solid #dcd1c4 !important;
  padding-bottom: 6px !important;
}
.dark .theme-editorial :deep(.markdown-body h1, .markdown-body h2, .markdown-body h3) {
  color: #fca5a5 !important;
  border-bottom-color: #4b3e34 !important;
}
.theme-editorial :deep(.markdown-body h1) { font-size: 1.55em !important; margin-top: 1.4em !important; text-align: center !important; }
.theme-editorial :deep(.markdown-body h2) { font-size: 1.3em !important; margin-top: 1.3em !important; }
.theme-editorial :deep(.markdown-body h3) { font-size: 1.15em !important; margin-top: 1.1em !important; }
.theme-editorial :deep(.markdown-body pre) {
  background-color: #f7f3ed !important; /* 泛黄纸张背景 */
  border: 1px solid #e2d7c9 !important;
  border-radius: 4px !important;
  padding: 1em !important;
}
.dark .theme-editorial :deep(.markdown-body pre) {
  background-color: #2c2520 !important;
  border-color: #4b3e34 !important;
}
.theme-editorial :deep(.markdown-body pre):before {
  display: none !important;
}
.theme-editorial :deep(.markdown-body code) {
  background-color: rgba(140, 45, 25, 0.06) !important;
  color: #8c2d19 !important;
  font-family: inherit !important;
  font-weight: bold !important;
}
.dark .theme-editorial :deep(.markdown-body code) {
  background-color: rgba(252, 165, 165, 0.1) !important;
  color: #fca5a5 !important;
}
.theme-editorial :deep(.markdown-body pre code) {
  background-color: transparent !important;
  font-family: Monaco, monospace !important;
  font-weight: normal !important;
  color: #2c2520 !important;
}
.dark .theme-editorial :deep(.markdown-body pre code) {
  color: #e5dcd3 !important;
}
.theme-editorial :deep(.markdown-body blockquote) {
  border-left: 2px solid #8c2d19 !important;
  background-color: #fcf9f5 !important;
  font-style: italic !important;
  color: #61554d !important;
  padding: 0.8em 1.2em !important;
  margin: 1.2em 0 !important;
}
.dark .theme-editorial :deep(.markdown-body blockquote) {
  border-left-color: #fca5a5 !important;
  background-color: #352d27 !important;
  color: #c4b5a9 !important;
}

/* ==================== 北欧禅意样式 (Zen Theme) ==================== */
.theme-zen :deep(.markdown-body) {
  font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", sans-serif !important;
  color: #4a5568 !important; /* 柔和无锋芒中灰 */
  line-height: 1.75 !important;
  font-size: 14.5px !important;
  font-weight: 300 !important; /* 偏轻的字重 */
  letter-spacing: 0.02em !important;
}
.dark .theme-zen :deep(.markdown-body) {
  color: #cbd5e1 !important;
}
.theme-zen :deep(.markdown-body p) {
  margin-bottom: 1em !important;
}
.theme-zen :deep(.markdown-body h1, .markdown-body h2, .markdown-body h3) {
  font-family: inherit !important;
  font-weight: 600 !important;
  color: #2f4f4f !important; /* 森林绿标题 */
  border-bottom: none !important;
}
.dark .theme-zen :deep(.markdown-body h1, .markdown-body h2, .markdown-body h3) {
  color: #a3e635 !important; /* 柔光绿 */
}
.theme-zen :deep(.markdown-body h1) { font-size: 1.5em !important; margin-top: 1.3em !important; }
.theme-zen :deep(.markdown-body h2) { font-size: 1.28em !important; margin-top: 1.2em !important; }
.theme-zen :deep(.markdown-body h3) { font-size: 1.15em !important; margin-top: 1.1em !important; }
.theme-zen :deep(.markdown-body pre) {
  background-color: #f4f6f4 !important; /* 治愈柔绿底 */
  border: none !important;
  border-radius: 18px !important; /* 超圆润大圆角 */
  padding: 1.2em !important;
  box-shadow: inset 0 2px 4px rgba(0, 0, 0, 0.02) !important;
}
.dark .theme-zen :deep(.markdown-body pre) {
  background-color: #1e2920 !important;
}
.theme-zen :deep(.markdown-body pre):before {
  display: none !important;
}
.theme-zen :deep(.markdown-body code) {
  background-color: #e8eee8 !important;
  color: #2f4f4f !important;
  border-radius: 6px !important;
  padding: 2px 5px !important;
}
.dark .theme-zen :deep(.markdown-body code) {
  background-color: #2a3a2d !important;
  color: #a3e635 !important;
}
.theme-zen :deep(.markdown-body pre code) {
  background-color: transparent !important;
  color: #4a5568 !important;
}
.dark .theme-zen :deep(.markdown-body pre code) {
  color: #cbd5e1 !important;
}
.theme-zen :deep(.markdown-body blockquote) {
  border-left: 3px solid #8fbc8f !important; /* 治愈系浅灰绿线 */
  background-color: rgba(143, 188, 143, 0.05) !important;
  color: #6b8e23 !important;
  padding: 0.8em 1.2em !important;
  border-radius: 0 12px 12px 0 !important;
}
.dark .theme-zen :deep(.markdown-body blockquote) {
  background-color: rgba(163, 230, 53, 0.02) !important;
  color: #a3e635 !important;
}

/* ==================== 快捷按钮文字颜色强制兜底 (Quick Buttons Contrast Fix) ==================== */
:deep(.markdown-body .quick-action-btn),
:deep(.markdown-body a[href^="quick:"]) {
  color: #ffffff !important;
}
</style>
