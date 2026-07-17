<template>
  <div class="min-h-screen flex items-center justify-center bg-gray-50 px-4">
    <div class="max-w-md w-full text-center space-y-8 p-10 bg-white rounded-2xl shadow-xl border border-gray-100 animate-fade-in">
      <div class="relative inline-block">
        <!-- Icon Layer -->
        <div class="bg-red-50 p-6 rounded-full inline-flex">
          <svg class="w-16 h-16 text-red-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
          </svg>
        </div>
        <!-- Pulse Effect -->
        <div class="absolute inset-0 rounded-full animate-ping bg-red-400 opacity-20"></div>
      </div>

      <div class="space-y-3">
        <h1 class="text-2xl font-black text-gray-900 tracking-tight">暂无界面权限</h1>
        <p class="text-sm text-gray-500 leading-relaxed">
          您的账号尚未分配任何可访问的功能模块。<br/>
          请联系系统管理员为您配置权限后重新登录。
        </p>
      </div>

      <div class="pt-6 flex flex-col gap-3">
        <button 
          @click="logout" 
          class="w-full py-3 px-4 bg-gray-900 text-white font-bold rounded-xl hover:bg-gray-800 transition-all shadow-lg shadow-gray-200 active:scale-95"
        >
          退出登录
        </button>
        <button 
          @click="refresh" 
          class="w-full py-3 px-4 bg-white text-gray-600 font-semibold border border-gray-200 rounded-xl hover:bg-gray-50 transition-all active:scale-95"
        >
          尝试刷新
        </button>
      </div>

      <div class="mt-8 border-t border-gray-50 pt-6">
        <span class="text-[10px] uppercase font-bold text-gray-300 tracking-widest">NanZi AI Agent Platform</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useRouter } from 'vue-router';

const router = useRouter();

const logout = () => {
  localStorage.removeItem('api_key');
  localStorage.removeItem('user_info');
  router.push('/login');
};

const refresh = () => {
  window.location.reload();
};
</script>

<style scoped>
@keyframes fade-in {
  from { opacity: 0; transform: translateY(10px); }
  to { opacity: 1; transform: translateY(0); }
}
.animate-fade-in {
  animation: fade-in 0.5s ease-out forwards;
}
</style>
