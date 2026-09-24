<template>
  <!--
    /embed/personal：供第三方页面 iframe 嵌入的「个人中心」。
    内容与 /dashboard/personal 完全一致（同一个 PersonalCenter 组件），
    唯一区别是**不渲染 Dashboard 的主题框架**——没有顶部 header（因此也没有面包屑导航）
    与左侧主导航，嵌入方页面里只出现个人中心本身。
    视觉口径对齐 Dashboard 的 <main>：同样的 bg-gray-100 底色、px-3 sm:px-4 内边距
    与滚动条样式，保证与 /dashboard/personal 看起来一模一样。

    凭据门禁：本页 meta.public，路由守卫不会拦，因此**必须自己在渲染前校验凭据**。
    无 portal_session / embed_session 时 PersonalCenter 会把「未登录」渲染成
    「一个还没设密码的普通用户」的空壳（头像 U、角色“普通用户”、密码卡片提示
    “尚未设置登录密码”），对第三方用户是严重误导，故直接拒绝渲染并给出明确提示。

    文案分层：主文案面向**最终用户**（发生了什么 + 我能做什么），技术线索降为灰色小字
    面向**集成方**（/embed/chat 是内部路由，对终端用户无意义，不该占据主视觉）。
  -->
  <div
    v-if="gateState === 'granted'"
    class="h-full w-full overflow-y-auto overflow-x-hidden bg-gray-100 custom-scrollbar px-3 sm:px-4"
  >
    <PersonalCenter />
  </div>

  <div
    v-else
    class="h-full w-full overflow-y-auto overflow-x-hidden bg-gray-100 custom-scrollbar flex items-center justify-center px-4 py-8 sm:px-6"
  >
    <!-- 核验中 -->
    <div v-if="gateState === 'checking'" class="text-center">
      <div class="animate-spin rounded-full h-10 w-10 border-b-2 border-primary mx-auto mb-3"></div>
      <p class="text-sm text-gray-500">正在核验访问权限…</p>
    </div>

    <!-- 未登录 / 会话失效：拒绝访问 -->
    <div
      v-else-if="gateState === 'unauthorized'"
      role="alert"
      aria-live="assertive"
      class="w-full max-w-md rounded-2xl border border-red-200 bg-white p-6 text-center shadow-sm sm:p-8"
    >
      <div class="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-full bg-red-50">
        <svg class="h-7 w-7 text-red-600" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M18.364 18.364A9 9 0 005.636 5.636m12.728 12.728A9 9 0 015.636 5.636m12.728 12.728L5.636 5.636" />
        </svg>
      </div>

      <span class="inline-flex items-center rounded-full border border-red-200 bg-red-50 px-3 py-1 text-xs font-bold tracking-[0.2em] text-red-600">
        未授权访问
      </span>

      <h1 class="mt-3 text-lg font-bold text-gray-900">无法访问个人中心</h1>
      <p class="mt-2 text-sm leading-relaxed text-gray-600">
        本页面需要平台账号登录后才能访问。<br />
        当前未登录，或登录状态已失效。
      </p>
      <p class="mt-2 text-sm leading-relaxed text-gray-500">
        请先登录平台账号，再重新打开本页面。
      </p>

      <button
        type="button"
        class="mt-5 inline-flex items-center gap-1.5 rounded-lg bg-red-600 px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-red-700 focus:outline-none focus:ring-2 focus:ring-red-500 focus:ring-offset-2"
        @click="verifyCredential"
      >
        我已登录，重新核验
      </button>

      <!-- 技术线索：给集成方排障用，不占据终端用户的注意力 -->
      <p class="mt-4 border-t border-gray-100 pt-3 text-[11px] leading-relaxed text-gray-400">
        第三方系统嵌入时，需先通过
        <code class="rounded bg-gray-100 px-1 py-0.5 font-mono">/embed/chat</code>
        建立会话，或联系管理员获取访问入口。
      </p>
    </div>

    <!-- 非认证类失败（断网 / 服务重启 / 上游 5xx）：不得误判成用户未登录 -->
    <div
      v-else
      role="alert"
      class="w-full max-w-md rounded-2xl border border-amber-200 bg-white p-6 text-center shadow-sm sm:p-8"
    >
      <div class="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-full bg-amber-50">
        <svg class="h-7 w-7 text-amber-600" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
        </svg>
      </div>

      <span class="inline-flex items-center rounded-full border border-amber-200 bg-amber-50 px-3 py-1 text-xs font-bold tracking-[0.2em] text-amber-600">
        暂时不可用
      </span>

      <h1 class="mt-3 text-lg font-bold text-gray-900">暂时无法确认访问权限</h1>
      <p class="mt-2 text-sm leading-relaxed text-gray-600">
        登录状态核验未能完成，可能是网络或服务暂时不可用。
      </p>
      <p class="mt-2 text-sm leading-relaxed text-gray-500">
        请稍后重试；若持续出现，请联系管理员。
      </p>

      <button
        type="button"
        class="mt-5 inline-flex items-center gap-1.5 rounded-lg bg-amber-600 px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-amber-700 focus:outline-none focus:ring-2 focus:ring-amber-500 focus:ring-offset-2"
        @click="verifyCredential"
      >
        重试
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
// 复用主站个人中心组件：任何 PersonalCenter 的功能更新都会同步到嵌入页，
// 不在此处复制实现，避免两份代码漂移。
import { onMounted, ref } from "vue";
import axios from "../utils/axios";
import PersonalCenter from "./PersonalCenter.vue";

/**
 * checking     正在核验
 * granted      凭据有效，渲染个人中心
 * unauthorized 认证类失败（401/403），即「未登录 / 会话失效」
 * error        非认证类失败（断网、服务重启、上游 5xx），不应诬指用户未登录
 */
type GateState = "checking" | "granted" | "unauthorized" | "error";

const gateState = ref<GateState>("checking");

/** 只有认证类失败才算「未登录」；网络抖动与上游 5xx 不是。 */
const isAuthFailure = (error: any): boolean => {
  const status = error?.response?.status;
  return status === 401 || status === 403;
};

/**
 * 渲染前的凭据门禁。
 *
 * 用与 PersonalCenter 同一个接口 /api/portal/auth/me 判定，凭据来源与主站完全一致
 * （X-API-Key header / portal_session Cookie / embed_session Cookie，见
 * app/core/dependencies.py 的 require_api_key），不引入新的鉴权口径。
 * /embed/ 路径下 401 已被 utils/axios.ts 抑制（不跳登录、不清本地存储），
 * 因此这里能可靠拿到失败结果并自行决策。
 */
const verifyCredential = async () => {
  gateState.value = "checking";
  try {
    const response = await axios.get("/api/portal/auth/me");
    // 200 但结构异常同样视为无有效身份，绝不带着空数据渲染
    gateState.value = response.data?.status === "success" ? "granted" : "unauthorized";
  } catch (error) {
    gateState.value = isAuthFailure(error) ? "unauthorized" : "error";
    if (gateState.value === "unauthorized") {
      console.warn("[EmbedPersonal] 未检测到有效登录凭据，已拒绝渲染个人中心");
    } else {
      console.error("[EmbedPersonal] 登录状态核验失败（非认证类）", error);
    }
  }
};

onMounted(verifyCredential);
</script>

<style scoped>
/* 与 Dashboard 布局的滚动条保持同一口径：常驻 gutter 避免布局抖动，仅 hover 时显示滑块 */
.custom-scrollbar::-webkit-scrollbar {
  width: 6px;
  height: 6px;
}
.custom-scrollbar::-webkit-scrollbar-track {
  background: transparent;
}
.custom-scrollbar::-webkit-scrollbar-thumb {
  background-color: transparent;
  border-radius: 10px;
}
.custom-scrollbar:hover::-webkit-scrollbar-thumb {
  background-color: rgba(156, 163, 175, 0.3);
}
.custom-scrollbar::-webkit-scrollbar-thumb:hover {
  background-color: rgba(107, 114, 128, 0.5);
}

.custom-scrollbar {
  scrollbar-gutter: stable;
  scrollbar-width: thin;
  scrollbar-color: transparent transparent;
}
.custom-scrollbar:hover {
  scrollbar-color: rgba(156, 163, 175, 0.3) transparent;
}
</style>
