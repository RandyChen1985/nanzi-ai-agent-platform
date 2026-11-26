<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  DEFAULT_LOGIN_THEME,
  LOGIN_THEMES,
  type LoginThemeId,
  type LoginThemeMeta,
} from '@/constants/loginThemes'
import { useLoginTheme } from '@/composables/useLoginTheme'

const emit = defineEmits<{
  (e: 'toast', message: string, type?: 'success' | 'error'): void
}>()

const { loginTheme, ensureLoginTheme, setLoginTheme } = useLoginTheme()
const savingThemeId = ref<LoginThemeId | null>(null)

const groupedThemes = computed(() => [
  {
    key: 'astrolabe',
    title: '精密星盘系',
    subtitle: '天文游标刻度与柔和亮色登录区融合，沉稳精密',
    items: LOGIN_THEMES.filter((t) => t.group === 'astrolabe'),
  },
  {
    key: 'editorial',
    title: '多元流派系',
    subtitle: '建筑水墨、物理干涉、长虹玻璃与航电地平仪等独立美学',
    items: LOGIN_THEMES.filter((t) => t.group === 'editorial'),
  },
])

const handleSelectTheme = async (theme: LoginThemeMeta) => {
  if (savingThemeId.value || loginTheme.value === theme.id) return
  savingThemeId.value = theme.id
  try {
    const status = await setLoginTheme(theme.id)
    const ok = status === 'saved'
    emit(
      'toast',
      ok ? `登录页风格已切换为「${theme.code} · ${theme.name}」` : '保存登录页风格失败，请稍后重试',
      ok ? 'success' : 'error',
    )
  } finally {
    savingThemeId.value = null
  }
}

const openFullPreview = (themeId: LoginThemeId) => {
  const url = `/login?preview=${encodeURIComponent(themeId)}`
  // 同源预览页不加 noopener，方便预览页点击「返回」直接关闭临时标签页回到个人中心
  window.open(url, '_blank')
}

onMounted(() => {
  void ensureLoginTheme()
})
</script>

<template>
  <div class="max-w-4xl space-y-6" data-user-panel="login-theme">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h2 class="text-lg font-bold text-gray-900">登录页风格</h2>
        <p class="mt-1 text-xs text-gray-500">
          为你的账号定制登录页视觉主题（默认 F1 · 双面穿界星盘）。偏好会同步到 Redis，退出登录或换设备登录都会记住你的选择。
        </p>
      </div>
      <button
        type="button"
        class="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 shadow-sm transition-colors hover:border-blue-300 hover:text-blue-600 cursor-pointer"
        @click="openFullPreview(loginTheme)"
      >
        <svg class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
        </svg>
        <span>全屏预览当前风格</span>
      </button>
    </div>

    <div v-for="group in groupedThemes" :key="group.key" class="space-y-3">
      <div class="flex items-baseline justify-between gap-2">
        <div class="flex items-center gap-2">
          <h3 class="text-sm font-semibold text-gray-800">{{ group.title }}</h3>
          <span class="text-xs text-gray-400">{{ group.subtitle }}</span>
        </div>
      </div>

      <div class="grid gap-3.5 sm:grid-cols-2">
        <div
          v-for="item in group.items"
          :key="item.id"
          role="button"
          tabindex="0"
          :data-login-theme-option="item.id"
          class="group relative flex flex-col overflow-hidden rounded-xl border text-left transition-all cursor-pointer outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
          :class="
            loginTheme === item.id
              ? 'border-blue-500 bg-blue-50/35 shadow-[0_0_0_3px_rgba(37,99,235,0.10)]'
              : 'border-gray-200 bg-white hover:border-gray-300 hover:shadow-sm'
          "
          @click="handleSelectTheme(item)"
          @keydown.enter.prevent="handleSelectTheme(item)"
          @keydown.space.prevent="handleSelectTheme(item)"
        >
          <!-- 微缩视觉预览舞台 -->
          <div
            class="relative h-28 w-full overflow-hidden border-b border-gray-100"
            :style="{ background: item.preview.bg }"
          >
            <!-- 星盘系 F1/F2/F4 缩略圆环 -->
            <svg
              v-if="item.id === 'f1' || item.id === 'f2' || item.id === 'f4'"
              class="absolute inset-0 h-full w-full"
              viewBox="0 0 280 112"
              preserveAspectRatio="xMidYMid slice"
              aria-hidden="true"
            >
              <circle cx="118" cy="58" r="22" fill="none" :stroke="item.preview.accent" stroke-opacity="0.35" stroke-width="1" />
              <circle cx="118" cy="58" r="40" fill="none" :stroke="item.preview.accent" stroke-opacity="0.55" stroke-width="1.2" stroke-dasharray="3 5" />
              <circle cx="118" cy="58" r="60" fill="none" :stroke="item.preview.secondary" stroke-opacity="0.3" stroke-width="1" />
              <circle cx="152" cy="36" r="2.8" :fill="item.preview.accent" />
            </svg>

            <!-- F3 引力卫星缩略图 -->
            <svg
              v-else-if="item.id === 'f3'"
              class="absolute inset-0 h-full w-full"
              viewBox="0 0 280 112"
              preserveAspectRatio="xMidYMid slice"
              aria-hidden="true"
            >
              <circle cx="100" cy="56" r="18" fill="none" stroke="#38bdf8" stroke-opacity="0.4" stroke-width="1" />
              <circle cx="100" cy="56" r="34" fill="none" stroke="#38bdf8" stroke-opacity="0.65" stroke-width="1.2" />
              <circle cx="100" cy="56" r="52" fill="none" stroke="#94a3b8" stroke-opacity="0.25" stroke-width="1" stroke-dasharray="3 5" />
              <line x1="100" y1="56" x2="128" y2="37" stroke="#38bdf8" stroke-opacity="0.45" stroke-dasharray="2 3" />
              <circle cx="100" cy="56" r="3" fill="#38bdf8" />
              <circle cx="128" cy="37" r="3.5" fill="#38bdf8" />
              <circle cx="128" cy="37" r="7" fill="none" stroke="#38bdf8" stroke-opacity="0.7" />
            </svg>

            <!-- G 东方山脊缩略图 -->
            <svg
              v-else-if="item.id === 'g'"
              class="absolute inset-0 h-full w-full"
              viewBox="0 0 280 112"
              preserveAspectRatio="none"
              aria-hidden="true"
            >
              <circle cx="66" cy="28" r="22" fill="#d9b382" fill-opacity="0.24" />
              <path d="M0 78 Q55 52 115 70 T280 62 L280 112 L0 112 Z" fill="#364152" fill-opacity="0.10" />
              <path d="M0 90 Q75 68 145 84 T280 78 L280 112 L0 112 Z" fill="#1e293b" fill-opacity="0.16" />
            </svg>

            <!-- H 物理波纹干涉缩略图 -->
            <svg
              v-else-if="item.id === 'h'"
              class="absolute inset-0 h-full w-full"
              viewBox="0 0 280 112"
              preserveAspectRatio="xMidYMid slice"
              aria-hidden="true"
            >
              <g fill="none" stroke="#0f172a" stroke-opacity="0.18" stroke-width="0.9">
                <circle cx="82" cy="56" r="14" /><circle cx="82" cy="56" r="26" /><circle cx="82" cy="56" r="38" /><circle cx="82" cy="56" r="50" />
              </g>
              <g fill="none" stroke="#2563eb" stroke-opacity="0.28" stroke-width="0.9">
                <circle cx="118" cy="52" r="14" /><circle cx="118" cy="52" r="26" /><circle cx="118" cy="52" r="38" /><circle cx="118" cy="52" r="50" />
              </g>
            </svg>

            <!-- I 长虹压花玻璃缩略图 -->
            <div
              v-else-if="item.id === 'i'"
              class="absolute inset-0"
              style="background-image: repeating-linear-gradient(90deg, rgba(255,255,255,0.2) 0px, rgba(255,255,255,0.02) 3px, rgba(8,14,32,0.24) 7px, rgba(255,255,255,0.12) 11px);"
              aria-hidden="true"
            />

            <!-- J 航电地平仪缩略图 -->
            <svg
              v-else-if="item.id === 'j'"
              class="absolute inset-0 h-full w-full"
              viewBox="0 0 280 112"
              preserveAspectRatio="xMidYMid slice"
              aria-hidden="true"
            >
              <circle cx="102" cy="56" r="38" fill="none" stroke="#34d399" stroke-opacity="0.32" stroke-width="1" />
              <line x1="60" y1="62" x2="144" y2="50" stroke="#34d399" stroke-opacity="0.65" stroke-width="1.2" />
              <line x1="86" y1="42" x2="118" y2="38" stroke="#6ee7b7" stroke-opacity="0.4" stroke-width="1" />
              <path d="M90 56 L98 56 L98 60 M114 56 L106 56 L106 60" fill="none" stroke="#f59e0b" stroke-width="1.6" />
            </svg>

            <!-- 右侧模拟登录表单卡片微缩 -->
            <div
              class="absolute right-3 top-3 bottom-3 w-[38%] rounded-lg border p-2 shadow-sm flex flex-col justify-between"
              :style="{
                background: item.preview.panelBg,
                borderColor: item.preview.isLight ? 'rgba(15,23,42,0.08)' : 'rgba(255,255,255,0.45)',
              }"
              aria-hidden="true"
            >
              <div class="space-y-1">
                <div class="h-1.5 w-10 rounded-full bg-slate-800/75" />
                <div class="h-1 w-14 rounded-full bg-slate-400/50" />
              </div>
              <div class="space-y-1.5">
                <div class="h-2.5 w-full rounded bg-white/90 border border-slate-300/60" />
                <div class="h-2.5 w-full rounded bg-white/90 border border-slate-300/60" />
                <div
                  class="h-3 w-full rounded"
                  :style="{ background: item.id === 'j' ? '#132a22' : item.id === 'f4' ? '#2563eb' : '#162442' }"
                />
              </div>
            </div>

            <!-- 左上角编号 + 默认标签 -->
            <div class="absolute left-2.5 top-2.5 flex items-center gap-1.5">
              <span
                class="rounded-md px-1.5 py-0.5 font-mono text-[10px] font-bold backdrop-blur-sm"
                :class="item.preview.isLight ? 'bg-white/80 text-slate-800 border border-slate-200/80' : 'bg-slate-900/70 text-sky-200 border border-white/15'"
              >
                {{ item.code }}
              </span>
              <span
                v-if="item.id === DEFAULT_LOGIN_THEME"
                class="rounded-md bg-blue-600/90 px-1.5 py-0.5 text-[10px] font-semibold text-white shadow-sm"
              >
                默认
              </span>
            </div>

            <!-- 悬浮预览按钮（触屏端常显，桌面端悬停/聚焦显现） -->
            <button
              type="button"
              class="absolute bottom-2 left-2.5 rounded-md px-2 py-1 text-[10px] font-medium backdrop-blur-md transition-opacity opacity-100 sm:opacity-0 sm:group-hover:opacity-100 focus:opacity-100 cursor-pointer"
              :class="item.preview.isLight ? 'bg-slate-900/80 text-white hover:bg-slate-900' : 'bg-white/90 text-slate-900 hover:bg-white'"
              @click.stop="openFullPreview(item.id)"
            >
              预览效果 ↗
            </button>
          </div>

          <!-- 文案信息区 -->
          <div class="flex flex-1 flex-col justify-between p-3.5">
            <div>
              <div class="flex items-center justify-between gap-2">
                <span class="text-sm font-semibold text-gray-900">{{ item.name }}</span>
                <span
                  v-if="savingThemeId === item.id"
                  class="text-[11px] text-blue-600"
                >
                  保存中...
                </span>
                <span
                  v-else-if="loginTheme === item.id"
                  class="inline-flex items-center gap-1 rounded bg-blue-100 px-1.5 py-0.5 text-[10px] font-semibold text-blue-700"
                >
                  当前使用
                </span>
              </div>
              <p class="mt-1 text-xs leading-relaxed text-gray-500">{{ item.tagline }}</p>
            </div>
          </div>
        </div>
      </div>
    </div>

    <p class="rounded-xl border border-gray-100 bg-gray-50/70 px-4 py-3 text-xs leading-relaxed text-gray-500">
      小提示：登录页风格只影响视觉呈现，SSO、本地账号、API Key、两步验证与账号注册等所有安全认证流程完全一致；切换后会同步保存到账号偏好（Redis），并在当前浏览器立即生效。
    </p>
  </div>
</template>
