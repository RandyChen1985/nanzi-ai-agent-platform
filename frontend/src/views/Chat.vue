<template>
  <div class="h-full w-full max-w-full flex flex-col bg-white overflow-hidden relative min-w-0">
    <!-- Header Decor Removed for full-screen look -->
    
    <div class="flex-1 relative w-full max-w-full min-w-0 overflow-hidden">
        <iframe 
            v-if="iframeUrl"
            ref="chatFrame"
            :src="iframeUrl"
            frameborder="0"
            class="w-full h-full min-w-full block"
            style="width: 100%; min-width: 100%; max-width: 100%; height: 100%; border: none;"
        ></iframe>
        
        <!-- Loading State -->
        <div v-if="loading" class="absolute inset-0 flex flex-col items-center justify-center bg-gray-50/50 backdrop-blur-sm z-20">
            <div class="w-12 h-12 border-4 border-blue-100 border-b-blue-600 rounded-full animate-spin mb-4"></div>
            <p class="text-sm font-bold text-gray-400 uppercase tracking-widest mb-4">初始化智能对话中...</p>
            
            <!-- Timeout Retry Button -->
            <div v-if="isInitTimedOut" class="flex flex-col items-center space-y-2 animate-fade-in">
                <span class="text-xs text-red-400">连接似乎有点慢...</span>
                <button 
                    @click="retryInit" 
                    class="px-4 py-2 bg-white border border-gray-200 shadow-sm text-sm text-gray-600 hover:text-blue-600 hover:border-blue-300 rounded-lg transition-all flex items-center space-x-2"
                >
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                    </svg>
                    <span>手动初始化</span>
                </button>
            </div>
        </div>
    </div>

    <!--
      移动端「我的账号」把手：与 Dashboard 顶部把手的**同一套视觉语言**——一道 32×4 的
      圆角灰线，只是旋转 90°。顶部把手长这样：

          ▬▬▬▬            （32×4 的圆角灰线，撑满 10px 高的白色顶条）

      顶部那条白条之所以不显眼，是因为它撑满宽度、与页面底色是同一块白；
      右侧照抄「白条」就会变成一条莫名其妙的**白边**（用户截图反馈）。
      所以这里只保留那道竖灰线，其余完全透明，不加背景色、图标、阴影与圆角。
      - 放在 Chat.vue 而不是 Dashboard：**谁持有 iframe，谁负责跟它通信**。
      - 只在 `embedView === 'chat'` 时出现：挂件切到 /embed/personal 后会上报
        EMBED_VIEW=personal，此时必须让位给账号页那颗同坐标的把手（否则上层那颗盖住下层，
        表现为「点了没反应」）。
      - 视觉 10px 宽，热区用 before 伪元素向左扩 44px（约 54×44），不影响可点性。
    -->
    <button
      v-if="embedView === 'chat'"
      type="button"
      class="group fixed right-0 top-1/2 z-40 -mt-[22px] flex h-11 w-2.5 items-center justify-center before:absolute before:inset-y-0 before:-left-11 before:w-11 before:content-[''] lg:hidden"
      title="我的账号"
      aria-label="我的账号"
      @click="openEmbeddedAccount"
    >
      <span class="h-8 w-1 rounded-full bg-gray-300 transition-colors group-hover:bg-gray-400 dark:bg-gray-600 dark:group-hover:bg-gray-500" />
    </button>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import {
    SAVED_REPORT_OPEN_EVENT,
    createSavedReportOpenMessage,
    type SavedReportOpenRequest,
} from '../utils/savedReportOpenProtocol';
const route = useRoute();
const router = useRouter();

const chatFrame = ref<HTMLIFrameElement | null>(null);
const iframeUrl = ref('');
const loading = ref(true);
const isInitTimedOut = ref(false);
const widgetReady = ref(false);
/** 挂件当前停在哪一屏：chat（对话）/ personal（我的账号）。 */
const embedView = ref<'chat' | 'personal'>('chat');
let timeoutTimer: any = null;
/** 挂件主动新开会话后清 URL 钉选时，跳过一次 query watch 触发的 INIT，避免重复初始化 */
let skipNextQueryInit = false;
/**
 * 门户快捷问题的幂等键：一个页面生命周期内固定，重推 INIT_CONFIG 时挂件据此去重。
 * 挂件侧按 request_id 记忆已消费的提问，避免「重试握手 / 切智能体重推」把同一问题发两次。
 */
const portalQuestionRequestId = `portal-q-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;

/**
 * 一次性消费 URL 里的门户提问参数。
 *
 * `portal_question` 以前一直留在地址栏：用户随手刷新，`Chat.vue` 重新挂载后会在
 * `NANZI_WIDGET_READY` 时把同一个问题再发一遍（`open_saved_report` 有 request_id 幂等，
 * 这里没有）。投递给挂件后立刻从 query 移除，刷新不再重放，也不会污染分享/回退链接。
 */
const consumePortalQuestionFromUrl = () => {
    if (!route.query.portal_question) return;
    const nextQuery = { ...route.query };
    delete nextQuery.portal_question;
    delete nextQuery.portal_action;
    delete nextQuery.source;
    router.replace({ path: route.path, query: nextQuery });
};

const parseSavedReportDetailTab = (value: unknown): SavedReportOpenRequest["detail_tab"] | null => {
  const tab = String(value || "").trim();
  if (tab === "info" || tab === "runs" || tab === "subscription") return tab;
  return null;
};

const initChat = () => {
    // 基础路径。预览入口（/dashboard/chat?preview=1）需把标记透传给挂件：
    // 挂件据此开一个干净的临时会话，而不是续接上次对话。
    const previewQuery = String(route.query.preview || '') === '1' ? '?preview=1' : '';
    iframeUrl.value = `/embed/chat${previewQuery}`;
    
    // 设置超时检测 (5秒)
    isInitTimedOut.value = false;
    clearTimeout(timeoutTimer);
    timeoutTimer = setTimeout(() => {
        if (loading.value) {
            isInitTimedOut.value = true;
            // 超时后必须把遮罩本身撤掉：它是 `bg-gray-50/50 + backdrop-blur-sm + z-20`，
            // 永久压在 iframe 之上会把挂件里已经正确渲染的画面糊成一片白雾
            // （用户反馈「dark 啥也看不清、内容隐约可见」）。撤掉后即使挂件真的没就绪，
            // 用户看到的是真实状态而不是一层蒙版。
            loading.value = false;
        }
    }, 5000);
};

const sendInitConfig = () => {
    if (chatFrame.value?.contentWindow) {
        chatFrame.value.contentWindow.postMessage({
            type: 'INIT_CONFIG',
            // 凭据由同源 HttpOnly Cookie 承载（iframe 指向同源 /embed/chat），
            // 不再从本地存储读取令牌；初始化配置必须照常下发，否则会话/报告等参数会一起丢失。
            conversation_id: route.query.conversation_id ? String(route.query.conversation_id) : null,
            agent_id: route.query.agent_id ? String(route.query.agent_id) : null,
            open_saved_report: route.query.report_id ? {
                report_id: String(route.query.report_id),
                run_id: String(route.query.run_id || ''),
                request_id: String(route.query.open_request_id || ''),
                ...(parseSavedReportDetailTab(route.query.report_detail_tab)
                  ? { detail_tab: parseSavedReportDetailTab(route.query.report_detail_tab) }
                  : {}),
                ...(route.query.run_now === '1' ? { run_now: true } : {}),
            } : null,
            portal_question: route.query.portal_question ? {
                query: String(route.query.portal_question),
                action: route.query.portal_action === 'fill' ? 'fill' : 'send',
                request_id: portalQuestionRequestId
            } : null
        }, window.location.origin);

        // 参数已经交给挂件，立刻从地址栏摘掉：否则这一页刷新（或回退再进）会把同一问题再发一遍。
        consumePortalQuestionFromUrl();
            
        // 发送成功后，稍微延迟关闭 loading，以确保子页面有时间渲染
        setTimeout(() => {
            loading.value = false;
        }, 500);
    }
};

const retryInit = () => {
    // 尝试直接发送配置
    sendInitConfig();
    // 同时也重置一下 iframe，双重保险 (Optional, logic depends on if postMessage works blindly)
    // iframeUrl.value = iframeUrl.value; // Force reload might be too aggressive if frame is just slow
};

const sendSavedReportOpenRequest = (target: SavedReportOpenRequest) => {
    if (!widgetReady.value || !target?.report_id || !chatFrame.value?.contentWindow) return;
    chatFrame.value.contentWindow.postMessage(createSavedReportOpenMessage(target), window.location.origin);
};

const handleSavedReportOpenEvent = (event: Event) => {
    sendSavedReportOpenRequest((event as CustomEvent<SavedReportOpenRequest>).detail);
};

/**
 * 右侧把手 → 让挂件切到「我的账号」。
 *
 * 走 postMessage 而不是改 iframe src：改 src 会把整个挂件重新加载，
 * 对话上下文、流式状态、未发完的消息全部丢失。挂件内部是 SPA 跳转，代价最小。
 */
const openEmbeddedAccount = () => {
    const frame = chatFrame.value?.contentWindow;
    if (!frame) return;
    frame.postMessage({ type: 'OPEN_ACCOUNT' }, window.location.origin);
};

const clearHostConversationPin = () => {
    if (!route.query.conversation_id) return;
    const nextQuery = { ...route.query };
    delete nextQuery.conversation_id;
    skipNextQueryInit = true;
    router.replace({ path: route.path, query: nextQuery });
};

const handleMessage = (event: MessageEvent) => {
    // 只认自己那颗 iframe 发来的消息：本页是全站最外层容器，任何同源/跨源的窗口都能
    // 向它 postMessage，不校验 source 就等于把「切视图/清置顶」这些动作开放给任意页面。
    const frame = chatFrame.value?.contentWindow;
    if (!frame || event.source !== frame) return;
    if (event.origin && event.origin !== window.location.origin) return;

    const data = event.data;
    if (!data || typeof data !== 'object') return;
    if (data.source === 'nanzi-agent-embed' && data.type === 'OPEN_DATA_PORTAL_FULL') {
        router.push({ path: '/dashboard/personal', query: { tab: 'data' } });
        return;
    }
    if (
        data.source === 'nanzi-agent-embed' &&
        data.type === 'CONVERSATION_CHANGED' &&
        data.clear_host_conversation_pin
    ) {
        clearHostConversationPin();
        return;
    }
    
    // 挂件上报当前视图：切到账号页时要收起宿主自己的「我的账号」把手，
    // 否则两颗按钮叠在同一像素上，点到的永远是上层那颗（表现为「点了没反应」）
    if (data.source === 'nanzi-agent-embed' && data.type === 'EMBED_VIEW') {
        embedView.value = data.view === 'personal' ? 'personal' : 'chat';
        return;
    }

    // 当挂件准备好后，注入当前用户信息
    if (data.source === 'nanzi-agent-embed' && data.type === 'NANZI_WIDGET_READY') {
        widgetReady.value = true;
        sendInitConfig();
    }
};

onMounted(() => {
    window.addEventListener('message', handleMessage);
    window.addEventListener(SAVED_REPORT_OPEN_EVENT, handleSavedReportOpenEvent);
    initChat();
});

// 工作台等入口再次带 agent_id / conversation_id 进入时，向已就绪的挂件重推配置
watch(
    () => [
        route.query.agent_id,
        route.query.conversation_id,
        route.query.report_id,
        route.query.run_id,
        route.query.report_detail_tab,
        route.query.run_now,
    ],
    () => {
        if (skipNextQueryInit) {
            skipNextQueryInit = false;
            return;
        }
        if (widgetReady.value) {
            sendInitConfig();
        }
    },
);

onUnmounted(() => {
    window.removeEventListener('message', handleMessage);
    window.removeEventListener(SAVED_REPORT_OPEN_EVENT, handleSavedReportOpenEvent);
    clearTimeout(timeoutTimer);
});
</script>

<style scoped>
@keyframes fade-in {
  from { opacity: 0; }
  to { opacity: 1; }
}
.animate-fade-in {
  animation: fade-in 0.3s ease-out forwards;
}
</style>
