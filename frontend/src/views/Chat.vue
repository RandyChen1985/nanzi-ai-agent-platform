<template>
  <div class="h-full flex flex-col bg-white overflow-hidden relative">
    <!-- Header Decor Removed for full-screen look -->
    
    <div class="flex-1 relative">
        <iframe 
            v-if="iframeUrl"
            ref="chatFrame"
            :src="iframeUrl"
            width="100%"
            height="100%"
            frameborder="0"
            class="w-full h-full"
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
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue';
import { useRoute } from 'vue-router';

const route = useRoute();

const chatFrame = ref<HTMLIFrameElement | null>(null);
const iframeUrl = ref('');
const loading = ref(true);
const isInitTimedOut = ref(false);
let timeoutTimer: any = null;

const initChat = () => {
    // 基础路径
    iframeUrl.value = '/embed/chat';
    
    // 设置超时检测 (5秒)
    isInitTimedOut.value = false;
    clearTimeout(timeoutTimer);
    timeoutTimer = setTimeout(() => {
        if (loading.value) {
            isInitTimedOut.value = true;
        }
    }, 5000);
};

const sendInitConfig = () => {
    const userInfoStr = localStorage.getItem('user_info');
    const apiKey = localStorage.getItem('api_key');
    
    if (userInfoStr && chatFrame.value?.contentWindow) {
        try {
            const userInfo = JSON.parse(userInfoStr);
            
            // 发送初始化配置，注入当前登录用户的 User ID 和 Name
            const displayName = userInfo.real_name || userInfo.user_name;
            chatFrame.value.contentWindow.postMessage({
                type: 'INIT_CONFIG',
                token: apiKey,
                user_info: {
                    user_id: userInfo.user_id,
                    user_name: userInfo.user_name,
                    real_name: userInfo.real_name,
                    role: userInfo.role
                },
                // 可以设置一些 Portal 特有的欢迎语
                welcome_message_override: `您好，${displayName}！我是您的智能助手，已为您准备就绪。`,
                open_saved_report: route.query.report_id ? { report_id: String(route.query.report_id), run_id: String(route.query.run_id || '') } : null
            }, '*');
            
            // 发送成功后，稍微延迟关闭 loading，以确保子页面有时间渲染
            setTimeout(() => {
                loading.value = false;
            }, 500);
            
        } catch (e) {
            console.error("Failed to parse user info for chat injection", e);
        }
    }
};

const retryInit = () => {
    // 尝试直接发送配置
    sendInitConfig();
    // 同时也重置一下 iframe，双重保险 (Optional, logic depends on if postMessage works blindly)
    // iframeUrl.value = iframeUrl.value; // Force reload might be too aggressive if frame is just slow
};

const handleMessage = (event: MessageEvent) => {
    const data = event.data;
    
    // 当挂件准备好后，注入当前用户信息
    if (data.source === 'yunshu-agent-embed' && data.type === 'YUNSHU_WIDGET_READY') {
        sendInitConfig();
    }
};

onMounted(() => {
    window.addEventListener('message', handleMessage);
    initChat();
});

onUnmounted(() => {
    window.removeEventListener('message', handleMessage);
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
