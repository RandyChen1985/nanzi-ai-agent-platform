<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted } from 'vue';
import hljs from 'highlight.js';
import 'highlight.js/styles/github.css';
import MermaidRenderer from '@/components/MermaidRenderer.vue';
import { renderMarkdown } from '@/utils/markdown';
import PivotTable from '@/components/embed/PivotTable.vue';
import { useToast } from '@/composables/useToast';
import { canWriteWorkspaceFile, resolvePublicUploadsPreviewUrl, saveWorkspaceFileContent } from '@/utils/workspaceFilePreview';

const props = withDefaults(
  defineProps<{
    visible: boolean;
    data: {
      type: 'html' | 'code' | 'mermaid' | 'pdf' | 'csv' | 'image' | 'compare';
      title: string;
      content: string;
      sourcePath?: string;
      compareContent?: string;
      compareTitle?: string;
    } | null;
    /** 工作空间预览时靠左停靠，避免与右侧抽屉重叠 */
    dockSide?: 'left' | 'right';
    /** 叠放在父级聊天区域内，不挤压主布局 */
    overlay?: boolean;
    conversationId?: string | null;
  }>(),
  { dockSide: 'right', overlay: false },
);

const emit = defineEmits<{
  (e: 'close'): void;
  (e: 'analyze-diff', question: string): void;
  (e: 'content-saved', payload: { path: string; content: string }): void;
}>();

const { showToast } = useToast();
const isFullscreen = ref(false);
const copied = ref(false);
const editorContent = ref('');
const savedContent = ref('');
const saving = ref(false);
const isMobile = ref(
  typeof window !== 'undefined' && window.matchMedia('(max-width: 639px)').matches,
);
let mobileMq: MediaQueryList | null = null;

const syncMobile = () => {
  isMobile.value = !!mobileMq?.matches;
};

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
  const text = codeTextContent.value;
  if (!text) return;
  navigator.clipboard.writeText(text).then(() => {
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
const canSaveWorkspaceFile = computed(() => {
  if (!props.data?.sourcePath) return false;
  return canWriteWorkspaceFile(props.data.title);
});

const isCodeEditing = computed(() => {
  if (!canSaveWorkspaceFile.value) return false;
  if (props.data?.type === 'code' && !isHtmlContent.value && !isMarkdownContent.value) return true;
  return activeTab.value === 'code';
});

const codeTextContent = computed(() => {
  if (isCodeEditing.value) return editorContent.value;
  return props.data?.content || '';
});

const isDirty = computed(() => canSaveWorkspaceFile.value && editorContent.value !== savedContent.value);

const highlightedCode = computed(() => {
  if (!props.data || (props.data.type !== 'code' && props.data.type !== 'html')) return '';
  const content = codeTextContent.value;

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
  const content = codeTextContent.value;
  if (!content) return [];
  const lines = content.split('\n');
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
// CSV Pivot Table Integration
// ==========================================
const csvViewMode = ref<'table' | 'pivot'>('table');

const pivotFields = computed(() => {
  return csvHeaders.value.map((header) => {
    let type = "string";
    const firstRow = csvRows.value[0];
    if (firstRow) {
      const idx = csvHeaders.value.indexOf(header);
      if (idx !== -1) {
        const sampleVal = firstRow[idx];
        if (sampleVal && !isNaN(Number(sampleVal))) {
          type = "number";
        }
      }
    }
    return {
      name: header,
      label: header,
      type: type,
    };
  });
});

const pivotRows = computed(() => {
  return csvRows.value.map((row) => {
    const obj: Record<string, any> = {};
    csvHeaders.value.forEach((header, idx) => {
      const val = row[idx];
      const isNum = pivotFields.value[idx]?.type === "number";
      obj[header] = isNum && val !== "" ? Number(val) : val;
    });
    return obj;
  });
});

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

const isMarkdownContent = computed(() => {
  if (!props.data) return false;
  if (props.data.type === 'code') {
    return props.data.title.toLowerCase().endsWith('.md');
  }
  return false;
});

const renderedMarkdownContent = computed(() => {
  const content = canSaveWorkspaceFile.value
    ? editorContent.value
    : (props.data?.content || '');
  if (!content) return '';
  return renderMarkdown(content);
});

const resolveConversationId = () =>
  props.conversationId
  || localStorage.getItem('yovole_embed_conv_id')
  || localStorage.getItem('agent_debug_conv_id')
  || '';

const syncEditorFromData = () => {
  const raw = props.data?.content;
  const text = typeof raw === 'string' && !raw.startsWith('blob:') ? raw : '';
  editorContent.value = text;
  savedContent.value = text;
};

const saveWorkspaceFile = async () => {
  if (!props.data?.sourcePath || !canSaveWorkspaceFile.value || saving.value) return;
  saving.value = true;
  try {
    await saveWorkspaceFileContent({
      path: props.data.sourcePath,
      content: editorContent.value,
      conversationId: resolveConversationId(),
    });
    savedContent.value = editorContent.value;
    emit('content-saved', { path: props.data.sourcePath, content: editorContent.value });
    showToast('文件已保存', 'success');
  } catch (err: any) {
    const errMsg = err.response?.data?.detail || err.response?.data?.message || err.message || '保存失败';
    showToast(errMsg, 'error');
  } finally {
    saving.value = false;
  }
};

const resolveUrlPath = (val: string): string => {
  if (!val) return '';
  if (val.startsWith('http://') || val.startsWith('https://') || val.startsWith('data:') || val.startsWith('quick:') || val.startsWith('canvas:')) {
    return val;
  }
  const publicUploadUrl = resolvePublicUploadsPreviewUrl(val);
  if (publicUploadUrl) return publicUploadUrl;
  // 兼容绝对路径与相对物理路径，只要它不属于静态路由与API接口路由，均通过后端预览API拉取
  if (!val.startsWith('/static/') &&
      !val.startsWith('/api/') &&
      !val.startsWith('/assets/')) {
    const convId = resolveConversationId();
    const convParam = convId ? `&conversation_id=${encodeURIComponent(convId)}` : "";
    return `/api/v1/chat/fs/preview?path=${encodeURIComponent(val)}${convParam}`;
  }
  return val;
};

const resolvedContent = computed(() => {
  const data = props.data;
  if (!data?.content && !canSaveWorkspaceFile.value) return '';
  const val = canSaveWorkspaceFile.value && isHtmlContent.value
    ? editorContent.value
    : (data?.content || '');

  if (isHtmlContent.value) {
    // 只要识别为可预览的 HTML 语法，就深度重写其内部的所有物理资源绝对路径引用
    return val.replace(/(src|href)=["']([^"']*)["']/gi, (_match, attr, pathVal) => {
      const newVal = resolveUrlPath(pathVal);
      return `${attr}="${newVal}"`;
    });
  }

  if (data && (data.type === 'image' || data.type === 'pdf' || data.type === 'csv')) {
    return resolveUrlPath(val);
  }

  return val;
});

// ==========================================
// 4. Watchers & Lifecycles
// ==========================================
watch(() => props.data, () => {
  resetTransform();
  syncEditorFromData();
  if (props.data?.type === 'csv') {
    csvSearchQuery.value = '';
    csvViewMode.value = 'table';
    loadCSVData();
  }

  // 自动根据类型与内容初始化当前激活 Tab
  if (props.data) {
    if (props.data.type === 'html' || isMarkdownContent.value) {
      activeTab.value = 'preview';
    } else {
      activeTab.value = 'code';
    }
  }
}, { immediate: true });

// ==========================================
// 5. Diff & Analysis Logic (Compare Mode)
// ==========================================
const diffSegments = computed(() => {
  if (!props.data || props.data.type !== 'compare') return [];
  const left = props.data.content || '';
  const right = props.data.compareContent || '';

  const leftLines = left.split('\n');
  const rightLines = right.split('\n');

  const matrix: number[][] = Array(leftLines.length + 1)
    .fill(null)
    .map(() => Array(rightLines.length + 1).fill(0));

  for (let i = 1; i <= leftLines.length; i++) {
    for (let j = 1; j <= rightLines.length; j++) {
      if (leftLines[i - 1] === rightLines[j - 1]) {
        matrix[i]![j] = (matrix[i - 1]?.[j - 1] ?? 0) + 1;
      } else {
        matrix[i]![j] = Math.max(matrix[i - 1]?.[j] ?? 0, matrix[i]?.[j - 1] ?? 0);
      }
    }
  }

  let i = leftLines.length;
  let j = rightLines.length;
  const result: { type: 'equal' | 'delete' | 'insert'; leftLineNum?: number; rightLineNum?: number; leftVal?: string; rightVal?: string }[] = [];

  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && leftLines[i - 1] === rightLines[j - 1]) {
      result.unshift({
        type: 'equal',
        leftLineNum: i,
        rightLineNum: j,
        leftVal: leftLines[i - 1],
        rightVal: rightLines[j - 1]
      });
      i--;
      j--;
    } else if (j > 0 && (i === 0 || (matrix[i]?.[j - 1] ?? 0) >= (matrix[i - 1]?.[j] ?? 0))) {
      result.unshift({
        type: 'insert',
        rightLineNum: j,
        rightVal: rightLines[j - 1]
      });
      j--;
    } else {
      result.unshift({
        type: 'delete',
        leftLineNum: i,
        leftVal: leftLines[i - 1]
      });
      i--;
    }
  }
  return result;
});

const analyzeDiff = () => {
  if (!props.data) return;
  const leftTitle = props.data.title || '原文件';
  const rightTitle = props.data.compareTitle || '对比文件';
  const question = `请深度对比分析并总结这两个文件的核心差异：\n1. 【${leftTitle}】\n2. 【${rightTitle}】\n\n请列出关键的新增、删除与变更点。`;
  emit('analyze-diff', question);
  handleClose();
};

onMounted(() => {
  mobileMq = window.matchMedia('(max-width: 639px)');
  syncMobile();
  mobileMq.addEventListener('change', syncMobile);
});

onUnmounted(() => {
  mobileMq?.removeEventListener('change', syncMobile);
});

const slideTransitionName = computed(() =>
  props.overlay || props.dockSide === 'left' ? 'slide-left' : 'slide',
);

const useBodyTeleport = computed(() =>
  !props.overlay || isFullscreen.value || isMobile.value,
);

const panelFrameClass = computed(() => {
  if (isFullscreen.value || (props.overlay && isMobile.value)) {
    return 'fixed inset-0 z-[260] w-full max-w-none pointer-events-auto border-0';
  }
  if (props.overlay) {
    return 'absolute inset-0 z-50 w-full max-w-none pointer-events-auto border-0';
  }
  if (props.dockSide === 'left') {
    return 'fixed left-0 right-auto top-0 bottom-0 z-[118] w-full sm:w-[min(28rem,92%)] sm:max-w-[28rem] border-r border-l-0';
  }
  return 'fixed right-0 left-0 sm:left-auto top-0 bottom-0 z-[118] w-full sm:w-[80%] sm:max-w-[520px] border-l border-r-0';
});

const overlayBackdropClass = computed(() =>
  isFullscreen.value || isMobile.value
    ? 'fixed inset-0 z-[259]'
    : 'absolute inset-0 z-40',
);
</script>

<template>
  <teleport to="body" :disabled="!useBodyTeleport">
    <Transition :name="slideTransitionName">
      <div
        v-if="visible && overlay"
        class="pointer-events-none"
        :class="overlayBackdropClass"
        aria-hidden="true"
      >
        <div class="absolute inset-0 bg-gray-900/5 dark:bg-black/15" />
      </div>
    </Transition>
    <Transition :name="slideTransitionName">
      <div
        v-if="visible"
        class="bg-white/95 dark:bg-gray-800/95 backdrop-blur-md shadow-2xl flex flex-col transition-all duration-300 border-gray-200 dark:border-gray-700"
        :class="panelFrameClass"
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
              data?.type === 'compare' ? 'bg-purple-100 dark:bg-purple-900/30 text-purple-600 dark:text-purple-400' :
              'bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400'
            "
          >
            {{
              data?.type === 'html' ? 'Application' :
              data?.type === 'image' ? 'Image' :
              data?.type === 'pdf' ? 'PDF' :
              data?.type === 'csv' ? 'CSV Table' :
              data?.type === 'mermaid' ? 'Diagram' :
              data?.type === 'compare' ? 'File Diff' :
              'Code'
            }}
          </span>
        </div>
        <div class="flex items-center space-x-2">
          <!-- AI Analyze Button (Only show in compare mode) -->
          <button
            v-if="data?.type === 'compare'"
            @click="analyzeDiff"
            class="px-2.5 py-1.5 text-[10px] font-bold text-white bg-gradient-to-r from-indigo-500 to-blue-600 hover:from-indigo-600 hover:to-blue-700 active:scale-95 rounded-lg shadow-xs transition-all flex items-center space-x-1"
            title="AI 一键智能分析差异"
          >
            <span>💡</span>
            <span>AI 分析差异</span>
          </button>

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

      <!-- HTML / Markdown Preview/Code Tabs Selector -->
      <div v-if="isHtmlContent || isMarkdownContent" class="px-4 py-2 border-b border-gray-100/50 dark:border-gray-700/50 bg-slate-50/50 dark:bg-gray-900/10 flex-shrink-0 flex items-center justify-center gap-2">
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
        <button
          v-if="canSaveWorkspaceFile && activeTab === 'code'"
          type="button"
          class="px-3 py-1.5 text-[11px] font-bold rounded-lg transition-all whitespace-nowrap"
          :class="isDirty
            ? 'text-white bg-primary hover:bg-primary/90 shadow-sm'
            : 'text-gray-400 bg-gray-100 dark:bg-gray-800 cursor-default'"
          :disabled="saving || !isDirty"
          @click="saveWorkspaceFile"
        >
          {{ saving ? '保存中...' : (isDirty ? '保存' : '已保存') }}
        </button>
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
          <div v-else class="w-full font-mono text-xs overflow-x-auto bg-white dark:bg-gray-900 rounded-xl border border-gray-100 dark:border-gray-850 shadow-inner flex leading-relaxed select-text min-h-[320px]">
            <template v-if="isCodeEditing">
              <div class="text-gray-300 dark:text-gray-600 text-right pr-3 py-4 select-none border-r border-gray-100 dark:border-gray-800 flex-shrink-0">
                <div v-for="num in lineCount" :key="num" class="h-5 leading-5">{{ num }}</div>
              </div>
              <textarea
                v-model="editorContent"
                class="flex-1 min-h-[300px] p-4 pl-3 bg-transparent outline-none resize-y text-gray-800 dark:text-gray-100 leading-5 custom-scrollbar"
                spellcheck="false"
              />
            </template>
            <template v-else>
              <div class="text-gray-300 dark:text-gray-600 text-right pr-4 py-4 select-none border-r border-gray-100 dark:border-gray-800 flex-shrink-0">
                <div v-for="num in lineCount" :key="num" class="h-5">{{ num }}</div>
              </div>
              <pre class="pl-4 flex-1 overflow-x-auto custom-scrollbar select-text py-4"><code class="hljs block whitespace-pre" v-html="highlightedCode"></code></pre>
            </template>
          </div>
        </template>

        <!-- Markdown Render Block / Code Switchable -->
        <template v-else-if="isMarkdownContent">
          <!-- Markdown Preview html -->
          <div v-if="activeTab === 'preview'" class="w-full h-full bg-white dark:bg-gray-900 rounded-xl p-6 border border-gray-100 dark:border-gray-850 shadow-inner select-text markdown-body" v-html="renderedMarkdownContent">
          </div>
          <!-- Markdown Source Code -->
          <div v-else class="w-full font-mono text-xs overflow-x-auto bg-white dark:bg-gray-900 rounded-xl border border-gray-100 dark:border-gray-850 shadow-inner flex leading-relaxed select-text min-h-[320px]">
            <template v-if="isCodeEditing">
              <div class="text-gray-300 dark:text-gray-600 text-right pr-3 py-4 select-none border-r border-gray-100 dark:border-gray-800 flex-shrink-0">
                <div v-for="num in lineCount" :key="num" class="h-5 leading-5">{{ num }}</div>
              </div>
              <textarea
                v-model="editorContent"
                class="flex-1 min-h-[300px] p-4 pl-3 bg-transparent outline-none resize-y text-gray-800 dark:text-gray-100 leading-5 custom-scrollbar"
                spellcheck="false"
              />
            </template>
            <template v-else>
              <div class="text-gray-300 dark:text-gray-600 text-right pr-4 py-4 select-none border-r border-gray-100 dark:border-gray-800 flex-shrink-0">
                <div v-for="num in lineCount" :key="num" class="h-5">{{ num }}</div>
              </div>
              <pre class="pl-4 flex-1 overflow-x-auto custom-scrollbar select-text py-4"><code class="hljs block whitespace-pre" v-html="highlightedCode"></code></pre>
            </template>
          </div>
        </template>

        <!-- General Code Block (Not HTML) -->
        <template v-else-if="data?.type === 'code'">
          <div v-if="canSaveWorkspaceFile" class="mb-2 flex justify-end">
            <button
              type="button"
              class="px-3 py-1.5 text-[11px] font-bold rounded-lg transition-all"
              :class="isDirty
                ? 'text-white bg-primary hover:bg-primary/90 shadow-sm'
                : 'text-gray-400 bg-gray-100 dark:bg-gray-800 cursor-default'"
              :disabled="saving || !isDirty"
              @click="saveWorkspaceFile"
            >
              {{ saving ? '保存中...' : (isDirty ? '保存' : '已保存') }}
            </button>
          </div>
          <div class="w-full font-mono text-xs overflow-x-auto bg-white dark:bg-gray-900 rounded-xl border border-gray-100 dark:border-gray-850 shadow-inner flex leading-relaxed select-text min-h-[320px]">
            <template v-if="isCodeEditing">
              <div class="text-gray-300 dark:text-gray-600 text-right pr-3 py-4 select-none border-r border-gray-100 dark:border-gray-800 flex-shrink-0">
                <div v-for="num in lineCount" :key="num" class="h-5 leading-5">{{ num }}</div>
              </div>
              <textarea
                v-model="editorContent"
                class="flex-1 min-h-[300px] p-4 pl-3 bg-transparent outline-none resize-y text-gray-800 dark:text-gray-100 leading-5 custom-scrollbar"
                spellcheck="false"
              />
            </template>
            <template v-else>
              <div class="text-gray-300 dark:text-gray-600 text-right pr-4 py-4 select-none border-r border-gray-100 dark:border-gray-800 flex-shrink-0">
                <div v-for="num in lineCount" :key="num" class="h-5">{{ num }}</div>
              </div>
              <pre class="pl-4 flex-1 overflow-x-auto custom-scrollbar select-text py-4"><code class="hljs block whitespace-pre" v-html="highlightedCode"></code></pre>
            </template>
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
          <!-- CSV View Mode Tabs -->
          <div v-if="!csvLoading && !csvError" class="mb-4 flex justify-center flex-shrink-0">
            <div class="bg-gray-150/80 dark:bg-gray-900 p-0.5 rounded-lg flex space-x-1 w-full max-w-[240px] border border-gray-200/20 shadow-inner">
              <button
                @click="csvViewMode = 'table'"
                class="flex-1 py-1 text-[11px] font-bold rounded-md transition-all duration-200 flex items-center justify-center space-x-1"
                :class="csvViewMode === 'table'
                  ? 'bg-white dark:bg-gray-800 text-gray-800 dark:text-white shadow-xs border border-gray-200/10'
                  : 'text-gray-400 hover:text-gray-600 dark:hover:text-gray-300'"
              >
                <span>📊</span>
                <span>原始表格</span>
              </button>
              <button
                @click="csvViewMode = 'pivot'"
                class="flex-1 py-1 text-[11px] font-bold rounded-md transition-all duration-200 flex items-center justify-center space-x-1"
                :class="csvViewMode === 'pivot'
                  ? 'bg-white dark:bg-gray-800 text-gray-800 dark:text-white shadow-xs border border-gray-200/10'
                  : 'text-gray-400 hover:text-gray-600 dark:hover:text-gray-300'"
              >
                <span>💡</span>
                <span>数据透视</span>
              </button>
            </div>
          </div>

          <div class="w-full h-full flex flex-col min-h-0">
            <!-- Search bar -->
            <div v-if="csvViewMode === 'table' && !csvLoading && !csvError" class="mb-3 flex-shrink-0">
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

            <template v-else>
              <!-- Raw Table View -->
              <div v-if="csvViewMode === 'table'" class="flex-grow overflow-auto border border-gray-200 dark:border-gray-750 rounded-xl bg-white dark:bg-gray-900/50 shadow-inner custom-scrollbar min-h-[360px]">
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

              <!-- Pivot View -->
              <div v-else-if="csvViewMode === 'pivot'" class="flex-grow overflow-hidden rounded-xl border border-gray-200 dark:border-gray-750 bg-white dark:bg-gray-900 shadow-inner">
                <PivotTable
                  :data="pivotRows"
                  :fields="pivotFields"
                />
              </div>
            </template>
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

        <!-- Compare Viewer (Split View Diff) -->
        <template v-else-if="data?.type === 'compare'">
          <div class="flex flex-col h-full min-h-[500px] bg-white dark:bg-gray-950 rounded-xl border border-gray-200 dark:border-gray-800 overflow-hidden shadow-inner select-text">
            <!-- 对比头部文件名 -->
            <div class="grid grid-cols-2 divide-x divide-gray-200 dark:divide-gray-800 bg-gray-50 dark:bg-gray-900 border-b border-gray-200 dark:border-gray-800 text-[11px] font-bold text-gray-500 dark:text-gray-400 select-none">
              <div class="px-4 py-2.5 truncate flex items-center space-x-1">
                <span class="text-red-500">◀</span>
                <span>{{ data?.title || '原文件' }}</span>
              </div>
              <div class="px-4 py-2.5 truncate flex items-center space-x-1">
                <span class="text-green-500">▶</span>
                <span>{{ data?.compareTitle || '对比文件' }}</span>
              </div>
            </div>

            <!-- 对比行展示区域 -->
            <div class="flex-1 overflow-auto font-mono text-[11px] leading-relaxed custom-scrollbar divide-y divide-gray-100/30 dark:divide-gray-800/30">
              <div v-for="(seg, idx) in diffSegments" :key="idx" class="grid grid-cols-2 divide-x divide-gray-200 dark:divide-gray-880 min-w-[700px]">
                <!-- 左侧行 -->
                <div
                  class="flex items-stretch min-h-[22px]"
                  :class="{
                    'bg-red-50/40 dark:bg-red-950/15 text-red-700 dark:text-red-300': seg.type === 'delete',
                    'bg-gray-50/20 dark:bg-gray-900/10 text-gray-400 dark:text-gray-600 empty-line-placeholder': seg.type === 'insert'
                  }"
                >
                  <!-- 行号 -->
                  <div class="w-12 text-right pr-2 text-[10px] text-gray-400 dark:text-gray-500 select-none border-r border-gray-100 dark:border-gray-800/80 py-0.5 font-sans flex-shrink-0">
                    {{ seg.type !== 'insert' ? seg.leftLineNum : '' }}
                  </div>
                  <!-- 差异标志 -->
                  <div class="w-5 text-center text-red-500 dark:text-red-400 font-bold select-none py-0.5 text-[10px] flex-shrink-0">
                    {{ seg.type === 'delete' ? '-' : '' }}
                  </div>
                  <!-- 内容 -->
                  <pre class="flex-1 px-2 py-0.5 overflow-x-auto whitespace-pre custom-scrollbar select-text font-mono text-[11px]">{{ seg.type !== 'insert' ? seg.leftVal : '' }}</pre>
                </div>

                <!-- 右侧行 -->
                <div
                  class="flex items-stretch min-h-[22px]"
                  :class="{
                    'bg-green-50/40 dark:bg-green-950/15 text-green-700 dark:text-green-300': seg.type === 'insert',
                    'bg-gray-50/20 dark:bg-gray-900/10 text-gray-400 dark:text-gray-600 empty-line-placeholder': seg.type === 'delete'
                  }"
                >
                  <!-- 行号 -->
                  <div class="w-12 text-right pr-2 text-[10px] text-gray-400 dark:text-gray-500 select-none border-r border-gray-100 dark:border-gray-800/80 py-0.5 font-sans flex-shrink-0">
                    {{ seg.type !== 'delete' ? seg.rightLineNum : '' }}
                  </div>
                  <!-- 差异标志 -->
                  <div class="w-5 text-center text-green-500 dark:text-green-400 font-bold select-none py-0.5 text-[10px] flex-shrink-0">
                    {{ seg.type === 'insert' ? '+' : '' }}
                  </div>
                  <!-- 内容 -->
                  <pre class="flex-1 px-2 py-0.5 overflow-x-auto whitespace-pre custom-scrollbar select-text font-mono text-[11px]">{{ seg.type !== 'delete' ? seg.rightVal : '' }}</pre>
                </div>
              </div>
            </div>
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

.slide-left-enter-active,
.slide-left-leave-active {
  transition: transform 0.3s cubic-bezier(0.16, 1, 0.3, 1), opacity 0.3s ease;
}
.slide-left-enter-from,
.slide-left-leave-to {
  transform: translateX(-100%);
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

.empty-line-placeholder {
  background-image: repeating-linear-gradient(
    -45deg,
    rgba(156, 163, 175, 0.08) 0px,
    rgba(156, 163, 175, 0.08) 2px,
    transparent 2px,
    transparent 8px
  );
}
.dark .empty-line-placeholder {
  background-image: repeating-linear-gradient(
    -45deg,
    rgba(156, 163, 175, 0.05) 0px,
    rgba(156, 163, 175, 0.05) 2px,
    transparent 2px,
    transparent 8px
  );
}
</style>
