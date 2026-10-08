<script setup lang="ts">
import { ref, computed, watch } from 'vue';
import type { AIAgent, AIAgentVersion } from '../../api/agent';
import { agentApi } from '../../api/agent';
import ConfirmModal from '../ConfirmModal.vue';
import AgentVersionDiffModal from './AgentVersionDiffModal.vue';
import { TrashIcon } from '@heroicons/vue/24/outline';
import {
  findPublishedAgentVersion,
  getAgentVersionDiffPair,
} from '../../utils/agentVersionDiff';
// We'll reuse the Toast logic from parent or implementing a simple one, 
// for now let's assume props or simple alert, or inject a notification service. 
// But to keep it standalone, let's allow events.

const props = defineProps<{
  agent: AIAgent | null;
  isOpen: boolean;
}>();

const emit = defineEmits<{
  (e: 'close'): void;
  (e: 'update'): void; // Trigger when something changes
  (e: 'edit-version', version: AIAgentVersion): void; // Request parent to open edit modal (since logic is complex)
  (e: 'create-version', baseVersion?: AIAgentVersion): void; // Request parent to open create modal, optionally with base version
  (e: 'publish-version', version: AIAgentVersion): void;
}>();

const versions = ref<AIAgentVersion[]>([]);
const loading = ref(false);

const activeVersions = computed(() => versions.value.filter((v) => v.status !== "ARCHIVED"));
const archivedVersions = computed(() => versions.value.filter((v) => v.status === "ARCHIVED"));
const showArchived = ref(false);
const diffVersion = ref<AIAgentVersion | null>(null);
const publishedVersion = computed(() => findPublishedAgentVersion(versions.value));

const openDiff = (version: AIAgentVersion) => {
  const pair = getAgentVersionDiffPair(versions.value, version.id);
  if (!pair) return;
  diffVersion.value = pair.sourceVersion;
};

const closeDiff = () => {
  diffVersion.value = null;
};

const loadVersions = async () => {
  if (!props.agent) return;
  loading.value = true;
  try {
    const res = await agentApi.listVersions(props.agent.id);
    versions.value = res.data || [];
  } catch (error) {
    console.error("Failed to load versions", error);
  } finally {
    loading.value = false;
  }
};

const publishVersion = async (version: AIAgentVersion) => {
  emit('publish-version', version);
};

// Expose refresh method
defineExpose({
    refresh: loadVersions
});

const deleteLoading = ref(false);
const showDeleteConfirm = ref(false);
const versionToDelete = ref<AIAgentVersion | null>(null);

const requestDeleteVersion = (version: AIAgentVersion) => {
    versionToDelete.value = version;
    showDeleteConfirm.value = true;
};

const confirmDeleteVersion = async () => {
    if (!props.agent || !versionToDelete.value) return;
    deleteLoading.value = true;
    try {
        await agentApi.deleteVersion(props.agent.id, versionToDelete.value.id);
        showDeleteConfirm.value = false;
        versionToDelete.value = null;
        loadVersions(); // Refresh list
    } catch (error) {
        console.error("Failed to delete version", error);
    } finally {
        deleteLoading.value = false;
    }
};

// Watch open state to load data
watch(() => props.isOpen, (newVal) => {
    if (newVal && props.agent) {
        loadVersions();
    } else if (!newVal) {
        closeDiff();
    }
});
</script>

<template>
  <!-- 弹层必须 Teleport 到 body：留在内容区内时，祖先一旦带上 transform（页面过渡的
       .page-enter-from 就是 translateY(4px)）会改写 fixed 的包含块，遮罩只盖住内容区，
       顶栏与左侧边栏都压不暗。层级同时抬到布局层之上（顶栏 z-30、移动端遮罩 z-40、侧边栏 z-50）。 -->
  <Teleport to="body">
    <div v-if="isOpen" class="fixed inset-0 overflow-hidden z-[100]">
      <div class="absolute inset-0 overflow-hidden">
        <!-- Overlay -->
        <div 
          class="absolute inset-0 bg-gray-500 bg-opacity-75 transition-opacity" 
          @click="emit('close')"
        ></div>

        <div class="fixed inset-y-0 right-0 pl-10 max-w-full flex">
          <div class="w-screen max-w-md transform transition-all ease-in-out duration-300 sm:duration-500">
            <div class="h-full flex flex-col bg-white shadow-xl overflow-y-scroll">
            
              <!-- Header -->
              <div class="px-4 py-6 bg-gray-50 border-b border-gray-200 sm:px-6">
                <div class="flex items-start justify-between">
                  <div>
                    <h2 class="text-lg font-medium text-gray-900">配置与发布</h2>
                    <p class="mt-1 text-sm text-gray-500" v-if="agent">
                       {{ agent.display_name }} ({{ agent.name }})
                    </p>
                  </div>
                  <div class="ml-3 h-7 flex items-center">
                    <button 
                      @click="emit('close')"
                      class="bg-white rounded-md text-gray-400 hover:text-gray-500 focus:outline-none"
                    >
                      <span class="sr-only">Close panel</span>
                      <svg class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
                      </svg>
                    </button>
                  </div>
                </div>
              </div>

              <!-- Content -->
              <div class="relative flex-1 px-4 py-6 sm:px-6">
                  <!-- RAGFlow Managed Mode Warning -->
                  <div v-if="agent?.engine_type === 'RAGFLOW'" class="bg-indigo-50 border border-indigo-100 rounded-lg p-4 mb-6">
                      <div class="flex">
                          <div class="flex-shrink-0">
                              <svg class="h-5 w-5 text-indigo-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                              </svg>
                          </div>
                          <div class="ml-3">
                              <h3 class="text-sm font-bold text-indigo-800">托管模式 (Managed Mode)</h3>
                              <div class="mt-1 text-xs text-indigo-700 leading-relaxed">
                                  此智能体由 RAGFlow 引擎驱动。其 Prompt、模型及知识库配置均在 RAGFlow 侧管理。本地版本仅用于历史记录，不影响实际对话。
                              </div>
                              <div class="mt-3">
                                  <button 
                                      @click="emit('close')"
                                      class="text-xs font-bold text-indigo-600 hover:text-indigo-500"
                                  >
                                      返回智能体设置 &rarr;
                                  </button>
                              </div>
                          </div>
                      </div>
                  </div>

                  <!-- Actions (Hidden for RAGFlow) -->
                  <div v-if="agent?.engine_type !== 'RAGFLOW'" class="flex justify-between items-center mb-6">
                      <div class="flex items-center">
                           <label class="flex items-center space-x-2 text-sm text-gray-500 cursor-pointer select-none">
                              <input type="checkbox" v-model="showArchived" class="form-checkbox h-4 w-4 text-primary rounded border-gray-300 focus:ring-primary" />
                              <span>显示归档版本</span>
                          </label>
                      </div>
                      <button 
                          v-if="agent?.is_editable !== false"
                          @click="emit('create-version')"
                          class="px-3 py-1.5 bg-primary text-white text-sm rounded-md shadow-sm hover:bg-primary-dark transition-colors"
                      >
                          + 新建版本
                      </button>
                  </div>

                  <div v-if="loading" class="text-center py-10 text-gray-400">
                      <svg class="animate-spin h-8 w-8 text-primary mx-auto mb-2" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                          <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                          <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                      </svg>
                      加载中...
                  </div>

                  <div v-else class="space-y-4">
                      <!-- Active List -->
                      <div v-for="v in activeVersions" :key="v.id" class="bg-white border rounded-lg p-4 shadow-sm hover:border-primary transition-colors relative">
                          <div class="flex justify-between items-start">
                               <!-- Left Content: Added flex-1 and min-w-0 to handle long text -->
                               <div class="flex-1 min-w-0">
                                  <div class="flex items-center space-x-2">
                                       <span class="text-lg font-bold text-gray-900">V{{ v.version_number }}</span>
                                       <span v-if="v.status === 'PUBLISHED'" class="px-2 py-0.5 rounded text-xs font-medium bg-green-100 text-green-800 border border-green-200">
                                           当前线上
                                       </span>
                                       <span v-else class="px-2 py-0.5 rounded text-xs font-medium bg-yellow-100 text-yellow-800 border border-yellow-200">
                                           草稿
                                       </span>
                                  </div>
                                  <div class="mt-1 text-xs text-gray-500">
                                      {{ new Date(v.created_at).toLocaleString() }}
                                  </div>
                                  <div class="mt-2 text-sm text-gray-700 font-mono bg-gray-50 p-2 rounded border border-gray-100 whitespace-pre-wrap overflow-y-auto max-h-32" v-if="v.comment">
                                      {{ v.comment }}
                                  </div>
                               </div>
                             
                               <!-- Right Actions: Added flex-shrink-0 and ml-4 to prevent squeezing -->
                               <div class="flex flex-col space-y-2 ml-4 flex-shrink-0">
                                   <button 
                                      @click="emit('edit-version', v)"
                                      class="text-xs text-primary hover:text-primary-dark font-medium"
                                   >
                                       {{ v.status === 'PUBLISHED' ? '查看' : '编辑' }}
                                   </button>
                                   <button
                                       v-if="publishedVersion && v.status !== 'PUBLISHED'"
                                       type="button"
                                       @click="openDiff(v)"
                                       class="text-xs text-blue-600 hover:text-blue-700 font-medium"
                                       title="与当前线上版本对比"
                                   >
                                       Diff
                                   </button>
                                   <button 
                                       v-if="agent?.is_editable !== false"
                                       @click="emit('create-version', v)"
                                       class="text-xs text-blue-600 hover:text-blue-700 font-medium"
                                       title="基于此版本新建"
                                   >
                                       克隆
                                   </button>
                                   <button 
                                      v-if="v.status === 'DRAFT' && agent?.is_editable !== false"
                                      @click="publishVersion(v)"
                                      class="text-xs text-green-600 hover:text-green-700 font-medium"
                                   >
                                       发布上线
                                   </button>
                                   <button 
                                       v-if="v.status === 'DRAFT' && agent?.is_editable !== false"
                                       @click="requestDeleteVersion(v)"
                                       class="text-xs text-red-500 hover:text-red-700 font-medium flex items-center"
                                   >
                                       <TrashIcon class="w-3.5 h-3.5 mr-1" />
                                       删除
                                   </button>
                               </div>
                          </div>
                      </div>

                      <!-- Archived List -->
                       <template v-if="showArchived">
                          <div class="relative py-2">
                              <div class="absolute inset-0 flex items-center" aria-hidden="true">
                                  <div class="w-full border-t border-gray-200"></div>
                              </div>
                              <div class="relative flex justify-center">
                                  <span class="px-2 bg-white text-sm text-gray-500">归档历史</span>
                              </div>
                          </div>

                          <div v-for="v in archivedVersions" :key="v.id" class="bg-gray-50 border border-gray-200 rounded-lg p-3 opacity-75 hover:opacity-100 transition-opacity">
                               <div class="flex justify-between items-center">
                                  <span class="text-sm font-medium text-gray-600">V{{ v.version_number }}</span>
                                  <span class="text-xs text-gray-400">{{ new Date(v.created_at).toLocaleDateString() }}</span>
                                   <div class="flex items-center space-x-3">
                                      <button 
                                          @click="emit('edit-version', v)"
                                          class="text-xs text-gray-500 hover:text-primary"
                                      >
                                          查看
                                      </button>
                                      <button
                                          v-if="publishedVersion && v.status !== 'PUBLISHED'"
                                          type="button"
                                          @click="openDiff(v)"
                                          class="text-xs text-blue-600 hover:text-blue-700 font-medium"
                                          title="与当前线上版本对比"
                                      >
                                          Diff
                                      </button>
                                      <button
                                          v-if="agent?.is_editable !== false"
                                          @click="emit('create-version', v)"
                                          class="text-xs text-blue-600 hover:text-blue-700 font-medium"
                                          title="基于此版本新建"
                                      >
                                          克隆
                                      </button>
                                      <button 
                                          v-if="agent?.is_editable !== false"
                                          @click="requestDeleteVersion(v)"
                                          class="text-xs text-red-400 hover:text-red-600 p-1"
                                          title="删除此版本"
                                      >
                                          <TrashIcon class="w-3.5 h-3.5" />
                                      </button>
                                   </div>
                               </div>
                          </div>
                       </template>
                  </div>
              </div>

            </div>
          </div>
        </div>
      </div>

      <!-- Delete Confirmation -->
      <ConfirmModal 
        v-if="showDeleteConfirm"
        title="确认删除版本？"
        :message="`确定要删除版本 V${versionToDelete?.version_number} 吗？此操作不可撤销。`"
        confirmText="确认删除"
        type="danger"
        @confirm="confirmDeleteVersion"
        @cancel="showDeleteConfirm = false"
      />

      <AgentVersionDiffModal
        :show="!!diffVersion"
        :source-version="diffVersion"
        :published-version="publishedVersion"
        @close="closeDiff"
      />
    </div>
  </Teleport>
</template>
