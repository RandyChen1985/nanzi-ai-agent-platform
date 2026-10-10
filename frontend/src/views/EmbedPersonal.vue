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

    凭据入口与 /embed/chat 同口径：`?ticket=emt_...`（推荐，一次性票据由宿主后端签发）
    或已有会话 Cookie。**不接受 `?token=` 长期 API Key**——嵌入个人中心没有存量宿主需要
    兼容，长期密钥一旦进浏览器地址栏就再也收不回来。

    文案分层：主文案面向**最终用户**（发生了什么 + 我能做什么），技术线索降为灰色小字
    面向**集成方**（/embed/chat 与 ?ticket= 是接入口径，对终端用户无意义，不该占据主视觉）。
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
        第三方系统嵌入时，由宿主后端签发一次性 Ticket 后以
        <code class="rounded bg-gray-100 px-1 py-0.5 font-mono">/embed/personal?ticket=emt_…</code>
        打开本页；也可先通过
        <code class="rounded bg-gray-100 px-1 py-0.5 font-mono">/embed/chat</code>
        建立会话。本页不接受长期 API Key。
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
import { onMounted, onUnmounted, ref } from "vue";
import axios from "../utils/axios";
import PersonalCenter from "./PersonalCenter.vue";

/**
 * checking     正在核验
 * granted      凭据有效，渲染个人中心
 * unauthorized 认证类失败（401/403），即「未登录 / 会话失效」
 * error        非认证类失败（断网、服务重启、上游 5xx），不应诬指用户未登录
 */
type GateState = "checking" | "granted" | "unauthorized" | "error";

/** 上报给宿主的失败原因，与 /embed/chat 的 INIT_FAILURE.reason 取值保持一致。 */
type InitFailureReason =
  | "missing_token"
  | "invalid_token"
  | "invalid_ticket"
  | "origin_not_allowed";

/** ticket 兑换失败的具体原因。
 *
 * - `origin_not_allowed`：403，来源不被该票的 `allowed_origins` 允许。属**可修复**的接入
 *   配置问题（此时后端刻意不核销票据，换正确来源重试即可）。
 * - `ticket_invalid`：400/网络异常，票不存在、已过期或已被使用，必须重新签发。
 *
 * 两者不能共用一句文案：把「域名白名单不匹配」说成「票被用过了」会把排查方向带偏。
 */
type TicketFailureReason = "" | "origin_not_allowed" | "ticket_invalid";

const gateState = ref<GateState>("checking");
const authFailureReason = ref<InitFailureReason>("missing_token");
const ticketFailureReason = ref<TicketFailureReason>("");

/** 只有认证类失败才算「未登录」；网络抖动与上游 5xx 不是。 */
const isAuthFailure = (error: any): boolean => {
  const status = error?.response?.status;
  return status === 401 || status === 403;
};

/** 把 ticket 兑换的失败原因映射为宿主可区分的 INIT_FAILURE.reason。 */
const resolveTicketFailureReason = (): InitFailureReason =>
  ticketFailureReason.value === "origin_not_allowed"
    ? "origin_not_allowed"
    : "invalid_ticket";

// --- PostMessage 协议（与 /embed/chat 同一信封：source + 可选 instance_id）---

const EMBED_MESSAGE_SOURCE = "nanzi-agent-embed";

/** 多实例隔离标识：一页嵌入多个本组件时，宿主凭它区分消息与本次会话。 */
const instanceId = ref("");

const normalizeInstanceId = (value: unknown): string =>
  String(value ?? "").trim().slice(0, 128);

const postMessageToHost = (payload: Record<string, any>) => {
  const message: Record<string, any> = { source: EMBED_MESSAGE_SOURCE, ...payload };
  if (instanceId.value) message.instance_id = instanceId.value;
  window.parent?.postMessage(message, "*");
};

// --- 嵌入会话令牌：ticket 兑换后注入请求头并落到本 tab ---

/**
 * 会话令牌的本 tab 持久化（sessionStorage）。
 *
 * 为什么必须有它：`portal_session` / `embed_session` 都是 `SameSite=Lax`，而 SameSite 的
 * 判定基准是**顶层站点的站**——跨站 iframe（宿主与平台不同主域）里的请求既不带也不接受
 * 这两个 Cookie。ticket 又只是一次性票据，撑不住刷新。能撑住刷新的只有换来的 `emb_ses_`
 * 令牌，因此必须落在本 tab。
 *
 * 隔离粒度是 **tab + origin**，不是 iframe：同一 tab 里多个同源 iframe 共享 sessionStorage，
 * 所以键名按 `instance_id` 分桶，避免一页多实例互相覆盖造成身份串号。
 * 它确实是 JS 可读的，因此这里只放**短期、可吊销**的会话令牌，绝不放长期 API Key。
 */
const EMBED_SESSION_STORAGE_PREFIX = "nzi_embed_personal_session_token";

const embedSessionStorageKey = (): string =>
  instanceId.value
    ? `${EMBED_SESSION_STORAGE_PREFIX}:${encodeURIComponent(instanceId.value)}`
    : EMBED_SESSION_STORAGE_PREFIX;

const persistEmbedSession = (token: string): boolean => {
  if (!token) return false;
  try {
    sessionStorage.setItem(embedSessionStorageKey(), token);
    return true;
  } catch (e) {
    // 隐私模式 / 存储被禁时降级：同站场景仍可由 Cookie 支撑刷新
    console.warn("[EmbedPersonal] 无法持久化嵌入会话令牌:", e);
    return false;
  }
};

const readEmbedSession = (): string => {
  try {
    return (sessionStorage.getItem(embedSessionStorageKey()) || "").trim();
  } catch {
    return "";
  }
};

const clearEmbedSession = (): void => {
  try {
    sessionStorage.removeItem(embedSessionStorageKey());
  } catch {
    /* 存储不可用时无需处理 */
  }
};

/**
 * 把会话令牌同时写入 axios 默认请求头与本 tab 存储。
 *
 * ⚠️ 写默认头这一步**不能省**：PersonalCenter 九个 Tab 的请求全部依赖 Cookie 认证，
 * 跨站 iframe 里这些请求同样收不到 Cookie。只把令牌用于门禁核验的话，页面的表现会是
 * 「门禁通过、各 Tab 却全是空数据 / 报错」——比拒绝渲染更难排查。
 *
 * 返回是否成功落到本 tab（供调用方决定能否安全清除 URL 里的 ticket）。
 */
const applySessionToken = (token: string): boolean => {
  const value = (token || "").trim();
  if (!value) return false;
  axios.defaults.headers.common["Authorization"] = `Bearer ${value}`;
  axios.defaults.headers.common["X-API-Key"] = value;
  return persistEmbedSession(value);
};

/** 会话被服务端拒绝时一并清掉注入的请求头，避免「带着失效令牌反复重试」。 */
const clearAppliedCredentials = (): void => {
  clearEmbedSession();
  delete axios.defaults.headers.common["Authorization"];
  delete axios.defaults.headers.common["X-API-Key"];
};

/** 从地址栏移除已核销的一次性 ticket：它会留在浏览器历史、录屏与分享链接里。
 *  只在「刷新确实有凭据可依」时调用——否则刷新会连凭据一起失去。 */
const stripUrlTicket = () => {
  try {
    const url = new URL(window.location.href);
    if (!url.searchParams.has("ticket")) return;
    url.searchParams.delete("ticket");
    // 只替换 path + query + hash，不触及 origin；instance_id 等参数原样保留
    window.history.replaceState(window.history.state, "", url.pathname + url.search + url.hash);
  } catch (e) {
    console.warn("[EmbedPersonal] 清除地址栏 ticket 失败:", e);
  }
};

/** 后端返回的 `session_cookie_issued` 只说明「服务端写了 Set-Cookie」，**不等于浏览器会存、
 *  更不等于后续请求会带上**（跨站 iframe 下的 Lax Cookie 不会被发送）。
 *  因此优先看本 tab 的 sessionStorage 是否真的拿到了令牌，Cookie 只作同站补充。 */
const lastSessionCookieIssued = ref(false);

const maybeStripUrlAfterSessionReady = () => {
  if (readEmbedSession() || lastSessionCookieIssued.value) {
    stripUrlTicket();
  }
};

// --- ticket 兑换 ---

const exchangeTicketAndApply = async (ticket: string): Promise<boolean> => {
  ticketFailureReason.value = "";
  try {
    const res = await axios.post("/api/v1/embed/tickets/exchange", { ticket: ticket.trim() });
    if (res.data && res.data.code === 200 && res.data.data?.session_token) {
      applySessionToken(String(res.data.data.session_token));
      lastSessionCookieIssued.value = Boolean(res.data.data.session_cookie_issued);
      maybeStripUrlAfterSessionReady();
      return true;
    }
    // 2xx 但业务码非 200：按「票不可用」处理
    ticketFailureReason.value = "ticket_invalid";
    return false;
  } catch (err: any) {
    // 403 = 来源不被该票的 allowed_origins 允许；其余（400 等）= 票已失效/已被使用
    const status = err?.response?.status;
    ticketFailureReason.value = status === 403 ? "origin_not_allowed" : "ticket_invalid";
    console.error("[EmbedPersonal] ticket 兑换失败:", err);
    return false;
  }
};

/** 同一张 ticket 的并发/重复兑换去重。
 *
 * URL `?ticket=` 与宿主经 postMessage 下发的 ticket 可能**同时**到达（宿主通常会在收到
 * NANZI_WIDGET_READY 后回传 ticket），而 ticket 是一次性的（后端 GETDEL 原子核销），
 * 并发兑换必然有一方失败并误报「凭证已失效」。这里让同一张票复用同一次兑换结果。
 */
const inflightTicketExchanges = new Map<string, Promise<boolean>>();
const consumedTicketValues = new Set<string>();

const exchangeTicketOnce = (ticket: string): Promise<boolean> => {
  const key = (ticket || "").trim();
  if (!key) return Promise.resolve(false);
  if (consumedTicketValues.has(key)) return Promise.resolve(true);
  const existing = inflightTicketExchanges.get(key);
  if (existing) return existing;

  const pending = exchangeTicketAndApply(key)
    .then((ok) => {
      if (ok) consumedTicketValues.add(key);
      return ok;
    })
    .finally(() => {
      inflightTicketExchanges.delete(key);
    });
  inflightTicketExchanges.set(key, pending);
  return pending;
};

/** 以 ticket 换取会话并重新核验；失败时如实上报具体原因。 */
const applyTicketFromHost = async (ticket: string) => {
  if (gateState.value !== "granted") gateState.value = "checking";
  const ok = await exchangeTicketOnce(ticket);
  if (!ok) {
    authFailureReason.value = resolveTicketFailureReason();
    gateState.value = "unauthorized";
    postMessageToHost({ type: "INIT_FAILURE", reason: authFailureReason.value });
    return;
  }
  await verifyCredential();
};

// --- 宿主下行指令 ---

const handleHostMessage = (event: MessageEvent) => {
  const data = event.data;
  if (!data || typeof data !== "object") return;

  const messageInstanceId = normalizeInstanceId((data as any).instance_id);
  if (messageInstanceId && instanceId.value && messageInstanceId !== instanceId.value) {
    return; // 属于其它实例的消息
  }

  const ticket = String((data as any).ticket || "").trim();
  // 只接受短期会话令牌的两种字段名，**刻意不接受 token / api_key**（长期 Key 不入嵌入页）
  const sessionToken = String((data as any).session_token || (data as any).new_token || "").trim();

  switch ((data as any).type) {
    case "INIT_CONFIG":
    case "RESET_SESSION":
      if (ticket) {
        void applyTicketFromHost(ticket);
      } else if (sessionToken) {
        applySessionToken(sessionToken);
        void verifyCredential();
      }
      break;
  }
};

// --- 启动引导与凭据门禁 ---

/** 一次性的启动引导：解析 URL、宣告就绪、把可用凭据落到请求头。
 *  返回 false 表示引导阶段已判定无凭据可用（状态与上报都已处理），不必再核验。 */
const bootstrapEmbedSession = async (): Promise<boolean> => {
  const query = new URLSearchParams(window.location.search);
  const queryInstanceId = normalizeInstanceId(query.get("instance_id"));
  if (queryInstanceId) instanceId.value = queryInstanceId;

  // 先宣告就绪：宿主收到后可能经 INIT_CONFIG / RESET_SESSION 下发 ticket
  //（把凭据放在 postMessage 里而不是 URL 上的接法）。
  postMessageToHost({ type: "NANZI_WIDGET_READY" });

  // 刷新场景：本 tab 已持久化的会话令牌。跨站 iframe 下 Cookie 不会被发送，
  // 这里读回的令牌就是唯一凭据来源。
  const persistedToken = readEmbedSession();
  if (persistedToken) applySessionToken(persistedToken);

  const ticketFromUrl = (query.get("ticket") || "").trim();
  if (ticketFromUrl) {
    const ok = await exchangeTicketOnce(ticketFromUrl);
    if (!ok) {
      // 宿主明确下发了票据却不可用，说明接入配置有误。**不回退 Cookie**：
      // 静默换成另一个身份比直接报错危险得多。
      authFailureReason.value = resolveTicketFailureReason();
      gateState.value = "unauthorized";
      postMessageToHost({ type: "INIT_FAILURE", reason: authFailureReason.value });
      return false;
    }
  }
  return true;
};

let bootstrapped = false;

/**
 * 渲染前的凭据门禁。
 *
 * 用与 PersonalCenter 同一个接口 /api/portal/auth/me 判定，凭据来源与主站完全一致
 * （X-API-Key header / portal_session Cookie / embed_session Cookie，见
 * app/core/dependencies.py 的 require_api_key），不引入新的鉴权口径。
 * /embed/ 路径下 401 已被 utils/axios.ts 抑制（不跳登录、不清本地存储），
 * 因此这里能可靠拿到失败结果并自行决策。
 *
 * 首次调用先完成嵌入引导（ticket 兑换 / 恢复本 tab 会话）；之后的调用
 *（用户点「我已登录，重新核验」或宿主经 postMessage 下发新凭据）只做核验。
 */
const verifyCredential = async () => {
  gateState.value = "checking";

  if (!bootstrapped) {
    bootstrapped = true;
    const ready = await bootstrapEmbedSession();
    if (!ready) return; // 引导阶段已判定失败并上报 INIT_FAILURE
  }

  try {
    const response = await axios.get("/api/portal/auth/me");
    // 200 但结构异常同样视为无有效身份，绝不带着空数据渲染
    gateState.value = response.data?.status === "success" ? "granted" : "unauthorized";
    if (gateState.value === "unauthorized") {
      authFailureReason.value = "invalid_token";
      postMessageToHost({ type: "INIT_FAILURE", reason: authFailureReason.value });
      return;
    }
    postMessageToHost({ type: "INIT_SUCCESS" });
  } catch (error) {
    gateState.value = isAuthFailure(error) ? "unauthorized" : "error";
    if (gateState.value === "unauthorized") {
      authFailureReason.value = "invalid_token";
      clearAppliedCredentials();
      console.warn("[EmbedPersonal] 未检测到有效登录凭据，已拒绝渲染个人中心");
      postMessageToHost({ type: "INIT_FAILURE", reason: authFailureReason.value });
    } else {
      // 断网 / 5xx 不是「未登录」，也不上报 INIT_FAILURE——那会让宿主去重签一张没问题的票
      console.error("[EmbedPersonal] 登录状态核验失败（非认证类）", error);
    }
  }
};

// 先注册监听再宣告就绪：宿主可能在收到 NANZI_WIDGET_READY 后立刻回传 ticket。
onMounted(() => {
  window.addEventListener("message", handleHostMessage);
});
onMounted(verifyCredential);

onUnmounted(() => {
  window.removeEventListener("message", handleHostMessage);
});
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
