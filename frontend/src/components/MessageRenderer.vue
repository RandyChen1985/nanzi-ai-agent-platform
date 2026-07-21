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

const props = defineProps<{
  content: string;
}>();

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
  const FILE_PATH_SEGMENT = String.raw`[^/\s<>"'，。；：！？、]+`;
  const filePathRegex = new RegExp(
    String.raw`(?:\./|/)?(?:${FILE_PATH_SEGMENT}/)+${FILE_PATH_SEGMENT}\.(?:${FILE_PATH_EXTENSIONS})`,
    'giu',
  );

  const appendOpenLinkToPath = (pathVal: string) => {
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
  <div class="message-renderer space-y-4" @click="handleContentClick">
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
          class="absolute top-2 right-2 p-1.5 bg-white/90 dark:bg-gray-800/90 text-gray-500 hover:text-[var(--primary-color)] rounded-lg shadow border border-gray-200 dark:border-gray-700 opacity-0 group-hover/mermaid:opacity-100 transition-all z-10 text-xs flex items-center space-x-1"
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

/* 快捷追问保留按钮识别，但以低强调描边融入回答内容。 */
.markdown-body :deep(.quick-action-btn),
.markdown-body :deep(a[href^="quick:"]) {
  display: inline-flex !important;
  align-items: center !important;
  margin: 4px 8px 4px 0 !important;
  max-width: 100% !important;
  min-height: 32px !important;
  padding: 5px 10px !important;
  background-color: #f8fbff !important;
  background-color: color-mix(in srgb, var(--primary-color, #0052D9) 4%, white) !important;
  border: 1px solid #dbe7f8 !important;
  border-color: color-mix(in srgb, var(--primary-color, #0052D9) 18%, white) !important;
  border-radius: 4px !important;
  color: var(--primary-color, #0052D9) !important;
  font-size: 13px !important;
  font-weight: 500 !important;
  text-decoration: none !important;
  transition: background-color 0.2s cubic-bezier(0.38, 0, 0.24, 1), border-color 0.2s cubic-bezier(0.38, 0, 0.24, 1) !important;
  cursor: pointer !important;
  line-height: 1.4 !important;
  white-space: normal !important;
}

/* 去掉强制 ⚡️ 前缀（label 里的 emoji 自行负责） */
.markdown-body :deep(.quick-action-btn::before),
.markdown-body :deep(a[href^="quick:"]::before) {
  content: none !important;
  margin-right: 0 !important;
}

.markdown-body :deep(.quick-action-btn:hover),
.markdown-body :deep(a[href^="quick:"]:hover) {
  color: #003CAB !important;
  background-color: #f1f6ff !important;
  background-color: color-mix(in srgb, var(--primary-color, #0052D9) 8%, white) !important;
  border-color: color-mix(in srgb, var(--primary-color, #0052D9) 38%, white) !important;
}

.markdown-body :deep(.quick-action-btn:focus-visible),
.markdown-body :deep(a[href^="quick:"]:focus-visible) {
  outline: 2px solid #80aaff !important;
  outline-color: color-mix(in srgb, var(--primary-color, #0052D9) 35%, white) !important;
  outline-offset: 2px !important;
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
</style>
