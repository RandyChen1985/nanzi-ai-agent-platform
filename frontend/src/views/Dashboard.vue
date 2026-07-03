<script setup lang="ts">
import { useRouter, useRoute } from "vue-router";
import { computed, ref, onMounted, onUnmounted, watch } from "vue";
import axios from "../utils/axios";
import Toast from "../components/Toast.vue";
import { useBranding } from "../composables/useBranding";

const router = useRouter();
const route = useRoute();
const { branding, loadBranding, applyDocumentTitle, resolveRepoUrl } = useBranding();
const repoUrl = computed(() => resolveRepoUrl());
const isCollapsed = ref(false);
const showMobileSidebar = ref(false);
const windowWidth = ref(window.innerWidth);
const isMobile = computed(() => windowWidth.value < 1024);
const appVersion = import.meta.env.VITE_APP_VERSION || "Dev Build";

const showLogoutDialog = ref(false);
const showUserInfoDialog = ref(false);
const showOnlineUsersDialog = ref(false);
const userApiKey = ref("");
const loadingApiKey = ref(false);
const onlineUserCount = ref(0);
const onlineUsers = ref<any[]>([]);

// Toast State
const toast = ref({
  show: false,
  message: "",
  type: "info" as "success" | "error" | "warning" | "info",
  key: 0,
});

const showToast = (
  message: string,
  type: "success" | "error" | "warning" | "info" = "info"
) => {
  toast.value = {
    show: true,
    message,
    type,
    key: toast.value.key + 1,
  };
};

const closeToast = () => {
  toast.value.show = false;
};

const userInfo = ref({
  id: 0,
  user_name: "",
  role: "",
  permissions: {},
  created_at: "",
  remark: "",
});

const fetchUserInfo = async () => {
  try {
    const apiKey = localStorage.getItem("api_key");
    if (!apiKey) {
      router.push("/login");
      return;
    }

    // First try to get from localStorage
    const cachedUserInfo = localStorage.getItem("user_info");
    if (cachedUserInfo) {
      userInfo.value = JSON.parse(cachedUserInfo);
    }

    // Refresh user info from server
    // 无需手动添加 header，拦截器会自动添加
    const response = await axios.get("/api/portal/auth/me");
    if (response.data && response.data.status === "success") {
      userInfo.value = response.data.data;
      localStorage.setItem("user_info", JSON.stringify(response.data.data));
    }
  } catch (e) {
    console.error("Auth check failed", e);
    // 拦截器已处理跳转，这里只需清理本地存储
    localStorage.removeItem("api_key");
    localStorage.removeItem("user_info");
    router.push("/login");
  }
};

onMounted(() => {
  loadBranding();
  fetchUserInfo();
  fetchOnlineUsers();
});

const fetchOnlineUsers = async () => {
  try {
    const response = await axios.get("/api/portal/dashboard/online-users");
    if (response.data) {
      onlineUserCount.value = response.data.count;
      onlineUsers.value = response.data.users || [];
    }
  } catch (e) {
    console.error("Failed to fetch online users", e);
  }
};

const openOnlineUsers = () => {
  if (userInfo.value.role === 'admin') {
    fetchOnlineUsers(); // Refresh
    showOnlineUsersDialog.value = true;
  }
};

const logout = () => {
  showLogoutDialog.value = true;
};

const confirmLogout = async () => {
  try {
    await axios.post("/api/portal/auth/logout");
  } catch (e) {
    console.error("Logout API failed", e);
  } finally {
    localStorage.removeItem("api_key");
    localStorage.removeItem("user_info");
    showLogoutDialog.value = false;
    router.push("/login");
  }
};

const cancelLogout = () => {
  showLogoutDialog.value = false;
};

const openUserInfo = () => {
  router.push("/dashboard/personal");
};

const closeUserInfo = () => {
  showUserInfoDialog.value = false;
};

const fetchApiKey = async () => {
  if (!userInfo.value.id) return;

  loadingApiKey.value = true;
  try {
    const response = await axios.get(
      `/api/portal/management/api-key/${userInfo.value.id}`
    );
    userApiKey.value = response.data.api_key;
  } catch (error: any) {
    console.error("Failed to fetch API key:", error);
    showToast(error.response?.data?.detail || "获取 API Key 失败", "error");
  } finally {
    loadingApiKey.value = false;
  }
};

const copyApiKey = async () => {
  if (!userApiKey.value) {
    showToast("API Key 未加载", "warning");
    return;
  }

  try {
    await navigator.clipboard.writeText(userApiKey.value);
    showToast("API Key 已复制到剪贴板", "success");
  } catch (err) {
    console.error("Failed to copy:", err);
    showToast("复制失败，请手动复制", "error");
  }
};

const handleEscape = (e: KeyboardEvent) => {
  if (e.key === "Escape") {
    if (showUserInfoDialog.value) {
      closeUserInfo();
    } else if (showLogoutDialog.value) {
      cancelLogout();
    }
  }
};

onMounted(() => {
  document.addEventListener("keydown", handleEscape);
});

onUnmounted(() => {
  document.removeEventListener("keydown", handleEscape);
});

// 监听路由变化，移动端下自动收起侧边栏
watch(() => route.path, () => {
  if (isMobile.value) {
    showMobileSidebar.value = false;
  }
});

const breadcrumbs = computed(() => {
  const matched = route.matched;
  return matched
    .map((m) => {
      const title = m.meta?.title;
      if (typeof title === "string") return title;
      return typeof m.name === "string" ? m.name : undefined;
    })
    .filter((item): item is string => Boolean(item));
});

watch(
  () => [route.path, breadcrumbs.value[breadcrumbs.value.length - 1], branding.value.product_name],
  () => {
    applyDocumentTitle(breadcrumbs.value[breadcrumbs.value.length - 1] || undefined);
  },
  { immediate: true },
);

const toggleSidebar = () => {
  if (isMobile.value) {
    showMobileSidebar.value = !showMobileSidebar.value;
  } else {
    isCollapsed.value = !isCollapsed.value;
  }
};

const handleResize = () => {
  windowWidth.value = window.innerWidth;
  if (!isMobile.value) {
    showMobileSidebar.value = false;
  }
};

onMounted(() => {
  window.addEventListener('resize', handleResize);
});

onUnmounted(() => {
  window.removeEventListener('resize', handleResize);
});

// 权限辅助函数
const hasMenuPerm = (perm: string) => {
  if (userInfo.value.role === 'admin') return true;
  const userMenus = (userInfo.value.permissions as any)?.menus || [];
  return userMenus.includes(perm);
};

interface MenuItem {
  name: string;
  to: string;
  icon: string;
  perm: string;
  desktopOnly?: boolean;
  activeNames?: string[];
}

interface MenuGroup {
  title: string;
  items: MenuItem[];
}

const collapsedMenuGroups = ref<Record<string, boolean>>({});

// 菜单组定义
const menuGroups: MenuGroup[] = [
  {
    title: '',
    items: [
      { name: '概览', to: '/dashboard', icon: 'dashboard', perm: 'menu:dashboard', activeNames: ['Overview'] }
    ]
  },
  {
    title: '智能助手',
    items: [
      { name: '智能助手', to: '/dashboard/chat', icon: 'chat', perm: 'menu:ai_chat' }
    ]
  },
  {
    title: '智能体开发平台',
    items: [
      { name: '智能体中心', to: '/dashboard/agent-management', icon: 'agent_mgmt', perm: 'menu:agent_management', activeNames: ['AgentManagement'] },
      { name: '技能工作台', to: '/dashboard/skills', icon: 'skills', perm: 'menu:skills_management', activeNames: ['SkillsManagement'] },
      { name: '记忆工作台', to: '/dashboard/memory', icon: 'memory', perm: 'menu:memory_management', activeNames: ['MemoryManagement'] },
      { name: '提示词工坊', to: '/dashboard/prompts', icon: 'prompts', perm: 'menu:prompts', desktopOnly: true, activeNames: ['PromptStudio'] },

      { name: '任务调度台', to: '/dashboard/tasks', icon: 'tasks', perm: 'menu:task_center', activeNames: ['TaskCenter'] },
      { name: '智能体测评', to: '/dashboard/agent-debug', icon: 'agent_debug', perm: 'menu:agent_debug', desktopOnly: true, activeNames: ['AgentDebug'] },
      { name: '接口调试台', to: '/dashboard/playground', icon: 'playground', perm: 'menu:playground', desktopOnly: true, activeNames: ['Playground'] },
      { name: '组件调试台', to: '/dashboard/widget-debug', icon: 'widget', perm: 'menu:widget_debug', desktopOnly: true, activeNames: ['WidgetDebugger'] }
    ]
  },
  {
    title: 'ChatBI 开发平台',
    items: [
      { name: '数据源管理', to: '/dashboard/data-sources', icon: 'data_source', perm: 'menu:data_sources', desktopOnly: true, activeNames: ['DataSourceManagement'] },
      { name: '元数据管理', to: '/dashboard/metadata', icon: 'metadata', perm: 'menu:metadata', desktopOnly: true, activeNames: ['Metadata', 'MetadataTables'] },
      { name: '案例集管理', to: '/dashboard/chatbi-examples', icon: 'chat_bubble_left_right', perm: 'menu:chatbi_examples', activeNames: ['ChatBIExampleManagement'] }
    ]
  },
  {
    title: '知识库开发平台',
    items: [
      { name: '知识库管理', to: '/dashboard/knowledge-bases', icon: 'knowledge_base', perm: 'menu:knowledge_management', desktopOnly: true, activeNames: ['KnowledgeBaseManagement'] },
      { name: '检索测试', to: '/dashboard/knowledge-retrieval-test', icon: 'playground', perm: 'menu:knowledge_retrieval_test', desktopOnly: true, activeNames: ['KnowledgeRetrievalTest'] }
    ]
  },
  {
    title: '日志&TOKEN分析',
    items: [
      { name: '审计日志', to: '/dashboard/audit', icon: 'audit', perm: 'menu:system:audit', desktopOnly: true, activeNames: ['Audit'] },
      { name: '聊天日志', to: '/dashboard/chat-logs', icon: 'chat_logs', perm: 'menu:chat_logs', desktopOnly: true, activeNames: ['ChatLogs'] },
      { name: 'Token 统计', to: '/dashboard/token-stats', icon: 'token_stats', perm: 'menu:system:audit', desktopOnly: true, activeNames: ['TokenStats'] }
    ]
  },
  {
    title: '系统管理',
    items: [
      { name: '用户管理', to: '/dashboard/users', icon: 'users', perm: 'menu:system:users', activeNames: ['Users'] },
      { name: '角色管理', to: '/dashboard/roles', icon: 'roles', perm: 'menu:system:roles', activeNames: ['Roles'] },
      { name: '系统配置', to: '/dashboard/system', icon: 'system', perm: 'menu:system:config', desktopOnly: true, activeNames: ['System'] }
    ]
  }
];

const isItemActive = (item: any) => {
  if (route.path === item.to) return true;
  if (item.activeNames && item.activeNames.includes(route.name)) return true;
  return false;
};

const isGroupActive = (group: MenuGroup) => {
  return group.items.some(item => isItemActive(item));
};

const isGroupCollapsed = (group: MenuGroup) => {
  if (!group.title) return false;
  if (isGroupActive(group)) return false;
  return Boolean(collapsedMenuGroups.value[group.title]);
};

const toggleMenuGroup = (group: MenuGroup) => {
  if (!group.title || isCollapsed.value) return;
  collapsedMenuGroups.value[group.title] = !collapsedMenuGroups.value[group.title];
};

const filteredMenuGroups = computed(() => {
  return menuGroups.map(group => ({
    ...group,
    items: group.items.filter(item => {
      // 检查权限
      if (!hasMenuPerm(item.perm)) return false;
      // 检查移动端限制
      if (item.desktopOnly && isMobile.value) return false;
      return true;
    })
  })).filter(group => group.items.length > 0);
});
</script>

<template>
  <div class="h-screen bg-gray-50 flex overflow-hidden relative">
    <!-- Mobile Overlay -->
    <div 
      v-if="isMobile && showMobileSidebar" 
      class="fixed inset-0 bg-black/50 z-20 transition-opacity backdrop-blur-sm"
      @click="showMobileSidebar = false"
    ></div>

    <!-- Sidebar -->
    <aside
      class="bg-sidebar text-white shadow-xl flex flex-col z-30 transition-all duration-300 ease-in-out flex-shrink-0"
      :class="[
        isMobile ? 'fixed inset-y-0 left-0 h-full' : 'relative',
        isMobile 
            ? (showMobileSidebar ? 'translate-x-0 w-[220px]' : '-translate-x-full w-[220px]') 
            : (isCollapsed ? 'w-20' : 'w-[220px]')
      ]"
    >
      <!-- Brand Header -->
      <div
        class="h-16 flex items-center bg-sidebar border-b border-gray-700 overflow-hidden whitespace-nowrap"
        :class="isCollapsed ? 'justify-center px-0' : 'px-4'"
      >
        <img
          :src="branding.icon_url"
          class="w-8 h-8 flex-shrink-0 rounded-lg object-cover"
          alt="Logo"
        />
        <transition name="fade">
          <div v-if="!isCollapsed" class="ml-2.5 flex flex-col justify-center -translate-y-0.5">
            <span class="text-[15px] font-bold tracking-wide leading-tight">{{ branding.product_name }}</span>
            <component
              :is="repoUrl ? 'a' : 'span'"
              :href="repoUrl || undefined"
              :target="repoUrl ? '_blank' : undefined"
              :rel="repoUrl ? 'noopener noreferrer' : undefined"
              class="group flex items-center text-[10px] text-gray-500 font-medium tracking-wider leading-none mt-0.5 transition-colors"
              :class="repoUrl ? 'hover:text-white' : ''"
              :title="repoUrl ? 'View on GitHub' : undefined"
            >
              <svg
                v-if="repoUrl"
                class="w-3 h-3 mr-1 opacity-80 group-hover:opacity-100 transition-opacity"
                fill="currentColor"
                viewBox="0 0 24 24"
                aria-hidden="true"
              >
                <path fill-rule="evenodd" d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.531 1.032 1.531 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0112 6.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0022 12.017C22 6.484 17.522 2 12 2z" clip-rule="evenodd" />
              </svg>
              <span>v{{ appVersion }}</span>
            </component>
          </div>
        </transition>
      </div>

      <!-- Navigation (Scrollable internally) -->
      <nav
        class="flex-1 py-4 space-y-4 overflow-y-auto overflow-x-hidden custom-scrollbar"
        @click="isMobile ? showMobileSidebar = false : null"
      >
        <div v-for="group in filteredMenuGroups" :key="group.title" class="space-y-1">
          <!-- Group Title (Hierarchy Name) -->
          <button 
            v-if="!isCollapsed && group.title" 
            type="button"
            class="py-2 flex items-center justify-between gap-2 text-left text-[10px] font-black text-gray-500 uppercase tracking-[0.15em] select-none hover:text-gray-300 transition-colors"
            :class="isCollapsed ? 'w-full justify-center px-0 mx-0' : 'w-[calc(100%-1.5rem)] mx-3 px-3'"
            :aria-expanded="!isGroupCollapsed(group)"
            @click.stop="toggleMenuGroup(group)"
          >
            <span class="truncate">{{ group.title }}</span>
            <svg 
              class="w-3 h-3 flex-shrink-0 transition-transform duration-200"
              :class="isGroupCollapsed(group) ? '-rotate-90' : 'rotate-0'"
              fill="none" 
              stroke="currentColor" 
              viewBox="0 0 24 24"
            >
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M19 9l-7 7-7-7" />
            </svg>
          </button>
          <div v-else-if="isCollapsed && group.title" class="h-px bg-gray-700/50 mx-4 my-2"></div>

          <!-- Group Items -->
          <transition name="menu-group">
            <div v-show="!isGroupCollapsed(group)" class="space-y-1">
              <router-link
                v-for="item in group.items"
                :key="item.to"
                :to="item.to"
                class="group flex items-center py-2 text-sm font-medium transition-all duration-200 whitespace-nowrap"
                :class="[
                  isItemActive(item)
                    ? 'bg-primary text-white shadow-md'
                    : 'text-gray-300 hover:bg-gray-800 hover:text-white',
                  isCollapsed ? 'justify-center px-0 mx-0 rounded-none' : 'px-4 mx-3 rounded-xl',
                ]"
              >
                <!-- Icons -->
                <svg v-if="item.icon === 'dashboard'" class="flex-shrink-0 h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6" /></svg>
                <svg v-else-if="item.icon === 'chat'" class="flex-shrink-0 h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z" /></svg>
                <svg v-else-if="item.icon === 'playground'" class="flex-shrink-0 h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4" /></svg>
                <svg v-else-if="item.icon === 'widget'" class="flex-shrink-0 h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 4a2 2 0 114 0v1a1 1 0 001 1h3a1 1 0 011 1v3a1 1 0 01-1 1h-1a2 2 0 100 4h1a1 1 0 011 1v3a1 1 0 01-1 1h-3a1 1 0 01-1-1v-1a2 2 0 10-4 0v1a1 1 0 01-1 1H7a1 1 0 01-1-1v-3a1 1 0 00-1-1H4a2 2 0 110-4h1a1 1 0 001-1V7a1 1 0 011-1h3a1 1 0 001-1V4z" /></svg>
                <svg v-else-if="item.icon === 'agent_debug'" class="flex-shrink-0 h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19.428 15.428a2 2 0 00-1.022-.547l-2.384-.477a6 6 0 00-3.86.517l-.318.158a6 6 0 01-3.86.517L6.05 15.21a2 2 0 00-1.806.547M8 4h8l-1 1v5.172a2 2 0 00.586 1.414l5 5c1.26 1.26.367 3.414-1.415 3.414H4.828c-1.782 0-2.674-2.154-1.414-3.414l5-5A2 2 0 009 10.172V5L8 4z" /></svg>
                <svg v-else-if="item.icon === 'data_source'" class="flex-shrink-0 h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 7c0 1.657 3.582 3 8 3s8-1.343 8-3-3.582-3-8-3-8 1.343-8 3zm0 0v5c0 1.657 3.582 3 8 3s8-1.343 8-3V7M4 12v5c0 1.657 3.582 3 8 3s8-1.343 8-3v-5" /></svg>
                <svg v-else-if="item.icon === 'metadata'" class="flex-shrink-0 h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 10h18M3 14h18m-9-4v8m-7 0h14a2 2 0 002-2V6a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" /></svg>
                <svg v-else-if="item.icon === 'knowledge_base'" class="flex-shrink-0 h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 14l9-5-9-5-9 5 9 5zm0 0l6.16-3.422a12.083 12.083 0 01.665 6.479A11.952 11.952 0 0012 20.055a11.952 11.952 0 00-6.824-2.998 12.078 12.078 0 01.665-6.479L12 14z" /></svg>
                <svg v-else-if="item.icon === 'agent_mgmt'" class="flex-shrink-0 h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z" /></svg>
                <svg v-else-if="item.icon === 'chat_bubble_left_right'" class="flex-shrink-0 h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 8h10M7 12h4m1 8l-4-4H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-3l-4 4z" /></svg>
                <svg v-else-if="item.icon === 'prompts'" class="flex-shrink-0 h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" /></svg>
                <svg v-else-if="item.icon === 'skills'" class="flex-shrink-0 h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5S19.832 5.477 21 6.253v13C19.832 18.477 18.246 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" /></svg>
                <svg v-else-if="item.icon === 'memory'" class="flex-shrink-0 h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4m0 5c0 2.21-3.582 4-8 4s-8-1.79-8-4" /></svg>

                <svg v-else-if="item.icon === 'tasks'" class="flex-shrink-0 h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
                <svg v-else-if="item.icon === 'audit'" class="flex-shrink-0 h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" /></svg>
                <svg v-else-if="item.icon === 'token_stats'" class="flex-shrink-0 h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 7h6m0 10v-3m-3 3h.01M9 17h.01M9 14h.01M12 14h.01M15 11h.01M12 11h.01M9 11h.01M7 21h10a2 2 0 002-2V5a2 2 0 00-2-2H7a2 2 0 00-2 2v14a2 2 0 002 2z" /></svg>
                <svg v-else-if="item.icon === 'chat_logs'" class="flex-shrink-0 h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z" /></svg>
                <svg v-else-if="item.icon === 'users'" class="flex-shrink-0 h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4.354a4 4 0 110 5.292M15 21H3v-1a6 6 0 0112 0v1zm0 0h6v-1a6 6 0 00-9-5.197M13 7a4 4 0 11-8 0 4 4 0 018 0z" /></svg>
                <svg v-else-if="item.icon === 'roles'" class="flex-shrink-0 h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" /></svg>
                <svg v-else-if="item.icon === 'system'" class="flex-shrink-0 h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" /><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" /></svg>

                <transition name="fade">
                  <span v-if="!isCollapsed" class="ml-3">{{ item.name }}</span>
                </transition>
              </router-link>
            </div>
          </transition>
        </div>
      </nav>

      <!-- User Profile (Clickable) -->
      <button
        @click="openUserInfo"
        class="py-4 bg-sidebar border-t border-gray-700 flex items-center overflow-hidden whitespace-nowrap hover:bg-gray-800 transition-colors w-full text-left focus:outline-none focus:ring-2 focus:ring-primary"
        :class="isCollapsed ? 'justify-center px-0' : 'px-4'"
        title="查看个人信息"
      >
        <div
          class="h-8 w-8 rounded-full bg-gray-500 flex flex-shrink-0 items-center justify-center text-xs font-bold text-white uppercase"
        >
          {{ userInfo.user_name ? userInfo.user_name.substring(0, 2) : "USER" }}
        </div>
        <transition name="fade">
          <div v-if="!isCollapsed" class="ml-3 flex-1">
            <p class="text-sm font-medium text-white">
              {{ userInfo.user_name || "Loading..." }}
            </p>
            <p class="text-xs text-gray-400">
              {{ userInfo.role === "admin" ? "管理员" : "普通用户" }}
            </p>
          </div>
        </transition>
        <transition name="fade">
          <svg
            v-if="!isCollapsed"
            class="h-4 w-4 text-gray-400"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              stroke-linecap="round"
              stroke-linejoin="round"
              stroke-width="2"
              d="M9 5l7 7-7 7"
            />
          </svg>
        </transition>
      </button>
    </aside>

    <!-- Main Content Area -->
    <div class="flex-1 flex flex-col overflow-hidden min-w-0">
      <!-- Top Header -->
      <header
        class="bg-white shadow-sm h-16 flex justify-between items-center px-4 sm:px-6 lg:px-8 z-10 border-b border-gray-200 flex-shrink-0"
      >
        <div class="flex items-center">
          <!-- Sidebar Toggle Button -->
          <button
            @click="toggleSidebar"
            class="mr-4 text-gray-500 hover:text-gray-700 focus:outline-none focus:ring-2 focus:ring-primary rounded-md p-1"
            title="Toggle Sidebar"
          >
            <svg
              class="h-6 w-6"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                v-if="!isCollapsed"
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M4 6h16M4 12h16M4 18h7"
              />
              <path
                v-else
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M4 6h16M4 12h16M4 18h16"
              />
            </svg>
          </button>

          <!-- Breadcrumbs -->
          <nav class="hidden md:flex" aria-label="Breadcrumb">
            <ol class="flex items-center space-x-2">
              <li>
                <router-link 
                  :to="isMobile ? '/dashboard/chat' : '/dashboard'"
                  class="text-gray-400 hover:text-primary transition-colors"
                >
                  <svg
                    class="flex-shrink-0 h-5 w-5"
                    fill="currentColor"
                    viewBox="0 0 20 20"
                  >
                    <path
                      d="M10.707 2.293a1 1 0 00-1.414 0l-7 7a1 1 0 001.414 1.414L4 10.414V17a1 1 0 001 1h2a1 1 0 001-1v-2a1 1 0 011-1h2a1 1 0 011 1v2a1 1 0 001 1h2a1 1 0 001-1v-6.586l.293.293a1 1 0 001.414-1.414l-7-7z"
                    />
                  </svg>
                </router-link>
              </li>
              <li
                v-for="(crumb, index) in breadcrumbs"
                :key="index"
                class="flex items-center"
              >
                <svg
                  class="flex-shrink-0 h-5 w-5 text-gray-300"
                  fill="currentColor"
                  viewBox="0 0 20 20"
                >
                  <path
                    fill-rule="evenodd"
                    d="M7.293 14.707a1 1 0 010-1.414L10.586 10 7.293 6.707a1 1 0 011.414-1.414l4 4a1 1 0 010 1.414l-4 4a1 1 0 01-1.414 0z"
                    clip-rule="evenodd"
                  />
                </svg>
                <span
                  class="ml-2 text-sm font-medium text-gray-500 hover:text-gray-700 capitalize"
                  >{{ crumb }}</span
                >
              </li>
            </ol>
          </nav>

          <!-- Mobile Title -->
          <div v-if="isMobile" class="flex-1 min-w-0 flex items-center">
             <router-link to="/dashboard/chat" class="p-1 mr-1 text-gray-400 hover:text-primary transition-colors">
                <svg class="w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
                    <path d="M10.707 2.293a1 1 0 00-1.414 0l-7 7a1 1 0 001.414 1.414L4 10.414V17a1 1 0 001 1h2a1 1 0 001-1v-2a1 1 0 011-1h2a1 1 0 011 1v2a1 1 0 001 1h2a1 1 0 001-1v-6.586l.293.293a1 1 0 001.414-1.414l-7-7z" />
                </svg>
             </router-link>
             <h1 class="text-sm font-bold text-gray-900 truncate">
                {{ breadcrumbs[breadcrumbs.length - 1] || branding.product_name }}
             </h1>
          </div>
        </div>

        <div class="flex items-center space-x-2 sm:space-x-4 flex-shrink-0 flex-nowrap">
          <!-- Online Users Widget -->
          <div 
            @click="openOnlineUsers"
            class="flex items-center px-2 sm:px-3 py-1 bg-green-50 border border-green-100/50 rounded-full transition-all shadow-sm flex-shrink-0 whitespace-nowrap" 
            :class="userInfo.role === 'admin' ? 'cursor-pointer hover:bg-green-100/80 active:scale-95' : 'cursor-default'"
            :title="userInfo.role === 'admin' ? '点击查看在线用户列表' : '当前实时在线人数'"
          >
            <span class="relative flex h-2 w-2 mr-1 sm:mr-2 flex-shrink-0">
              <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75"></span>
              <span class="relative inline-flex rounded-full h-2 w-2 bg-green-500"></span>
            </span>
            <span class="text-[10px] sm:text-xs font-bold text-green-700 tracking-tight tabular-nums">
              {{ onlineUserCount }} <span class="font-medium opacity-80 ml-0.5">在线</span>
            </span>
          </div>
          <div class="h-6 w-px bg-gray-200 mx-1 sm:mx-2 flex-shrink-0" aria-hidden="true"></div>
          <button
            @click="logout"
            class="flex items-center px-2 sm:px-3 py-1.5 text-xs sm:text-sm font-medium text-red-600 bg-red-50 hover:bg-red-100 rounded-lg transition-all duration-200 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-red-500 group flex-shrink-0 whitespace-nowrap"
          >
            <svg
              class="h-4 w-4 mr-1 sm:mr-1.5 transition-transform group-hover:translate-x-0.5"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1"
              />
            </svg>
            <span class="hidden xs:inline">退出</span>
          </button>
        </div>
      </header>

      <!-- Main Scrollable Content -->
      <main 
        class="flex-1 overflow-y-auto bg-gray-100 custom-scrollbar"
        :class="route.name === 'AIChat' ? 'p-0' : 'p-0 sm:p-4 md:p-8'"
      >
        <router-view v-slot="{ Component }">
          <transition name="page">
            <Suspense>
              <template #default>
                <component :is="Component" :key="$route.fullPath" />
              </template>
              <template #fallback>
                <div class="flex items-center justify-center h-64">
                  <div class="text-center">
                    <div
                      class="animate-spin rounded-full h-12 w-12 border-b-2 border-primary mx-auto mb-4"
                    ></div>
                    <p class="text-gray-500 text-sm">加载中...</p>
                  </div>
                </div>
              </template>
            </Suspense>
          </transition>
        </router-view>
      </main>
    </div>

    <!-- Global Toast Notification -->
    <teleport to="body">
      <Toast
        v-if="toast.show"
        :key="toast.key"
        :message="toast.message"
        :type="toast.type"
        @close="closeToast"
      />
    </teleport>

    <!-- Logout Confirmation Dialog -->
    <teleport to="body">
      <transition name="dialog">
        <div
          v-if="showLogoutDialog"
          class="fixed inset-0 z-50 flex items-center justify-center bg-black bg-opacity-50"
          @click.self="cancelLogout"
        >
          <div class="bg-white rounded-lg shadow-xl max-w-md w-full mx-4 p-6">
            <div class="flex items-start">
              <div class="flex-shrink-0">
                <svg
                  class="h-6 w-6 text-yellow-600"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    stroke-linecap="round"
                    stroke-linejoin="round"
                    stroke-width="2"
                    d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
                  />
                </svg>
              </div>
              <div class="ml-3 flex-1">
                <h3 class="text-lg font-medium text-gray-900">确认退出</h3>
                <p class="mt-2 text-sm text-gray-500">您确定要退出登录吗？</p>
              </div>
            </div>
            <div class="mt-6 flex justify-end space-x-3">
              <button
                @click="cancelLogout"
                class="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-md hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-primary"
              >
                取消
              </button>
              <button
                @click="confirmLogout"
                class="px-4 py-2 text-sm font-medium text-white bg-red-600 rounded-md hover:bg-red-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-red-500"
              >
                退出登录
              </button>
            </div>
          </div>
        </div>
      </transition>
    </teleport>

    <!-- User Info Dialog -->
    <teleport to="body">
      <transition name="dialog">
        <div
          v-if="showUserInfoDialog"
          class="fixed inset-0 z-50 flex items-center justify-center bg-black bg-opacity-50 p-4"
          @click.self="closeUserInfo"
        >
          <div class="bg-white rounded-lg shadow-xl max-w-lg w-full p-6">
            <!-- Header -->
            <div class="flex items-center justify-between mb-4">
              <h3 class="text-xl font-semibold text-gray-900">个人信息</h3>
              <button
                @click="closeUserInfo"
                class="text-gray-400 hover:text-gray-600 focus:outline-none focus:ring-2 focus:ring-primary rounded-md p-1"
              >
                <svg
                  class="h-6 w-6"
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

            <!-- User Avatar -->
            <div class="flex items-center mb-6">
              <div
                class="h-16 w-16 rounded-full bg-primary flex items-center justify-center text-2xl font-bold text-white uppercase"
              >
                {{
                  userInfo.user_name ? userInfo.user_name.substring(0, 2) : "U"
                }}
              </div>
              <div class="ml-4">
                <h4 class="text-lg font-medium text-gray-900">
                  {{ userInfo.user_name }}
                </h4>
                <p class="text-sm text-gray-500">
                  <span
                    class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium"
                    :class="
                      userInfo.role === 'admin'
                        ? 'bg-purple-100 text-purple-800'
                        : 'bg-blue-100 text-blue-800'
                    "
                  >
                    {{ userInfo.role === "admin" ? "管理员" : "普通用户" }}
                  </span>
                </p>
              </div>
            </div>

            <!-- User Details -->
            <div class="space-y-4">
              <div>
                <label class="block text-sm font-medium text-gray-700"
                  >用户名</label
                >
                <p class="mt-1 text-sm text-gray-900">
                  {{ userInfo.user_name }}
                </p>
              </div>

              <div v-if="userInfo.remark">
                <label class="block text-sm font-medium text-gray-700"
                  >备注</label
                >
                <p class="mt-1 text-sm text-gray-900">{{ userInfo.remark }}</p>
              </div>

              <div>
                <label class="block text-sm font-medium text-gray-700"
                  >创建时间</label
                >
                <p class="mt-1 text-sm text-gray-900">
                  {{ userInfo.created_at || "-" }}
                </p>
              </div>

              <!-- API Key Section -->
              <div>
                <label class="block text-sm font-medium text-gray-700 mb-2"
                  >API Key</label
                >
                <div class="flex items-center space-x-2">
                  <input
                    type="text"
                    :value="userApiKey || '点击“查看”加载 API Key'"
                    readonly
                    class="flex-1 px-3 py-2 border border-gray-300 rounded-md bg-gray-50 text-sm font-mono"
                  />
                  <button
                    v-if="!userApiKey"
                    @click="fetchApiKey"
                    :disabled="loadingApiKey"
                    class="px-4 py-2 bg-primary text-white text-sm font-medium rounded-md hover:bg-primary-dark focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-primary disabled:opacity-50"
                  >
                    {{ loadingApiKey ? "加载中..." : "查看" }}
                  </button>
                  <button
                    v-else
                    @click="copyApiKey"
                    class="px-4 py-2 bg-green-600 text-white text-sm font-medium rounded-md hover:bg-green-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-green-500"
                    title="复制 API Key"
                  >
                    <svg
                      class="h-5 w-5"
                      fill="none"
                      stroke="currentColor"
                      viewBox="0 0 24 24"
                    >
                      <path
                        stroke-linecap="round"
                        stroke-linejoin="round"
                        stroke-width="2"
                        d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"
                      />
                    </svg>
                  </button>
                </div>
                <p class="mt-1 text-xs text-gray-500">
                  ⚠️ API Key 支持重复查看和复制
                </p>
              </div>
            </div>

            <!-- Footer -->
            <div class="mt-6 flex justify-end">
              <button
                @click="closeUserInfo"
                class="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-md hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-primary"
              >
                关闭
              </button>
            </div>
          </div>
        </div>
      </transition>
    </teleport>

    <!-- Online Users Dialog -->
    <teleport to="body">
      <transition name="dialog">
        <div
          v-if="showOnlineUsersDialog"
          class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4"
          @click.self="showOnlineUsersDialog = false"
        >
          <div class="bg-white dark:bg-gray-800 rounded-2xl shadow-2xl max-w-md w-full overflow-hidden flex flex-col border border-gray-200 dark:border-gray-700 animate-slide-up">
            <!-- Header -->
            <div class="px-6 py-4 border-b border-gray-100 dark:border-gray-700 flex justify-between items-center bg-green-50/30 dark:bg-green-900/10">
              <div class="flex items-center gap-2">
                <div class="w-2 h-2 rounded-full bg-green-500 animate-pulse"></div>
                <h3 class="text-lg font-black text-gray-800 dark:text-gray-100 uppercase tracking-widest">在线用户列表</h3>
              </div>
              <button @click="showOnlineUsersDialog = false" class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200">
                <svg class="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" /></svg>
              </button>
            </div>

            <!-- List -->
            <div class="flex-1 overflow-y-auto max-h-[60vh] p-4 custom-scrollbar">
              <div v-if="onlineUsers.length === 0" class="text-center py-10 text-gray-400 text-xs font-bold uppercase tracking-widest">
                暂无在线用户信息
              </div>
              <div v-else class="space-y-3">
                <div v-for="user in onlineUsers" :key="user.user_name" class="flex items-center p-3 rounded-xl bg-gray-50 dark:bg-gray-900/50 border border-gray-100 dark:border-gray-800 transition-all hover:border-green-200">
                  <div class="w-10 h-10 rounded-full bg-primary flex items-center justify-center text-white font-black uppercase text-sm flex-shrink-0">
                    {{ (user.real_name || user.user_name).substring(0, 2) }}
                  </div>
                  <div class="ml-3 flex-1 min-w-0">
                    <div class="flex items-center gap-2">
                      <span class="font-bold text-gray-800 dark:text-gray-100 truncate">{{ user.real_name || user.user_name }}</span>
                      <span class="px-1.5 py-0.5 rounded text-[9px] font-black uppercase tracking-tighter border" :class="user.role === 'admin' ? 'bg-purple-50 text-purple-600 border-purple-100' : 'bg-blue-50 text-blue-600 border-blue-100'">
                        {{ user.role === 'admin' ? 'Admin' : 'User' }}
                      </span>
                    </div>
                    <div class="text-[10px] text-gray-400 font-mono mt-0.5 truncate">@{{ user.user_name }}</div>
                  </div>
                  <div class="text-[10px] text-green-500 font-bold uppercase tracking-widest flex items-center gap-1 flex-shrink-0">
                    <span class="w-1.5 h-1.5 rounded-full bg-green-500"></span>
                    Active
                  </div>
                </div>
              </div>
            </div>

            <!-- Footer -->
            <div class="p-4 bg-gray-50 dark:bg-gray-800/50 border-t border-gray-100 dark:border-gray-700 flex justify-between items-center text-[10px] text-gray-400 font-black uppercase tracking-widest">
              <span>共计: {{ onlineUsers.length }} 位用户</span>
              <span class="text-[8px] opacity-60">实时更新</span>
            </div>
          </div>
        </div>
      </transition>
    </teleport>

    <!-- Permissions Modal Removed -->
  </div>
</template>

<style scoped>
/* Page Transition - Optimized for stability */
.page-enter-active {
  transition: opacity 0.2s ease-out, transform 0.2s ease-out;
}

.page-leave-active {
  transition: opacity 0.15s ease-in, transform 0.15s ease-in;
}

.page-enter-from {
  opacity: 0;
  transform: translateY(4px);
}

.page-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}

/* Sidebar Text Fade */
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.15s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
  width: 0; /* Helps collapse/expand smoothness for text */
}

.menu-group-enter-active,
.menu-group-leave-active {
  transition: opacity 0.18s ease, transform 0.18s ease;
}

.menu-group-enter-from,
.menu-group-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}

/* Dialog Animation */
.dialog-enter-active,
.dialog-leave-active {
  transition: opacity 0.3s ease;
}

.dialog-enter-from,
.dialog-leave-to {
  opacity: 0;
}

.dialog-enter-active > div,
.dialog-leave-active > div {
  transition: transform 0.3s ease, opacity 0.3s ease;
}

.dialog-enter-from > div,
.dialog-leave-to > div {
  transform: scale(0.9);
  opacity: 0;
}

/* Optional: Custom scrollbar styling for Webkit */
.custom-scrollbar::-webkit-scrollbar {
  width: 4px;
  height: 4px;
}
.custom-scrollbar::-webkit-scrollbar-track {
  background: transparent;
}
.custom-scrollbar::-webkit-scrollbar-thumb {
  background-color: transparent;
  border-radius: 10px;
}
.custom-scrollbar:hover::-webkit-scrollbar-thumb {
  background-color: rgba(156, 163, 175, 0.3); /* gray-400 with low opacity */
}
.custom-scrollbar::-webkit-scrollbar-thumb:hover {
  background-color: rgba(107, 114, 128, 0.5); /* gray-500 */
}

/* Firefox scrollbar hiding */
.custom-scrollbar {
  scrollbar-width: none;
}
.custom-scrollbar:hover {
  scrollbar-width: thin;
  scrollbar-color: rgba(156, 163, 175, 0.3) transparent;
}

/* Custom Tooltip Styles */
.custom-tooltip {
  position: relative;
  display: inline-flex;
  align-items: center;
}

.custom-tooltip::after {
  content: attr(data-tooltip);
  position: absolute;
  top: 150%;
  right: 0;
  transform: none;
  background-color: rgba(31, 41, 55, 0.95);
  color: white;
  padding: 8px 12px;
  border-radius: 8px;
  font-size: 13px;
  line-height: 1.5;
  white-space: pre-wrap;
  width: max-content;
  max-width: 300px;
  opacity: 0;
  visibility: hidden;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  z-index: 9999;
  box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.1),
    0 4px 6px -2px rgba(0, 0, 0, 0.05);
  pointer-events: none;
  font-weight: normal;
}

.custom-tooltip::before {
  content: "";
  position: absolute;
  top: 120%;
  right: 20px;
  border-width: 6px;
  border-style: solid;
  border-color: transparent transparent rgba(31, 41, 55, 0.95) transparent;
  opacity: 0;
  visibility: hidden;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  z-index: 9999;
}

.custom-tooltip:hover::after {
  opacity: 1;
  visibility: visible;
  top: 160%;
}

.custom-tooltip:hover::before {
  opacity: 1;
  visibility: visible;
  top: 130%;
}
</style>
