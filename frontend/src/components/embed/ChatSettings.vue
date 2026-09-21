<script setup lang="ts">
import { ref, watch } from 'vue';
import { useRouter } from 'vue-router';
import GroundingHelpPopover from '@/components/GroundingHelpPopover.vue';
import Switch from '@/components/Switch.vue';
import { useToast } from '@/composables/useToast';
import axios from '@/utils/axios';
import defaultAgentAvatarUrl from '@/assets/nanzi-agent-avatar.svg';
import { PRESET_AGENT_AVATARS } from '@/utils/presetAgentAvatars';
import AvatarCropperModal from '@/components/common/AvatarCropperModal.vue';

const props = defineProps<{
  visible: boolean;
  config: any;
  allowedAgents: any[];
  routingLocked?: boolean;
  isAdmin?: boolean;
}>();

const emit = defineEmits<{
  (e: 'update:visible', val: boolean): void;
  (e: 'reset-session'): void;
  (e: 'fetch-agents'): void;
  (e: 'save-settings'): void;
  (e: 'set-theme', theme: string): void;
  (e: 'set-color', color: string): void;
  (e: 'mode-change', mode: string): void;
  (e: 'switch-to-auto'): void;
  (e: 'switch-to-expert', agentId: string): void;
}>();

const router = useRouter();
const { showToast } = useToast();
type RoutingMode = 'auto' | 'expert';
const routingMode = ref<RoutingMode>(props.config.routingMode === 'expert' ? 'expert' : 'auto');
const activeColor = ref("#1677ff");
const customAvatarInput = ref(props.config.agentAvatar || "");
/**
 * AI 头像的「显式编辑」标记。
 *
 * 只有用户主动改过输入框（输入/回车/点预设/上传）才允许回写服务端；单纯失焦
 * 绝不提交，否则旧页面会在 config.agentAvatar 被服务端刷新后，用输入框里的陈旧
 * 值把全局头像倒灌覆盖回去。
 */
const isAvatarInputDirty = ref(false);
let avatarInputFocused = false;
/** 服务端权威头像（Redis 全局键值），作为乐观并发的 base_avatar 基线 */
const serverAgentAvatar = ref(props.config.agentAvatar || "");
const avatarSyncing = ref(false);
const avatarUploading = ref(false);
const fileInputRef = ref<HTMLInputElement | null>(null);
const presetColors = [
  "#1677ff",
  "#f97316",
  "#10b981",
  "#8b5cf6",
  "#ec4899",
  "#06b6d4",
  "#eab308",
  "#ef4444",
  "#64748b",
];

const close = () => emit('update:visible', false);

watch(() => props.visible, (visible) => {
  if (visible) {
    routingMode.value = props.config.routingMode === 'expert' ? 'expert' : 'auto';
    isAvatarInputDirty.value = false;
    avatarInputFocused = false;
    syncAgentAvatarFromServer(props.config.agentAvatar || "");
  }
});

/** 服务端权威值变化时同步 UI；用户正在编辑（聚焦/已改动）时不抢占输入内容。 */
function syncAgentAvatarFromServer(value: string) {
  serverAgentAvatar.value = value || "";
  if (avatarInputFocused || isAvatarInputDirty.value) return;
  customAvatarInput.value = serverAgentAvatar.value;
}

watch(() => props.config.agentAvatar, (value) => {
  syncAgentAvatarFromServer(typeof value === "string" ? value : "");
});

const markAvatarInputDirty = () => {
  isAvatarInputDirty.value = true;
};

const handleAvatarInputFocus = () => {
  avatarInputFocused = true;
};

const handleAvatarInputBlur = () => {
  avatarInputFocused = false;
  // 仅在用户确实编辑过时提交，避免"切走焦点"把陈旧值回写服务端。
  if (isAvatarInputDirty.value) {
    handleCustomAvatarBlur();
  }
};

const handleSetAgentAvatar = async (avatar: string, toastMessage = "AI 助手头像已更新"): Promise<boolean> => {
  if (avatarSyncing.value) return false;
  const target = String(avatar || "").trim();
  const baseAvatar = serverAgentAvatar.value;
  const previousAvatar = props.config.agentAvatar || "";

  isAvatarInputDirty.value = false;
  if (target === baseAvatar) {
    // 与服务端一致：只收敛本地显示，不做无意义回写。
    props.config.agentAvatar = target;
    customAvatarInput.value = target;
    showToast(toastMessage, "success");
    saveSettings();
    return true;
  }

  props.config.agentAvatar = target;
  customAvatarInput.value = target;
  localStorage.removeItem("yovole_embed_agent_avatar");
  avatarSyncing.value = true;

  try {
    // 携带 base_avatar 做乐观并发：服务端若发现全局值已被其他管理员更新，会拒绝写入。
    const res = await axios.put("/api/portal/portal-prefs/agent-avatar", {
      avatar: target,
      base_avatar: baseAvatar,
    });
    const saved = String(res.data?.data?.agent_avatar ?? target);
    serverAgentAvatar.value = saved;
    props.config.agentAvatar = saved;
    customAvatarInput.value = saved;
    showToast(toastMessage, "success");
    saveSettings();
    return true;
  } catch (err: any) {
    const status = err?.response?.status;
    if (status === 409) {
      // 其它管理员已改过全局头像：以服务端为准，绝不覆盖。
      const detail = err?.response?.data?.detail;
      const current = String(
        (detail && typeof detail === "object" ? detail.agent_avatar : "") ?? baseAvatar,
      );
      serverAgentAvatar.value = current;
      props.config.agentAvatar = current;
      customAvatarInput.value = current;
      showToast(
        (detail && typeof detail === "object" && detail.message) ||
          "AI 助手头像已被其他管理员更新，已同步为最新形象，请确认后重试",
        "warning",
      );
      return false;
    } else {
      // 保存失败（无权限/网络异常）必须回滚，避免"本地显示新头像、服务端仍是旧值"。
      serverAgentAvatar.value = baseAvatar;
      props.config.agentAvatar = previousAvatar;
      customAvatarInput.value = previousAvatar;
      const failureDetail = err?.response?.data?.detail;
      showToast(
        typeof failureDetail === "string" && failureDetail
          ? failureDetail
          : "AI 助手头像保存失败，请稍后重试",
        "error",
      );
      return false;
    }
  } finally {
    avatarSyncing.value = false;
  }
};

const handleResetAgentAvatar = () => {
  void handleSetAgentAvatar("", "已恢复官方默认 AI 助手头像");
};

const handleCustomAvatarBlur = () => {
  const trimmed = customAvatarInput.value.trim();
  isAvatarInputDirty.value = false;
  if (trimmed !== (props.config.agentAvatar || "")) {
    void handleSetAgentAvatar(trimmed);
  }
};

const showAvatarCropper = ref(false);
const cropperImageSrc = ref("");

const triggerAvatarUpload = () => {
  fileInputRef.value?.click();
};

const uploadAvatarFileDirectly = async (file: File) => {
  avatarUploading.value = true;
  try {
    const formData = new FormData();
    formData.append("file", file);
    const res = await axios.post("/api/portal/portal-prefs/agent-avatar/upload", formData, {
      headers: { "Content-Type": "multipart/form-data" },
    });
    const avatarUrl = res.data?.data?.avatar_url;
    if (avatarUrl) {
      // 上传成功后由 handleSetAgentAvatar 负责带 base_avatar 提交全局设置，
      // 并在 409/失败时回滚显示，因此这里不再乐观关闭裁剪弹窗。
      const applied = await handleSetAgentAvatar(avatarUrl, "AI 助手头像设置成功");
      if (applied) {
        showAvatarCropper.value = false;
      }
    } else {
      showToast("上传头像失败，返回数据异常", "error");
    }
  } catch (error: any) {
    const msg = error.response?.data?.detail || "上传头像失败，请稍后重试";
    showToast(msg, "error");
  } finally {
    avatarUploading.value = false;
  }
};

const handleAvatarFileUpload = async (event: Event) => {
  const target = event.target as HTMLInputElement;
  const file = target.files?.[0];
  if (!file) return;

  // 允许选择大图（最大 30MB），位图将经由 Canvas 进行高清裁剪并轻量化输出
  if (file.size > 30 * 1024 * 1024) {
    showToast("图片过大，请选择 30MB 以内的图片", "warning");
    target.value = "";
    return;
  }

  // 矢量图无需裁剪，直接上传
  if (file.type === "image/svg+xml") {
    await uploadAvatarFileDirectly(file);
    target.value = "";
    return;
  }

  const supported = ["image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif"];
  if (!supported.includes(file.type)) {
    showToast("仅支持 PNG、JPEG、WebP、GIF、SVG 格式图片", "error");
    target.value = "";
    return;
  }

  const reader = new FileReader();
  reader.onload = (e) => {
    cropperImageSrc.value = (e.target?.result as string) || "";
    showAvatarCropper.value = true;
  };
  reader.readAsDataURL(file);
  target.value = "";
};

const handleAvatarCropped = async (blob: Blob) => {
  const file = new File([blob], "agent_avatar.png", { type: "image/png" });
  await uploadAvatarFileDirectly(file);
};

const saveSettings = () => {
  emit('save-settings');
};

const handleSetTheme = (theme: string) => {
  emit('set-theme', theme);
  saveSettings();
};

const handleSetColor = (color: string) => {
  activeColor.value = color;
  emit('set-color', color);
  saveSettings();
};

const handleColorInput = (e: any) => {
    handleSetColor(e.target.value);
};

const handleSetRoutingMode = (mode: 'auto' | 'expert') => {
    if (props.routingLocked) return;
    if (mode === 'auto') {
        routingMode.value = 'auto';
        emit('switch-to-auto');
        return;
    }
    // 先停留在默认智能体页，等用户明确选中下拉项后再提交并关闭。
    routingMode.value = 'expert';
};

const handleSetExpertAgent = (event: Event) => {
    if (props.routingLocked) return;
    const agentId = String((event.target as HTMLSelectElement)?.value || '').trim();
    if (!agentId) return;
    emit('switch-to-expert', agentId);
    saveSettings();
};

const handleSetMultiAgent = (enabled: boolean) => {
    if (props.config.enableMultiAgent === enabled) {
        saveSettings();
        return;
    }
    props.config.enableMultiAgent = enabled;
    showToast(
        enabled ? "多智能体协同已开启" : "多智能体协同已关闭",
        enabled ? "success" : "info"
    );
    saveSettings();
};

const handleSetSqlPlan = (enabled: boolean) => {
    if (props.config.enableSqlPlan === enabled) {
        saveSettings();
        return;
    }
    props.config.enableSqlPlan = enabled;
    showToast(
        enabled ? "SQL PLAN 中间层已开启" : "SQL PLAN 中间层已关闭",
        enabled ? "success" : "info"
    );
    saveSettings();
};

const handleSetExpandThoughts = (enabled: boolean) => {
    if (props.config.expandThoughts === enabled) {
        saveSettings();
        return;
    }
    props.config.expandThoughts = enabled;
    showToast(
        enabled ? "思考过程默认展开已开启" : "思考过程默认展开已关闭",
        enabled ? "success" : "info"
    );
    saveSettings();
};

const handleSetMarkdownTheme = (theme: string) => {
    if (props.config.markdownTheme === theme) {
        saveSettings();
        return;
    }
    props.config.markdownTheme = theme;
    localStorage.setItem("user_has_custom_theme", "true");
    localStorage.setItem("yovole_markdown_theme", theme);
    // 异步同步到后端 Redis 持久化，无需阻塞前端 UI
    void axios.put("/api/portal/portal-prefs/markdown-theme", { theme }).catch((err) => {
        console.error("Failed to sync markdown theme preference to Redis", err);
    });

    const themeNames: Record<string, string> = {
        default: "现代",
        minimal: "极简",
        academic: "学术",
        apple: "苹果",
        warm: "护眼",
        compact: "紧凑",
        bauhaus: "包豪斯",
        editorial: "日报",
        zen: "禅意",
    };
    const name = themeNames[theme] || theme;
    showToast(`排版样式已切换为: ${name}`, "success");

    saveSettings();
};

const handleSetMessageBorder = (hidden: boolean) => {
    props.config.hideMessageBorder = hidden;
    localStorage.setItem("user_has_custom_border_preference", "true");
    localStorage.setItem("yovole_hide_message_border", hidden ? "1" : "0");
    showToast(
        hidden ? "AI 消息外框已隐藏" : "AI 消息外框已显示",
        hidden ? "success" : "info",
    );
    saveSettings();
};

const handleSetBashBanner = (visible: boolean) => {
    if (props.config.showBashBanner === visible) {
      saveSettings();
      return;
    }

    props.config.showBashBanner = visible;
    localStorage.setItem("bash_env_banner_ignored", visible ? "0" : "1");
    showToast(
      visible ? "Bash 运行环境横幅提示已开启" : "Bash 运行环境横幅提示已关闭",
      visible ? "success" : "info",
    );
    saveSettings();
};

const handleSetGrounding = (enabled: boolean) => {
    if (props.config.enableGrounding === enabled) {
      saveSettings();
      return;
    }

    props.config.enableGrounding = enabled;
    showToast(
      enabled ? "反幻觉校验已开启" : "反幻觉校验已关闭",
      enabled ? "success" : "info",
    );
    saveSettings();
};

const handleSetGroundingBlockMode = (enabled: boolean) => {
    const mode = enabled ? 'stream_with_retraction' : 'strict_buffer';
    if (props.config.groundingBlockMode === mode) {
      saveSettings();
      return;
    }

    props.config.groundingBlockMode = mode;
    showToast(
      enabled ? "已开启实时输出，校验失败时会自动撤回" : "已切换为安全缓冲输出",
      enabled ? "info" : "success",
    );
    saveSettings();
};

const showConfirmModal = ref(false);

const confirmReset = () => {
    showConfirmModal.value = true;
};

const handleReset = () => {
    emit('reset-session');
    showConfirmModal.value = false;
    close();
};

const handleLogout = () => {
    localStorage.removeItem('user_info');
    localStorage.removeItem('token');
    localStorage.removeItem('yovole_embed_token');
    localStorage.removeItem('yovole_embed_agent_avatar');
    router.push('/login');
};
</script>

<template>
    <!-- Settings Modal -->
    <div
      v-if="visible"
      class="absolute inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm"
      @click.self="close"
    >
      <div
        class="bg-white/95 dark:bg-gray-800/95 backdrop-blur-md rounded-2xl shadow-2xl w-[90vw] sm:w-[460px] max-h-[85vh] p-5 sm:p-6 border border-gray-200/80 dark:border-gray-700/80 transform transition-all scale-100 animate-fade-in-up flex flex-col"
      >
        <!-- Header -->
        <div class="flex justify-between items-center mb-5 flex-shrink-0">
          <h3 class="text-sm font-black text-gray-800 dark:text-gray-100 uppercase tracking-widest flex items-center gap-1.5">
            <svg class="w-4 h-4 text-blue-600 dark:text-blue-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" /><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" /></svg>
            界面设置
          </h3>
          <button
            @click="close"
            class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 p-1 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
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

        <!-- Scrollable Content -->
        <div class="flex-1 overflow-y-auto pr-1 custom-scrollbar space-y-5">
          
          <!-- Group 1: 个性视觉 (Visual Styles) -->
          <div class="bg-gray-50/50 dark:bg-gray-900/20 border border-gray-100 dark:border-gray-700/40 rounded-xl p-3.5 space-y-4">
            <div class="flex items-center space-x-1.5 pb-1.5 border-b border-gray-100 dark:border-gray-700/50">
              <svg class="w-3.5 h-3.5 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" /><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" /></svg>
              <h4 class="text-[10px] font-black text-gray-400 uppercase tracking-widest">个性视觉 / Styles</h4>
            </div>

            <!-- Theme Mode -->
            <div>
              <label class="block text-[10px] font-black text-gray-500 dark:text-gray-400 mb-2 uppercase tracking-wider">主题模式</label>
              <div class="flex bg-gray-100 dark:bg-gray-700 rounded-lg p-1">
                <button
                  @click="handleSetTheme('light')"
                  class="flex-1 py-1 text-xs rounded-md font-medium transition-all"
                  :class="
                    config.theme === 'light'
                      ? 'bg-white text-gray-900 shadow-sm'
                      : 'text-gray-500 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200'
                  "
                >
                  浅色
                </button>
                <button
                  @click="handleSetTheme('dark')"
                  class="flex-1 py-1 text-xs rounded-md font-medium transition-all"
                  :class="
                    config.theme === 'dark'
                      ? 'bg-gray-600 text-white shadow-sm'
                      : 'text-gray-500 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200'
                  "
                >
                  深色
                </button>
              </div>
            </div>

            <!-- Theme Color -->
            <div>
              <label class="block text-[10px] font-black text-gray-500 dark:text-gray-400 mb-2 uppercase tracking-wider">主题颜色</label>
              <div class="grid grid-cols-5 sm:grid-cols-10 gap-2">
                <button
                  v-for="color in presetColors"
                  :key="color"
                  @click="handleSetColor(color)"
                  class="w-7 h-7 rounded-full transition-all duration-300 hover:scale-110 flex items-center justify-center relative active:scale-90"
                  :class="
                    activeColor === color
                      ? 'ring-2 ring-offset-2 ring-blue-500 border-transparent scale-110 shadow-md'
                      : 'border-transparent hover:shadow'
                  "
                  :style="{ backgroundColor: color, '--tw-ring-color': color }"
                >
                  <!-- Tick mark for selected color -->
                  <svg v-if="activeColor === color" class="w-3.5 h-3.5 text-white filter drop-shadow-sm" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 13l4 4L19 7" /></svg>
                </button>
                <!-- Custom Color (Basic Input) -->
                <div
                  class="relative w-7 h-7 rounded-full overflow-hidden border-2 border-transparent hover:scale-110 transition-transform cursor-pointer group flex items-center justify-center bg-gradient-to-br from-red-500 via-green-500 to-blue-500 shadow-sm active:scale-90"
                >
                  <input
                    type="color"
                    class="absolute -top-2 -left-2 w-16 h-16 cursor-pointer opacity-0"
                    @change="handleColorInput"
                  />
                  <!-- Custom color indicator icon -->
                  <svg class="w-3.5 h-3.5 text-white filter drop-shadow-sm pointer-events-none" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M12 4v16m8-8H4" /></svg>
                </div>
              </div>
            </div>

            <!-- AI Agent Avatar Settings (Admin Configurable) -->
            <div>
              <div class="flex items-center justify-between mb-2">
                <label class="text-[10px] font-black text-gray-500 dark:text-gray-400 uppercase tracking-wider flex items-center gap-1">
                  <span>AI 助手头像</span>
                  <span class="text-[9px] font-normal text-gray-400">（聊天消息气泡旁展示）</span>
                </label>
                <span v-if="!isAdmin" class="text-[9.5px] text-gray-400 dark:text-gray-500 bg-gray-100 dark:bg-gray-800 px-1.5 py-0.5 rounded">
                  管理员统一配置
                </span>
                <button
                  v-else-if="config.agentAvatar"
                  @click="handleResetAgentAvatar"
                  type="button"
                  class="text-[10px] text-blue-500 hover:text-blue-600 dark:hover:text-blue-400 hover:underline flex items-center gap-0.5"
                >
                  <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" /></svg>
                  恢复默认
                </button>
              </div>

              <!-- Admin Controls -->
              <template v-if="isAdmin">
                <!-- Presets Row -->
                <div class="flex items-center gap-2 mb-2.5">
                  <button
                    v-for="preset in PRESET_AGENT_AVATARS"
                    :key="preset.id"
                    type="button"
                    @click="handleSetAgentAvatar(preset.isDefault ? '' : preset.url, `已切换为「${preset.name}」`)"
                    :title="preset.name"
                    class="relative w-8 h-8 rounded-full overflow-hidden border-2 transition-all duration-200 hover:scale-110 active:scale-95 flex items-center justify-center bg-gray-100 dark:bg-gray-700"
                    :class="
                      (preset.isDefault && !config.agentAvatar) || config.agentAvatar === preset.url
                        ? 'border-blue-500 ring-2 ring-blue-500/30 scale-105 shadow-sm'
                        : 'border-transparent hover:border-gray-300 dark:hover:border-gray-600'
                    "
                  >
                    <img :src="preset.url" :alt="preset.name" class="w-full h-full object-cover" />
                    <span
                      v-if="(preset.isDefault && !config.agentAvatar) || config.agentAvatar === preset.url"
                      class="absolute inset-0 bg-blue-600/20 flex items-center justify-center"
                    >
                      <svg class="w-3 h-3 text-white filter drop-shadow" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7" /></svg>
                    </span>
                  </button>
                </div>

                <!-- Custom Avatar: Upload & Link Input -->
                <div class="flex items-center gap-1.5">
                  <input
                    type="file"
                    ref="fileInputRef"
                    accept="image/png,image/jpeg,image/webp,image/svg+xml,image/gif"
                    class="hidden"
                    @change="handleAvatarFileUpload"
                  />
                  <button
                    type="button"
                    @click="triggerAvatarUpload"
                    :disabled="avatarUploading"
                    class="shrink-0 px-2.5 py-1.5 text-[11px] rounded-lg border border-dashed border-gray-300 dark:border-gray-600 hover:border-blue-500 dark:hover:border-blue-400 bg-white dark:bg-gray-800 text-gray-600 dark:text-gray-300 hover:text-blue-600 dark:hover:text-blue-400 flex items-center gap-1 transition-all disabled:opacity-50"
                    title="上传本地图片作为 AI 头像"
                  >
                    <svg v-if="avatarUploading" class="w-3.5 h-3.5 animate-spin text-blue-500" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"/></svg>
                    <svg v-else class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" /></svg>
                    <span>{{ avatarUploading ? '上传中...' : '上传图片' }}</span>
                  </button>

                  <div class="flex-1 relative">
                    <input
                      v-model="customAvatarInput"
                      @input="markAvatarInputDirty"
                      @focus="handleAvatarInputFocus"
                      @blur="handleAvatarInputBlur"
                      @keyup.enter="handleCustomAvatarBlur"
                      :disabled="avatarSyncing"
                      placeholder="或粘贴网络图片 URL"
                      class="w-full text-[11px] px-2.5 py-1.5 rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-200 placeholder-gray-400 outline-none focus:border-blue-400 focus:ring-1 focus:ring-blue-100 dark:focus:ring-blue-900/40 transition-all disabled:opacity-60"
                    />
                  </div>
                </div>
              </template>

              <!-- Readonly View for Non-Admin -->
              <div v-else class="flex items-center gap-2.5 p-2 rounded-lg bg-gray-100/60 dark:bg-gray-800/40 border border-gray-200/50 dark:border-gray-700/50">
                <div class="w-8 h-8 rounded-full overflow-hidden border border-gray-200 dark:border-gray-600 flex-shrink-0">
                  <img :src="config.agentAvatar || defaultAgentAvatarUrl" class="w-full h-full object-cover" alt="AI Avatar" />
                </div>
                <div class="text-[10px] text-gray-500 dark:text-gray-400 leading-tight">
                  <p class="font-medium text-gray-700 dark:text-gray-300">系统官方 AI 助手形象</p>
                  <p class="text-[9px] text-gray-400 mt-0.5">普通用户仅供浏览，需管理员权限方可定制变更</p>
                </div>
              </div>
            </div>

            <!-- Markdown Theme (6 styles in 3x2 Grid) -->
            <div>
              <label class="block text-[10px] font-black text-gray-500 dark:text-gray-400 mb-2 uppercase tracking-wider">AI消息排版样式</label>
              <div class="grid grid-cols-3 gap-1.5">
                <button
                  @click="handleSetMarkdownTheme('default')"
                  class="py-1.5 text-[10px] border rounded-lg font-medium transition-all text-center flex items-center justify-center gap-1 active:scale-95"
                  :class="
                    config.markdownTheme === 'default' || !config.markdownTheme
                      ? 'bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 border-blue-200 dark:border-blue-800/60 shadow-sm font-black'
                      : 'bg-white dark:bg-gray-800 text-gray-500 dark:text-gray-400 border-gray-100 dark:border-gray-700/60 hover:bg-gray-50 dark:hover:bg-gray-700/50'
                  "
                >
                  <span>✨</span>
                  <span>现代</span>
                </button>
                <button
                  @click="handleSetMarkdownTheme('minimal')"
                  class="py-1.5 text-[10px] border rounded-lg font-medium transition-all text-center flex items-center justify-center gap-1 active:scale-95"
                  :class="
                    config.markdownTheme === 'minimal'
                      ? 'bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 border-blue-200 dark:border-blue-800/60 shadow-sm font-black'
                      : 'bg-white dark:bg-gray-800 text-gray-500 dark:text-gray-400 border-gray-100 dark:border-gray-700/60 hover:bg-gray-50 dark:hover:bg-gray-700/50'
                  "
                >
                  <span>🍃</span>
                  <span>极简</span>
                </button>
                <button
                  @click="handleSetMarkdownTheme('academic')"
                  class="py-1.5 text-[10px] border rounded-lg font-medium transition-all text-center flex items-center justify-center gap-1 active:scale-95"
                  :class="
                    config.markdownTheme === 'academic'
                      ? 'bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 border-blue-200 dark:border-blue-800/60 shadow-sm font-black'
                      : 'bg-white dark:bg-gray-800 text-gray-500 dark:text-gray-400 border-gray-100 dark:border-gray-700/60 hover:bg-gray-50 dark:hover:bg-gray-700/50'
                  "
                >
                  <span>📖</span>
                  <span>学术</span>
                </button>
                <button
                  @click="handleSetMarkdownTheme('apple')"
                  class="py-1.5 text-[10px] border rounded-lg font-medium transition-all text-center flex items-center justify-center gap-1 active:scale-95"
                  :class="
                    config.markdownTheme === 'apple'
                      ? 'bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 border-blue-200 dark:border-blue-800/60 shadow-sm font-black'
                      : 'bg-white dark:bg-gray-800 text-gray-500 dark:text-gray-400 border-gray-100 dark:border-gray-700/60 hover:bg-gray-50 dark:hover:bg-gray-700/50'
                  "
                >
                  <span>🍎</span>
                  <span>苹果</span>
                </button>
                <button
                  @click="handleSetMarkdownTheme('warm')"
                  class="py-1.5 text-[10px] border rounded-lg font-medium transition-all text-center flex items-center justify-center gap-1 active:scale-95"
                  :class="
                    config.markdownTheme === 'warm'
                      ? 'bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 border-blue-200 dark:border-blue-800/60 shadow-sm font-black'
                      : 'bg-white dark:bg-gray-800 text-gray-500 dark:text-gray-400 border-gray-100 dark:border-gray-700/60 hover:bg-gray-50 dark:hover:bg-gray-700/50'
                  "
                >
                  <span>🍂</span>
                  <span>护眼</span>
                </button>
                <button
                  @click="handleSetMarkdownTheme('compact')"
                  class="py-1.5 text-[10px] border rounded-lg font-medium transition-all text-center flex items-center justify-center gap-1 active:scale-95"
                  :class="
                    config.markdownTheme === 'compact'
                      ? 'bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 border-blue-200 dark:border-blue-800/60 shadow-sm font-black'
                      : 'bg-white dark:bg-gray-800 text-gray-500 dark:text-gray-400 border-gray-100 dark:border-gray-700/60 hover:bg-gray-50 dark:hover:bg-gray-700/50'
                  "
                >
                  <span>🔎</span>
                  <span>紧凑</span>
                </button>
                <button
                  @click="handleSetMarkdownTheme('bauhaus')"
                  class="py-1.5 text-[10px] border rounded-lg font-medium transition-all text-center flex items-center justify-center gap-1 active:scale-95"
                  :class="
                    config.markdownTheme === 'bauhaus'
                      ? 'bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 border-blue-200 dark:border-blue-800/60 shadow-sm font-black'
                      : 'bg-white dark:bg-gray-800 text-gray-500 dark:text-gray-400 border-gray-100 dark:border-gray-700/60 hover:bg-gray-50 dark:hover:bg-gray-700/50'
                  "
                >
                  <span>📐</span>
                  <span>包豪斯</span>
                </button>
                <button
                  @click="handleSetMarkdownTheme('editorial')"
                  class="py-1.5 text-[10px] border rounded-lg font-medium transition-all text-center flex items-center justify-center gap-1 active:scale-95"
                  :class="
                    config.markdownTheme === 'editorial'
                      ? 'bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 border-blue-200 dark:border-blue-800/60 shadow-sm font-black'
                      : 'bg-white dark:bg-gray-800 text-gray-500 dark:text-gray-400 border-gray-100 dark:border-gray-700/60 hover:bg-gray-50 dark:hover:bg-gray-700/50'
                  "
                >
                  <span>📰</span>
                  <span>日报</span>
                </button>
                <button
                  @click="handleSetMarkdownTheme('zen')"
                  class="py-1.5 text-[10px] border rounded-lg font-medium transition-all text-center flex items-center justify-center gap-1 active:scale-95"
                  :class="
                    config.markdownTheme === 'zen'
                      ? 'bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 border-blue-200 dark:border-blue-800/60 shadow-sm font-black'
                      : 'bg-white dark:bg-gray-800 text-gray-500 dark:text-gray-400 border-gray-100 dark:border-gray-700/60 hover:bg-gray-50 dark:hover:bg-gray-700/50'
                  "
                >
                  <span>🍃</span>
                  <span>禅意</span>
                </button>
              </div>
            </div>

            <!-- Message Border -->
            <div class="flex items-start justify-between py-1">
              <div class="flex items-start space-x-2.5 pr-2">
                <div class="mt-0.5 text-gray-400 dark:text-gray-500 shrink-0">
                  <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 7a3 3 0 013-3h10a3 3 0 013 3v10a3 3 0 01-3 3H7a3 3 0 01-3-3V7z" /></svg>
                </div>
                <div>
                  <h5 class="text-xs font-black text-gray-700 dark:text-gray-200">隐藏 AI 消息外框</h5>
                  <p class="text-[9.5px] text-gray-400 dark:text-gray-500 leading-normal mt-0.5">仅隐藏消息气泡外框，Markdown 表格边框保留</p>
                </div>
              </div>
              <Switch :modelValue="!!config.hideMessageBorder" @update:modelValue="handleSetMessageBorder" class="scale-[0.8] origin-right" />
            </div>

            <!-- Bash 运行环境横幅提示 -->
            <div class="flex items-start justify-between py-1">
              <div class="flex items-start space-x-2.5 pr-2">
                <div class="mt-0.5 text-gray-400 dark:text-gray-500 shrink-0">
                  <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 9h8M8 13h5M8 17h8M4 4h16v16H4z" /></svg>
                </div>
                <div>
                  <h5 class="text-xs font-black text-gray-700 dark:text-gray-200">Bash 运行环境横幅提示</h5>
                  <p class="text-[9.5px] text-gray-400 dark:text-gray-500 leading-normal mt-0.5">输入框上方提示 Bash 运行在宿主机或容器沙箱</p>
                </div>
              </div>
              <Switch :modelValue="!!config.showBashBanner" @update:modelValue="handleSetBashBanner" class="scale-[0.8] origin-right" />
            </div>
          </div>

          <!-- Group 2: 主专家自动委派 -->
          <div v-if="!routingLocked" class="bg-gray-50/50 dark:bg-gray-900/20 border border-gray-100 dark:border-gray-700/40 rounded-xl p-3.5 space-y-4">
            <div class="flex items-center space-x-1.5 pb-1.5 border-b border-gray-100 dark:border-gray-700/50">
              <svg class="w-3.5 h-3.5 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M8 7h8M8 12h8M8 17h5M5 4h14a2 2 0 012 2v12a2 2 0 01-2 2H5a2 2 0 01-2-2V6a2 2 0 012-2z" /></svg>
              <h4 class="text-[10px] font-black text-gray-400 uppercase tracking-widest">主专家自动委派 / Delegation</h4>
            </div>

            <div class="flex bg-gray-100 dark:bg-gray-700 rounded-lg p-1">
              <button
                type="button"
                @click="handleSetRoutingMode('auto')"
                class="flex-1 py-1.5 text-xs rounded-md font-medium transition-all"
                :class="routingMode === 'auto'
                  ? 'bg-white dark:bg-gray-600 text-blue-600 dark:text-blue-400 shadow-sm font-black'
                  : 'text-gray-500 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200'"
              >
                主专家自动委派
              </button>
              <button
                type="button"
                @click="handleSetRoutingMode('expert')"
                class="flex-1 py-1.5 text-xs rounded-md font-medium transition-all"
                :class="routingMode === 'expert'
                  ? 'bg-white dark:bg-gray-600 text-blue-600 dark:text-blue-400 shadow-sm font-black'
                  : 'text-gray-500 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200'"
              >
                默认智能体
              </button>
            </div>

            <p v-if="routingMode === 'auto'" class="text-[9.5px] text-gray-400 dark:text-gray-500 leading-normal">
              未指定专家时，默认由主专家直接回答，或按任务需要自动委派其他智能体，统一流程并减少额外判断耗时。
            </p>

            <div v-if="routingMode === 'expert'" class="space-y-1.5">
              <label class="block text-[10px] font-black text-gray-500 dark:text-gray-400 uppercase tracking-wider">选择默认智能体</label>
              <select
                :value="config.expertAgentId"
                :disabled="allowedAgents.length === 0"
                class="w-full rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 text-xs text-gray-700 dark:text-gray-200 outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100 dark:focus:ring-blue-900/40 disabled:opacity-50"
                @change="handleSetExpertAgent"
              >
                <option v-if="allowedAgents.length === 0" value="">暂无可用智能体</option>
                <option v-for="agent in allowedAgents" :key="agent.id" :value="agent.id">
                  {{ agent.display_name || agent.name }}
                </option>
              </select>
              <p class="text-[9.5px] text-gray-400 dark:text-gray-500 leading-normal">
                默认智能体优先处理当前问题；主专家仍可按任务需要调用其他智能体，适合明确主责领域的场景。
              </p>
            </div>
          </div>

          <!-- Group 3: 智能特性 (Intelligent Features with Switches) -->
          <div class="bg-gray-50/50 dark:bg-gray-900/20 border border-gray-100 dark:border-gray-700/40 rounded-xl p-3.5 space-y-4">
            <div class="flex items-center space-x-1.5 pb-1.5 border-b border-gray-100 dark:border-gray-700/50">
              <svg class="w-3.5 h-3.5 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M13 10V3L4 14h7v7l9-11h-7z" /></svg>
              <h4 class="text-[10px] font-black text-gray-400 uppercase tracking-widest">智能特性 / Features</h4>
            </div>

            <!-- Switch Row 1: Multi-Agent Collaboration -->
            <div class="flex items-start justify-between py-1">
              <div class="flex items-start space-x-2.5 pr-2">
                <div class="mt-0.5 text-gray-400 dark:text-gray-500 shrink-0">
                  <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20H7v-2C7 15 5 13 3 13m4 7v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z" /></svg>
                </div>
                <div>
                  <h5 class="text-xs font-black text-gray-700 dark:text-gray-200">多智能体协同</h5>
                  <p class="text-[9.5px] text-gray-400 dark:text-gray-500 leading-normal mt-0.5">开启后支持跨领域任务并行执行</p>
                </div>
              </div>
              <Switch :modelValue="config.enableMultiAgent" @update:modelValue="handleSetMultiAgent" class="scale-[0.8] origin-right" />
            </div>

            <!-- Switch Row 2: SQL Plan -->
            <div class="flex items-start justify-between py-1">
              <div class="flex items-start space-x-2.5 pr-2">
                <div class="mt-0.5 text-gray-400 dark:text-gray-500 shrink-0">
                  <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 17v-2m3 2v-4m3 4v-6m2 10H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" /></svg>
                </div>
                <div>
                  <h5 class="text-xs font-black text-gray-700 dark:text-gray-200">SQL PLAN 中间层</h5>
                  <p class="text-[9.5px] text-gray-400 dark:text-gray-500 leading-normal mt-0.5">高风险查数先校验执行计划</p>
                </div>
              </div>
              <Switch :modelValue="config.enableSqlPlan" @update:modelValue="handleSetSqlPlan" class="scale-[0.8] origin-right" />
            </div>

            <!-- Switch Row 3: Thoughts Expand -->
            <div class="flex items-start justify-between py-1">
              <div class="flex items-start space-x-2.5 pr-2">
                <div class="mt-0.5 text-gray-400 dark:text-gray-500 shrink-0">
                  <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" /></svg>
                </div>
                <div>
                  <h5 class="text-xs font-black text-gray-700 dark:text-gray-200">思考过程</h5>
                  <p class="text-[9.5px] text-gray-400 dark:text-gray-500 leading-normal mt-0.5">智能体推理思维链默认展开展示</p>
                </div>
              </div>
              <Switch :modelValue="config.expandThoughts" @update:modelValue="handleSetExpandThoughts" class="scale-[0.8] origin-right" />
            </div>

            <!-- Switch Row 4: Grounding Toggle -->
            <div class="flex items-start justify-between py-1">
              <div class="flex items-start space-x-2.5 pr-2">
                <div class="mt-0.5 text-gray-400 dark:text-gray-500 shrink-0">
                  <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" /></svg>
                </div>
                <div>
                  <h5 class="text-xs font-black text-gray-750 dark:text-gray-250 flex items-center gap-1.5">
                    反幻觉校验
                    <GroundingHelpPopover />
                  </h5>
                  <p class="text-[9.5px] text-gray-400 dark:text-gray-500 leading-normal mt-0.5">开启后校验回答的事实来源并提示风险</p>
                </div>
              </div>
              <Switch :modelValue="config.enableGrounding" @update:modelValue="handleSetGrounding" class="scale-[0.8] origin-right" />
            </div>

            <!-- Switch Row 5: Streaming Retraction Toggle -->
            <div v-if="config.enableGrounding" class="flex items-start justify-between py-1">
              <div class="flex items-start space-x-2.5 pr-2">
                <div class="mt-0.5 text-gray-400 dark:text-gray-500 shrink-0">
                  <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7h8m-8 5h5m-5 5h8M5 4h14a2 2 0 012 2v12a2 2 0 01-2 2H5a2 2 0 01-2-2V6a2 2 0 012-2z" /></svg>
                </div>
                <div>
                  <h5 class="text-xs font-black text-gray-700 dark:text-gray-200">实时输出</h5>
                  <p class="text-[9.5px] text-gray-400 dark:text-gray-500 leading-normal mt-0.5">校验失败后撤回，内容可能短暂显示</p>
                </div>
              </div>
              <Switch
                :modelValue="config.groundingBlockMode === 'stream_with_retraction'"
                @update:modelValue="handleSetGroundingBlockMode"
                class="scale-[0.8] origin-right"
              />
            </div>

          </div>

          <!-- Bottom Action Buttons -->
          <div class="mt-6 pt-4 border-t border-gray-150 dark:border-gray-700/50 space-y-2">
            <button
              @click="confirmReset"
              class="w-full py-2 text-xs font-bold text-blue-600 hover:bg-blue-50 dark:hover:bg-blue-900/20 active:scale-98 rounded-xl transition-all flex items-center justify-center space-x-2 border border-blue-100 dark:border-blue-900/30 shadow-sm"
            >
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M12 4v16m8-8H4" /></svg>
              <span>开启新会话</span>
            </button>
            <!-- Mobile Only Logout Button -->
            <button
              @click="handleLogout"
              class="sm:hidden w-full py-2 text-xs font-bold text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 active:scale-98 rounded-xl transition-all flex items-center justify-center space-x-2 border border-red-100 dark:border-red-900/30"
            >
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" /></svg>
              <span>退出登录</span>
            </button>
          </div>

        </div>
      </div>
    </div>

    <!-- Confirm Modal -->
    <div v-if="showConfirmModal" class="absolute inset-0 z-[60] flex items-center justify-center bg-black/40 backdrop-blur-sm p-4">
        <div class="bg-white dark:bg-gray-800 rounded-2xl shadow-2xl w-64 p-4 border border-gray-200 dark:border-gray-700 animate-fade-in-up">
            <h3 class="text-sm font-black text-gray-850 dark:text-gray-150 mb-2 flex items-center gap-1.5">
                <svg class="w-4 h-4 text-amber-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" /></svg>
                确认开启新会话？
            </h3>
            <p class="text-[10.5px] text-gray-500 dark:text-gray-400 leading-normal mb-4">当前页面将被清空以开始新对话。旧的对话记录仍可在“历史”中查阅。</p>
            <div class="flex space-x-2">
                <button @click="showConfirmModal = false" class="flex-1 py-1.5 text-xs font-bold text-gray-500 bg-gray-100 hover:bg-gray-200 dark:bg-gray-700 dark:text-gray-300 dark:hover:bg-gray-600 rounded-xl transition-colors">取消</button>
                <button @click="handleReset" class="flex-1 py-1.5 text-xs font-bold text-white bg-blue-600 hover:bg-blue-700 rounded-xl transition-colors">确认开启</button>
            </div>
        </div>
    </div>

    <!-- Avatar Cropper Modal -->
    <AvatarCropperModal
      :visible="showAvatarCropper"
      :image-src="cropperImageSrc"
      :loading="avatarUploading"
      @close="showAvatarCropper = false"
      @confirm="handleAvatarCropped"
    />
</template>

<style scoped>
.custom-scrollbar::-webkit-scrollbar {
  width: 4px;
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
</style>
