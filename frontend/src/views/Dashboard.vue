<script setup lang="ts">
import { useRouter, useRoute } from "vue-router";
import { computed, ref, onMounted, onUnmounted, watch, nextTick } from "vue";
import axios from "../utils/axios";
import Toast from "../components/Toast.vue";
import { useBranding } from "../composables/useBranding";
import { useAppTheme } from "../composables/useAppTheme";
import { persistUserInfo, clearUserSession } from "../utils/userSession";
import { userInitialOf } from "../utils/userInitial";
import { resolvePasswordExpiryNotice, passwordExpiryToastType, type PasswordInfo } from "../utils/passwordExpiry";
import PortalNotificationBell from "../components/PortalNotificationBell.vue";
import ConfirmModal from "../components/ConfirmModal.vue";
import SidebarMenuLink from "../components/menu/SidebarMenuLink.vue";
import { useNavigation, type NavNode } from "../composables/useNavigation";

const router = useRouter();
const route = useRoute();
const { branding, loadBranding, applyDocumentTitle, resolveRepoUrl } = useBranding();
const { theme, toggleTheme } = useAppTheme();
const repoUrl = computed(() => (branding.value.hide_version_link ? "" : resolveRepoUrl()));
const LEFT_SIDEBAR_COLLAPSED_KEY = "embed_left_sidebar_collapsed";
const isDesktopSidebar = () => window.matchMedia("(min-width: 1024px)").matches;
const readLeftSidebarCollapsedCache = () => {
  try {
    return localStorage.getItem(LEFT_SIDEBAR_COLLAPSED_KEY) === "1";
  } catch {
    return false;
  }
};
// 账号偏好加载前先用本机缓存占位，避免刷新时先展开再收起。
const isCollapsed = ref(isDesktopSidebar() && readLeftSidebarCollapsedCache());
const showMobileSidebar = ref(false);
// 移动端抽屉打开时锁定背景滚动，避免背景跟随滑动
watch(showMobileSidebar, (open) => {
  document.body.style.overflow = open ? "hidden" : "";
});
const dashboardContentRef = ref<HTMLElement | null>(null);
// 断点用 matchMedia 跟踪：只在跨越 1024px 时更新一次，
// 避免拖动窗口时每个 resize 事件都触发侧边栏（20+ 菜单项）重渲染
const desktopMediaQuery = window.matchMedia("(min-width: 1024px)");
const isMobile = ref(!desktopMediaQuery.matches);
/** chat 页（/dashboard/chat）整页只有一个 iframe，挂件自带标题栏与「更多」菜单；
 *  移动端再叠一条平台顶栏就是两条 header 抢同一块屏幕，所以这里默认收起。 */
const isChatRoute = computed(() => route.name === "AIChat");
/** chat 页移动端顶栏是否被唤出：默认 false，只留顶部一条把手可点开 */
const chatChromeOpen = ref(false);
const showDashboardHeader = computed(
  () => !isMobile.value || !isChatRoute.value || chatChromeOpen.value,
);
const displayVersion = computed(() => {
  const raw = (import.meta.env.VITE_APP_VERSION || "").trim();
  if (!raw || raw.toLowerCase() === "dev build" || raw.toLowerCase() === "dev" || raw.toLowerCase() === "development") {
    return "dev";
  }
  return raw.startsWith("v") || raw.startsWith("V") ? raw : `v${raw}`;
});
const dashboardContentSpacing = computed(() => {
  if (route.name === "AIChat") return "p-0";
  // 个人中心：两边都不再叠加外层水平边距。内容自身的内边距由 PersonalCenter 内部按 Tab
  // 分组设置（普通 Tab 有 px-4 sm:px-6，data/skills/mcp/tasks 全宽无内边距），这里再加
  // 一层只会白吃掉内容宽度——移动端 393px 视口下尤其明显，也与 /embed/personal 口径一致。
  if (route.name === "PersonalCenter") return "p-0";
  // 外链内嵌：内容是要铺满的第三方应用，四周留白会把对方的界面挤成"卡片里的网页"，
  // 也会在窄屏下白吃掉两侧宽度（对方站点常有最小宽度要求）。
  if (route.name === "ExternalMenu") return "p-0";
  if (route.name === "PersonalWorkbench") return "p-0 sm:px-4 sm:pt-2.5 sm:pb-4 md:px-6 md:pt-3 md:pb-6 lg:px-8 lg:pt-3.5 lg:pb-6";
  return "p-0 sm:px-4 sm:pt-2.5 sm:pb-4 md:px-6 md:pt-3 md:pb-6 lg:px-8 lg:pt-3.5 lg:pb-6";
});

const showLogoutDialog = ref(false);
const showOnlineUsersDialog = ref(false);
/** 退出请求进行中：禁用确认按钮，避免连点重复提交 */
const loggingOut = ref(false);
const onlineUserCount = ref(0);

/** 在线用户条目（/api/portal/dashboard/online-users 返回） */
interface OnlineUser {
  user_id: number;
  user_name: string;
  real_name?: string;
  role?: string;
  last_active?: string | number;
}

const onlineUsers = ref<OnlineUser[]>([]);
const onlineUsersLoading = ref(false);
const onlineUsersError = ref(false);
let onlineUsersRefreshTimer: ReturnType<typeof setInterval> | null = null;

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
  real_name: "",
  role: "",
  permissions: {},
  created_at: "",
  remark: "",
  // 自定义头像短路径（/auth/me 下发）；为空表示未上传，回退姓名首字。
  avatar_url: "" as string | null,
});

/** 左下角头像简称：中文取后两字、英文取前两字母（规则见 utils/userInitial） */
const sidebarInitial = computed(() =>
  userInitialOf(userInfo.value?.real_name || userInfo.value?.user_name, "US")
);
/**
 * 两个字比单字宽：桌面端 28px 方块里 12px 汉字会贴边，收一档到 11px；
 * 单字保持原字号，视觉重量与旧版一致。
 */
const sidebarInitialClass = computed(() => {
  const twoChars = Array.from(sidebarInitial.value).length > 1;
  if (isMobile.value) return twoChars ? "h-8 w-8 text-xs" : "h-8 w-8 text-sm";
  return twoChars ? "h-7 w-7 text-[11px]" : "h-7 w-7 text-xs";
});
// 落地页已可在「菜单管理」里配置（is_landing），点 Logo 就该回那里；
// 菜单数据还没到位时退回原有口径，避免首屏跳到一个空路由。
const homeRoute = computed(
  () => landingPath.value || (userInfo.value.role === 'admin' ? '/dashboard' : '/dashboard/workbench'),
);

// 刷新时的会话校验策略：服务重启窗口期（超时/5xx/断网）保留本地会话并退避重试，
// 只有确认是认证类失败（401/403）才清理凭据并回到登录页。
const AUTH_CHECK_RETRY_DELAYS = [1500, 3000];
let authCheckRetryTimer: ReturnType<typeof setTimeout> | null = null;
let authCheckRetryCount = 0;

/** 仅认证类失败才代表登录态失效；网络抖动、上游 5xx、超时都不是登出理由。 */
const isAuthFailure = (error: any) => {
  const status = error?.response?.status;
  return status === 401 || status === 403;
};

/** 请求序号：并发触发时丢弃过期响应，避免旧数据覆盖新数据 */
let userInfoSeq = 0;

const fetchUserInfo = async () => {
  const seq = ++userInfoSeq;
  try {
    // 登录态一律以后端 /auth/me 的响应为准：门户会话凭据已收敛为 HttpOnly Cookie，
    // 前端不再（也无法）通过读取 localStorage 判断是否登录。

    // First try to get from localStorage
    const cachedUserInfo = localStorage.getItem("user_info");
    if (cachedUserInfo) {
      try {
        userInfo.value = JSON.parse(cachedUserInfo);
      } catch {
        localStorage.removeItem("user_info");
      }
    }

    // Refresh user info from server
    // 无需手动添加 header，拦截器会自动添加
    const response = await axios.get("/api/portal/auth/me");
    if (seq !== userInfoSeq) return; // 已被更新的请求取代，丢弃本次响应
    if (response.data && response.data.status === "success") {
      authCheckRetryCount = 0;
      if (authCheckRetryTimer) {
        clearTimeout(authCheckRetryTimer);
        authCheckRetryTimer = null;
      }
      userInfo.value = response.data.data;
      persistUserInfo(response.data.data);
      // 触发登录后密码到期检测（方式 1: 轻量 Toast 提醒）
      triggerLoginPasswordNoticeToast(response.data.data);
      // 检查当前会话是否临时关闭过 Banner
      const uid = response.data.data.id || response.data.data.user_id || 'current';
      if (sessionStorage.getItem(`dismiss_pwd_banner_${uid}`)) {
        isPasswordExpireBannerDismissed.value = true;
      } else {
        isPasswordExpireBannerDismissed.value = false;
      }
    }
  } catch (e) {
    if (seq !== userInfoSeq) return; // 过期请求的失败不应影响当前状态
    console.error("Auth check failed", e);
    if (isAuthFailure(e)) {
      // 拦截器已处理跳转，这里彻底清理本地与 Cookie 凭据
      clearUserSession();
      localStorage.removeItem("api_key");
      localStorage.removeItem("user_info");
      router.push("/login");
      return;
    }
    // 服务重启/网络抖动/上游 5xx：保留本地会话，退避重试，避免被误踢到登录页
    console.warn("[Auth] 会话校验遇到非认证类失败，保留本地会话并重试");
    scheduleAuthCheckRetry();
  }
};

const scheduleAuthCheckRetry = () => {
  if (authCheckRetryCount >= AUTH_CHECK_RETRY_DELAYS.length) {
    return;
  }
  const delay = AUTH_CHECK_RETRY_DELAYS[authCheckRetryCount];
  authCheckRetryCount += 1;
  if (authCheckRetryTimer) {
    clearTimeout(authCheckRetryTimer);
  }
  authCheckRetryTimer = setTimeout(() => {
    authCheckRetryTimer = null;
    void fetchUserInfo();
  }, delay);
};

onUnmounted(() => {
  if (authCheckRetryTimer) {
    clearTimeout(authCheckRetryTimer);
    authCheckRetryTimer = null;
  }
});

// 密码到期提醒状态管理 (方式 1: 登录即时 Toast + 方式 2: 全局常驻 Top Banner)
const isPasswordExpireBannerDismissed = ref(false);

const passwordExpireNoticeData = computed(() =>
  resolvePasswordExpiryNotice((userInfo.value as any)?.password_info as PasswordInfo | undefined),
);

const showPasswordExpireBanner = computed(() => {
  if (!passwordExpireNoticeData.value) return false;
  // 已过期（逾期）状态下，横幅强制一直显示，不允许被关闭隐藏
  if (passwordExpireNoticeData.value.isExpired) return true;
  if (isPasswordExpireBannerDismissed.value) return false;
  return true;
});

const passwordExpireBannerStyle = computed(() => {
  const data = passwordExpireNoticeData.value;
  if (!data) return {} as any;

  switch (data.level) {
    case 'expired':
      return {
        wrapper: 'bg-red-50 dark:bg-red-950/40 border-red-200 dark:border-red-900/60 text-red-800 dark:text-red-200',
        btn: 'bg-red-600 hover:bg-red-700 text-white',
        iconType: 'error',
      };
    case 'urgent':
      return {
        wrapper: 'bg-rose-50 dark:bg-rose-950/40 border-rose-200 dark:border-rose-900/60 text-rose-900 dark:text-rose-200',
        btn: 'bg-rose-600 hover:bg-rose-700 text-white',
        iconType: 'warning',
      };
    case 'warning':
      return {
        wrapper: 'bg-amber-50 dark:bg-amber-950/40 border-amber-200 dark:border-amber-900/60 text-amber-900 dark:text-amber-200',
        btn: 'bg-amber-600 hover:bg-amber-700 text-white',
        iconType: 'warning',
      };
    case 'notice':
    default:
      return {
        wrapper: 'bg-yellow-50 dark:bg-yellow-950/40 border-yellow-200 dark:border-yellow-900/60 text-yellow-900 dark:text-yellow-200',
        btn: 'bg-yellow-600 hover:bg-yellow-700 text-white',
        iconType: 'info',
      };
  }
});

const dismissPasswordExpireBanner = () => {
  if (passwordExpireNoticeData.value?.isExpired) return;
  isPasswordExpireBannerDismissed.value = true;
  const uid = (userInfo.value as any)?.id || (userInfo.value as any)?.user_id || 'current';
  sessionStorage.setItem(`dismiss_pwd_banner_${uid}`, '1');
};

const goToChangePassword = () => {
  router.push({
    path: '/dashboard/personal',
    query: { tab: 'info', subtab: 'security' }
  });
};

/** 登录后一次性密码到期提醒（同一会话、同一等级只提示一次） */
const triggerLoginPasswordNoticeToast = (info: any) => {
  const notice = resolvePasswordExpiryNotice(info?.password_info as PasswordInfo | undefined);
  if (!notice) return;

  const uid = info?.id || info?.user_id || 'current';
  const sessionToastKey = `pwd_toast_notified_${uid}_${notice.isExpired ? 'expired' : notice.daysUntil}`;
  if (sessionStorage.getItem(sessionToastKey)) return;
  sessionStorage.setItem(sessionToastKey, '1');

  showToast(notice.toastMessage, passwordExpiryToastType(notice));
};

const handleUserInfoUpdated = async () => {
  isPasswordExpireBannerDismissed.value = false;
  await fetchUserInfo();
};

onMounted(async () => {
  loadBranding(true);
  window.addEventListener('user-info-updated', handleUserInfoUpdated);
  await fetchUserInfo();
  // 左侧主导航折叠状态只存本机：初始值已由 isCollapsed 的初始化表达式从缓存取好。
  // 在线人数仅管理员可见，普通用户不请求、不展示
  if (userInfo.value.role === "admin") {
    await fetchOnlineUsers();
    startOnlineUsersRefresh();
  }
});

/** 请求序号：轮询与手动刷新并发时，只采用最新一次结果 */
let onlineUsersSeq = 0;

const fetchOnlineUsers = async () => {
  const seq = ++onlineUsersSeq;
  onlineUsersLoading.value = true;
  onlineUsersError.value = false;
  try {
    const response = await axios.get("/api/portal/dashboard/online-users");
    if (seq !== onlineUsersSeq) return;
    if (response.data) {
      onlineUserCount.value = response.data.count;
      onlineUsers.value = response.data.users || [];
    }
  } catch (e) {
    if (seq !== onlineUsersSeq) return;
    console.error("Failed to fetch online users", e);
    onlineUsersError.value = true;
  } finally {
    if (seq === onlineUsersSeq) onlineUsersLoading.value = false;
  }
};

/** 在线人数轮询间隔 */
const ONLINE_USERS_REFRESH_INTERVAL_MS = 60_000;

const startOnlineUsersRefresh = () => {
  stopOnlineUsersRefresh();
  onlineUsersRefreshTimer = setInterval(() => {
    // 标签页不可见时跳过本轮，避免无意义的后台请求
    if (document.visibilityState === "hidden") return;
    void fetchOnlineUsers();
  }, ONLINE_USERS_REFRESH_INTERVAL_MS);
};

const stopOnlineUsersRefresh = () => {
  if (!onlineUsersRefreshTimer) return;
  clearInterval(onlineUsersRefreshTimer);
  onlineUsersRefreshTimer = null;
};

const openOnlineUsers = () => {
  if (userInfo.value.role === "admin") {
    fetchOnlineUsers(); // Refresh
    showOnlineUsersDialog.value = true;
  }
};

const formatActiveRelativeTime = (lastActive: string | number | undefined | null) => {
  if (!lastActive) return "刚刚活跃";
  const ts = typeof lastActive === "string" ? Number(lastActive) : lastActive;
  if (!ts || isNaN(ts)) return "刚刚活跃";
  const timeMs = ts < 1e11 ? ts * 1000 : ts;
  const diff = Date.now() - timeMs;
  if (diff < 0 || diff < 60_000) {
    return "刚刚活跃";
  }
  const minutes = Math.floor(diff / 60_000);
  if (minutes < 60) {
    return `${minutes} 分钟前`;
  }
  const hours = Math.floor(minutes / 60);
  if (hours < 24) {
    return `${hours} 小时前`;
  }
  return `${Math.floor(hours / 24)} 天前`;
};

const formatExactActiveTime = (lastActive: string | number | undefined | null) => {
  if (!lastActive) return "";
  const ts = typeof lastActive === "string" ? Number(lastActive) : lastActive;
  if (!ts || isNaN(ts)) return "";
  const timeMs = ts < 1e11 ? ts * 1000 : ts;
  const d = new Date(timeMs);
  return `最后活跃: ${d.toLocaleString("zh-CN", { hour12: false })}`;
};

const logout = () => {
  showLogoutDialog.value = true;
};

const confirmLogout = async () => {
  if (loggingOut.value) return;
  loggingOut.value = true;
  try {
    await axios.post("/api/portal/auth/logout");
  } catch (e) {
    // 服务端会话未能吊销时不要静默假装成功：HttpOnly 门户会话 Cookie 仍在，
    // 此时跳转登录页会让用户误以为已安全退出，因此保留弹窗并允许重试。
    console.error("Logout API failed", e);
    loggingOut.value = false;
    showToast("退出失败：服务端会话未能注销，请重试", "error");
    return;
  }
  // 服务端已吊销会话，统一清理本地凭据（clearUserSession 含历史遗留键）
  clearUserSession();
  stopOnlineUsersRefresh();
  showLogoutDialog.value = false;
  loggingOut.value = false;
  router.push("/login");
};

const cancelLogout = () => {
  showLogoutDialog.value = false;
};

const openUserInfo = () => {
  router.push("/dashboard/personal");
};

const handleEscape = (e: KeyboardEvent) => {
  if (e.key !== "Escape") return;
  if (showLogoutDialog.value) {
    cancelLogout();
  } else if (showOnlineUsersDialog.value) {
    showOnlineUsersDialog.value = false;
  } else if (showMobileSidebar.value) {
    // 移动端抽屉此前无法用 Esc 关闭
    showMobileSidebar.value = false;
  }
};

onMounted(() => {
  document.addEventListener("keydown", handleEscape);
});

onUnmounted(() => {
  document.removeEventListener("keydown", handleEscape);
  window.removeEventListener("user-info-updated", handleUserInfoUpdated);
  stopOnlineUsersRefresh();
});

// 监听路由变化，移动端下自动收起侧边栏
watch(() => route.path, async () => {
  await nextTick();
  if (dashboardContentRef.value) {
    dashboardContentRef.value.scrollTop = 0;
  }
  if (isMobile.value) {
    showMobileSidebar.value = false;
  }
  // 换页后顶栏回到收起态：chat 页进来应当是沉浸式全屏，不要沿用上一次的唤出状态
  chatChromeOpen.value = false;
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

watch(isCollapsed, (collapsed) => {
  // 移动端抽屉是临时态，不覆盖桌面端的本机偏好。只存本机，不与账号偏好同步。
  if (!isDesktopSidebar()) return;
  try {
    localStorage.setItem(LEFT_SIDEBAR_COLLAPSED_KEY, collapsed ? "1" : "0");
  } catch {
    // localStorage 不可用时仅本次生效
  }
});

const toggleSidebar = () => {
  if (isMobile.value) {
    showMobileSidebar.value = !showMobileSidebar.value;
  } else {
    isCollapsed.value = !isCollapsed.value;
  }
};

/** 跨越断点时同步移动端标记；切回桌面态顺带收起抽屉 */
const handleBreakpointChange = (event: MediaQueryListEvent) => {
  isMobile.value = !event.matches;
  if (!isMobile.value) {
    showMobileSidebar.value = false;
  }
  // 断点来回切时不要停留在「唤出态」：移动端 chat 页默认仍是沉浸式
  chatChromeOpen.value = false;
};

onMounted(() => {
  desktopMediaQuery.addEventListener?.('change', handleBreakpointChange);
});

onUnmounted(() => {
  desktopMediaQuery.removeEventListener?.('change', handleBreakpointChange);
  // 兜底恢复，避免组件卸载后残留 body 滚动锁
  document.body.style.overflow = "";
});

// ---------------- 侧栏菜单：数据来自后端，已按当前用户权限过滤 ----------------
// 改造前这里是一份写死的菜单常量 + 本地权限过滤；现在菜单全部入库，
// 由 GET /api/portal/menus/nav 下发，前端只负责渲染，不再持有"全部菜单"的概念。
const {
  groups: navGroups,
  isReady: navReady,
  isStale: navStale,
  error: navError,
  landingPath,
  ensureNavigation,
} = useNavigation();

const collapsedMenuGroups = ref<Record<string, boolean>>({});

/** 移动端隐藏 `desktop_only` 菜单（与改造前一致），并丢掉因此变空的分组 */
const visibleNavNodes = computed(() => {
  const strip = (node: NavNode): NavNode => ({
    ...node,
    children: (node.children || [])
      .filter((child) => !(child.desktop_only && isMobile.value))
      .map(strip),
  });
  return navGroups.value
    .filter((node) => !(node.desktop_only && isMobile.value))
    .map(strip)
    .filter((node) => node.node_type !== 'group' || (node.children || []).length > 0);
});

const isNodeActive = (node: NavNode) => {
  if (node.node_type === 'external') {
    return node.open_mode === 'embed' && route.path === `/dashboard/external/${node.code}`;
  }
  if (node.path && route.path === node.path) return true;
  return (node.active_route_names || []).includes(String(route.name || ''));
};

const isGroupActive = (group: NavNode) => (group.children || []).some(isNodeActive);

const isGroupCollapsed = (group: NavNode) => {
  // 当前所在分组永远展开，避免"选中项被自己折叠起来"
  if (isGroupActive(group)) return false;
  return Boolean(collapsedMenuGroups.value[group.code]);
};

const toggleMenuGroup = (group: NavNode) => {
  if (isCollapsed.value) return;
  collapsedMenuGroups.value[group.code] = !collapsedMenuGroups.value[group.code];
};

const retryNavigation = () => {
  void ensureNavigation(true);
};

onMounted(() => {
  void ensureNavigation();
});
</script>

<template>
  <!-- h-screen 只是兜底：真实高度走 --app-height（visualViewport 实测，见
       composables/useAppViewportHeight.ts）。移动端地址栏伸缩、软键盘弹出时
       100vh 不变，会把底部输入框压住一截。 -->
  <div
    class="h-screen bg-gray-50 flex overflow-hidden relative"
    :style="{ height: 'var(--app-height, 100dvh)' }"
  >
    <!-- Mobile Overlay -->
    <div 
      v-if="isMobile && showMobileSidebar" 
      class="fixed inset-0 bg-black/50 z-40 transition-opacity backdrop-blur-sm"
      @click="showMobileSidebar = false"
    ></div>

    <!-- Sidebar -->
    <aside
      aria-label="主导航"
      class="bg-sidebar text-white flex flex-col z-50 transition-all duration-300 ease-in-out flex-shrink-0 border-r"
      :class="[
        theme === 'light' 
          ? '!bg-white !text-gray-700 !border-gray-200/80' 
          : 'border-white/[0.06]',
        isMobile ? 'fixed inset-y-0 left-0 h-full' : 'relative',
        isMobile 
            ? (showMobileSidebar ? 'translate-x-0 w-64' : '-translate-x-full w-64') 
            : (isCollapsed ? 'w-20' : 'w-[208px]')
      ]"
    >
      <!-- Brand Header -->
      <div
        class="h-12 flex items-center border-b transition-colors overflow-hidden whitespace-nowrap"
        :class="[
          isCollapsed ? 'justify-center px-0' : 'px-3',
          theme === 'light' ? 'bg-white border-gray-200/80' : 'bg-sidebar border-white/[0.06]',
        ]"
      >
        <router-link
          :to="homeRoute"
          class="flex items-center flex-1 min-w-0 group/brand outline-none focus:outline-none focus:ring-0 focus-visible:outline-none focus-visible:ring-0 select-none py-0.5"
          :class="isCollapsed ? 'justify-center w-full px-0' : 'px-0.5'"
          :title="`返回首页 - ${branding.product_name}`"
        >
          <img
            :src="branding.icon_url"
            class="w-7 h-7 flex-shrink-0 rounded-lg object-cover ring-1 ring-white/10 dark:ring-white/10 shadow-xs transition-transform duration-200 group-hover/brand:scale-105"
            alt="Logo"
          />
          <transition name="fade">
            <div v-if="!isCollapsed" class="ml-2.5 flex items-center flex-1 min-w-0">
              <span
                class="text-sm font-semibold leading-tight truncate tracking-tight antialiased transition-colors"
                :class="theme === 'light' ? 'text-gray-900 group-hover/brand:text-primary' : 'text-white group-hover/brand:text-blue-300'"
                :title="branding.product_name"
              >{{ branding.product_name }}</span>
            </div>
          </transition>
        </router-link>
      </div>

      <!-- Navigation (Scrollable internally) -->
      <nav
        class="flex-1 py-3 px-2 space-y-3 overflow-y-auto overflow-x-hidden custom-scrollbar"
        @click="isMobile ? showMobileSidebar = false : null"
      >
        <!-- 首次加载失败且本机无快照：必须给出可重试的错误态，不能渲染空侧栏 -->
        <div
          v-if="!navReady && navError"
          class="mx-1 rounded-lg border px-2.5 py-3 text-xs"
          :class="theme === 'light' ? 'border-red-200 bg-red-50 text-red-700' : 'border-red-400/30 bg-red-500/10 text-red-300'"
        >
          <p class="font-semibold">菜单加载失败</p>
          <p class="mt-1 leading-snug opacity-80">请检查网络后重试，持续失败请联系管理员。</p>
          <button
            type="button"
            class="mt-2 rounded-md px-2 py-1 text-[11px] font-bold"
            :class="theme === 'light' ? 'bg-red-600 text-white' : 'bg-white/15 text-white'"
            @click.stop="retryNavigation"
          >重试</button>
        </div>

        <div v-else-if="!navReady" class="space-y-2 px-1" aria-hidden="true">
          <div
            v-for="index in 8"
            :key="index"
            class="h-8 animate-pulse rounded-lg"
            :class="theme === 'light' ? 'bg-gray-100' : 'bg-white/[0.06]'"
          ></div>
        </div>

        <template v-else>
          <div v-for="node in visibleNavNodes" :key="node.code" class="space-y-1">
            <!-- 分组：标题可折叠；收起侧栏时退化为分隔线 -->
            <template v-if="node.node_type === 'group'">
              <button
                v-if="!isCollapsed"
                type="button"
                class="flex items-center justify-between gap-1.5 text-left tracking-wider select-none transition-colors group/header"
                :class="[
                  isMobile
                    ? 'w-[calc(100%-1rem)] mx-2 px-2.5 py-2.5 text-xs font-semibold'
                    : 'w-[calc(100%-1rem)] mx-2 px-2 py-1.5 text-[11px] font-medium',
                  theme === 'light' ? 'text-gray-400 hover:text-gray-700' : 'text-slate-400 hover:text-slate-200',
                ]"
                :aria-expanded="!isGroupCollapsed(node)"
                :aria-controls="`menu-group-${node.code}`"
                @click.stop="toggleMenuGroup(node)"
              >
                <span class="truncate">{{ node.label }}</span>
                <svg
                  class="w-3.5 h-3.5 flex-shrink-0 opacity-60 group-hover/header:opacity-100 transition-all duration-200"
                  :class="isGroupCollapsed(node) ? '-rotate-90' : 'rotate-0'"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
                </svg>
              </button>
              <div
                v-else
                class="h-px mx-3 my-2"
                :class="theme === 'light' ? 'bg-gray-200/80' : 'bg-white/[0.06]'"
              ></div>

              <transition name="menu-group">
                <div :id="`menu-group-${node.code}`" v-show="!isGroupCollapsed(node)" class="space-y-1">
                  <SidebarMenuLink
                    v-for="item in node.children"
                    :key="item.code"
                    :node="item"
                    :collapsed="isCollapsed"
                    :mobile="isMobile"
                    :active="isNodeActive(item)"
                    :light-theme="theme === 'light'"
                  />
                </div>
              </transition>
            </template>

            <!-- 顶层页面（如「概览」）：不属于任何分组 -->
            <SidebarMenuLink
              v-else
              :node="node"
              :collapsed="isCollapsed"
              :mobile="isMobile"
              :active="isNodeActive(node)"
              :light-theme="theme === 'light'"
            />
          </div>
        </template>

        <!-- 有快照但刷新失败：继续用旧菜单，同时提示可能不是最新 -->
        <p
          v-if="navReady && navStale"
          class="mx-2 rounded-md px-2 py-1 text-[10px] leading-snug"
          :class="theme === 'light' ? 'bg-amber-50 text-amber-700' : 'bg-amber-500/10 text-amber-300'"
        >菜单数据可能不是最新，点击右上角刷新重试</p>
      </nav>

      <!-- User Profile (Clickable Card) -->
      <div
        class="border-t transition-colors"
        :class="[
          isMobile ? 'p-2.5' : 'p-2',
          theme === 'light' ? 'border-gray-200/80 bg-white' : 'border-white/[0.06] bg-sidebar',
        ]"
      >
        <button
          @click="openUserInfo"
          class="w-full flex items-center overflow-hidden whitespace-nowrap transition-all rounded-lg text-left focus:outline-none focus:ring-2 focus:ring-primary/40 group/user"
          :class="[
            isCollapsed ? 'justify-center p-1.5' : (isMobile ? 'p-2.5' : 'p-2'),
            theme === 'light' ? 'hover:bg-gray-100/80' : 'hover:bg-white/[0.06]',
          ]"
          title="查看个人信息"
        >
          <!-- 已上传自定义头像时优先渲染图片（圆形、object-cover）；否则维持姓名首字 -->
          <img
            v-if="userInfo.avatar_url"
            :src="userInfo.avatar_url"
            alt="我的头像"
            class="flex-shrink-0 rounded-full object-cover ring-1 ring-black/5"
            :class="sidebarInitialClass"
          />
          <div
            v-else
            class="rounded-lg flex flex-shrink-0 items-center justify-center font-bold uppercase leading-none tracking-tight transition-colors"
            :class="[
              sidebarInitialClass,
              theme === 'light'
                ? 'bg-blue-50 !text-blue-600 border border-blue-200/80 shadow-xs'
                : 'bg-slate-800 !text-slate-200 border border-white/10 shadow-sm'
            ]"
          >
            {{ sidebarInitial }}
          </div>
          <transition name="fade">
            <div v-if="!isCollapsed" class="flex-1 min-w-0" :class="isMobile ? 'ml-3' : 'ml-2.5'">
              <p 
                class="font-medium truncate leading-tight" 
                :class="[
                  isMobile ? 'text-[15px]' : 'text-sm',
                  theme === 'light' ? 'text-gray-900' : 'text-slate-200'
                ]"
              >
                {{ userInfo.user_name || "Loading..." }}
              </p>
              <p 
                class="truncate mt-0.5 leading-none" 
                :class="[
                  isMobile ? 'text-xs' : 'text-[11px]',
                  theme === 'light' ? 'text-gray-400' : 'text-slate-400'
                ]"
              >
                {{ userInfo.role === "admin" ? "管理员" : "普通用户" }}
              </p>
            </div>
          </transition>
          <transition name="fade">
            <svg
              v-if="!isCollapsed"
              class="opacity-40 group-hover/user:opacity-80 transition-opacity ml-1 flex-shrink-0"
              :class="[
                isMobile ? 'h-4 w-4' : 'h-3.5 w-3.5',
                theme === 'light' ? 'text-gray-400' : 'text-slate-400'
              ]"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
            </svg>
          </transition>
        </button>


        <!-- Version Footer -->
        <transition name="fade">
          <div
            v-if="!isCollapsed"
            class="mt-1.5 pt-1.5 px-2 flex items-center justify-between"
            :class="theme === 'light' ? 'border-t border-gray-100' : 'border-t border-white/[0.05]'"
          >
            <span
              class="text-[11px] font-normal tracking-tight select-none opacity-80"
              :class="theme === 'light' ? 'text-gray-400' : 'text-slate-400'"
            >平台版本</span>
            <component
              :is="repoUrl ? 'a' : 'span'"
              :href="repoUrl || undefined"
              :target="repoUrl ? '_blank' : undefined"
              :rel="repoUrl ? 'noopener noreferrer' : undefined"
              class="group inline-flex items-center text-[10px] font-mono font-medium rounded-full px-2 py-0.5 transition-all border leading-none shrink-0 outline-none focus:outline-none focus:ring-0 select-none"
              :class="[
                theme === 'light'
                  ? (repoUrl ? 'bg-gray-100/80 text-gray-500 border-gray-200/70 hover:text-gray-900 hover:border-gray-300 hover:bg-gray-100' : 'bg-gray-50 text-gray-400 border-gray-200/50')
                  : (repoUrl ? 'bg-white/[0.04] text-slate-400 border-white/[0.06] hover:text-white hover:bg-white/[0.08] hover:border-white/15' : 'bg-white/[0.02] text-slate-400 border-white/[0.04]'),
                repoUrl ? 'cursor-pointer' : 'cursor-default'
              ]"
              :title="repoUrl ? 'View on GitHub' : undefined"
            >
              <svg
                v-if="repoUrl"
                class="w-2.5 h-2.5 mr-1 opacity-70 group-hover:opacity-100 transition-opacity shrink-0"
                fill="currentColor"
                viewBox="0 0 24 24"
                aria-hidden="true"
              >
                <path fill-rule="evenodd" d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.531 1.032 1.531 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0112 6.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0022 12.017C22 6.484 17.522 2 12 2z" clip-rule="evenodd" />
              </svg>
              <span>{{ displayVersion }}</span>
            </component>
          </div>
          <div v-else-if="repoUrl" class="mt-1.5 flex justify-center">
            <a
              :href="repoUrl"
              target="_blank"
              rel="noopener noreferrer"
              class="p-1 rounded-md text-gray-400 hover:text-gray-700 dark:hover:text-white transition-colors"
              :title="`版本: ${displayVersion} (查看 GitHub 仓库)`"
            >
              <svg
                class="w-3.5 h-3.5 opacity-70 hover:opacity-100 transition-opacity"
                fill="currentColor"
                viewBox="0 0 24 24"
                aria-hidden="true"
              >
                <path fill-rule="evenodd" d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.531 1.032 1.531 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0112 6.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0022 12.017C22 6.484 17.522 2 12 2z" clip-rule="evenodd" />
              </svg>
            </a>
          </div>
        </transition>
      </div>
    </aside>

    <!-- Main Content Area -->
    <div class="flex-1 flex flex-col overflow-hidden min-w-0">
      <!-- Top Header -->
      <header
        v-if="showDashboardHeader"
        class="relative bg-white shadow-sm h-12 flex justify-between items-center px-4 z-30 border-b border-gray-200 flex-shrink-0"
      >
        <div class="flex items-center">
          <!-- Sidebar Toggle Button -->
          <button
            type="button"
            :aria-label="isCollapsed ? '展开侧边栏' : '收起侧边栏'"
            :aria-expanded="!isCollapsed"
            @click="toggleSidebar"
            class="-ml-1 mr-3 text-gray-500 hover:text-gray-700 hover:bg-gray-100 dark:hover:bg-gray-800 focus:outline-none focus:ring-2 focus:ring-primary rounded-lg p-1.5 transition-colors"
            title="切换侧边栏"
          >
            <svg
              class="h-5 w-5"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
              aria-hidden="true"
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
                  :to="homeRoute"
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
                  :aria-current="index === breadcrumbs.length - 1 ? 'page' : undefined"
                  >{{ crumb }}</span
                >
              </li>
            </ol>
          </nav>

          <!-- Mobile Title -->
          <div v-if="isMobile" class="flex-1 min-w-0 flex items-center">
             <router-link :to="homeRoute" class="p-1 mr-1 text-gray-400 hover:text-primary transition-colors">
                <svg class="w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
                    <path d="M10.707 2.293a1 1 0 00-1.414 0l-7 7a1 1 0 001.414 1.414L4 10.414V17a1 1 0 001 1h2a1 1 0 001-1v-2a1 1 0 011-1h2a1 1 0 011 1v2a1 1 0 001 1h2a1 1 0 001-1v-6.586l.293.293a1 1 0 001.414-1.414l-7-7z" />
                </svg>
             </router-link>
            <h1 class="text-sm font-bold text-gray-900 dark:text-gray-100 truncate">
                {{ breadcrumbs[breadcrumbs.length - 1] || branding.product_name }}
             </h1>
          </div>
        </div>

        <div class="flex items-center space-x-2 sm:space-x-4 flex-shrink-0 flex-nowrap">
          <!-- Online Users Widget：仅管理员可见 -->
          <template v-if="userInfo.role === 'admin'">
            <button
              type="button"
              aria-label="查看在线用户列表"
              class="online-users-widget flex items-center px-2 sm:px-2.5 py-0.5 bg-green-50 border border-green-100/50 rounded-full transition-all shadow-sm flex-shrink-0 whitespace-nowrap cursor-pointer hover:bg-green-100/80 active:scale-95 focus:outline-none focus-visible:ring-2 focus-visible:ring-green-400"
              title="点击查看在线用户列表"
              @click="openOnlineUsers"
            >
              <span class="relative flex h-2 w-2 mr-1 sm:mr-1.5 flex-shrink-0" aria-hidden="true">
                <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75"></span>
                <span class="relative inline-flex rounded-full h-2 w-2 bg-green-500"></span>
              </span>
              <span class="text-[10px] sm:text-xs font-bold text-green-700 tracking-tight tabular-nums">
                {{ onlineUserCount }} <span class="font-medium opacity-80 ml-0.5">在线</span>
              </span>
            </button>
            <div class="online-users-divider h-4 w-px bg-gray-200 mx-1 sm:mx-2 flex-shrink-0" aria-hidden="true"></div>
          </template>
          <PortalNotificationBell />
          <button
            type="button"
            aria-label="切换界面主题"
            :title="theme === 'light' ? '当前亮色，切换到暗色' : '当前暗色，切换到亮色'"
            class="relative p-1.5 rounded-lg text-gray-500 hover:bg-gray-100 transition-colors focus:outline-none focus:ring-2 focus:ring-primary"
            :class="theme === 'light' ? 'text-amber-500' : 'text-gray-500'"
            @click="toggleTheme"
          >
            <svg v-if="theme === 'light'" class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
              <circle cx="12" cy="12" r="4" stroke-width="2" />
              <path stroke-linecap="round" stroke-width="2" d="M12 2v2m0 16v2M4.93 4.93l1.42 1.42m11.3 11.3l1.42 1.42M2 12h2m16 0h2M4.93 19.07l1.42-1.42m11.3-11.3l1.42-1.42" />
            </svg>
            <svg v-else class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21.752 15.002A9.718 9.718 0 0112.582 21 9.75 9.75 0 0112 1.25c.906 0 1.79.124 2.632.355A7.501 7.501 0 0021.752 15.002z" />
            </svg>
          </button>
          <button
            type="button"
            aria-label="退出登录"
            @click="logout"
            class="flex items-center px-2 sm:px-2.5 py-1 text-xs font-medium text-red-600 bg-red-50 hover:bg-red-100 rounded-lg transition-all duration-200 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-red-500 group flex-shrink-0 whitespace-nowrap"
          >
            <svg
              class="h-4 w-4 mr-1 sm:mr-1.5 transition-transform group-hover:translate-x-0.5"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
              aria-hidden="true"
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
          <!-- chat 页移动端：顶栏是唤出态，给一个明确收起入口（否则只能靠再点把手） -->
          <button
            v-if="isMobile && isChatRoute"
            type="button"
            aria-label="收起顶栏"
            title="收起顶栏"
            class="p-1.5 rounded-lg text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-600 focus:outline-none focus:ring-2 focus:ring-primary dark:hover:bg-gray-800"
            @click="chatChromeOpen = false"
          >
            <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 14l8-8 8 8" />
            </svg>
          </button>
        </div>
      </header>

      <!-- chat 页移动端顶栏收起后的把手：只占 10px，居中一条灰条，点一下把完整顶栏唤出。
           用布局内元素而不是 fixed 覆盖，避免挡住挂件自己标题栏顶部的点击热区。 -->
      <button
        v-if="!showDashboardHeader"
        type="button"
        aria-label="展开顶栏"
        title="展开顶栏"
        class="group flex h-2.5 w-full flex-shrink-0 items-center justify-center bg-white dark:bg-gray-900"
        @click="chatChromeOpen = true"
      >
        <span
          class="h-1 w-8 rounded-full bg-gray-300 transition-colors group-hover:bg-gray-400 dark:bg-gray-600 dark:group-hover:bg-gray-500"
        />
      </button>

      <!-- 全局密码到期/即将到期常驻横幅 (Top Banner) -->
      <transition
        enter-active-class="transition-all duration-300 ease-out"
        enter-from-class="opacity-0 -translate-y-2 max-h-0"
        enter-to-class="opacity-100 translate-y-0 max-h-16"
        leave-active-class="transition-all duration-200 ease-in"
        leave-from-class="opacity-100 translate-y-0 max-h-16"
        leave-to-class="opacity-0 -translate-y-2 max-h-0"
      >
        <div
          v-if="showPasswordExpireBanner && passwordExpireNoticeData"
          class="flex-shrink-0 px-4 py-2 sm:py-2.5 text-xs sm:text-sm font-medium border-b flex items-center justify-between gap-3 shadow-xs z-20 overflow-hidden"
          :class="passwordExpireBannerStyle.wrapper"
        >
          <div class="flex items-center gap-2.5 min-w-0">
            <!-- 警示图标 -->
            <svg v-if="passwordExpireBannerStyle.iconType === 'error'" class="w-4 h-4 shrink-0 text-red-600 dark:text-red-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
            </svg>
            <svg v-else-if="passwordExpireBannerStyle.iconType === 'warning'" class="w-4 h-4 shrink-0 text-amber-600 dark:text-amber-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
            </svg>
            <svg v-else class="w-4 h-4 shrink-0 text-yellow-600 dark:text-yellow-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            <span class="truncate leading-tight">
              {{ passwordExpireNoticeData.message }}
            </span>
          </div>

          <div class="flex items-center gap-2 shrink-0">
            <button
              type="button"
              @click="goToChangePassword"
              class="px-2.5 py-1 rounded-md text-xs font-semibold shadow-xs transition-colors cursor-pointer"
              :class="passwordExpireBannerStyle.btn"
            >
              立即修改
            </button>
            <button
              v-if="!passwordExpireNoticeData.isExpired"
              type="button"
              @click="dismissPasswordExpireBanner"
              class="p-1 rounded-md hover:bg-black/5 dark:hover:bg-white/10 transition-colors opacity-70 hover:opacity-100 cursor-pointer"
              title="暂时忽略"
            >
              <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        </div>
      </transition>

      <!-- Main Scrollable Content -->
      <main 
        ref="dashboardContentRef"
        class="flex-1 overflow-y-auto overflow-x-hidden min-w-0 w-full max-w-full bg-gray-100 custom-scrollbar"
        :class="dashboardContentSpacing"
      >
        <router-view v-slot="{ Component }">
          <transition name="page">
            <Suspense>
              <template #default>
                <component :is="Component" :key="$route.path" />
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

    <!-- Logout Confirmation Dialog：复用 ConfirmModal（自带 Teleport、焦点管理、Esc/Enter 与 loading 态） -->
    <ConfirmModal
      v-if="showLogoutDialog"
      title="确认退出"
      message="您确定要退出登录吗？"
      confirm-text="退出登录"
      type="danger"
      :loading="loggingOut"
      @confirm="confirmLogout"
      @cancel="cancelLogout"
    />

    <!-- Online Users Dialog -->
    <teleport to="body">
      <transition name="dialog">
        <div
          v-if="showOnlineUsersDialog"
          class="fixed inset-0 z-[60] flex items-center justify-center bg-black/40 backdrop-blur-sm p-4"
          @click.self="showOnlineUsersDialog = false"
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="online-users-dialog-title"
            class="bg-white dark:bg-gray-800 rounded-2xl shadow-2xl max-w-md w-full overflow-hidden flex flex-col border border-gray-200 dark:border-gray-700 animate-slide-up"
          >
            <!-- Header -->
            <div class="px-6 py-4 border-b border-gray-100 dark:border-gray-700 flex justify-between items-center bg-green-50/30 dark:bg-green-900/10">
              <div class="flex items-center gap-2">
                <div class="w-2 h-2 rounded-full bg-green-500 animate-pulse" aria-hidden="true"></div>
                <h3 id="online-users-dialog-title" class="text-lg font-black text-gray-800 dark:text-gray-100 uppercase tracking-widest">在线用户列表</h3>
              </div>
              <button
                type="button"
                aria-label="关闭在线用户列表"
                @click="showOnlineUsersDialog = false"
                class="rounded text-gray-400 hover:text-gray-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-gray-300 dark:hover:text-gray-200"
              >
                <svg class="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" /></svg>
              </button>
            </div>

            <!-- List -->
            <div class="flex-1 overflow-y-auto max-h-[60vh] p-4 custom-scrollbar">
              <div v-if="onlineUsersLoading" class="text-center py-10 text-gray-400 text-xs font-bold uppercase tracking-widest">
                正在加载在线用户…
              </div>
              <div v-else-if="onlineUsersError" class="text-center py-10">
                <p class="text-xs font-bold uppercase tracking-widest text-red-500">在线用户加载失败</p>
                <button
                  type="button"
                  class="mt-3 rounded-lg border border-gray-200 px-3 py-1.5 text-xs font-medium text-gray-600 hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-gray-300 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-700"
                  @click="fetchOnlineUsers"
                >
                  重试
                </button>
              </div>
              <div v-else-if="onlineUsers.length === 0" class="text-center py-10 text-gray-400 text-xs font-bold uppercase tracking-widest">
                暂无在线用户信息
              </div>
              <div v-else class="space-y-3">
                <div v-for="user in onlineUsers" :key="user.user_id || user.user_name" class="flex items-center p-3 rounded-xl bg-gray-50 dark:bg-gray-900/50 border border-gray-100 dark:border-gray-800 transition-all hover:border-green-200">
                  <div class="w-10 h-10 rounded-full bg-primary flex items-center justify-center text-white font-black uppercase text-sm flex-shrink-0 leading-none">
                    {{ userInitialOf(user.real_name || user.user_name) }}
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
                  <div class="flex flex-col items-end flex-shrink-0 text-right">
                    <div class="text-[10px] text-green-500 font-bold uppercase tracking-widest flex items-center gap-1">
                      <span class="w-1.5 h-1.5 rounded-full bg-green-500"></span>
                      Active
                    </div>
                    <span
                      class="text-[10px] text-gray-400 dark:text-gray-500 font-mono mt-0.5"
                      :title="formatExactActiveTime(user.last_active)"
                    >
                      {{ formatActiveRelativeTime(user.last_active) }}
                    </span>
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

/* Keep scrollbar gutter stable; only fade the thumb on hover to avoid layout shift. */
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
  background-color: rgba(156, 163, 175, 0.3); /* gray-400 with low opacity */
}
.custom-scrollbar::-webkit-scrollbar-thumb:hover {
  background-color: rgba(107, 114, 128, 0.5); /* gray-500 */
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
