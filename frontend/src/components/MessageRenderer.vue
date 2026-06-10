<script setup lang="ts">
import { computed } from 'vue';
import { renderMarkdown } from '@/utils/markdown';
import { parseQuickButtons, postProcessQuickButtonHtml } from '@/utils/quickButtons';
import { mergeChartDefaults, parseChartOptions } from '@/utils/chartRenderer';
import MermaidRenderer from './MermaidRenderer.vue';

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
  (e: 'show-citation', id: string): void;
}>();

interface ContentSegment {
  type: 'text' | 'chart' | 'mermaid' | 'thought' | 'analysis';
  content: string;
  chartData?: any;
  title?: string;
}

  /**
   * 后处理：修复被 Markdown 引擎“误杀”转义的 HTML
   */
  const postProcessHtml = (html: string) => {
    if (!html) return '';
    let res = postProcessQuickButtonHtml(html);
  
    // 处理 RAGFlow 引用的展示样式
    res = res.replace(/(?:[\[【]ID:(\w+)[\]】])|(?:Fig\.\s*(\d+))/gi, (match, id, figNum) => {
      const finalId = id || figNum;
      return `<span class="citation-badge" data-cite-id="${finalId}">${match}</span>`;
    });
  
    return res;
  };

const handleContentClick = (event: MouseEvent) => {
  const target = event.target as HTMLElement;
  const citeEl = target.closest('[data-cite-id]');
  if (citeEl) {
    const citeId = citeEl.getAttribute('data-cite-id');
    if (citeId) { emit('show-citation', citeId); event.preventDefault(); event.stopPropagation(); return; }
  }

  const linkEl = target.closest('a');
  if (linkEl) {
    const href = linkEl.getAttribute('href');
    if (href && href.startsWith('quick:')) {
      const rawQuestion = href.replace(/^quick:/, '');
      let question = '';
      try { question = decodeURIComponent(decodeURIComponent(rawQuestion)); } 
      catch { question = decodeURIComponent(rawQuestion); }
      if (question) { emit('quick-question', question.trim()); event.preventDefault(); event.stopPropagation(); return; }
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

  const regex = /(?:<thought>([\s\S]*?)<\/thought>)|(?:<chart>([\s\S]*?)<\/chart>)|(?:```\s*(?:chart|echarts|json)\s*([\s\S]*?)```)|(?:```\s*mermaid\s*([\s\S]*?)```)|(?::::analysis\s*([^\n]*)\n([\s\S]*?)\n:::)/gi;
  const result: ContentSegment[] = [];
  let lastIndex = 0;
  let match;

  while ((match = regex.exec(cleanContent)) !== null) {
    if (match.index > lastIndex) {
      result.push({ type: 'text', content: postProcessHtml(renderMarkdown(cleanContent.slice(lastIndex, match.index))) });
    }
    if (match[1]) result.push({ type: 'thought', content: renderMarkdown(match[1].trim()) });
    else if (match[4]) result.push({ type: 'mermaid', content: match[4].trim() });
    else if (match[5] || match[6]) {
      result.push({ 
        type: 'analysis', 
        title: 'AI分析推理过程 ...',
        content: renderMarkdown(match[6]?.trim() || '') 
      });
    }
    else if (match[2] || match[3]) {
      const rawJson = (match[2] || match[3]) as string;
      const jsonStr = rawJson.trim().replace(/^(json|javascript|js|xml|chart)\s+/i, '');
      const parsed = parseChartOptions(jsonStr);
      if (parsed.ok) {
        result.push({ type: 'chart', content: jsonStr, chartData: mergeChartDefaults(parsed.option) });
      } else {
        console.error('Failed to parse chart options:', parsed.error);
        result.push({ type: 'text', content: renderMarkdown(`> ⚠️ **Chart Render Failed**\n\n\`\`\`json\n${jsonStr}\n\`\`\``) });
      }
    }
    lastIndex = regex.lastIndex;
  }

  if (lastIndex < cleanContent.length) {
    result.push({ type: 'text', content: postProcessHtml(renderMarkdown(cleanContent.slice(lastIndex))) });
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
      <div v-else-if="segment.type === 'mermaid'" class="w-full my-4"><MermaidRenderer :content="segment.content" /></div>
      <div v-else-if="segment.type === 'chart'" class="w-full h-64 bg-white rounded-lg border border-gray-100 p-2 shadow-sm"><v-chart class="chart" :option="segment.chartData" autoresize /></div>
      
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
.markdown-body :deep(pre), .markdown-body :deep(code) { white-space: pre-wrap; overflow-x: auto; }

/* Analysis Block Styling - Refined for clarity and "thinking" vibe */
.analysis-block {
  margin: 1.5rem 0;
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
