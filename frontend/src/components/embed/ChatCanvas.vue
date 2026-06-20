<script setup lang="ts">
import { ref, computed, watch } from 'vue';
import hljs from 'highlight.js';
import 'highlight.js/styles/github.css';
import MermaidRenderer from '@/components/MermaidRenderer.vue';

const props = defineProps<{
  visible: boolean;
  data: {
    type: 'html' | 'code' | 'mermaid' | 'pdf' | 'csv' | 'image';
    title: string;
    content: string;
  } | null;
}>();

const emit = defineEmits<{
  (e: 'close'): void;
}>();

const isFullscreen = ref(false);
const copied = ref(false);

const toggleFullscreen = () => {
  isFullscreen.value = !isFullscreen.value;
};

const handleClose = () => {
  isFullscreen.value = false;
  emit('close');
};

// 提取代码文件的默认后缀
const getFileExtension = (title: string): string => {
  const lower = title.toLowerCase();
  if (lower.includes('python')) return 'py';
  if (lower.includes('javascript') || lower.includes('js')) return 'js';
  if (lower.includes('typescript') || lower.includes('ts')) return 'ts';
  if (lower.includes('sql')) return 'sql';
  if (lower.includes('html')) return 'html';
  if (lower.includes('css')) return 'css';
  if (lower.includes('json')) return 'json';
  if (lower.includes('shell') || lower.includes('bash')) return 'sh';
  return 'txt';
};

// 复制代码内容
const copyContent = () => {
  if (!props.data?.content) return;
  navigator.clipboard.writeText(props.data.content).then(() => {
    copied.value = true;
    setTimeout(() => {
      copied.value = false;
    }, 2000);
  });
};

// 下载代码或 HTML
const downloadFile = () => {
  if (!props.data) return;
  const content = resolvedContent.value;
  
  // 对于图片、PDF以及CSV（这里的CSV content存放的是文件链接），我们可以通过原生a标签进行链接下载或跳转
  if (props.data.type === 'image' || props.data.type === 'pdf' || props.data.type === 'csv') {
    const a = document.createElement('a');
    a.href = content;
    a.download = props.data.title || 'download';
    a.target = '_blank';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    return;
  }
  
  const extension = props.data.type === 'html' ? 'html' : getFileExtension(props.data.title);
  const blob = new Blob([content], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `canvas_export.${extension}`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
};

// 使用 hljs 进行代码渲染（带行号）
const highlightedCode = computed(() => {
  if (!props.data || (props.data.type !== 'code' && props.data.type !== 'html')) return '';
  const content = props.data.content;
  
  let lang = 'txt';
  if (props.data.type === 'html') {
    lang = 'xml';
  } else {
    const title = props.data.title.toLowerCase();
    if (title.includes('python')) lang = 'python';
    else if (title.includes('javascript') || title.includes('js')) lang = 'javascript';
    else if (title.includes('typescript') || title.includes('ts')) lang = 'typescript';
    else if (title.includes('sql')) lang = 'sql';
    else if (title.includes('css')) lang = 'css';
    else if (title.includes('html')) lang = 'xml';
    else if (title.includes('json')) lang = 'json';
    else if (title.includes('sh') || title.includes('bash')) lang = 'bash';
  }

  try {
    if (hljs.getLanguage(lang)) {
      return hljs.highlight(content, { language: lang, ignoreIllegals: true }).value;
    }
  } catch (e) {
    console.error('Highlight failed, fallback to safe html escape', e);
  }
  
  // Safe fallback html escaping
  return content
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
});

// 计算行号数组
const lineCount = computed(() => {
  if (!props.data?.content) return [];
  const lines = props.data.content.split('\n');
  // 保持与内容的行号一致，数组值为 1, 2, 3...
  return Array.from({ length: lines.length }, (_, i) => i + 1);
});

// ==========================================
// 1. Mermaid & Image Pan & Zoom 缩放拖动交互
// ==========================================
const isDragging = ref(false);
const startX = ref(0);
const startY = ref(0);
const translateX = ref(0);
const translateY = ref(0);
const scale = ref(1);

const isZoomable = computed(() => {
  return props.data?.type === 'mermaid' || props.data?.type === 'image';
});

const transformStyle = computed(() => {
  return {
    transform: `translate(${translateX.value}px, ${translateY.value}px) scale(${scale.value})`,
    transition: isDragging.value ? 'none' : 'transform 0.1s ease-out'
  };
});

const startDrag = (e: MouseEvent) => {
  if (!isZoomable.value) return;
  e.preventDefault();
  isDragging.value = true;
  startX.value = e.clientX - translateX.value;
  startY.value = e.clientY - translateY.value;
  
  window.addEventListener('mousemove', onDrag);
  window.addEventListener('mouseup', stopDrag);
};

const onDrag = (e: MouseEvent) => {
  if (!isDragging.value) return;
  translateX.value = e.clientX - startX.value;
  translateY.value = e.clientY - startY.value;
};

const stopDrag = () => {
  if (!isDragging.value) return;
  isDragging.value = false;
  window.removeEventListener('mousemove', onDrag);
  window.removeEventListener('mouseup', stopDrag);
};

const handleZoom = (e: WheelEvent) => {
  if (!isZoomable.value) return;
  e.preventDefault();
  const zoomFactor = 0.08;
  const delta = e.deltaY < 0 ? 1 : -1;
  const newScale = scale.value + delta * zoomFactor;
  scale.value = Math.max(0.2, Math.min(6, newScale));
};

const zoomIn = () => {
  if (!isZoomable.value) return;
  scale.value = Math.min(6, scale.value + 0.15);
};

const zoomOut = () => {
  if (!isZoomable.value) return;
  scale.value = Math.max(0.2, scale.value - 0.15);
};

const resetTransform = () => {
  scale.value = 1;
  translateX.value = 0;
  translateY.value = 0;
};

// ==========================================
// 2. CSV Parser & Local Search Filter
// ==========================================
const csvLoading = ref(false);
const csvError = ref('');
const csvHeaders = ref<string[]>([]);
const csvRows = ref<string[][]>([]);
const csvSearchQuery = ref('');

const loadCSVData = async () => {
  if (!resolvedContent.value) return;
  csvLoading.value = true;
  csvError.value = '';
  csvHeaders.value = [];
  csvRows.value = [];
  
  try {
    const response = await fetch(resolvedContent.value);
    if (!response.ok) {
      throw new Error(`加载 CSV 数据失败: ${response.statusText}`);
    }
    const text = await response.text();
    parseCSV(text);
  } catch (err: any) {
    console.error('Fetch CSV error:', err);
    csvError.value = err.message || '加载失败，请尝试直接下载文件。';
  } finally {
    csvLoading.value = false;
  }
};

const parseCSV = (text: string) => {
  if (!text) return;
  const lines = text.split(/\r?\n/).filter(line => line.trim() !== '');
  if (lines.length === 0) return;
  
  const rawRows = lines.map(line => {
    const cells: string[] = [];
    let currentCell = '';
    let inQuotes = false;
    
    for (let i = 0; i < line.length; i++) {
      const char = line[i];
      if (char === '"') {
        inQuotes = !inQuotes;
      } else if (char === ',' && !inQuotes) {
        cells.push(currentCell.replace(/^"|"$/g, '').trim());
        currentCell = '';
      } else {
        currentCell += char;
      }
    }
    cells.push(currentCell.replace(/^"|"$/g, '').trim());
    return cells;
  });
  
  if (rawRows.length > 0) {
    csvHeaders.value = rawRows[0] || [];
    csvRows.value = rawRows.slice(1) || [];
  }
};

const filteredCsvRows = computed(() => {
  if (!csvSearchQuery.value) return csvRows.value;
  const q = csvSearchQuery.value.trim().toLowerCase();
  return csvRows.value.filter(row => {
    return row.some(cell => cell.toLowerCase().includes(q));
  });
});

const highlightMatch = (text: string) => {
  if (!csvSearchQuery.value) return text;
  const q = csvSearchQuery.value.trim();
  const regex = new RegExp(`(${escapeRegExp(q)})`, 'gi');
  return text.replace(regex, '<mark class="bg-yellow-200 dark:bg-yellow-800/60 dark:text-white px-0.5 rounded">$1</mark>');
};

const escapeRegExp = (str: string) => {
  return str.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
};

// ==========================================
// 3. HTML Preview & Code Tab Switcher
// ==========================================
const activeTab = ref<'preview' | 'code'>('preview');

const isHtmlContent = computed(() => {
  if (!props.data) return false;
  if (props.data.type === 'html') return true;
  if (props.data.type === 'code') {
    const content = props.data.content.trim().toLowerCase();
    const title = props.data.title.toLowerCase();
    return (
      content.startsWith('<!doctype') ||
      content.startsWith('<html') ||
      content.includes('<div') ||
      content.includes('<img') ||
      content.includes('<iframe') ||
      title.includes('html') ||
      title.includes('xml')
    );
  }
  return false;
});

const resolveUrlPath = (val: string): string => {
  if (!val) return '';
  if (val.startsWith('http://') || val.startsWith('https://') || val.startsWith('data:')) {
    return val;
  }
  if (val.includes('uploads/')) {
    const parts = val.split('uploads/');
    return '/static/uploads/' + parts[parts.length - 1];
  }
  if (val.startsWith('/') && 
      !val.startsWith('/static/') && 
      !val.startsWith('/api/') && 
      !val.startsWith('/assets/')) {
    return `/api/v1/chat/fs/preview?path=${encodeURIComponent(val)}`;
  }
  return val;
};

const resolvedContent = computed(() => {
  if (!props.data?.content) return '';
  const val = props.data.content;
  
  if (props.data.type === 'html') {
    // 深度重写 HTML 中的所有物理资源绝对路径引用
    return val.replace(/(src|href)=["']([^"']*)["']/gi, (match, attr, pathVal) => {
      const newVal = resolveUrlPath(pathVal);
      return `${attr}="${newVal}"`;
    });
  }
  
  if (props.data.type === 'image' || props.data.type === 'pdf' || props.data.type === 'csv') {
    return resolveUrlPath(val);
  }
  
  return val;
});

// ==========================================
// 4. Watchers & Lifecycles
// ==========================================
watch(() => props.data, () => {
  resetTransform();
  if (props.data?.type === 'csv') {
    csvSearchQuery.value = '';
    loadCSVData();
  }
  
  // 自动根据类型与内容初始化当前激活 Tab
  if (props.data) {
    if (props.data.type === 'html') {
      activeTab.value = 'preview';
    } else {
      activeTab.value = 'code';
    }
  }
}, { immediate: true });
</script>

<template>
  <teleport to="body">
    <Transition name="slide">
      <div
        v-if="visible"
        class="fixed right-0 top-0 bottom-0 z-[120] bg-white/95 dark:bg-gray-800/95 backdrop-blur-md border-l border-gray-200 dark:border-gray-700 shadow-2xl flex flex-col transition-all duration-300"
        :class="isFullscreen ? 'left-0 w-full' : 'w-full sm:w-[80%] sm:max-w-[520px] left-0 sm:left-auto'"
      >
      <!-- Header -->
      <div class="p-4 border-b border-gray-100 dark:border-gray-700 flex justify-between items-center flex-shrink-0">
        <div class="flex items-center space-x-2">
          <span class="text-sm font-bold text-gray-800 dark:text-gray-200 truncate max-w-[200px] sm:max-w-[320px]">
            {{ data?.title || '画布' }}
          </span>
          <span
            class="px-1.5 py-0.5 text-[8px] rounded font-bold uppercase"
            :class="
              data?.type === 'html' ? 'bg-blue-100 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400' :
              data?.type === 'image' ? 'bg-emerald-100 dark:bg-emerald-900/30 text-emerald-600 dark:text-emerald-400' :
              data?.type === 'pdf' ? 'bg-rose-100 dark:bg-rose-900/30 text-rose-600 dark:text-rose-400' :
              data?.type === 'csv' ? 'bg-amber-100 dark:bg-amber-900/30 text-amber-600 dark:text-amber-400' :
              data?.type === 'mermaid' ? 'bg-indigo-100 dark:bg-indigo-900/30 text-indigo-600 dark:text-indigo-400' :
              'bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400'
            "
          >
            {{ 
              data?.type === 'html' ? 'Application' : 
              data?.type === 'image' ? 'Image' :
              data?.type === 'pdf' ? 'PDF' :
              data?.type === 'csv' ? 'CSV Table' :
              data?.type === 'mermaid' ? 'Diagram' :
              'Code' 
            }}
          </span>
        </div>
        <div class="flex items-center space-x-2">
          <!-- Fullscreen Toggle Button -->
          <button
            @click="toggleFullscreen"
            class="p-1.5 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg transition-colors"
            :title="isFullscreen ? '半屏展示' : '全屏展示'"
          >
            <svg
              class="w-4 h-4"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                v-if="isFullscreen"
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M4 14h6v6m10-6h-6v6M4 10h6V4m10 6h-6V4"
              />
              <path
                v-else
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M4 8V4h4m12 4V4h-4M4 16v4h4m12-4v4h-4"
              />
            </svg>
          </button>
          <!-- Close Button -->
          <button
            @click="handleClose"
            class="p-1.5 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg transition-colors"
            title="关闭画布"
          >
            <svg
              class="w-4 h-4"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M6 18L18 6M6 6l12 12"
              />
            </svg>
          </button>
        </div>
      </div>

      <!-- HTML Preview/Code Tabs Selector -->
      <div v-if="isHtmlContent" class="px-4 py-2 border-b border-gray-100/50 dark:border-gray-700/50 bg-slate-50/50 dark:bg-gray-900/10 flex-shrink-0 flex justify-center">
        <div class="bg-gray-150/80 dark:bg-gray-900 p-0.5 rounded-lg flex space-x-1 w-full max-w-[240px] border border-gray-200/20 shadow-inner">
          <button 
            @click="activeTab = 'preview'"
            class="flex-1 py-1 text-[11px] font-bold rounded-md transition-all duration-200 flex items-center justify-center space-x-1"
            :class="activeTab === 'preview' 
              ? 'bg-white dark:bg-gray-800 text-gray-800 dark:text-white shadow-xs border border-gray-200/10' 
              : 'text-gray-400 hover:text-gray-600 dark:hover:text-gray-300'"
          >
            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z"/></svg>
            <span>效果预览</span>
          </button>
          <button 
            @click="activeTab = 'code'"
            class="flex-1 py-1 text-[11px] font-bold rounded-md transition-all duration-200 flex items-center justify-center space-x-1"
            :class="activeTab === 'code' 
              ? 'bg-white dark:bg-gray-800 text-gray-800 dark:text-white shadow-xs border border-gray-200/10' 
              : 'text-gray-400 hover:text-gray-600 dark:hover:text-gray-300'"
          >
            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4"/></svg>
            <span>源代码</span>
          </button>
        </div>
      </div>

      <!-- Content Area -->
      <div class="flex-1 overflow-auto p-4 custom-scrollbar bg-slate-50/50 dark:bg-gray-900/10">
        <!-- HTML Safe Sandbox Rendering / Code Switchable -->
        <template v-if="isHtmlContent">
          <!-- HTML Preview iframe -->
          <div v-if="activeTab === 'preview'" class="w-full h-full bg-white dark:bg-gray-950 rounded-xl overflow-hidden shadow-inner border border-gray-100 dark:border-gray-800 min-h-[500px]">
            <iframe
              :srcdoc="resolvedContent"
              sandbox="allow-scripts"
              class="w-full h-full border-none"
            ></iframe>
          </div>
          <!-- HTML Source Code -->
          <div v-else class="w-full font-mono text-xs overflow-x-auto bg-white dark:bg-gray-900 rounded-xl p-4 border border-gray-100 dark:border-gray-850 shadow-inner flex leading-relaxed select-text">
            <!-- Line Numbers Column -->
            <div class="text-gray-300 dark:text-gray-600 text-right pr-4 select-none border-r border-gray-100 dark:border-gray-800 flex-shrink-0">
              <div v-for="num in lineCount" :key="num" class="h-5">
                {{ num }}
              </div>
            </div>
            <!-- Highlighted Code Column -->
            <pre class="pl-4 flex-1 overflow-x-auto custom-scrollbar select-text"><code class="hljs block whitespace-pre" v-html="highlightedCode"></code></pre>
          </div>
        </template>

        <!-- General Code Block (Not HTML) -->
        <template v-else-if="data?.type === 'code'">
          <div class="w-full font-mono text-xs overflow-x-auto bg-white dark:bg-gray-900 rounded-xl p-4 border border-gray-100 dark:border-gray-850 shadow-inner flex leading-relaxed select-text">
            <!-- Line Numbers Column -->
            <div class="text-gray-300 dark:text-gray-600 text-right pr-4 select-none border-r border-gray-100 dark:border-gray-800 flex-shrink-0">
              <div v-for="num in lineCount" :key="num" class="h-5">
                {{ num }}
              </div>
            </div>
            <!-- Highlighted Code Column -->
            <pre class="pl-4 flex-1 overflow-x-auto custom-scrollbar select-text"><code class="hljs block whitespace-pre" v-html="highlightedCode"></code></pre>
          </div>
        </template>

        <!-- PDF Viewer Sandbox -->
        <template v-else-if="data?.type === 'pdf'">
          <div class="w-full h-full bg-white dark:bg-gray-950 rounded-xl overflow-hidden shadow-inner border border-gray-100 dark:border-gray-800 min-h-[500px]">
            <iframe
              :src="resolvedContent"
              class="w-full h-full border-none"
            ></iframe>
          </div>
        </template>

        <!-- Mermaid Diagram Viewer (with Drag & Zoom) -->
        <template v-else-if="data?.type === 'mermaid'">
          <div class="w-full h-full flex flex-col min-h-0 relative select-none">
            <div 
              class="flex-1 relative w-full overflow-hidden cursor-grab active:cursor-grabbing border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 rounded-xl shadow-inner min-h-[480px]"
              @mousedown="startDrag"
              @wheel.prevent="handleZoom"
            >
              <div 
                :style="transformStyle" 
                class="w-full h-full flex items-center justify-center p-8 origin-center"
              >
                <MermaidRenderer :content="data.content" />
              </div>
              
              <!-- Zoom Control overlay -->
              <div class="absolute bottom-4 right-4 flex items-center space-x-1 bg-white/90 dark:bg-gray-800/90 shadow border border-gray-100 dark:border-gray-700 rounded-lg p-1 z-10 backdrop-blur-xs select-none">
                <button @click="zoomIn" class="w-7 h-7 flex items-center justify-center text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 rounded transition-colors text-sm font-bold">+</button>
                <button @click="zoomOut" class="w-7 h-7 flex items-center justify-center text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 rounded transition-colors text-sm font-bold">-</button>
                <button @click="resetTransform" class="px-2 h-7 flex items-center justify-center text-[10px] font-bold text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700 rounded transition-colors whitespace-nowrap">复位</button>
              </div>
            </div>
            <p class="mt-2 text-[10px] text-gray-400 dark:text-gray-500 text-center select-none">
              提示：可以使用鼠标滚轮缩放图表，按住左键拖拽平移。
            </p>
          </div>
        </template>

        <!-- CSV Data Table Viewer (with Filter) -->
        <template v-else-if="data?.type === 'csv'">
          <div class="w-full h-full flex flex-col min-h-0">
            <!-- Search bar -->
            <div class="mb-3 flex-shrink-0">
              <div class="relative">
                <input 
                  v-model="csvSearchQuery"
                  type="text" 
                  placeholder="全局模糊搜索过滤数据..."
                  class="w-full text-xs bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-750 focus:border-primary focus:ring-1 focus:ring-primary rounded-lg pl-8 pr-3 py-2 outline-none text-gray-700 dark:text-gray-300 transition-all"
                />
                <div class="absolute left-2.5 top-2.5 text-gray-400">
                  <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/></svg>
                </div>
              </div>
            </div>

            <!-- Loading & Error states -->
            <div v-if="csvLoading" class="flex-grow flex flex-col items-center justify-center py-12">
              <div class="animate-spin rounded-full h-6 w-6 border-b-2 border-primary mb-2"></div>
              <span class="text-xs text-gray-400">正在拉取并解析数据...</span>
            </div>
            <div v-else-if="csvError" class="flex-grow flex flex-col items-center justify-center py-12 px-4 text-center">
              <span class="text-xl mb-2">⚠️</span>
              <span class="text-xs text-red-500 font-mono">{{ csvError }}</span>
            </div>
            <div v-else class="flex-grow overflow-auto border border-gray-200 dark:border-gray-750 rounded-xl bg-white dark:bg-gray-900/50 shadow-inner custom-scrollbar min-h-[360px]">
              <table class="min-w-full divide-y divide-gray-200 dark:divide-gray-700 text-left border-collapse table-auto">
                <thead class="bg-gray-50 dark:bg-gray-800/80 sticky top-0 z-10">
                  <tr>
                    <th 
                      v-for="(header, hIdx) in csvHeaders" 
                      :key="hIdx"
                      class="px-4 py-2.5 text-xs font-bold text-gray-500 dark:text-gray-400 border-b border-gray-200 dark:border-gray-750 whitespace-nowrap"
                    >
                      {{ header }}
                    </th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-gray-150 dark:divide-gray-800 text-[11px] text-gray-700 dark:text-gray-300">
                  <tr 
                    v-for="(row, rIdx) in filteredCsvRows" 
                    :key="rIdx"
                    class="hover:bg-gray-50/60 dark:hover:bg-gray-800/20"
                  >
                    <td 
                      v-for="(cell, cIdx) in row" 
                      :key="cIdx"
                      class="px-4 py-2 border-b border-gray-100 dark:border-gray-800 break-words whitespace-normal"
                      v-html="highlightMatch(cell)"
                    >
                    </td>
                  </tr>
                  <tr v-if="filteredCsvRows.length === 0">
                    <td :colspan="csvHeaders.length" class="text-center py-10 text-xs text-gray-400 select-none">
                      无匹配结果
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </template>

        <!-- Image Viewer (with Drag & Zoom) -->
        <template v-else-if="data?.type === 'image'">
          <div class="w-full h-full flex flex-col min-h-0 relative select-none">
            <div 
              class="flex-1 relative w-full overflow-hidden cursor-grab active:cursor-grabbing border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 rounded-xl shadow-inner min-h-[480px] flex items-center justify-center"
              @mousedown="startDrag"
              @wheel.prevent="handleZoom"
            >
              <div 
                :style="transformStyle" 
                class="max-w-full max-h-full flex items-center justify-center p-4 origin-center"
              >
                <img 
                  :src="resolvedContent" 
                  :alt="data.title" 
                  class="max-w-full max-h-[80vh] object-contain pointer-events-none select-none rounded-lg"
                />
              </div>
              
              <!-- Zoom Control overlay -->
              <div class="absolute bottom-4 right-4 flex items-center space-x-1 bg-white/90 dark:bg-gray-800/90 shadow border border-gray-100 dark:border-gray-700 rounded-lg p-1 z-10 backdrop-blur-xs select-none">
                <button @click="zoomIn" class="w-7 h-7 flex items-center justify-center text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 rounded transition-colors text-sm font-bold">+</button>
                <button @click="zoomOut" class="w-7 h-7 flex items-center justify-center text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 rounded transition-colors text-sm font-bold">-</button>
                <button @click="resetTransform" class="px-2 h-7 flex items-center justify-center text-[10px] font-bold text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700 rounded transition-colors whitespace-nowrap">复位</button>
              </div>
            </div>
            <p class="mt-2 text-[10px] text-gray-400 dark:text-gray-500 text-center select-none">
              提示：可以使用鼠标滚轮缩放图片，按住左键拖拽平移。
            </p>
          </div>
        </template>
      </div>

      <!-- Action Footer -->
      <div class="p-3 border-t border-gray-100 dark:border-gray-700 bg-white dark:bg-gray-800/80 flex space-x-3 flex-shrink-0">
        <button
          @click="copyContent"
          class="flex-1 py-2 text-xs font-bold rounded-lg transition-colors flex items-center justify-center space-x-1.5"
          :class="copied 
            ? 'bg-emerald-50 text-emerald-600 border border-emerald-100 dark:bg-emerald-900/20 dark:text-emerald-400 dark:border-emerald-900/30' 
            : 'bg-gray-100 hover:bg-gray-200 text-gray-700 border border-transparent dark:bg-gray-700 dark:hover:bg-gray-600 dark:text-gray-300'"
        >
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path
              v-if="copied"
              stroke-linecap="round"
              stroke-linejoin="round"
              stroke-width="2"
              d="M5 13l4 4L19 7"
            />
            <path
              v-else
              stroke-linecap="round"
              stroke-linejoin="round"
              stroke-width="2"
              d="M8 5H6a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2v-1M8 5a2 2 0 002 2h2a2 2 0 002-2M8 5a2 2 0 012-2h2a2 2 0 012 2m0 0h2a2 2 0 012 2v3m2 4H10m0 0l3-3m-3 3l3 3"
            />
          </svg>
          <span>{{ copied ? '已复制！' : (data?.type === 'image' || data?.type === 'pdf' ? '复制链接' : '复制代码') }}</span>
        </button>

        <button
          @click="downloadFile"
          class="flex-1 py-2 text-xs font-bold text-white bg-blue-600 hover:bg-blue-700 active:scale-[0.98] rounded-lg transition-all flex items-center justify-center space-x-1.5 shadow-sm shadow-blue-600/10"
        >
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
          </svg>
          <span>{{ data?.type === 'image' || data?.type === 'pdf' ? '下载文件' : '下载数据' }}</span>
        </button>
      </div>
      </div>
    </Transition>
  </teleport>
</template>

<style scoped>
.slide-enter-active,
.slide-leave-active {
  transition: transform 0.3s cubic-bezier(0.16, 1, 0.3, 1), opacity 0.3s ease;
}
.slide-enter-from,
.slide-leave-to {
  transform: translateX(100%);
  opacity: 0;
}

.custom-scrollbar::-webkit-scrollbar {
  width: 4px;
  height: 4px;
}
.custom-scrollbar::-webkit-scrollbar-track {
  background: transparent;
}
.custom-scrollbar::-webkit-scrollbar-thumb {
  background: #cbd5e1;
  border-radius: 2px;
}
.dark .custom-scrollbar::-webkit-scrollbar-thumb {
  background: #4b5563;
}
.custom-scrollbar::-webkit-scrollbar-thumb:hover {
  background: #94a3b8;
}

/* Override hljs styles locally for consistency */
.hljs {
  background: transparent;
  padding: 0;
  color: #374151;
}
.dark .hljs {
  color: #d1d5db;
}
.dark .hljs :deep(.hljs-keyword),
.dark .hljs :deep(.hljs-selector-tag) {
  color: #f472b6;
}
.dark .hljs :deep(.hljs-string) {
  color: #34d399;
}
.dark .hljs :deep(.hljs-number),
.dark .hljs :deep(.hljs-literal) {
  color: #fb7185;
}
.dark .hljs :deep(.hljs-title),
.dark .hljs :deep(.hljs-section) {
  color: #60a5fa;
}
.dark .hljs :deep(.hljs-comment) {
  color: #9ca3af;
}
</style>
