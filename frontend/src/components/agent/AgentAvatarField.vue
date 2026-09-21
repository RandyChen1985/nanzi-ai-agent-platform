<script setup lang="ts">
/**
 * 智能体头像设置控件（新建 / 编辑智能体共用）。
 *
 * 用户反馈「新建时只有一个输入框，编辑时才有上传与预设」——两处必须**外观与能力完全一致**，
 * 因此整块 UI（预览 / URL 输入 / 上传 / 继承全局形象 / 预设快选 / 裁剪弹窗）收敛到本组件，
 * 由调用方用 `v-model` 绑定表单里的 avatar_url。
 *
 * 上传只回填 URL，不直接写库：智能体编辑是「表单 + 保存」语义。
 * 新建时还没有 agent_id，走待绑定上传接口（见 useAgentAvatarUpload）。
 */
import { computed, ref } from 'vue';
import AvatarCropperModal from '../common/AvatarCropperModal.vue';
import { AGENT_AVATAR_URL_MAX_LENGTH } from '@/utils/agentAvatar';
import { PRESET_AGENT_AVATARS } from '@/utils/presetAgentAvatars';
import { useAgentAvatarUpload } from '@/composables/useAgentAvatarUpload';

const props = defineProps<{
  /** 头像地址（表单字段） */
  modelValue?: string;
  /** 已有智能体 ID；新建流程为空，此时上传走待绑定接口 */
  agentId?: string;
  /** 自定义提示文案；不传则用默认的继承说明 */
  hint?: string;
}>();

const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void;
}>();

const avatarUrl = computed({
  get: () => String(props.modelValue || ''),
  set: (value: string) => emit('update:modelValue', value),
});
const previewUrl = computed(() => avatarUrl.value.trim());
const defaultHint = computed(
  () =>
    `未设置时继承管理员配置的全局 AI 形象；上传会裁剪为 256×256 圆形头像，地址上限 ${AGENT_AVATAR_URL_MAX_LENGTH} 字符。`,
);

const fileInput = ref<HTMLInputElement | null>(null);
const {
  uploading,
  showCropper,
  cropperSrc,
  pickFile,
  handleFileChange,
  handleCropped,
} = useAgentAvatarUpload({
  getAgentId: () => props.agentId,
  onUploaded: (url) => {
    avatarUrl.value = url;
  },
});
</script>

<template>
  <div class="rounded-xl border border-gray-200 bg-gray-50/70 p-3">
    <div class="flex items-center justify-between gap-2">
      <label class="flex items-center gap-1 text-sm font-medium text-gray-700">
        <span>智能体头像</span>
        <span
          class="inline-flex h-4 w-4 cursor-help items-center justify-center rounded-full border border-gray-300 text-[10px] font-semibold text-gray-400"
          title="仅影响该智能体在对话气泡与智能体列表中的形象；留空则继承全局 AI 形象"
          >?</span
        >
      </label>
      <button
        v-if="avatarUrl"
        type="button"
        class="text-[11px] text-blue-500 hover:text-blue-600 hover:underline"
        @click="avatarUrl = ''"
      >
        继承全局形象
      </button>
    </div>

    <div class="mt-3 flex items-center gap-3">
      <img
        v-if="previewUrl"
        :src="previewUrl"
        class="h-12 w-12 shrink-0 rounded-full border border-gray-200 object-cover"
        alt="智能体头像预览"
      />
      <div
        v-else
        class="flex h-12 w-12 shrink-0 items-center justify-center rounded-full border border-dashed border-gray-300 text-[10px] text-gray-400"
      >
        未设置
      </div>
      <div class="min-w-0 flex-1">
        <div class="flex items-center gap-2">
          <input
            v-model="avatarUrl"
            :maxlength="AGENT_AVATAR_URL_MAX_LENGTH"
            placeholder="可选：填写图片 URL，或点右侧上传"
            class="w-full min-w-0 rounded-lg border border-gray-300 px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/30"
          />
          <button
            type="button"
            class="shrink-0 rounded-lg border border-gray-300 bg-white px-3 py-2 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-60"
            :disabled="uploading"
            @click="pickFile(fileInput)"
          >
            {{ uploading ? '上传中…' : '上传' }}
          </button>
        </div>
        <p class="mt-1 text-[11px] text-gray-400">{{ hint || defaultHint }}</p>
      </div>
    </div>

    <!-- 预设快选：与全局头像同一套资源；「官方默认」= 清空（继承全局） -->
    <div class="mt-3 flex flex-wrap items-center gap-2">
      <button
        v-for="preset in PRESET_AGENT_AVATARS"
        :key="preset.id"
        type="button"
        :title="preset.isDefault ? '继承全局形象（清空本智能体头像）' : preset.name"
        class="h-8 w-8 overflow-hidden rounded-full border-2 transition-all hover:scale-110 active:scale-95"
        :class="
          (preset.isDefault && !avatarUrl) || avatarUrl === preset.url
            ? 'border-blue-500 ring-2 ring-blue-500/30'
            : 'border-transparent hover:border-gray-300'
        "
        @click="avatarUrl = preset.isDefault ? '' : preset.url"
      >
        <img :src="preset.url" class="h-full w-full object-cover" :alt="preset.name" />
      </button>
    </div>

    <input
      ref="fileInput"
      type="file"
      accept="image/png,image/jpeg,image/webp,image/gif,image/svg+xml"
      class="hidden"
      @change="handleFileChange"
    />

    <AvatarCropperModal
      :visible="showCropper"
      :image-src="cropperSrc"
      :loading="uploading"
      title="裁剪智能体头像"
      @close="showCropper = false"
      @confirm="handleCropped"
    />
  </div>
</template>
