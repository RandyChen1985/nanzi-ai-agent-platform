<script setup lang="ts">
import { ref, reactive, nextTick, computed, watch, onMounted, onUnmounted } from "vue";
import MentionList from "@/components/agent/MentionList.vue";
import AttachmentImageThumb from "@/components/embed/AttachmentImageThumb.vue";
import { isImageAttachment } from "@/utils/attachmentImages";

const props = defineProps<{
  modelValue: string;
  isProcessing: boolean;
  showShortcuts: boolean;
  slashCommands: any[];
  allowedAgents: any[];
  currentUser: any;
  windowWidth: number;
}>();

const emit = defineEmits<{
  (e: 'update:modelValue', val: string): void;
  (e: 'send'): void;
  (e: 'stop'): void;
  (e: 'toggle-shortcuts'): void;
  (e: 'open-command-manager'): void;
  (e: 'upload-image'): void;
  (e: 'edit-command', cmd: any): void;
  (e: 'delete-command', cmd: any, event: Event): void;
  (e: 'switch-mode', agent: any): void;
  (e: 'drag-start', event: DragEvent, index: number): void;
  (e: 'drop-cmd', event: DragEvent, index: number): void;
  (e: 'reorder-commands', data: any[]): void;
  (e: 'select-skill'): void;
  (e: 'select-knowledge-base'): void;
  (e: 'select-local-fs'): void;
}>();

const inputRef = ref<HTMLTextAreaElement | null>(null);
const isComposing = ref(false);
const showMentionList = ref(false);
const mentionKeyword = ref("");
const mentionPosition = reactive({ top: 0, left: 0 });
const showCommandMenu = ref(false);
const activeCommandIndex = ref(0);
const mentionListRef = ref<any>(null);
const isDrawerExpanded = ref(false);

const handleCompositionStart = () => {
  isComposing.value = true;
};

const handleCompositionEnd = () => {
  // Delay setting isComposing to false to allow the last keydown (like Enter) to be handled correctly
  setTimeout(() => {
    isComposing.value = false;
  }, 100);
};

const filteredCommands = computed(() => {
  if (!props.modelValue.startsWith('/')) return props.slashCommands;
  const query = props.modelValue.slice(1).toLowerCase();
  if (!query) return props.slashCommands;
  return props.slashCommands.filter(cmd => (cmd.command?.toLowerCase().includes(query)) || (cmd.label?.toLowerCase().includes(query)));
});

const filteredUserCommands = computed(() => filteredCommands.value.filter(c => !String(c.id).startsWith('sys_')));
const filteredSystemCommands = computed(() => filteredCommands.value.filter(c => String(c.id).startsWith('sys_')));

watch(() => filteredCommands.value, () => {
  activeCommandIndex.value = 0;
});

let draggedItem: any = null;
const handleDragStart = (e: DragEvent, cmd: any, type: string) => {
    if (type !== 'user') return;
    draggedItem = cmd;
    if (e.dataTransfer) {
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/plain', String(cmd.id));
    }
};

const handleDrop = (_e: DragEvent, targetCmd: any, type: string) => {
    if (type !== 'user' || !draggedItem || draggedItem.id === targetCmd.id) return;
    const items = [...props.slashCommands.filter(c => !String(c.id).startsWith('sys_'))];
    const fromIndex = items.findIndex(i => i.id === draggedItem.id);
    const toIndex = items.findIndex(i => i.id === targetCmd.id);
    if (fromIndex !== -1 && toIndex !== -1) {
        items.splice(fromIndex, 1);
        items.splice(toIndex, 0, draggedItem);
        const reorderData = items.map((item, index) => ({ id: item.id, sort_order: (index + 1) * 10 }));
        emit('reorder-commands', reorderData);
    }
    draggedItem = null;
};

watch(() => props.modelValue, (val) => { if (!val && inputRef.value) inputRef.value.style.height = "auto"; });

const handleInput = (e: Event) => {
  emit('update:modelValue', (e.target as HTMLTextAreaElement).value);
  const target = e.target as HTMLTextAreaElement;
  const val = target.value;
  const cursor = target.selectionStart;
  if (inputRef.value) {
    inputRef.value.style.height = "auto";
    inputRef.value.style.height = Math.min(inputRef.value.scrollHeight, 120) + "px";
  }
  // Don't show command menu during composition
  if (val.startsWith("/") && props.windowWidth < 640 && !isComposing.value) {
    showCommandMenu.value = true;
    activeCommandIndex.value = 0;
  } else {
    showCommandMenu.value = false;
  }
  const atMatch = val.slice(0, cursor).match(/[@＠]([^\s@＠]*)$/);
  if (atMatch) {
    const atIndex = val.slice(0, cursor).lastIndexOf(atMatch[0][0] || '@');
    const isStartOfWord = atIndex === 0 || val[atIndex - 1] === ' ' || val[atIndex - 1] === '\n';
    const query = atMatch[1] || '';
    if (isStartOfWord && props.allowedAgents.length > 0) {
        mentionKeyword.value = query;
        showMentionList.value = true;
        const rect = target.getBoundingClientRect();
        mentionPosition.top = rect.top - 220; 
        mentionPosition.left = rect.left + 20;
        return;
    }
  }
  showMentionList.value = false;
};

const handleKeydown = (e: KeyboardEvent) => {
  if (e.isComposing || isComposing.value) return;
  if (showMentionList.value && mentionListRef.value && mentionListRef.value.handleKeydown(e)) return;
  if (showCommandMenu.value) {
    if (e.key === "ArrowUp") { e.preventDefault(); activeCommandIndex.value = (activeCommandIndex.value - 1 + filteredCommands.value.length) % filteredCommands.value.length; return; }
    if (e.key === "ArrowDown") { e.preventDefault(); activeCommandIndex.value = (activeCommandIndex.value + 1) % filteredCommands.value.length; return; }
    if (e.key === "Enter") { e.preventDefault(); selectCommand(filteredCommands.value[activeCommandIndex.value]); return; }
    if (e.key === "Escape") { showCommandMenu.value = false; return; }
  }
  if (e.key === "Enter" && !e.shiftKey) {
    if (!canSend.value) return;
    e.preventDefault();
    emit('send');
  }
};

const selectCommand = (cmd: any) => { emit('update:modelValue', cmd.command); showCommandMenu.value = false; emit('send'); };

const handleMentionSelect = (agent: any) => {
  const target = inputRef.value;
  if (!target) return;
  const val = props.modelValue;
  const cursor = target.selectionStart;
  const lastAt = val.lastIndexOf('@', cursor - 1);
  if (lastAt !== -1) {
      const before = val.slice(0, lastAt);
      const after = val.slice(cursor);
      emit('update:modelValue', before + after);
      nextTick(() => { target.selectionStart = target.selectionEnd = before.length; target.focus(); });
      emit('switch-mode', agent);
  }
  showMentionList.value = false;
};

const handleShortcutClick = (cmd: any) => {
    if (!cmd) return;
    emit('update:modelValue', cmd.command);
    emit('send');
};

import axios from "@/utils/axios";

// 附件上传状态
const uploadedFiles = ref<any[]>([]);

const canSend = computed(
  () => !!props.modelValue.trim() || uploadedFiles.value.length > 0,
);

const plusMenuContainerRef = ref<HTMLElement | null>(null);

const handleGlobalClick = (event: MouseEvent) => {
  if (showPlusMenu.value && plusMenuContainerRef.value && !plusMenuContainerRef.value.contains(event.target as Node)) {
    showPlusMenu.value = false;
  }
};

const handleGlobalKeydown = (event: KeyboardEvent) => {
  if (event.key === 'Escape') {
    showPlusMenu.value = false;
  }
};

onMounted(() => {
  document.addEventListener('click', handleGlobalClick);
  document.addEventListener('keydown', handleGlobalKeydown);
});

onUnmounted(() => {
  document.removeEventListener('click', handleGlobalClick);
  document.removeEventListener('keydown', handleGlobalKeydown);
});
const showPlusMenu = ref(false);
const isUploading = ref(false);
const fileInputRef = ref<HTMLInputElement | null>(null);

const togglePlusMenu = () => {
  showPlusMenu.value = !showPlusMenu.value;
};

const triggerFileInput = () => {
  showPlusMenu.value = false;
  fileInputRef.value?.click();
};

const isImage = isImageAttachment;

const openImagePreview = (url: string) => {
  window.open(url, "_blank");
};

const formatSize = (bytes: number) => {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
};

const removeFile = (index: number) => {
  uploadedFiles.value.splice(index, 1);
};

// 核心文件上传逻辑
const uploadSingleFile = async (file: File) => {
  if (file.size > 20 * 1024 * 1024) {
    alert("文件大小不能超过 20MB");
    return;
  }
  const name = file.name;
  const ext = '.' + name.split('.').pop()?.toLowerCase();
  const forbiddenExts = ['.exe', '.bat', '.sh', '.cmd', '.msi', '.php', '.js', '.html'];
  if (forbiddenExts.includes(ext)) {
    alert("暂不支持上传该类型的危险脚本文件");
    return;
  }

  isUploading.value = true;
  const formData = new FormData();
  formData.append("file", file);

  try {
    const res = await axios.post("/api/v1/chat/upload", formData, {
      headers: {
        "Content-Type": "multipart/form-data",
      },
    });
    if (res.data && res.data.data) {
      uploadedFiles.value.push(res.data.data);
    } else {
      throw new Error("上传失败");
    }
  } catch (error: any) {
    console.error("Upload error:", error);
    alert(error.response?.data?.message || error.message || "上传文件时出错，请重试");
  } finally {
    isUploading.value = false;
  }
};

const handleFileChange = async (e: Event) => {
  const target = e.target as HTMLInputElement;
  if (!target.files) return;
  const filesArray = Array.from(target.files);
  for (const file of filesArray) {
    await uploadSingleFile(file);
  }
  target.value = ''; // 清空 input 避免无法重复选择同一文件
};

// 拖拽与粘贴
const handlePaste = async (e: ClipboardEvent) => {
  const items = e.clipboardData?.items;
  if (!items) return;
  for (const item of Array.from(items)) {
    if (item.kind === 'file') {
      const file = item.getAsFile();
      if (file) {
        e.preventDefault();
        await uploadSingleFile(file);
      }
    }
  }
};

const handleDropFile = async (e: DragEvent) => {
  e.preventDefault();
  const files = e.dataTransfer?.files;
  if (!files) return;
  for (const file of Array.from(files)) {
    await uploadSingleFile(file);
  }
};

// 暴露属性给父组件
defineExpose({
  uploadedFiles
});
</script>

<template>
    <div class="flex-shrink-0 bg-white dark:bg-gray-900 border-t border-gray-100 dark:border-gray-800 flex flex-col relative z-20">
      <slot name="banner"></slot>

      <div class="p-3 pb-2">
        <!-- Shortcut Bar -->
        <div v-if="showShortcuts" class="flex items-center space-x-2 mb-2 px-1 relative h-8">
            <!-- 1. Left Toggle Button (Visible on all devices now) -->
            <div @click="emit('toggle-shortcuts')" class="flex items-center space-x-1 cursor-pointer select-none group flex-shrink-0 bg-white dark:bg-gray-900 pr-2 z-10">
                <span class="text-[10px] font-black text-gray-400 group-hover:text-primary transition-colors tracking-tighter">⚡️ 快捷指令</span>
                <svg class="w-3 h-3 text-gray-300 group-hover:text-primary transition-transform duration-200 rotate-180" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M19 9l-7 7-7-7" /></svg>
            </div>

            <!-- 2. Middle Content -->
            <div class="flex-1 min-w-0 relative">
                <transition enter-active-class="transition-all duration-300 ease-out" enter-from-class="opacity-0 -translate-y-2" enter-to-class="opacity-100 translate-y-0" leave-active-class="transition-all duration-200 ease-in" leave-to-class="opacity-0 -translate-y-2">
                    <div class="w-full">
                        <div v-if="!isDrawerExpanded" class="flex items-center space-x-2">
                            <div class="flex flex-1 items-center space-x-2 overflow-x-auto no-scrollbar scroll-smooth pr-12">
                                <template v-if="windowWidth < 640">
                                    <button @click="handleShortcutClick(filteredSystemCommands.find(c => c.id === 'sys_history'))" class="px-3 py-1 text-[10px] font-bold bg-gray-100 dark:bg-gray-800 text-gray-600 rounded-full whitespace-nowrap">🕒 历史</button>
                                    <button @click="handleShortcutClick(filteredSystemCommands.find(c => c.id === 'sys_clear'))" class="px-3 py-1 text-[10px] font-bold bg-gray-100 dark:bg-gray-800 text-gray-600 rounded-full whitespace-nowrap">💬 新会话</button>
                                </template>
                                <template v-else>
                                    <template v-for="cmd in filteredSystemCommands" :key="'row-sys-'+cmd.id">
                                        <button @click="handleShortcutClick(cmd)" class="px-2.5 py-1 text-[10px] font-bold bg-gray-100/80 dark:bg-gray-800 text-gray-500 rounded-full whitespace-nowrap hover:bg-gray-200 transition-colors">{{ cmd.label }}</button>
                                    </template>
                                    <div class="w-px h-3 bg-gray-200 dark:bg-gray-700 flex-shrink-0"></div>
                                    <template v-for="cmd in filteredUserCommands" :key="'row-user-'+cmd.id">
                                        <button @click="handleShortcutClick(cmd)" class="px-2.5 py-1 text-[10px] font-bold bg-blue-50 dark:bg-blue-900/30 text-blue-600 dark:text-blue-300 border border-blue-100/50 dark:border-blue-800 rounded-full whitespace-nowrap hover:bg-blue-100 transition-colors">{{ cmd.label }}</button>
                                    </template>
                                </template>
                            </div>
                            <button @click="isDrawerExpanded = true" class="absolute right-0 top-0 bottom-0 bg-gradient-to-l from-white via-white dark:from-gray-900 dark:via-gray-900 pl-6 pr-1 flex items-center text-[10px] font-black text-primary hover:opacity-80 transition-all z-10">
                                更多 <svg class="w-3 h-3 ml-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M19 9l-7 7-7-7" /></svg>
                            </button>
                        </div>

                        <!-- Bottom Drawer -->
                        <div v-else class="z-50" :class="windowWidth < 640 ? 'fixed inset-0 bg-black/40 backdrop-blur-sm flex flex-col justify-end p-0' : 'absolute bottom-full left-0 right-0 mb-3 animate-fade-in-up px-1'" @click.self="isDrawerExpanded = false">
                            <div class="bg-white/95 dark:bg-gray-800/95 backdrop-blur-xl border border-gray-200 dark:border-gray-700 p-4 shadow-2xl overflow-y-auto custom-scrollbar ring-1 ring-black/5" :class="windowWidth < 640 ? 'rounded-t-3xl max-h-[85vh] pb-12 animate-slide-up' : 'rounded-2xl max-h-[24rem]'" @click.stop>
                                <div class="flex items-center justify-between mb-6">
                                    <div class="flex items-center space-x-2">
                                        <span class="w-1.5 h-4 bg-primary rounded-full"></span>
                                        <span class="text-[11px] font-black text-gray-800 dark:text-gray-100 uppercase tracking-widest">指令库 · Commands</span>
                                    </div>
                                    <button @click="isDrawerExpanded = false" class="w-8 h-8 flex items-center justify-center rounded-full bg-gray-100 dark:bg-gray-700 text-gray-500 hover:bg-gray-200 transition-all">
                                        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M19 9l-7 7-7-7" /></svg>
                                    </button>
                                </div>
                                <div class="space-y-6">
                                    <div v-if="filteredUserCommands.length > 0">
                                        <div class="text-[10px] font-black text-blue-500 mb-3 px-1 flex items-center uppercase tracking-tighter">Mine · 我的常用</div>
                                        <div class="grid grid-cols-2 sm:grid-cols-3 gap-3">
                                            <div v-for="cmd in filteredUserCommands" :key="'grid-user-'+cmd.id" class="relative group/grid-item">
                                                <button draggable="true" @dragstart="handleDragStart($event, cmd, 'user')" @dragover.prevent @drop="handleDrop($event, cmd, 'user')" @click="handleShortcutClick(cmd); isDrawerExpanded = false;" class="w-full text-left p-3.5 rounded-2xl bg-gray-50 dark:bg-gray-900/50 border border-gray-100 dark:border-gray-800 hover:border-primary/30 hover:bg-white dark:hover:bg-gray-900 hover:shadow-md transition-all">
                                                    <div class="text-xs font-bold text-gray-800 dark:text-gray-200 mb-1 truncate">{{ cmd.label }}</div>
                                                    <div class="text-[9px] text-gray-400 truncate opacity-60 font-mono">{{ cmd.command }}</div>
                                                </button>
                                                <button v-if="currentUser && cmd.created_by === currentUser.user_name" @click.stop="$emit('delete-command', cmd, $event)" class="absolute -top-1.5 -right-1.5 w-6 h-6 bg-red-500 text-white rounded-full flex items-center justify-center shadow-lg z-10 hover:scale-110 active:scale-95" :class="windowWidth < 640 ? 'opacity-100' : 'opacity-0 group-hover/grid-item:opacity-100'"><svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M6 18L18 6M6 6l12 12" /></svg></button>
                                            </div>
                                        </div>
                                    </div>
                                    <div>
                                        <div class="text-[10px] font-black text-gray-400 mb-3 px-1 flex items-center uppercase tracking-tighter">System · 系统功能</div>
                                        <div class="grid grid-cols-2 sm:grid-cols-3 gap-3">
                                            <button v-for="cmd in filteredSystemCommands" :key="'grid-sys-'+cmd.id" @click="handleShortcutClick(cmd); isDrawerExpanded = false;" class="w-full text-left p-3.5 rounded-2xl bg-gray-50/50 dark:bg-gray-900/30 border border-transparent hover:bg-gray-100 dark:hover:bg-gray-800 transition-all">
                                                <div class="text-xs font-bold text-gray-600 dark:text-gray-400 mb-1 truncate">{{ cmd.label }}</div>
                                                <div class="text-[9px] text-gray-400/60 truncate font-mono">{{ cmd.command }}</div>
                                            </button>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </transition>
            </div>

            <!-- Add Button -->
            <button @click="emit('open-command-manager')" class="flex-shrink-0 p-1.5 text-gray-400 hover:text-primary transition-all rounded-full hover:bg-gray-100 dark:hover:bg-gray-800 group" title="新建快捷指令">
                <svg class="w-4 h-4 transform group-hover:rotate-90 transition-transform duration-300" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M12 4v16m8-8H4" /></svg>
            </button>
        </div>

        <!-- Attachments Preview Bar -->
        <div v-if="uploadedFiles.length > 0" class="flex flex-wrap gap-2 px-1 mb-2 max-h-36 overflow-y-auto no-scrollbar py-1">
            <div v-for="(file, idx) in uploadedFiles" :key="idx" class="relative flex items-center group bg-gray-100/80 dark:bg-gray-800/80 border border-gray-200/30 dark:border-gray-700/30 rounded-lg p-1.5 pr-8 max-w-[200px] transition-all hover:bg-white dark:hover:bg-gray-800 hover:shadow-sm">
                <!-- Image Preview -->
                <AttachmentImageThumb
                  v-if="isImage(file)"
                  :file="file"
                  clickable
                  class="mr-2"
                  @click="openImagePreview"
                />
                <!-- Knowledge Base Icon -->
                <div v-else-if="file.type === 'knowledge_base'" class="w-8 h-8 rounded bg-emerald-500/10 dark:bg-emerald-500/20 flex items-center justify-center text-emerald-500 text-sm flex-shrink-0 mr-2">
                    📚
                </div>
                <!-- Skill Icon -->
                <div v-else-if="file.type === 'skill'" class="w-8 h-8 rounded bg-amber-500/10 dark:bg-amber-500/20 flex items-center justify-center text-amber-500 text-sm flex-shrink-0 mr-2 font-mono">
                    ⚙️
                </div>
                <!-- Server File Icon -->
                <div v-else-if="file.type === 'local_file'" class="w-8 h-8 rounded bg-blue-500/10 dark:bg-blue-500/20 flex items-center justify-center text-blue-500 text-sm flex-shrink-0 mr-2">
                    💻
                </div>
                <!-- Server Dir Icon -->
                <div v-else-if="file.type === 'local_dir'" class="w-8 h-8 rounded bg-yellow-500/10 dark:bg-yellow-500/20 flex items-center justify-center text-yellow-500 text-sm flex-shrink-0 mr-2">
                    📁
                </div>
                <!-- File Icon -->
                <div v-else class="w-8 h-8 rounded bg-primary/10 dark:bg-primary/20 flex items-center justify-center text-primary text-sm flex-shrink-0 mr-2">
                    📄
                </div>
                <!-- Metadata -->
                <div class="flex-1 min-w-0 flex flex-col">
                    <span class="text-xs font-bold text-gray-700 dark:text-gray-200 truncate">{{ file.filename }}</span>
                    <span class="text-[9px] text-gray-400 font-mono">
                        {{ 
                          file.type === 'skill' ? '生态技能' : 
                          file.type === 'knowledge_base' ? '知识库' : 
                          file.type === 'local_file' ? (isImage(file) ? '服务器图片' : '服务器文件') :
                          file.type === 'local_dir' ? '服务器目录' :
                          formatSize(file.size) 
                        }}
                    </span>
                </div>
                <!-- Remove Button -->
                <button @click="removeFile(idx)" class="absolute right-1 top-1/2 -translate-y-1/2 w-5 h-5 flex items-center justify-center rounded-full bg-gray-200/50 hover:bg-red-500 hover:text-white dark:bg-gray-700/50 text-gray-500 dark:text-gray-400 opacity-0 group-hover:opacity-100 transition-all duration-150 focus:outline-none">
                    <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M6 18L18 6M6 6l12 12" /></svg>
                </button>
            </div>
            
            <!-- Uploading indicator -->
            <div v-if="isUploading" class="flex items-center space-x-2 bg-gray-100/50 dark:bg-gray-800/50 border border-dashed border-gray-300 dark:border-gray-700 rounded-lg px-3 py-1.5 max-w-[200px]">
                <div class="w-3.5 h-3.5 border-2 border-primary border-t-transparent rounded-full animate-spin"></div>
                <span class="text-[10px] text-gray-400 font-medium">正在上传...</span>
            </div>
        </div>

        <!-- Input Box -->
        <div @dragover.prevent @drop="handleDropFile" class="relative flex items-end bg-gray-50 dark:bg-gray-800 rounded-xl px-2 py-2 border border-gray-200 dark:border-gray-700 transition-all duration-500" :class="{ 'ring-2 ring-primary/20 border-primary/30 bg-white dark:bg-gray-900 shadow-lg shadow-primary/5': isProcessing }">
            <div v-if="isProcessing" class="absolute inset-0 rounded-xl opacity-40 pointer-events-none overflow-hidden">
                <div class="absolute inset-0 bg-gradient-to-r from-transparent via-primary/20 to-transparent w-[200%] animate-scan"></div>
            </div>
            
            <!-- Plus Button & Menu (Premium Glassmorphism Style) -->
            <div ref="plusMenuContainerRef" class="relative flex-shrink-0 z-30 mr-1 self-center">
                <button @click="togglePlusMenu" class="w-8 h-8 flex items-center justify-center rounded-lg text-gray-400 hover:text-primary hover:bg-gray-100 dark:hover:bg-gray-700 transition-all duration-200 focus:outline-none" :class="{ 'text-primary bg-gray-100 dark:bg-gray-700 rotate-45': showPlusMenu }">
                    <svg class="w-5 h-5 transition-transform duration-200" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M12 4v16m8-8H4" />
                    </svg>
                </button>
                
                <input type="file" multiple ref="fileInputRef" @change="handleFileChange" class="hidden" />

                <!-- Menu Dropdown -->
                <transition 
                  enter-active-class="transition ease-out duration-100" 
                  enter-from-class="transform opacity-0 scale-95" 
                  enter-to-class="transform opacity-100 scale-100" 
                  leave-active-class="transition ease-in duration-75" 
                  leave-from-class="transform opacity-100 scale-100" 
                  leave-to-class="transform opacity-0 scale-95"
                >
                    <div v-if="showPlusMenu" class="absolute bottom-full left-0 mb-2 w-52 bg-white/95 dark:bg-gray-800/95 backdrop-blur-md rounded-xl shadow-xl border border-gray-200/50 dark:border-gray-700/50 py-1.5 z-50 animate-fade-in-up">
                        <!-- Upload File (Active) -->
                        <button @click="triggerFileInput" class="w-full flex items-center space-x-3 px-3 py-2 text-sm text-gray-700 dark:text-gray-200 hover:bg-primary/10 dark:hover:bg-primary/20 hover:text-primary transition-all duration-150">
                            <span class="text-lg">📁</span>
                            <span class="font-medium text-left">上传本地文件</span>
                        </button>

                        <!-- Browse Server Files (Active) -->
                        <button @click="showPlusMenu = false; emit('select-local-fs');" class="w-full flex items-center space-x-3 px-3 py-2 text-sm text-gray-700 dark:text-gray-200 hover:bg-primary/10 dark:hover:bg-primary/20 hover:text-primary transition-all duration-150">
                            <span class="text-lg">💻</span>
                            <span class="font-medium text-left">浏览服务器文件</span>
                        </button>
                        
                        <!-- Knowledge Base -->
                        <button @click="showPlusMenu = false; emit('select-knowledge-base');" class="w-full flex items-center justify-between px-3 py-2 text-sm text-gray-700 dark:text-gray-200 hover:bg-primary/10 dark:hover:bg-primary/20 hover:text-primary transition-all duration-150">
                            <div class="flex items-center space-x-3">
                                <span class="text-lg">📚</span>
                                <span class="font-medium text-left">选择知识库</span>
                            </div>
                        </button>
                        
                        <!-- Skills (Active) -->
                        <button @click="showPlusMenu = false; emit('select-skill');" class="w-full flex items-center space-x-3 px-3 py-2 text-sm text-gray-700 dark:text-gray-200 hover:bg-primary/10 dark:hover:bg-primary/20 hover:text-primary transition-all duration-150">
                            <span class="text-lg">⚙️</span>
                            <span class="font-medium text-left">调用技能工作流</span>
                        </button>
                    </div>
                </transition>
            </div>
            
            <textarea ref="inputRef" :value="modelValue" @input="handleInput" @keydown="handleKeydown" @compositionstart="handleCompositionStart" @compositionend="handleCompositionEnd" @paste="handlePaste" rows="1" class="flex-1 bg-transparent border-none outline-none focus:ring-0 text-base sm:text-sm placeholder:text-sm py-2 px-2 resize-none max-h-32 text-gray-900 dark:text-gray-100 placeholder-gray-400 peer z-10 relative" :placeholder="windowWidth < 640 ? '输入消息，或 \'/\' 使用快捷指令...' : '输入消息...'"></textarea>
            <button @click="isProcessing ? emit('stop') : emit('send')" :disabled="!isProcessing && !canSend" class="flex-shrink-0 mb-1 ml-2 p-1.5 rounded-lg text-white hover:opacity-90 disabled:opacity-50 transition-all z-10 relative" :style="{ backgroundColor: 'var(--primary-color, #1677ff)' }">
                <svg v-if="isProcessing" class="w-4 h-4" fill="currentColor" viewBox="0 0 20 20"><rect x="5" y="5" width="10" height="10" /></svg>
                <svg v-else class="w-4 h-4 transform rotate-90" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" /></svg>
            </button>
        </div>
        <div class="mt-1 text-center"><span class="text-[10px] text-gray-300 dark:text-gray-600">Powered by Yunshu AI</span></div>
      </div>

      <div
        v-if="showCommandMenu && filteredCommands.length > 0"
        class="fixed z-[100] bg-white dark:bg-gray-800 rounded-xl shadow-2xl border border-gray-200 dark:border-gray-700 overflow-hidden flex flex-col animate-fade-in-up"
        :class="[
          'w-[calc(100vw-2rem)] sm:w-64'
        ]"
        :style="{ 
          bottom: '80px',
          left: windowWidth < 640 ? '1rem' : 'auto',
          right: windowWidth < 640 ? 'auto' : '1rem',
          maxHeight: windowWidth < 640 ? '40vh' : '18rem' 
        }"
      >
        <div class="px-3 py-2 bg-gray-50 dark:bg-gray-700 border-b border-gray-100 dark:border-gray-600 flex justify-between items-center">
          <div class="flex items-center space-x-2">
            <span class="text-[10px] font-black text-gray-400 dark:text-gray-400 uppercase tracking-widest">快捷指令库</span>
            <span class="px-1.5 py-0.5 bg-primary/10 text-primary text-[9px] font-bold rounded-md">{{ filteredCommands.length }} 匹配</span>
          </div>
        </div>
        
        <div class="overflow-y-auto custom-scrollbar p-1">
          <div
            v-for="(cmd, index) in filteredCommands"
            :key="cmd.id"
            @click="selectCommand(cmd)"
            class="flex items-center space-x-3 px-3 py-2 rounded-lg cursor-pointer transition-all"
            :class="index === activeCommandIndex ? 'bg-primary/10 dark:bg-primary/20 ring-1 ring-primary/20' : 'hover:bg-gray-50 dark:hover:bg-gray-700'"
          >
            <div class="flex-1 min-w-0">
              <div class="flex items-center space-x-2">
                <span class="text-sm font-bold text-gray-900 dark:text-gray-100 truncate" :class="index === activeCommandIndex ? 'text-primary' : ''">
                  {{ cmd.label }}
                </span>
                <span v-if="String(cmd.id).startsWith('sys_')" class="px-1 py-0.5 rounded text-[8px] bg-gray-100 text-gray-500 border border-gray-200 dark:bg-gray-700 dark:border-gray-600 dark:text-gray-400 font-black uppercase tracking-tighter">SYS</span>
              </div>
              <div class="text-[10px] text-gray-400 truncate font-mono opacity-70">
                {{ cmd.command }}
              </div>
            </div>
          </div>
        </div>
      </div>

      <MentionList ref="mentionListRef" :visible="showMentionList" :keyword="mentionKeyword" :agents="allowedAgents" :position="mentionPosition" @select="handleMentionSelect" @close="showMentionList = false" />
    </div>
</template>

<style scoped>
@keyframes scan { from { transform: translateX(-100%); } to { transform: translateX(100%); } }
.animate-scan { animation: scan 2s linear infinite; }
@keyframes slide-up { from { transform: translateY(100%); } to { transform: translateY(0); } }
.animate-slide-up { animation: slide-up 0.4s cubic-bezier(0.16, 1, 0.3, 1) forwards; }
.custom-scrollbar::-webkit-scrollbar { width: 4px; }
.custom-scrollbar::-webkit-scrollbar-track { background: transparent; }
.custom-scrollbar::-webkit-scrollbar-thumb { background-color: rgba(156, 163, 175, 0.5); border-radius: 2px; }
</style>
