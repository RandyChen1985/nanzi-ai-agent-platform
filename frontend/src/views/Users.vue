<template>
  <div class="space-y-5 relative" @click="closeMenus">
    <!-- Full-page Sync Overlay -->
    <Teleport to="body">
      <div
        v-if="syncingSso"
        class="fixed inset-0 z-[10000] bg-gray-900/60 backdrop-blur-sm flex flex-col items-center justify-center text-white"
      >
        <div class="w-16 h-16 border-4 border-white border-t-transparent rounded-full animate-spin mb-4"></div>
        <h3 class="text-xl font-black">正在同步 SSO 用户数据...</h3>
        <p class="text-gray-300 mt-2 text-sm">正在为您生成 API Key 并同步权限，请勿刷新页面</p>
      </div>
    </Teleport>

    <!-- Header：与角色管理一致：标题在左，搜索 / 筛选 / 操作在右同一行（lg 以上）；
         窄屏自动折行——搜索框独占一行，按钮组在下一行换行排列 -->
    <div class="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
      <div class="min-w-0">
        <h1 class="text-xl font-bold text-gray-900 sm:text-2xl">用户管理</h1>
        <p class="mt-1 text-sm text-gray-500">管理账号、身份角色与 API 访问凭证</p>
      </div>

      <div class="flex flex-wrap items-center gap-2.5 sm:gap-3">
        <button
          type="button"
          class="relative inline-flex items-center gap-1.5 rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-700 shadow-sm hover:bg-gray-50"
          title="审批他人对你名下知识库的访问申请"
          @click="openAccessReviews"
        >
          访问审批
          <span
            v-if="pendingReviewCount"
            class="inline-flex h-5 min-w-[1.25rem] items-center justify-center rounded-full bg-red-500 px-1 text-[10px] font-bold text-white"
          >{{ pendingReviewCount }}</span>
        </button>
        <div class="relative w-full sm:w-56 lg:w-64">
          <span class="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-3">
            <svg class="h-4 w-4 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
            </svg>
          </span>
          <input
            v-model="searchQuery"
            type="search"
            placeholder="搜索用户名、姓名或邮箱..."
            class="w-full rounded-lg border border-gray-300 bg-white py-2 pl-9 pr-3 text-sm shadow-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
            @input="debouncedSearch"
          />
        </div>

        <!-- 筛选：身份 / 状态 / 部门合并进一个面板，组间用分隔线区分 -->
        <div
          v-if="activeView === 'list'"
          class="relative shrink-0"
          @click.stop
          @keydown.esc="showFilterPanel = false"
        >
          <button
            type="button"
            class="inline-flex items-center gap-1.5 rounded-lg border bg-white px-3 py-2 text-sm font-medium shadow-sm transition-colors"
            :class="activeFilterCount ? 'border-primary/40 text-gray-800' : 'border-gray-300 text-gray-700 hover:bg-gray-50'"
            :aria-expanded="showFilterPanel"
            aria-haspopup="true"
            @click="toggleFilterPanel"
          >
            <FunnelIcon class="h-4 w-4" :class="activeFilterCount ? 'text-primary' : 'text-gray-400'" />
            筛选
            <span
              v-if="activeFilterCount"
              class="inline-flex min-w-[1.125rem] items-center justify-center rounded-full bg-primary/10 px-1 text-[10px] font-bold text-primary"
            >{{ activeFilterCount }}</span>
            <ChevronDownIcon
              class="h-4 w-4 text-gray-400 transition-transform"
              :class="{ 'rotate-180': showFilterPanel }"
            />
          </button>

          <div
            v-if="showFilterPanel"
            class="absolute left-0 z-30 mt-1.5 w-[min(20rem,calc(100vw-2rem))] rounded-xl border border-gray-200 bg-white p-3 shadow-lg"
            role="group"
            aria-label="筛选条件"
          >
            <div>
              <p class="mb-1.5 text-xs font-semibold text-gray-500">身份</p>
              <div class="flex flex-wrap gap-1.5">
                <button
                  v-for="opt in roleFilterOptions"
                  :key="`role-${opt.value}`"
                  type="button"
                  class="rounded-md border px-2.5 py-1 text-xs font-medium transition-colors"
                  :class="roleFilter === opt.value
                    ? 'border-primary bg-primary/10 text-primary'
                    : 'border-gray-200 bg-white text-gray-600 hover:border-gray-300 hover:bg-gray-50'"
                  :aria-pressed="roleFilter === opt.value"
                  @click="setRoleFilter(opt.value)"
                >{{ opt.label }}</button>
              </div>
            </div>

            <div class="my-3 border-t border-gray-100"></div>

            <div>
              <p class="mb-1.5 text-xs font-semibold text-gray-500">状态</p>
              <div class="flex flex-wrap gap-1.5">
                <button
                  v-for="opt in statusFilterOptions"
                  :key="`status-${opt.value}`"
                  type="button"
                  class="rounded-md border px-2.5 py-1 text-xs font-medium transition-colors"
                  :class="statusFilter === opt.value
                    ? 'border-primary bg-primary/10 text-primary'
                    : 'border-gray-200 bg-white text-gray-600 hover:border-gray-300 hover:bg-gray-50'"
                  :aria-pressed="statusFilter === opt.value"
                  @click="setStatusFilter(opt.value)"
                >{{ opt.label }}</button>
              </div>
            </div>

            <template v-if="deptOptions.length">
              <div class="my-3 border-t border-gray-100"></div>
              <div>
                <p class="mb-1.5 text-xs font-semibold text-gray-500">部门</p>
                <select
                  v-model="deptFilter"
                  class="w-full rounded-lg border border-gray-300 bg-white px-2.5 py-1.5 text-xs outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
                  @change="applyFilters"
                >
                  <option value="">全部部门</option>
                  <option v-for="dept in deptOptions" :key="dept.id" :value="String(dept.id)">
                    {{ "\u3000".repeat(dept.depth) }}{{ dept.name }}
                  </option>
                </select>
                <label
                  v-if="deptFilter"
                  class="mt-2 inline-flex items-center gap-1.5 text-xs text-gray-600"
                  title="勾选后把子部门的成员也列出来"
                >
                  <input
                    v-model="includeSub"
                    type="checkbox"
                    class="h-3.5 w-3.5 rounded border-gray-300"
                    @change="applyFilters"
                  />
                  含子部门
                </label>
              </div>
            </template>

            <div class="mt-3 flex items-center justify-between border-t border-gray-100 pt-2.5">
              <span class="text-[11px] text-gray-400">
                {{ activeFilterCount ? `已选 ${activeFilterCount} 项` : '未设置筛选' }}
              </span>
              <button
                type="button"
                class="text-xs font-medium text-primary transition-colors hover:text-primary-dark disabled:cursor-not-allowed disabled:text-gray-300"
                :disabled="!hasActiveFilters"
                @click="resetFilters"
              >清除全部</button>
            </div>
          </div>
        </div>

        <button
          type="button"
          class="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-gray-300 bg-white text-gray-500 shadow-sm transition-colors hover:bg-gray-50 hover:text-primary"
          title="刷新列表"
          @click="fetchUsers"
        >
            <ArrowPathIcon class="h-4 w-4" :class="{ 'animate-spin': loading }" />
          </button>

          <div v-if="showMoreMenuPanel" class="relative shrink-0" @click.stop>
            <button
              type="button"
              class="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-700 shadow-sm transition-colors hover:bg-gray-50"
              @click="showMoreMenu = !showMoreMenu; openRowMenuId = null"
            >
              更多
              <ChevronDownIcon class="h-4 w-4 text-gray-400" :class="{ 'rotate-180': showMoreMenu }" />
            </button>
            <div
              v-if="showMoreMenu"
              class="absolute right-0 z-30 mt-1.5 w-52 rounded-xl border border-gray-200 bg-white py-1 shadow-lg"
            >
              <button
                v-if="showSsoSync && canEditUser"
                type="button"
                class="flex w-full items-center gap-2 px-3 py-2.5 text-left text-sm text-gray-700 hover:bg-gray-50"
                @click="showMoreMenu = false; openSsoModal()"
              >
                <UserGroupIcon class="h-4 w-4 text-indigo-500" />
                同步 SSO 用户
              </button>
              <button
                v-if="canEditUser"
                type="button"
                class="flex w-full items-center gap-2 px-3 py-2.5 text-left text-sm text-gray-700 hover:bg-gray-50"
                @click="showMoreMenu = false; showThirdPartyDrawer = true"
              >
                <ArrowPathIcon class="h-4 w-4 text-violet-500" />
                同步第三方用户
              </button>
              <button
                v-if="canEditUser"
                type="button"
                class="flex w-full items-center gap-2 px-3 py-2.5 text-left text-sm text-gray-700 hover:bg-gray-50"
                @click="showMoreMenu = false; showImportDialog = true"
              >
                <ArrowUpTrayIcon class="h-4 w-4 text-emerald-500" />
                批量导入用户
              </button>
              <button
                v-if="canOpenSystemQuota"
                type="button"
                class="flex w-full items-center gap-2 px-3 py-2.5 text-left text-sm text-gray-700 hover:bg-gray-50"
                @click="showMoreMenu = false; showSystemQuotaModal = true"
              >
                <ChartBarIcon class="h-4 w-4 text-amber-500" />
                系统默认额度
              </button>
            </div>
          </div>

        <!-- 创建用户：主操作放最后，与角色管理一致 -->
        <button
          v-if="canEditUser && activeView === 'list'"
          type="button"
          class="inline-flex shrink-0 items-center justify-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-white shadow-sm transition-all hover:bg-primary-dark"
          @click="openCreateDialog"
        >
          <PlusIcon class="h-4 w-4" />
          <span class="hidden sm:inline">创建用户</span>
          <span class="sm:hidden">创建</span>
        </button>
      </div>
    </div>

    <!-- 视图切换：用户列表 / 待审核申请 -->
    <div class="flex items-center gap-1 border-b border-gray-200">
      <button
        v-for="tab in [
          { id: 'list', label: '用户列表' },
          { id: 'review', label: '待审核' },
          { id: 'wechat', label: '微信绑定' },
        ]"
        :key="tab.id"
        type="button"
        class="-mb-px flex items-center gap-1.5 border-b-2 px-3 py-2.5 text-sm font-semibold transition-colors"
        :class="
          activeView === tab.id
            ? 'border-primary text-primary'
            : 'border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700'
        "
        @click="switchView(tab.id as 'list' | 'review' | 'wechat')"
      >
        {{ tab.label }}
        <span
          v-if="tab.id === 'review' && pendingCount > 0"
          class="inline-flex min-w-[1.25rem] items-center justify-center rounded-full bg-amber-100 px-1.5 py-0.5 text-[10px] font-bold text-amber-700"
          aria-label="待审核数量"
        >{{ pendingCount }}</span>
      </button>
    </div>

    <!-- 批量启用 / 禁用操作条 -->
    <div
      v-if="canEditUser && selectedUserIds.length > 0"
      class="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border border-indigo-200 bg-indigo-50/70 px-4 py-2.5"
    >
      <span class="text-sm font-medium text-indigo-900">
        已选 <span class="font-bold">{{ selectedUserIds.length }}</span> 个用户
      </span>
      <div class="ml-auto flex items-center gap-2">
        <button
          type="button"
          class="rounded-lg border border-emerald-300 bg-white px-3 py-1.5 text-sm font-medium text-emerald-700 shadow-sm transition-colors hover:bg-emerald-50 disabled:cursor-not-allowed disabled:opacity-50"
          :disabled="batchSubmitting"
          @click="openBatchStatusDialog(1)"
        >
          批量启用
        </button>
        <button
          type="button"
          class="rounded-lg border border-red-300 bg-white px-3 py-1.5 text-sm font-medium text-red-600 shadow-sm transition-colors hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-50"
          :disabled="batchSubmitting"
          @click="openBatchStatusDialog(0)"
        >
          批量禁用
        </button>
        <button
          type="button"
          class="rounded-lg px-3 py-1.5 text-sm text-gray-500 transition-colors hover:bg-white hover:text-gray-700 disabled:cursor-not-allowed disabled:opacity-50"
          :disabled="batchSubmitting"
          @click="clearSelection"
        >
          取消选择
        </button>
      </div>
    </div>

    <!-- ===================== 待审核视图 =====================
         独立轻量卡片列表：待审核账号唯一可做的动作是「通过 / 禁用」，
         主表格的批量勾选、API Key、角色编辑、2FA 徽标对它全是噪音。 -->
    <div v-if="activeView === 'review'" class="space-y-3">
      <div v-if="loading" class="flex flex-col items-center justify-center py-16">
        <div class="w-10 h-10 border-4 border-primary border-t-transparent rounded-full animate-spin"></div>
        <p class="text-sm text-gray-500 mt-4 font-medium">加载待审核申请...</p>
      </div>

      <div
        v-else-if="users.length === 0"
        class="flex flex-col items-center justify-center min-h-[280px] bg-white border border-gray-200 rounded-lg shadow-sm px-6"
      >
        <svg class="w-14 h-14 text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 12.75L11.25 15 15 9.75M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
        <p class="text-sm text-gray-500 mt-4 font-semibold">暂无待审核申请</p>
        <p class="text-xs text-gray-400 mt-1">开启「系统配置 › 常规设置 › 账号自主注册申请」后，用户可在登录页自助申请账号</p>
      </div>

      <template v-else>
        <div
          v-for="user in users"
          :key="user.id"
          class="bg-white border border-gray-200 rounded-lg shadow-sm p-4"
        >
          <div class="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div class="min-w-0">
              <div class="flex items-center gap-2 min-w-0">
                <span class="text-base font-semibold text-gray-900 truncate" :title="user.user_name">
                  {{ user.user_name }}
                </span>
                <span class="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-amber-50 text-amber-700 border border-amber-200/80 shrink-0">
                  待审核
                </span>
              </div>
              <p class="mt-1 text-xs text-gray-500">
                {{ user.real_name || "未设置姓名" }}
                <span class="text-gray-300">·</span>
                <span class="font-mono">#{{ user.id }}</span>
                <span class="text-gray-300">·</span>
                申请时间 {{ formatDate(user.created_at) }}
              </p>
              <p class="mt-2 text-xs text-gray-500 break-words">
                <span class="text-gray-400">备注：</span>{{ user.remark || "—" }}
              </p>
              <p class="mt-1 text-xs break-words">
                <span class="text-gray-400">邮箱：</span>
                <span v-if="user.email" class="font-mono text-gray-600">{{ canEditUser ? user.email : maskEmail(user.email) }}</span>
                <span v-else class="text-gray-300">未设置</span>
              </p>
            </div>

            <div v-if="canEditUser" class="flex shrink-0 items-center gap-2">
              <button
                type="button"
                class="rounded-lg bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white shadow-sm transition-colors hover:bg-emerald-700 disabled:opacity-50"
                :disabled="reviewSubmittingId === user.id"
                @click="openApproveDialog(user)"
              >
                通过并启用
              </button>
              <button
                type="button"
                class="rounded-lg border border-red-300 bg-white px-3 py-1.5 text-sm font-medium text-red-600 shadow-sm transition-colors hover:bg-red-50 disabled:opacity-50"
                :disabled="reviewSubmittingId === user.id"
                @click="openRejectDialog(user)"
              >
                禁用
              </button>
              <button
                v-if="canResendReviewMail(user)"
                type="button"
                class="rounded-lg border border-amber-300 bg-white px-3 py-1.5 text-sm font-medium text-amber-700 shadow-sm transition-colors hover:bg-amber-50 disabled:opacity-50"
                :disabled="resendingMailId === user.id"
                @click="resendReviewMail(user.id, reviewApprovedFor(user))"
              >
                {{ resendingMailId === user.id ? "重发中..." : "重发通知邮件" }}
              </button>
            </div>
            <span v-else class="shrink-0 text-xs text-gray-300">无审核权限</span>
          </div>
        </div>

        <div class="bg-white border border-gray-200 rounded-lg px-4 py-3 flex flex-col sm:flex-row items-center justify-between gap-3">
          <div class="text-sm text-gray-700">
            共 {{ total }} 条，第 {{ page }}/{{ totalPages }} 页
          </div>
          <div class="flex gap-2 w-full sm:w-auto">
            <button
              type="button"
              class="flex-1 sm:flex-none px-4 py-2 border border-gray-300 rounded-lg bg-white hover:bg-gray-50 disabled:opacity-50 text-sm font-medium"
              :disabled="page <= 1"
              @click="page > 1 && (page--, fetchUsers())"
            >
              上一页
            </button>
            <button
              type="button"
              class="flex-1 sm:flex-none px-4 py-2 border border-gray-300 rounded-lg bg-white hover:bg-gray-50 disabled:opacity-50 text-sm font-medium"
              :disabled="page >= totalPages"
              @click="page < totalPages && (page++, fetchUsers())"
            >
              下一页
            </button>
          </div>
        </div>
      </template>
    </div>

    <!-- 微信绑定视图：一行 = 一条绑定记录（同一账号将来可能挂多个微信身份） -->
    <template v-if="activeView === 'wechat'">
      <div class="flex flex-wrap items-center gap-2">
        <input
          v-model="bindingSearch"
          type="search"
          placeholder="搜索用户名 / 姓名"
          class="w-64 rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-primary focus:ring-2 focus:ring-primary/30"
          @keyup.enter="bindingPage = 1; fetchBindings()"
        />
        <button
          type="button"
          class="rounded-lg border border-gray-300 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50"
          @click="bindingPage = 1; fetchBindings()"
        >查询</button>
      </div>

      <div class="bg-white border border-gray-200 shadow-sm rounded-lg">
        <div class="overflow-x-auto">
          <table class="min-w-full divide-y divide-gray-200">
            <thead class="bg-gray-50">
              <tr>
                <th class="px-5 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">用户</th>
                <th class="px-5 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">来源 AppID</th>
                <th class="px-5 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">openid</th>
                <th class="px-5 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">unionid</th>
                <th class="px-5 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">绑定时间</th>
                <th class="px-5 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">最后登录</th>
              </tr>
            </thead>
            <tbody class="bg-white divide-y divide-gray-200">
              <tr v-for="row in bindings" :key="row.id" class="hover:bg-gray-50/80">
                <td class="px-5 py-4 whitespace-nowrap">
                  <div class="text-sm font-medium text-gray-900">{{ row.user_name }}</div>
                  <div class="text-xs text-gray-500">#{{ row.user_id }}</div>
                </td>
                <td class="px-5 py-4 whitespace-nowrap text-sm text-gray-700">{{ row.appid }}</td>
                <td class="px-5 py-4 whitespace-nowrap text-sm text-gray-700">
                  <span class="font-mono">{{ row.openid_masked }}</span>
                  <button
                    type="button"
                    class="ml-2 text-xs text-primary hover:underline"
                    title="复制完整 openid（会记入日志）"
                    @click="copyBindingIdentity(row)"
                  >复制完整值</button>
                </td>
                <td class="px-5 py-4 whitespace-nowrap text-sm text-gray-700">
                  <span class="font-mono">{{ row.unionid_masked || '—' }}</span>
                </td>
                <td class="px-5 py-4 whitespace-nowrap text-sm text-gray-500">{{ row.created_at || '—' }}</td>
                <td class="px-5 py-4 whitespace-nowrap text-sm text-gray-500">{{ row.last_login_at || '—' }}</td>
              </tr>
              <tr v-if="!bindingLoading && bindings.length === 0">
                <td colspan="6" class="px-5 py-10 text-center text-sm text-gray-500">
                  暂无微信绑定记录
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <div class="flex items-center justify-between border-t border-gray-200 px-5 py-3">
          <div class="text-sm text-gray-600">
            共 {{ bindingTotal }} 条，第 {{ bindingPage }}/{{ bindingTotalPages || 1 }} 页
          </div>
          <div class="flex gap-2">
            <button
              type="button"
              class="rounded-lg border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50"
              :disabled="bindingPage <= 1"
              @click="bindingPage > 1 && (bindingPage--, fetchBindings())"
            >上一页</button>
            <button
              type="button"
              class="rounded-lg border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50"
              :disabled="bindingPage >= bindingTotalPages"
              @click="bindingPage < bindingTotalPages && (bindingPage++, fetchBindings())"
            >下一页</button>
          </div>
        </div>
      </div>
    </template>

    <!-- ===================== 用户列表视图 ===================== -->
    <template v-if="activeView === 'list'">
    <!-- Loading -->
    <div v-if="loading" class="flex flex-col items-center justify-center py-16">
      <div class="w-10 h-10 border-4 border-primary border-t-transparent rounded-full animate-spin"></div>
      <p class="text-sm text-gray-500 mt-4 font-medium">加载用户列表...</p>
    </div>

    <!-- Empty -->
    <div
      v-else-if="users.length === 0"
      class="flex flex-col items-center justify-center min-h-[320px] bg-white border border-gray-200 rounded-lg shadow-sm px-6"
    >
      <svg class="w-14 h-14 text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 4.354a4 4 0 110 5.292M15 21H3v-1a6 6 0 0112 0v1zm0 0h6v-1a6 6 0 00-9-5.197M13 7a4 4 0 11-8 0 4 4 0 018 0z" />
      </svg>
      <template v-if="isSearchEmpty">
        <p class="text-sm text-gray-500 mt-4 font-semibold">没有符合当前筛选条件的用户</p>
        <p class="text-xs text-gray-400 mt-1">可调整关键词或筛选条件后重试</p>
        <button
          type="button"
          @click="resetFilters"
          class="mt-5 px-4 py-2 text-sm font-medium text-blue-600 border border-blue-200 rounded-lg hover:bg-blue-50 transition-colors"
        >
          清除筛选
        </button>
      </template>
      <template v-else>
        <p class="text-sm text-gray-500 mt-4 font-semibold">暂无用户</p>
        <p class="text-xs text-gray-400 mt-1">创建账号，或从「更多」同步 SSO / 第三方用户</p>
        <button
          v-if="canEditUser"
          type="button"
          @click="openCreateDialog"
          class="mt-5 px-4 py-2 text-sm font-medium text-white bg-primary hover:bg-primary-dark rounded-lg transition-colors"
        >
          创建用户
        </button>
      </template>
    </div>

    <!-- User List -->
    <div v-else>
      <!-- Desktop Table：分页放在 overflow 容器外，避免行内更多菜单被裁切 -->
      <div v-if="!isMobile" class="bg-white border border-gray-200 shadow-sm rounded-lg">
        <div class="overflow-x-auto rounded-t-lg">
          <table class="min-w-full divide-y divide-gray-200">
            <thead class="bg-gray-50">
              <tr>
                <th v-if="canEditUser" class="w-10 px-3 py-3">
                  <input
                    type="checkbox"
                    class="h-4 w-4 cursor-pointer rounded border-gray-300 text-primary focus:ring-2 focus:ring-primary/30"
                    :checked="isAllPageSelected"
                    :indeterminate.prop="isPagePartiallySelected"
                    :disabled="users.length === 0"
                    title="全选当前页"
                    aria-label="全选当前页用户"
                    @change="toggleSelectAllPage"
                  />
                </th>
                <th class="px-5 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">用户</th>
                <th class="px-5 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">身份 / 角色</th>
                <th class="px-5 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">备注</th>
                <th class="px-5 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">状态</th>
                <th class="px-5 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">创建时间</th>
                <th class="px-5 py-3 text-right text-xs font-semibold text-gray-500 uppercase tracking-wider">操作</th>
              </tr>
            </thead>
            <tbody class="bg-white divide-y divide-gray-200">
              <tr
                v-for="user in users"
                :key="user.id"
                class="group hover:bg-gray-50/80 transition-colors"
                :class="{ 'bg-indigo-50/40': selectedUserIds.includes(user.id) }"
              >
                <td v-if="canEditUser" class="w-10 px-3 py-4">
                  <input
                    type="checkbox"
                    class="h-4 w-4 cursor-pointer rounded border-gray-300 text-primary focus:ring-2 focus:ring-primary/30"
                    :checked="selectedUserIds.includes(user.id)"
                    :aria-label="`选择用户 ${user.user_name}`"
                    @change="toggleSelectUser(user.id)"
                  />
                </td>
                <td class="px-5 py-4 whitespace-nowrap">
                  <div class="min-w-0">
                    <div class="flex items-center gap-1.5 min-w-0">
                      <span
                        v-if="user.wechat_bound"
                        class="inline-flex shrink-0 items-center rounded px-1.5 py-0.5 text-[10px] font-semibold text-emerald-700 bg-emerald-50"
                        title="该账号已绑定微信（小程序微端）"
                      >微信</span>
                      <span class="text-sm font-semibold text-gray-900 truncate max-w-[14rem]" :title="user.user_name">
                        {{ user.user_name }}
                      </span>
                      <!-- 2FA 开启安全徽标 -->
                      <span
                        v-if="user.two_factor_enabled"
                        class="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] font-medium bg-emerald-50 text-emerald-700 border border-emerald-200/80 shrink-0 cursor-default"
                        title="已开启 Google 两步验证 (2FA)"
                      >
                        <svg class="w-3 h-3 text-emerald-600 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
                        </svg>
                        <span>2FA</span>
                      </span>
                    </div>
                    <div class="mt-0.5 flex items-center gap-1.5 text-xs text-gray-400">
                      <span>{{ user.real_name || "未设置姓名" }}</span>
                      <span class="text-gray-300">·</span>
                      <span class="font-mono">#{{ user.id }}</span>
                      <template v-if="user.primary_dept_name">
                        <span class="text-gray-300">·</span>
                        <span
                          class="truncate text-blue-500"
                          :title="(user.dept_names || []).join('、')"
                        >{{ user.primary_dept_name }}<template v-if="(user.dept_names || []).length > 1">+{{ user.dept_names.length - 1 }}</template></span>
                      </template>
                    </div>
                    <div
                      v-if="user.email"
                      class="mt-0.5 min-w-0 text-xs text-gray-500"
                      :title="canEditUser ? user.email : maskEmail(user.email)"
                    >
                      <span class="block max-w-[16rem] truncate font-mono">{{ canEditUser ? user.email : maskEmail(user.email) }}</span>
                    </div>
                  </div>
                </td>
                <td class="px-5 py-4 whitespace-nowrap text-sm">
                  <div class="flex flex-col gap-1.5 items-start">
                    <span
                      :class="
                        user.role === 'admin'
                          ? 'bg-purple-50 text-purple-700 border-purple-100'
                          : 'bg-blue-50 text-blue-700 border-blue-100'
                      "
                      class="px-2 py-0.5 text-[11px] font-semibold rounded-md border"
                    >
                      {{ user.role === "admin" ? "系统管理员" : "普通用户" }}
                    </span>
                    <RoleList
                      v-if="user.role_names && user.role_names.length > 0"
                      :roles="user.role_names"
                      :limit="2"
                    />
                  </div>
                </td>
                <td class="px-5 py-4 text-sm text-gray-500 max-w-[10rem] truncate" :title="user.remark">
                  {{ user.remark || "—" }}
                </td>
                <td class="px-5 py-4 whitespace-nowrap">
                  <div class="flex items-center">
                    <Switch
                      v-if="canEditUser"
                      :model-value="user.status === 1"
                      @update:model-value="toggleStatus(user)"
                    />
                    <span
                      class="text-xs"
                      :class="[
                        canEditUser ? 'ml-2' : '',
                        user.status === 1 ? 'text-emerald-600' : 'text-gray-400',
                      ]"
                    >
                      <!-- 三态写全：待审核账号已不在此列表（见 exclude_status），
                           但若只剩「非 1 即已禁用」的写法，一旦过滤失效或将来被去掉，
                           管理员会把待审核账号误读成「已禁用」 -->
                      {{ user.status === 1 ? "已启用" : user.status === 2 ? "待审核" : "已禁用" }}
                    </span>
                  </div>
                </td>
                <td class="px-5 py-4 whitespace-nowrap text-sm text-gray-500 font-mono">
                  {{ formatDate(user.created_at) }}
                </td>
                <td class="px-5 py-4 whitespace-nowrap text-right text-sm font-medium">
                  <div v-if="hasAnyRowAction" class="flex justify-end items-center gap-0.5">
                    <button
                      v-if="canEditUser"
                      @click="editUser(user)"
                      class="p-1.5 rounded-lg text-blue-600 hover:bg-blue-50 transition-colors"
                      title="编辑用户"
                    >
                      <PencilSquareIcon class="w-4 h-4" />
                    </button>
                    <button
                      v-if="canViewUserKey"
                      @click="viewApiKey(user)"
                      class="p-1.5 rounded-lg text-gray-500 hover:text-indigo-600 hover:bg-indigo-50 transition-colors"
                      title="查看 API Key"
                    >
                      <KeyIcon class="w-4 h-4" />
                    </button>
                    <div v-if="hasRowMoreActions" class="relative" @click.stop>
                      <button
                        type="button"
                        @click="toggleRowMenu(user, $event)"
                        class="p-1.5 rounded-lg text-gray-400 hover:text-gray-700 hover:bg-gray-100 transition-colors"
                        title="更多操作"
                      >
                        <EllipsisHorizontalIcon class="w-4 h-4" />
                      </button>
                    </div>
                  </div>
                  <span v-else class="text-xs text-gray-300">—</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <div class="bg-gray-50 px-4 py-3 border-t border-gray-200 flex flex-col sm:flex-row items-center justify-between gap-3 rounded-b-lg">
          <div class="text-sm text-gray-700">
            共 {{ total }} 条，第 {{ page }}/{{ totalPages }} 页
          </div>
          <div class="flex gap-2 w-full sm:w-auto">
            <button
              @click="page > 1 && (page--, fetchUsers())"
              :disabled="page <= 1"
              class="flex-1 sm:flex-none px-4 py-2 border border-gray-300 rounded-lg bg-white hover:bg-gray-50 disabled:opacity-50 text-sm font-medium"
            >
              上一页
            </button>
            <button
              @click="page < totalPages && (page++, fetchUsers())"
              :disabled="page >= totalPages"
              class="flex-1 sm:flex-none px-4 py-2 border border-gray-300 rounded-lg bg-white hover:bg-gray-50 disabled:opacity-50 text-sm font-medium"
            >
              下一页
            </button>
          </div>
        </div>
      </div>

      <!-- Mobile Card List -->
      <div v-else class="space-y-3">
        <div
          v-for="user in users"
          :key="user.id"
          class="bg-white border rounded-lg p-4 shadow-sm"
          :class="
            selectedUserIds.includes(user.id)
              ? 'border-indigo-300 ring-1 ring-indigo-200'
              : 'border-gray-200'
          "
        >
          <div class="flex justify-between items-start gap-3">
            <div v-if="canEditUser" class="pt-0.5 shrink-0">
              <input
                type="checkbox"
                class="h-4 w-4 cursor-pointer rounded border-gray-300 text-primary focus:ring-2 focus:ring-primary/30"
                :checked="selectedUserIds.includes(user.id)"
                :aria-label="`选择用户 ${user.user_name}`"
                @change="toggleSelectUser(user.id)"
              />
            </div>
            <div class="min-w-0 flex-1">
              <div class="flex items-center gap-1.5 min-w-0">
                <h3 class="text-base font-semibold text-gray-900 truncate">
                  {{ user.user_name }}
                </h3>
                <span
                  v-if="user.two_factor_enabled"
                  class="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] font-medium bg-emerald-50 text-emerald-700 border border-emerald-200/80 shrink-0"
                  title="已开启 Google 两步验证 (2FA)"
                >
                  <svg class="w-3 h-3 text-emerald-600 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
                  </svg>
                  <span>2FA</span>
                </span>
              </div>
              <p class="text-xs text-gray-500 mt-0.5">
                {{ user.real_name || "未设置姓名" }} · #{{ user.id }}
                <template v-if="user.primary_dept_name">
                  · <span class="text-blue-500" :title="(user.dept_names || []).join('、')">{{ user.primary_dept_name }}<template v-if="(user.dept_names || []).length > 1">+{{ user.dept_names.length - 1 }}</template></span>
                </template>
              </p>
              <p class="text-xs mt-0.5 truncate" :class="user.email ? 'text-gray-500' : 'text-gray-300'">
                {{ user.email ? (canEditUser ? user.email : maskEmail(user.email)) : "未设置邮箱" }}
              </p>
            </div>
            <div class="flex items-center gap-2 shrink-0">
              <span
                :class="
                  user.role === 'admin'
                    ? 'bg-purple-50 text-purple-700'
                    : 'bg-blue-50 text-blue-700'
                "
                class="px-2 py-0.5 text-[10px] font-semibold rounded"
              >
                {{ user.role === "admin" ? "管理员" : "普通用户" }}
              </span>
              <Switch
                v-if="canEditUser"
                :model-value="user.status === 1"
                @update:model-value="toggleStatus(user)"
              />
              <span
                v-else
                class="text-[10px] font-medium"
                :class="user.status === 1 ? 'text-emerald-600' : 'text-gray-400'"
              >
                {{ user.status === 1 ? "启用" : "禁用" }}
              </span>
            </div>
          </div>

          <p v-if="user.remark" class="mt-2 text-xs text-gray-500 truncate">{{ user.remark }}</p>

          <div
            v-if="user.role_names && user.role_names.length > 0"
            class="flex flex-wrap gap-1 mt-2"
          >
            <span
              v-for="(rname, idx) in user.role_names"
              :key="idx"
              class="bg-gray-50 text-gray-500 px-1.5 py-0.5 text-[9px] rounded border border-gray-100"
            >
              {{ rname }}
            </span>
          </div>

          <div class="flex justify-between items-center pt-3 mt-3 border-t border-gray-100">
            <span class="text-[10px] text-gray-400 font-mono">{{ formatDate(user.created_at).split(" ")[0] }}</span>
            <div v-if="hasAnyRowAction" class="flex items-center gap-1">
              <button v-if="canEditUser" @click="editUser(user)" class="p-1.5 rounded-lg text-blue-600 hover:bg-blue-50" title="编辑">
                <PencilSquareIcon class="w-4 h-4" />
              </button>
              <button v-if="canViewUserKey" @click="viewApiKey(user)" class="p-1.5 rounded-lg text-gray-500 hover:bg-indigo-50" title="密钥">
                <KeyIcon class="w-4 h-4" />
              </button>
              <button v-if="canEditUser" @click="openSetPasswordDialog(user)" class="p-1.5 rounded-lg text-gray-500 hover:bg-emerald-50" title="密码">
                <LockClosedIcon class="w-4 h-4" />
              </button>
              <button
                v-if="canEditUser && user.two_factor_enabled"
                @click="openDisable2FADialog(user)"
                class="p-1.5 rounded-lg text-purple-600 hover:bg-purple-50"
                title="关闭 2FA"
              >
                <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 11V7a4 4 0 118 0m-4 8v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2z" />
                </svg>
              </button>
              <button v-if="canResetUserKey" @click="regenerateApiKey(user)" class="p-1.5 rounded-lg text-gray-500 hover:bg-amber-50" title="重置 Key">
                <ArrowPathIcon class="w-4 h-4" />
              </button>
              <button
                v-if="canDeleteUser && user.user_name !== 'admin'"
                @click="confirmDelete(user)"
                class="p-1.5 rounded-lg text-gray-400 hover:text-red-600 hover:bg-red-50"
                title="删除"
              >
                <TrashIcon class="w-4 h-4" />
              </button>
            </div>
          </div>
        </div>

        <div class="bg-white border border-gray-200 rounded-lg px-4 py-3 flex flex-col sm:flex-row items-center justify-between gap-3">
          <div class="text-sm text-gray-700">
            共 {{ total }} 条，第 {{ page }}/{{ totalPages }} 页
          </div>
          <div class="flex gap-2 w-full sm:w-auto">
            <button
              @click="page > 1 && (page--, fetchUsers())"
              :disabled="page <= 1"
              class="flex-1 sm:flex-none px-4 py-2 border border-gray-300 rounded-lg hover:bg-gray-50 disabled:opacity-50 text-sm font-medium"
            >
              上一页
            </button>
            <button
              @click="page < totalPages && (page++, fetchUsers())"
              :disabled="page >= totalPages"
              class="flex-1 sm:flex-none px-4 py-2 border border-gray-300 rounded-lg hover:bg-gray-50 disabled:opacity-50 text-sm font-medium"
            >
              下一页
            </button>
          </div>
        </div>
      </div>
    </div>
    </template>

    <!-- 行内「更多」菜单：Teleport 到 body，避免被表格 overflow 裁切 -->
    <Teleport to="body">
      <div
        v-if="openRowMenuUser"
        class="fixed w-40 bg-white border border-gray-200 rounded-xl shadow-lg py-1 z-[200]"
        :style="rowMenuStyle"
        @click.stop
      >
        <button
          v-if="canEditUser"
          type="button"
          class="w-full text-left px-3 py-2 text-sm text-gray-700 hover:bg-gray-50 flex items-center gap-2"
          @click="openSetPasswordDialog(openRowMenuUser); closeMenus()"
        >
          <LockClosedIcon class="w-4 h-4 text-emerald-500" />
          设置密码
        </button>
        <button
          v-if="canEditUser && openRowMenuUser.two_factor_enabled"
          type="button"
          class="w-full text-left px-3 py-2 text-sm text-purple-700 hover:bg-purple-50 flex items-center gap-2"
          @click="openDisable2FADialog(openRowMenuUser); closeMenus()"
        >
          <svg class="w-4 h-4 text-purple-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 11V7a4 4 0 118 0m-4 8v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2z" />
          </svg>
          关闭 2FA
        </button>
        <button
          v-if="canResetUserKey"
          type="button"
          class="w-full text-left px-3 py-2 text-sm text-gray-700 hover:bg-gray-50 flex items-center gap-2"
          @click="regenerateApiKey(openRowMenuUser); closeMenus()"
        >
          <ArrowPathIcon class="w-4 h-4 text-amber-500" />
          重置 API Key
        </button>
        <button
          v-if="canEditUser && canResendReviewMail(openRowMenuUser)"
          type="button"
          class="w-full text-left px-3 py-2 text-sm text-amber-700 hover:bg-amber-50 flex items-center gap-2 disabled:opacity-50"
          :disabled="resendingMailId === openRowMenuUser.id"
          @click="resendReviewMail(openRowMenuUser.id, reviewApprovedFor(openRowMenuUser)); closeMenus()"
        >
          <ArrowPathIcon class="w-4 h-4 text-amber-500" :class="{ 'animate-spin': resendingMailId === openRowMenuUser.id }" />
          重发通知邮件
        </button>
        <button
          v-if="canDeleteUser && openRowMenuUser.user_name !== 'admin'"
          type="button"
          class="w-full text-left px-3 py-2 text-sm text-red-600 hover:bg-red-50 flex items-center gap-2"
          @click="confirmDelete(openRowMenuUser); closeMenus()"
        >
          <TrashIcon class="w-4 h-4" />
          删除用户
        </button>
      </div>
    </Teleport>

    <!-- Create/Edit Dialog -->
    <Teleport to="body">
      <div
        v-if="showCreateDialog || showEditDialog"
        class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-[9990]"
        @click.self="closeDialogs"
      >
        <div
          class="bg-white p-4 sm:p-6 w-full max-w-4xl flex flex-col transition-all duration-300"
          :class="
            isMobile
              ? 'h-full rounded-none'
              : 'max-h-[90vh] rounded-lg shadow-2xl'
          "
        >
          <div class="flex justify-between items-center mb-4">
            <h2 class="text-xl font-bold">
              {{ showEditDialog ? "编辑用户" : "创建用户" }}
            </h2>
            <button
              v-if="isMobile"
              @click="closeDialogs"
              class="p-2 text-gray-400"
            >
              <svg
                class="w-6 h-6"
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

          <!-- Tabs for Edit：权限 tab 只对「打开时后端角色就是普通用户」的账号出现，
               与拉取 / 保存共用同一份真实身份（canEditPermissions） -->
          <div
            v-if="canEditPermissions"
            class="border-b border-gray-200 mb-4"
          >
            <nav
              class="-mb-px flex overflow-x-auto whitespace-nowrap scrollbar-hide"
            >
              <button
                @click="activeTab = 'info'"
                :class="
                  activeTab === 'info'
                    ? 'border-blue-500 text-blue-600'
                    : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
                "
                class="px-4 py-2.5 border-b-2 font-medium text-sm transition-colors flex-1 sm:flex-none text-center"
              >
                基本信息
              </button>
              <button
                @click="activeTab = 'permissions'"
                :class="
                  activeTab === 'permissions'
                    ? 'border-blue-500 text-blue-600'
                    : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
                "
                class="px-4 py-2.5 border-b-2 font-medium text-sm transition-colors flex-1 sm:flex-none text-center"
              >
                权限配置
              </button>
              <button
                @click="activeTab = 'quota'"
                :class="
                  activeTab === 'quota'
                    ? 'border-blue-500 text-blue-600'
                    : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
                "
                class="px-4 py-2.5 border-b-2 font-medium text-sm transition-colors flex-1 sm:flex-none text-center"
              >
                额度
              </button>
            </nav>
          </div>

          <div class="flex-1 overflow-y-auto custom-scrollbar pr-1 pt-3">
            <!-- INFO TAB -->
            <div v-show="activeTab === 'info'" class="space-y-4 py-2">
              <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <label
                    class="block text-xs font-bold text-gray-400 uppercase mb-1"
                    >用户名</label
                  >
                  <input
                    v-model="formData.user_name"
                    type="text"
                    :disabled="showEditDialog"
                    class="w-full border border-gray-300 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:bg-gray-100 text-sm"
                  />
                </div>
                <div>
                  <label
                    class="block text-xs font-bold text-gray-400 uppercase mb-1"
                    >真实姓名</label
                  >
                  <input
                    v-model="formData.real_name"
                    type="text"
                    class="w-full border border-gray-300 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500 text-sm"
                    placeholder="请输入真实姓名"
                  />
                </div>
                <div>
                  <label
                    class="block text-xs font-bold text-gray-400 uppercase mb-1"
                    >邮箱</label
                  >
                  <input
                    v-model="formData.email"
                    type="email"
                    autocapitalize="none"
                    autocorrect="off"
                    spellcheck="false"
                    class="w-full border border-gray-300 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500 text-sm"
                    placeholder="name@example.com（留空表示不设置）"
                  />
                  <p class="mt-1 text-[10px] text-gray-400 leading-relaxed">
                    邮箱全局唯一，用于接收账号审核结果等通知。编辑时清空该框即可删除已设置的邮箱。
                  </p>
                </div>
                <div>
                  <label
                    class="block text-xs font-bold text-gray-400 uppercase mb-1"
                    >所属部门</label
                  >
                  <DepartmentPicker
                    v-model="formData.dept_ids"
                    v-model:primary-id="formData.primary_dept_id"
                  />
                </div>
                <div class="sm:col-span-2">
                  <div class="flex items-center justify-between mb-1">
                    <label class="block text-xs font-bold text-gray-400 uppercase">
                      预留扩展数据 (JSON)
                    </label>
                    <div class="flex bg-gray-100 p-0.5 rounded-lg border border-gray-200">
                      <button
                        type="button"
                        @click="handleExtraDataTabChange('visual')"
                        :class="[
                          extraDataTab === 'visual'
                            ? 'bg-white text-blue-600 shadow-sm'
                            : 'text-gray-500 hover:text-gray-700',
                        ]"
                        class="px-2 py-1 text-[10px] font-black rounded-md transition-all"
                      >
                        可视化
                      </button>
                      <button
                        type="button"
                        @click="handleExtraDataTabChange('json')"
                        :class="[
                          extraDataTab === 'json'
                            ? 'bg-white text-blue-600 shadow-sm'
                            : 'text-gray-500 hover:text-gray-700',
                        ]"
                        class="px-2 py-1 text-[10px] font-black rounded-md transition-all"
                      >
                        JSON
                      </button>
                    </div>
                  </div>

                  <!-- Visual Mode -->
                  <div
                    v-if="extraDataTab === 'visual'"
                    class="border border-gray-300 rounded-lg p-3 bg-gray-50 space-y-2"
                  >
                    <div
                      v-for="(pair, index) in extraDataPairs"
                      :key="index"
                      class="flex items-center gap-2 animate-fade-in"
                    >
                      <input
                        v-model="pair.key"
                        type="text"
                        placeholder="Key"
                        class="flex-1 border border-gray-200 rounded px-3 py-1.5 text-xs focus:ring-1 focus:ring-blue-500 focus:outline-none"
                      />
                      <span class="text-gray-300 text-xs">:</span>
                      <input
                        v-model="pair.value"
                        type="text"
                        placeholder="Value"
                        class="flex-1 border border-gray-200 rounded px-3 py-1.5 text-xs focus:ring-1 focus:ring-blue-500 focus:outline-none"
                      />
                      <button
                        type="button"
                        @click="removeExtraDataPair(index)"
                        class="p-1 text-red-400 hover:text-red-600 hover:bg-red-50 rounded"
                      >
                        <TrashIcon class="w-4 h-4" />
                      </button>
                    </div>
                    <div v-if="extraDataPairs.length === 0" class="text-center py-2">
                      <p class="text-[10px] text-gray-400 italic mb-2">暂无扩展数据</p>
                    </div>
                    <button
                      type="button"
                      @click="addExtraDataPair"
                      class="w-full py-1.5 border-2 border-dashed border-gray-200 rounded-lg text-gray-400 hover:text-blue-500 hover:border-blue-200 hover:bg-blue-50/30 text-xs font-bold flex items-center justify-center gap-1 transition-all"
                    >
                      <PlusIcon class="w-3 h-3" /> 添加键值对
                    </button>
                    <p class="text-[10px] text-gray-400 leading-relaxed pt-1">
                      值统一按字符串保存（如
                      <code class="font-mono">"teamId": "116573125898872954882"</code>），
                      避免长数字被当作 number 丢掉精度。
                    </p>
                  </div>

                  <!-- JSON Mode -->
                  <div v-else>
                    <textarea
                      v-model="formData.extra_data"
                      rows="4"
                      class="w-full border border-gray-300 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500 text-sm font-mono"
                      placeholder='{"key": "value"}'
                    ></textarea>
                  </div>
                </div>
              </div>
              <div>
                <label
                  class="block text-xs font-bold text-gray-400 uppercase mb-1"
                  >系统身份</label
                >
                <select
                  v-model="formData.role"
                  class="w-full border border-gray-300 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500 text-sm"
                >
                  <option value="user">普通用户</option>
                  <option value="admin">管理员</option>
                </select>
              </div>

              <div v-if="formData.role === 'user'">
                <div class="mb-1 flex items-center justify-between gap-2">
                  <div class="flex min-w-0 items-center gap-2">
                    <label class="block text-xs font-bold text-gray-400 uppercase"
                      >业务角色</label
                    >
                    <span
                      v-if="selectedRoleSummary.selectedCount > 0"
                      class="shrink-0 rounded-full bg-blue-50 px-2 py-0.5 text-[10px] font-bold text-blue-600"
                    >已选 {{ selectedRoleSummary.selectedCount }} 个</span>
                    <span
                      v-if="selectedRoleSummary.missingCount > 0"
                      class="truncate text-[10px] text-gray-400"
                      title="不在当前搜索/分页结果中，保存时仍会保留"
                    >其中 {{ selectedRoleSummary.missingCount }} 个不在当前列表，保存时保留</span>
                  </div>
                  <input
                    v-model="roleSearchQuery"
                    type="search"
                    placeholder="搜索角色（支持编码）..."
                    class="text-xs border border-gray-200 rounded px-2 py-0.5 w-40 focus:outline-none focus:border-blue-400"
                  />
                </div>
                <div
                  v-if="roleListError && businessRoles.length === 0"
                  class="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2.5 text-xs text-amber-800"
                >
                  获取角色列表失败<template v-if="roleListErrorStatus">（HTTP {{ roleListErrorStatus }}）</template>，暂时无法分配业务角色。请确认当前账号拥有「角色管理」查看权限或「编辑用户信息」权限，或关闭后重新打开本弹窗重试。
                </div>
                <div
                  v-else-if="businessRoles.length === 0"
                  class="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2.5 text-xs text-gray-500"
                >
                  暂无可分配的业务角色。请先在「角色管理」里创建角色。
                </div>
                <div
                  v-else
                  class="border border-gray-300 rounded-lg p-3 max-h-40 overflow-y-auto bg-gray-50 grid grid-cols-1 sm:grid-cols-2 gap-2"
                >
                  <!-- 有角色但搜不到时给明确反馈，否则空框看起来像「角色不存在」 -->
                  <p
                    v-if="filteredBusinessRoles.length === 0"
                    class="col-span-full px-1 py-2 text-xs text-gray-500"
                  >
                    没有匹配「{{ roleSearchQuery }}」的角色。
                    <template v-if="businessRolesTotal > businessRoles.length"
                      >共 {{ businessRolesTotal }} 个角色，搜索会在全部角色中查找。</template
                    >
                  </p>
                  <label
                    v-for="role in filteredBusinessRoles"
                    :key="role.id"
                    class="flex items-center space-x-2 text-sm cursor-pointer hover:bg-gray-100 p-1.5 rounded bg-white sm:bg-transparent shadow-sm sm:shadow-none border sm:border-0 border-gray-100"
                  >
                    <input
                      type="checkbox"
                      :value="role.id"
                      v-model="formData.role_ids"
                      class="h-4 w-4 rounded text-blue-600 focus:ring-blue-500"
                    />
                    <span class="text-xs sm:text-sm truncate">{{
                      role.name
                    }}</span>
                  </label>
                  <p
                    v-if="businessRolesTotal > businessRoles.length"
                    class="col-span-full px-1 text-[10px] text-gray-400"
                  >
                    共 {{ businessRolesTotal }} 个角色，仅显示前 {{ businessRoles.length }} 个，输入关键词可搜索全部。
                  </p>
                </div>
              </div>

              <div>
                <label
                  class="block text-xs font-bold text-gray-400 uppercase mb-1"
                  >备注</label
                >
                <textarea
                  v-model="formData.remark"
                  rows="3"
                  class="w-full border border-gray-300 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500 text-sm"
                  placeholder="备注信息..."
                ></textarea>
              </div>

              <!-- API Key Presentation -->
              <div
                v-if="createdApiKey"
                class="bg-yellow-50 border border-yellow-200 rounded-lg p-4 animate-fade-in"
              >
                <p
                  class="text-xs font-bold text-yellow-800 mb-2 uppercase tracking-tight"
                >
                  ✅ 用户创建成功！请立即保存 API Key：
                </p>
                <div
                  class="flex flex-col sm:flex-row items-stretch sm:items-center gap-2"
                >
                  <code
                    class="flex-1 bg-white px-3 py-2 rounded border text-[10px] sm:text-xs break-all font-mono select-all"
                    >{{ createdApiKey }}</code
                  >
                  <button
                    @click="copyApiKey"
                    class="px-4 py-2 bg-yellow-600 text-white rounded-lg hover:bg-yellow-700 whitespace-nowrap text-sm font-bold shadow-sm"
                  >
                    复制密钥
                  </button>
                </div>
              </div>
            </div>

            <!-- PERMISSIONS TAB：内容区同样以真实身份为条件，避免 tab 隐藏后仍可能被渲染/提交 -->
            <div
              v-show="activeTab === 'permissions' && canEditPermissions"
              class="space-y-4 py-2 flex flex-col h-full min-h-[400px]"
            >
              <div
                v-if="loadingResources"
                class="flex flex-col items-center justify-center flex-1 min-h-[320px] gap-4"
              >
                <div class="relative">
                  <div class="w-10 h-10 rounded-full border-[3px] border-blue-100"></div>
                  <div class="absolute inset-0 w-10 h-10 rounded-full border-[3px] border-blue-500 border-t-transparent animate-spin"></div>
                </div>
                <div class="text-center space-y-1">
                  <p class="text-sm font-medium text-gray-600">正在加载权限资源</p>
                  <p class="text-xs text-gray-400">数据资产与界面访问配置即将就绪</p>
                </div>
                <div class="w-full max-w-md mt-2 space-y-3 px-6 opacity-60">
                  <div class="flex gap-2">
                    <div class="h-7 w-20 rounded-md bg-gray-100 animate-pulse"></div>
                    <div class="h-7 w-20 rounded-md bg-gray-100 animate-pulse"></div>
                  </div>
                  <div class="space-y-2 rounded-xl border border-gray-100 bg-gray-50/80 p-3">
                    <div class="h-3 w-2/3 rounded bg-gray-200/80 animate-pulse"></div>
                    <div class="h-3 w-full rounded bg-gray-200/70 animate-pulse"></div>
                    <div class="h-3 w-5/6 rounded bg-gray-200/60 animate-pulse"></div>
                    <div class="h-3 w-1/2 rounded bg-gray-200/50 animate-pulse"></div>
                  </div>
                </div>
              </div>
              <div v-else class="flex flex-col flex-1 overflow-hidden">
                <!-- 权限没加载成功时必须说清楚，并让用户知道"现在保存不会动权限"：
                     否则他看到的是空配置，一保存就把真实配置清了（见 saveUser 的守卫） -->
                <div
                  v-if="permissionsLoadError"
                  class="mb-3 bg-red-50 border border-red-200 rounded-lg p-2.5 text-[11px] sm:text-xs text-red-700 leading-relaxed"
                >
                  <p class="font-bold mb-0.5">权限配置加载失败</p>
                  <p class="opacity-80">
                    {{ permissionsLoadError }}。为避免覆盖现有配置，加载成功前不会提交权限修改。
                  </p>
                </div>
                <!-- Sub Tabs -->
                <div
                  class="flex space-x-2 mb-4 border-b border-gray-200 overflow-x-auto whitespace-nowrap"
                >
                  <button
                    @click="activePermissionSubTab = 'assets'"
                    :class="
                      activePermissionSubTab === 'assets'
                        ? 'border-blue-500 text-blue-600'
                        : 'border-transparent text-gray-500'
                    "
                    class="px-4 py-2 border-b-2 text-sm font-medium"
                  >
                    数据资产
                  </button>
                  <button
                    @click="activePermissionSubTab = 'ui'"
                    :class="
                      activePermissionSubTab === 'ui'
                        ? 'border-blue-500 text-blue-600'
                        : 'border-transparent text-gray-500'
                    "
                    class="px-4 py-2 border-b-2 text-sm font-medium"
                  >
                    界面访问
                  </button>
                </div>

                <!-- Assets Content -->
                <div
                  v-if="activePermissionSubTab === 'assets'"
                  class="flex-1 flex flex-col overflow-hidden"
                >
                  <div
                    class="flex items-center justify-between mb-2 overflow-x-auto pb-1"
                  >
                    <div class="flex space-x-1 sm:space-x-2">
                      <button
                        v-for="type in resourceTypes"
                        :key="type"
                        @click="activeResTab = type"
                        :class="[
                          activeResTab === type
                            ? `text-${resourceConfig[type].color}-600 border-${resourceConfig[type].color}-600 bg-${resourceConfig[type].color}-50`
                            : 'text-gray-500 border-transparent',
                        ]"
                        class="px-2 sm:px-3 py-1.5 border-b-2 text-[10px] sm:text-xs font-bold flex items-center gap-1 whitespace-nowrap"
                      >
                        <span
                          v-html="resourceConfig[type].icon"
                          class="w-3 h-3"
                        ></span>
                        {{ resourceConfig[type].label }}

                        <span
                          class="text-[10px] leading-none min-w-[1.25rem] text-center px-1.5 py-0.5 rounded-full bg-gray-100 text-gray-600"
                          :class="activeResTab === type ? 'bg-gray-50' : ''"
                        >
                          {{ resourceTabCount(type) }}
                        </span>
                      </button>
                    </div>
                  </div>
                  <!-- 可用模型页固定说明：这里的语义与 element:/menu: 相反 -->
                  <div
                    v-if="activeResTab === 'models'"
                    class="mb-2 text-[10px] sm:text-xs text-gray-500 leading-tight"
                  >
                    未配置 = 不限制；配置后只能在勾选的模型（与其角色白名单取并集）中手动切换。
                  </div>
                  <!-- 可用模型搜索：只在模型较多时出现；命中之外，已勾选项也会置顶保留 -->
                  <div
                    v-if="activeResTab === 'models' && showModelSearch"
                    class="mb-2"
                  >
                    <input
                      v-model="modelSearchQuery"
                      type="search"
                      placeholder="搜索模型…"
                      class="w-full sm:w-56 rounded-lg border border-gray-200 bg-white px-2 py-1 text-[11px] text-gray-700 outline-none focus:border-primary/40 dark:border-gray-600 dark:bg-gray-900 dark:text-gray-200"
                    />
                  </div>
                  <div
                    class="bg-gray-50 p-3 sm:p-4 rounded-lg flex-1 overflow-y-auto border border-gray-200"
                  >
                    <!-- Hint Alert -->
                    <div
                      v-if="activeResTab === 'agents'"
                      class="mb-3 bg-blue-50 border border-blue-100 rounded-md p-2.5 flex items-start gap-2"
                    >
                      <svg
                        class="w-4 h-4 text-blue-500 mt-0.5 flex-shrink-0"
                        fill="none"
                        viewBox="0 0 24 24"
                        stroke="currentColor"
                      >
                        <path
                          stroke-linecap="round"
                          stroke-linejoin="round"
                          stroke-width="2"
                          d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
                        />
                      </svg>
                      <div
                        class="text-[10px] sm:text-xs text-blue-700 leading-relaxed"
                      >
                        <p class="font-bold mb-0.5">自动授权说明</p>
                        <p class="opacity-80">
                          个人创建的智能体自动拥有权限，此处仅配置“系统级”公共智能体。
                        </p>
                      </div>
                    </div>

                    <div
                      v-if="activeResTab === 'forbidden_configs'"
                      class="space-y-3 text-left"
                    >
                      <div class="bg-red-50 border border-red-100 rounded-md p-2.5 flex items-start gap-2 mb-2 select-none">
                        <svg class="w-4 h-4 text-red-500 mt-0.5 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/>
                        </svg>
                        <div class="text-[10px] sm:text-xs text-red-700 leading-relaxed">
                          <p class="font-bold mb-0.5">安全策略与黑名单说明</p>
                          <p class="opacity-80">配置禁用工具后该用户完全无法调用该工具。配置禁用命令后，当智能体尝试替该用户运行含有敏感词的系统命令时，将被运行时强行拦截。</p>
                        </div>
                      </div>

                      <ForbiddenResourceTabs
                        v-model:forbidden-tools="permissionData.forbidden_tools"
                        v-model:forbidden-skills="permissionData.forbidden_skills"
                        v-model:commands-text="forbiddenCommandsText"
                        :inherited-tools="inheritedForbidden.forbidden_tools"
                        :inherited-skills="inheritedForbidden.forbidden_skills"
                        :disabled="!permissionsLoaded"
                        command-subject="该用户"
                      >
                        <!-- 角色来源的禁用项固定在页签上方：它是跨类别的全景，
                             按类别拆进各页签后要逐个翻才能看全 -->
                        <template #inherited>
                        <!-- 角色来源的禁用项（只读）：并集里有、直配里没有的部分，只展示不提交 -->
                        <div
                          v-if="inheritedForbiddenRows.length"
                          class="border border-gray-200 rounded-xl p-3 bg-gray-50"
                        >
                          <h4
                            class="text-xs font-bold text-gray-700 mb-1.5 flex items-center gap-1.5 select-none"
                          >
                            <span class="w-1.5 h-1.5 bg-gray-400 rounded-full"></span>
                            来自角色（只读）
                          </h4>
                          <p class="text-[10px] text-gray-500 mb-2 leading-relaxed">
                            该禁用来自角色，需到角色页调整；这里只能查看，保存本页不会改动它们（本页只提交该用户的直配项）。
                          </p>
                          <div class="space-y-1.5">
                            <div
                              v-for="row in inheritedForbiddenRows"
                              :key="row.key"
                              class="flex items-start gap-2 text-[11px] leading-snug"
                            >
                              <span class="shrink-0 font-bold text-gray-500">{{ row.label }}</span>
                              <span class="min-w-0 font-mono text-gray-600 break-all">{{ row.items.join('、') }}</span>
                            </div>
                          </div>
                        </div>
                        </template>
                      </ForbiddenResourceTabs>
                    </div>

                    <!-- 失联知识库开关：默认隐藏，但已授权的失联库始终保留 -->
                    <div
                      v-if="activeResTab === 'datasets' && missingDatasetCount"
                      class="flex items-center justify-end mb-1"
                    >
                      <label
                        class="flex items-center gap-1.5 text-[11px] text-gray-500 select-none cursor-pointer"
                      >
                        <input
                          type="checkbox"
                          v-model="showMissingDatasets"
                          class="h-3.5 w-3.5 rounded text-blue-600 focus:ring-blue-500"
                        />
                        显示失联知识库（{{ missingDatasetCount }}）
                      </label>
                    </div>
                    <!--
                      注意：上面的失联开关是独立工具条，**不能**和这里串成
                      v-if / v-else-if / v-else 链。曾经写成 v-else-if 时，
                      只要存在失联库（开关显示），列表分支就永远不渲染 ——
                      表现为"勾了『显示失联知识库』依然一片空白"。
                    -->
                    <div
                      v-if="modelResourceEntries.length === 0"
                      class="text-center py-4 text-gray-500 text-xs"
                    >
                      {{ emptyResourceText }}
                    </div>
                    <div v-else class="grid grid-cols-1 sm:grid-cols-2 gap-2">
                      <template v-for="res in modelResourceEntries" :key="res.entryKey">
                      <div
                        v-if="res.entryType === 'header'"
                        class="col-span-full flex items-center gap-2 pt-1 pb-0.5 text-[11px] font-semibold text-gray-500"
                      >
                        <span>{{ res.label }}</span>
                        <span class="h-px flex-1 bg-gray-200"></span>
                      </div>
                      <label
                        v-else
                        class="flex items-start p-2 rounded border bg-white transition-colors"
                        :class="
                          isPrivateKnowledgeBase(res)
                            ? 'opacity-70 cursor-not-allowed border-gray-200 bg-gray-50/60'
                            : isMissingKnowledgeBase(res)
                              ? isSelectedDatasetResource(res)
                                // 已授权 + 失联：保持可读、可取消，不做灰化
                                ? 'border-amber-300 bg-amber-50/60 cursor-pointer'
                                : 'opacity-70 cursor-not-allowed border-amber-200 bg-amber-50/40'
                              : 'hover:bg-gray-50 cursor-pointer'
                        "
                        :title="
                          isPrivateKnowledgeBase(res)
                            ? '个人私有知识库不参与授权：只有创建人自己可见，勾选后对方也不会获得访问权'
                            : isMissingKnowledgeBase(res)
                              ? isSelectedDatasetResource(res)
                                ? '该知识库已在 RAGFlow 失联：此前分配的授权仍保留，库恢复后自动生效；取消勾选并保存即可移除'
                                : '该知识库已在 RAGFlow 失联，不可分配权限'
                              : undefined
                        "
                      >
                        <input
                          type="checkbox"
                          :value="res.id"
                          v-model="(permissionData as any)[activeResTab]"
                          :disabled="
                            isPrivateKnowledgeBase(res) ||
                            (isMissingKnowledgeBase(res) && !isSelectedDatasetResource(res))
                          "
                          class="mt-1 h-4 w-4 rounded text-blue-600 focus:ring-blue-500 disabled:cursor-not-allowed disabled:opacity-50"
                        />
                        <div class="ml-2 min-w-0 flex-1">
                          <div class="flex items-center gap-1.5 min-w-0">
                            <p
                              class="text-xs sm:text-sm font-bold text-gray-900 truncate"
                            >
                              {{ res.platform_name || res.display_name || res.name }}
                            </p>
                            <span
                              v-if="res.method"
                              class="flex-shrink-0 text-[9px] px-1 py-0.5 rounded font-mono font-bold leading-none"
                              :class="methodBadgeClass(res.method)"
                              >{{ res.method }}</span
                            >
                            <span
                              v-if="isMissingKnowledgeBase(res)"
                              class="flex-shrink-0 text-[9px] px-1.5 py-0.5 rounded-full bg-amber-100 text-amber-700 font-semibold leading-none"
                              >失联</span
                            >
                            <span
                              v-if="activeResTab === 'datasets'"
                              class="flex-shrink-0 text-[9px] px-1.5 py-0.5 rounded-full font-semibold leading-none"
                              :class="knowledgeScopeBadge(res).cls"
                              >{{ knowledgeScopeBadge(res).label }}</span
                            >
                            <span
                              v-if="isDisabledModel(res)"
                              class="flex-shrink-0 text-[9px] px-1.5 py-0.5 rounded-full bg-gray-100 text-gray-500 font-semibold leading-none"
                              >{{ DISABLED_MODEL_LABEL }}</span
                            >
                          </div>
                          <p
                            v-if="res.path"
                            class="text-[9px] text-gray-500 truncate font-mono leading-tight"
                          >
                            {{ res.path }}
                          </p>
                          <p class="text-[9px] text-gray-400 truncate font-mono">
                            {{ res.description || res.id }}
                          </p>
                        </div>
                        <!-- 智能体卡片：只有已勾选（已授权）的智能体才值得配「功能限定」，
                             没分配就不必配；角标 = 该智能体上已禁用的工具/技能条目数 -->
                        <button
                          v-if="activeResTab === 'agents' && (permissionData as any).agents.includes(res.id)"
                          type="button"
                          class="shrink-0 self-center ml-1 px-2 py-1 rounded-lg border text-[10px] font-bold whitespace-nowrap"
                          :class="
                            agentRestrictionCount(res.id) > 0
                              ? 'border-red-200 bg-red-50 text-red-600 hover:bg-red-100'
                              : 'border-gray-200 bg-white text-gray-600 hover:bg-gray-50'
                          "
                          title="只在该智能体里禁用选定的工具 / MCP 工具 / 技能"
                          @click.stop.prevent="openAgentRestriction(res)"
                        >
                          功能限定
                          <span
                            v-if="agentRestrictionCount(res.id) > 0"
                            class="ml-1 inline-block min-w-[1.1rem] text-center px-1 rounded-full bg-red-600 text-white text-[9px] leading-4"
                            >{{ agentRestrictionCount(res.id) }}</span
                          >
                        </button>
                      </label>
                      </template>
                    </div>
                  </div>
                  <div
                    v-if="activeResTab !== 'forbidden_configs'"
                    class="flex justify-between items-center mt-2 text-[10px] text-gray-500"
                  >
                    <span
                      >已勾选:
                      <b class="text-blue-600">{{
                        (permissionData as any)[activeResTab].length
                      }}</b></span
                    >
                    <button
                      @click="toggleSelectAll"
                      class="font-black text-blue-600 hover:underline px-2 py-1"
                    >
                      {{ isAllSelected ? "全部取消" : "本页全选" }}
                    </button>
                  </div>
                </div>

                <!-- UI Content -->
                <div
                  v-else
                  class="flex-1 overflow-y-auto bg-gray-50 p-2 sm:p-4 rounded-lg border border-gray-200"
                >
                  <div class="space-y-3">
                    <!-- 汇总条：一眼看出「直配 + 角色授予」的总量与来源构成 -->
                    <div
                      class="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-gray-200 bg-white px-3 py-2 text-[11px] text-gray-500"
                    >
                      <span>
                        已授予
                        <b class="text-gray-800">{{ grantedMenuCount }}</b> 个菜单 ·
                        <b class="text-gray-800">{{ grantedElementCount }}</b> 个功能点
                      </span>
                      <span
                        v-if="inheritedMenus.length || inheritedElements.length"
                        class="inline-flex items-center gap-1"
                      >
                        <span class="rounded-full bg-slate-200 px-1.5 py-0.5 text-[9px] font-bold text-slate-600">角色</span>
                        其中角色授予
                        <b class="text-slate-600">{{ inheritedMenus.length }}</b> 个菜单 /
                        <b class="text-slate-600">{{ inheritedElements.length }}</b> 个功能点
                      </span>
                      <span v-else class="text-gray-400">当前角色未额外授予页面权限</span>
                    </div>

                    <MenuAccessTree
                      :tree="permissionTree"
                      :menus="permissionData.menus"
                      :elements="permissionData.elements"
                      :inherited-menus="inheritedMenus"
                      :inherited-elements="inheritedElements"
                      :disabled="!permissionsLoaded"
                      @toggle-menu="toggleTreeItem"
                      @toggle-element="toggleTreeItem"
                      @toggle-menu-descendants="toggleMenuDescendants"
                      @inherited-blocked="onInheritedBlocked"
                    />
                  </div>
                </div>
              </div>
            </div>

            <!-- QUOTA TAB -->
            <div v-show="activeTab === 'quota'" class="space-y-4 py-2">
              <QuotaPolicyPanel
                v-if="editingUserId"
                scope-type="user"
                :scope-id="editingUserId"
                show-effective
              />
            </div>
          </div>

          <div
            class="mt-4 sm:mt-6 flex flex-col sm:flex-row sm:items-center justify-end gap-2 pt-4 border-t border-gray-200"
          >
            <p
              v-if="activeTab === 'quota'"
              class="order-3 sm:order-1 sm:mr-auto text-xs text-gray-400"
            >
              额度请在上方点击「保存额度」，与用户信息分开保存
            </p>
            <button
              @click="closeDialogs"
              class="order-2 sm:order-2 px-4 py-2.5 border border-gray-300 rounded-lg hover:bg-gray-50 text-sm font-medium"
            >
              {{ activeTab === 'quota' ? '关闭' : '取消' }}
            </button>
            <button
              v-if="!createdApiKey && activeTab !== 'quota'"
              @click="saveUser"
              :disabled="submitting"
              class="order-1 sm:order-3 px-4 py-2.5 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 text-sm font-bold shadow-md shadow-blue-100"
            >
              {{ showEditDialog ? "保存更新" : "立即创建用户" }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- 智能体级功能限定：用户 × 单个智能体 × 工具/MCP工具/技能（与全局禁用取并集） -->
    <AgentRestrictionDialog
      v-model="showAgentRestrictionDialog"
      kind="user"
      :scope-id="editingUserId"
      :agent-id="agentRestrictionTargetId"
      :agent-label="agentRestrictionTargetLabel"
      @saved="onAgentRestrictionSaved"
    />

    <!-- 知识库访问审批：申请人是同事，审批人是库归属人 -->
    <Teleport to="body">
      <div
        v-if="showAccessReviews"
        class="fixed inset-0 z-[9990] flex items-center justify-center bg-black bg-opacity-50"
        @click.self="showAccessReviews = false"
      >
        <div class="flex w-full max-w-2xl flex-col rounded-lg bg-white p-4 shadow-xl sm:p-6">
          <div class="mb-3 flex items-center justify-between">
            <h2 class="text-lg font-bold text-gray-900">知识库访问审批</h2>
            <button type="button" class="text-sm text-gray-400 hover:text-gray-600" @click="showAccessReviews = false">关闭</button>
          </div>
          <p class="mb-3 text-xs leading-relaxed text-gray-500">
            只列出你名下有管理权、或你是管理员的那些库的待审批申请。通过后会直接给对方加上访问授权；驳回建议写清原因，对方在「个人中心 → 我的申请」里能看到。
          </p>

          <div v-if="accessReviewsLoading" class="py-10 text-center text-sm text-gray-400">加载中…</div>
          <div v-else-if="!accessReviews.length" class="rounded-xl border border-dashed border-gray-200 py-10 text-center text-sm text-gray-400">
            没有待审批的申请
          </div>
          <ul v-else class="max-h-[55vh] space-y-2 overflow-y-auto pr-1">
            <li v-for="item in accessReviews" :key="item.id" class="rounded-xl border border-gray-200 p-3">
              <div class="flex items-start justify-between gap-2">
                <div class="min-w-0">
                  <p class="truncate text-sm font-semibold text-gray-900">{{ item.dataset_name || item.dataset_id }}</p>
                  <p class="mt-0.5 text-xs text-gray-500">
                    申请人：<span class="font-medium text-gray-700">{{ item.applicant_user_name || item.applicant_user_id }}</span>
                    · {{ String(item.created_at || '').replace('T', ' ').slice(0, 16) }}
                  </p>
                  <p v-if="item.reason" class="mt-1.5 rounded-lg bg-gray-50 px-2 py-1.5 text-xs leading-relaxed text-gray-600">理由：{{ item.reason }}</p>
                </div>
              </div>
              <div class="mt-2 flex flex-col gap-2 sm:flex-row sm:items-center">
                <input
                  v-model="reviewComments[item.id]"
                  type="text"
                  maxlength="2000"
                  placeholder="审批意见（驳回时建议填写）"
                  class="min-w-0 flex-1 rounded-lg border border-gray-300 px-2.5 py-1.5 text-xs"
                />
                <div class="flex gap-2">
                  <button
                    type="button"
                    class="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-emerald-700 disabled:opacity-50"
                    :disabled="reviewingRequestId === item.id"
                    @click="decideAccessReview(item, true)"
                  >通过</button>
                  <button
                    type="button"
                    class="rounded-lg border border-red-200 px-3 py-1.5 text-xs font-semibold text-red-600 hover:bg-red-50 disabled:opacity-50"
                    :disabled="reviewingRequestId === item.id"
                    @click="decideAccessReview(item, false)"
                  >驳回</button>
                </div>
              </div>
            </li>
          </ul>

          <div class="mt-3 flex items-center justify-between border-t pt-2">
            <span class="text-xs text-gray-500">共 {{ accessReviewTotal }} 条待审批</span>
            <button type="button" class="text-xs text-blue-600 hover:underline disabled:opacity-50" :disabled="accessReviewsLoading" @click="fetchAccessReviews">刷新</button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- View API Key Dialog -->
    <Teleport to="body">
      <div
        v-if="showViewKeyDialog"
        class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-[9990]"
        @click.self="closeViewKeyDialog"
      >
        <div class="bg-white rounded-lg p-6 w-full max-w-lg shadow-xl">
          <h2
            class="text-xl font-bold mb-4 flex items-center gap-2 text-indigo-600"
          >
            <KeyIcon class="w-6 h-6" /> 查看 API Key
          </h2>
          <div class="bg-indigo-50 border border-indigo-100 rounded-lg p-4 mb-6">
            <div v-if="loadingViewKey" class="text-center py-4 text-gray-500">
              加载中...
            </div>
            <div v-else-if="viewedApiKey" class="flex items-center gap-2">
              <input
                type="text"
                :value="viewedApiKey"
                readonly
                class="flex-1 px-3 py-2 border border-gray-300 rounded bg-white text-xs font-mon read-only:bg-gray-100o"
              />
              <button
                @click="copyViewedApiKey"
                class="px-3 py-2 bg-indigo-600 text-white rounded hover:bg-indigo-700"
              >
                复制
              </button>
            </div>
          </div>
          <div class="flex justify-end">
            <button
              @click="closeViewKeyDialog"
              class="px-4 py-2 border border-gray-300 rounded-lg hover:bg-gray-50"
            >
              关闭
            </button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- Batch Status Confirmation -->
    <Teleport to="body">
      <div
        v-if="showBatchStatusDialog"
        class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-[9990]"
        @click.self="closeBatchStatusDialog"
      >
        <div class="bg-white rounded-lg p-6 w-full max-w-md shadow-xl text-center">
          <div
            class="p-3 rounded-full inline-block mb-4"
            :class="
              batchStatusTarget === 0
                ? 'bg-red-100 text-red-600'
                : 'bg-emerald-100 text-emerald-600'
            "
          >
            <svg
              v-if="batchStatusTarget === 0"
              class="w-8 h-8"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
            >
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M18.364 18.364A9 9 0 005.636 5.636m12.728 12.728A9 9 0 015.636 5.636m12.728 12.728L5.636 5.636"
              />
            </svg>
            <svg v-else class="w-8 h-8" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M9 12.75L11.25 15 15 9.75M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
              />
            </svg>
          </div>
          <h2 class="text-xl font-bold mb-2 text-gray-900">
            {{ batchStatusTarget === 0 ? "确认批量禁用" : "确认批量启用" }}
          </h2>
          <p class="text-gray-500 mb-2">
            将对
            <strong class="text-gray-900">{{ selectedUserIds.length }}</strong>
            个用户执行「{{ batchStatusTarget === 0 ? "禁用" : "启用" }}」。
          </p>
          <p
            v-if="batchStatusTarget === 0"
            class="text-xs text-red-500 bg-red-50 border border-red-100 rounded-lg px-3 py-2 mb-6 text-left"
          >
            禁用后这些用户将<strong>无法登录系统</strong>，已登录的会话会被立即踢出。请确认无误后再继续。
          </p>
          <p v-else class="text-xs text-gray-400 mb-6">
            启用后这些用户可正常登录系统，权限按原有配置恢复。
          </p>
          <div class="flex justify-center gap-3">
            <button
              :disabled="batchSubmitting"
              class="px-6 py-2 border border-gray-300 rounded-lg hover:bg-gray-50 font-medium disabled:opacity-50"
              @click="closeBatchStatusDialog"
            >
              取消
            </button>
            <button
              :disabled="batchSubmitting"
              class="px-6 py-2 text-white rounded-lg font-medium disabled:opacity-50"
              :class="
                batchStatusTarget === 0
                  ? 'bg-red-600 hover:bg-red-700'
                  : 'bg-emerald-600 hover:bg-emerald-700'
              "
              @click="submitBatchStatus"
            >
              {{ batchSubmitting ? "处理中..." : batchStatusTarget === 0 ? "确认禁用" : "确认启用" }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- 审核通过确认：可选分配业务角色 -->
    <Teleport to="body">
      <div
        v-if="reviewApproveTarget"
        class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-[9990] px-4"
        @click.self="closeApproveDialog"
      >
        <div class="bg-white rounded-lg p-6 w-full max-w-lg shadow-xl">
          <h2 class="text-xl font-bold mb-1 text-gray-900">审核通过并启用</h2>
          <p class="text-sm text-gray-500 mb-4">
            将为
            <strong class="text-gray-900">{{ reviewApproveTarget.user_name }}</strong>
            （{{ reviewApproveTarget.real_name || "未设置姓名" }}）启用账号，该用户即可使用账号密码登录。
          </p>

          <div class="mb-5">
            <div class="flex items-center justify-between mb-2">
              <label class="text-xs font-semibold text-gray-600">业务角色（可选）</label>
              <span class="text-[11px] text-gray-400">不选则先不分配，稍后可在角色管理中补充</span>
            </div>

            <div v-if="businessRoles.length === 0" class="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2.5 text-xs text-gray-500">
              无可分配的业务角色（需具备角色管理权限），可直接通过审核。
            </div>

            <div v-else class="max-h-56 overflow-y-auto rounded-lg border border-gray-200 divide-y divide-gray-100">
              <label
                v-for="role in businessRoles"
                :key="role.id"
                class="flex items-center gap-3 px-3 py-2 cursor-pointer hover:bg-gray-50"
              >
                <input
                  type="checkbox"
                  class="h-4 w-4 rounded border-gray-300 text-primary focus:ring-2 focus:ring-primary/30"
                  :value="role.id"
                  v-model="reviewRoleIds"
                />
                <span class="min-w-0">
                  <span class="block text-sm text-gray-800 truncate">{{ role.name }}</span>
                  <span v-if="role.code" class="block text-[10px] text-gray-400 font-mono truncate">{{ role.code }}</span>
                </span>
              </label>
            </div>
          </div>

          <div class="flex justify-end gap-3">
            <button
              type="button"
              :disabled="reviewSubmitting"
              class="px-5 py-2 border border-gray-300 rounded-lg hover:bg-gray-50 font-medium disabled:opacity-50"
              @click="closeApproveDialog"
            >
              取消
            </button>
            <button
              type="button"
              :disabled="reviewSubmitting"
              class="px-5 py-2 text-white rounded-lg font-medium bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50"
              @click="approveUser"
            >
              {{ reviewSubmitting ? "处理中..." : "确认通过并启用" }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- 审核禁用（拒绝）确认 -->
    <Teleport to="body">
      <div
        v-if="reviewRejectTarget"
        class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-[9990] px-4"
        @click.self="closeRejectDialog"
      >
        <div class="bg-white rounded-lg p-6 w-full max-w-md shadow-xl text-center">
          <div class="p-3 rounded-full inline-block mb-4 bg-red-100 text-red-600">
            <svg class="w-8 h-8" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M18.364 18.364A9 9 0 005.636 5.636m12.728 12.728A9 9 0 015.636 5.636m12.728 12.728L5.636 5.636" />
            </svg>
          </div>
          <h2 class="text-xl font-bold mb-2 text-gray-900">确认禁用该申请</h2>
          <p class="text-gray-500 mb-2">
            将对
            <strong class="text-gray-900">{{ reviewRejectTarget.user_name }}</strong>
            执行「禁用」。
          </p>
          <p class="text-xs text-red-500 bg-red-50 border border-red-100 rounded-lg px-3 py-2 mb-6 text-left">
            该用户将<strong>无法登录系统</strong>，且账号名<strong>永久占用</strong>——被禁用的账号不会释放账号名，该用户无法用同名账号重新申请。请确认无误后再继续。
          </p>
          <div class="flex justify-center gap-3">
            <button
              type="button"
              :disabled="reviewSubmitting"
              class="px-6 py-2 border border-gray-300 rounded-lg hover:bg-gray-50 font-medium disabled:opacity-50"
              @click="closeRejectDialog"
            >
              取消
            </button>
            <button
              type="button"
              :disabled="reviewSubmitting"
              class="px-6 py-2 text-white rounded-lg font-medium bg-red-600 hover:bg-red-700 disabled:opacity-50"
              @click="rejectUser"
            >
              {{ reviewSubmitting ? "处理中..." : "确认禁用" }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- Delete Confirmation -->
    <Teleport to="body">
      <div
        v-if="showDeleteDialog"
        class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-[9990]"
        @click.self="showDeleteDialog = false"
      >
        <div
          class="bg-white rounded-lg p-6 w-full max-w-md shadow-xl text-center"
        >
          <div class="p-3 bg-red-100 rounded-full inline-block mb-4 text-red-600">
            <TrashIcon class="w-8 h-8" />
          </div>
          <h2 class="text-xl font-bold mb-2 text-gray-900">确认删除用户</h2>
          <p class="text-gray-500 mb-6">
            您确定要注销账号 <strong>{{ userToDelete?.user_name }}</strong> 吗？
          </p>
          <div class="flex justify-center gap-3">
            <button
              @click="showDeleteDialog = false"
              class="px-6 py-2 border border-gray-300 rounded-lg hover:bg-gray-50 font-medium"
            >
              取消
            </button>
            <button
              @click="deleteUser"
              class="px-6 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 font-medium"
            >
              确认删除
            </button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- Set Password Dialog：与部门成员页共用同一组件，等保规则只有一份实现 -->
    <PasswordResetDialog
      v-if="showSetPasswordDialog && userToSetPassword"
      title="设置密码"
      :display-name="userToSetPassword.user_name"
      :submitting="settingPassword"
      @cancel="closeSetPasswordDialog"
      @submit="executeSetPassword"
    />

    <!-- Regenerate Key Dialog -->
    <Teleport to="body">
      <div
        v-if="showRegenerateDialog"
        class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-[9990]"
        @click.self="closeRegenerateDialog"
      >
        <div
          class="bg-white rounded-lg p-6 w-full max-w-lg shadow-xl text-center"
        >
          <h2 class="text-xl font-bold mb-4 text-amber-600">重置 API Key</h2>
          <div v-if="!regeneratedApiKey" class="space-y-4">
            <p class="text-gray-600">
              确定要重置用户
              <strong>{{ userToRegenerate?.user_name }}</strong>
              的密钥吗？旧密钥将立即失效。
            </p>
            <div class="flex justify-center gap-3">
              <button
                @click="closeRegenerateDialog"
                class="px-6 py-2 border border-gray-300 rounded-lg hover:bg-gray-50"
              >
                取消
              </button>
              <button
                @click="executeRegenerateApiKey"
                class="px-6 py-2 bg-amber-600 text-white rounded-lg hover:bg-amber-700 font-medium"
              >
                确认重置
              </button>
            </div>
          </div>
          <div v-else class="space-y-4">
            <p class="text-sm font-bold text-green-600">新密钥生成成功！</p>
            <div class="flex items-center gap-2">
              <code
                class="flex-1 bg-gray-50 p-2 rounded border text-xs break-all font-mono select-all"
                >{{ regeneratedApiKey }}</code
              >
              <button
                @click="copyRegeneratedApiKey"
                class="px-3 py-1.5 bg-green-600 text-white rounded text-xs"
              >
                复制
              </button>
            </div>
            <button
              @click="closeRegenerateDialog"
              class="w-full py-2 bg-gray-900 text-white rounded font-bold"
            >
              已保存，关闭
            </button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- Disable 2FA Dialog -->
    <Teleport to="body">
      <div
        v-if="showDisable2FADialog"
        class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-[9990]"
        @click.self="closeDisable2FADialog"
      >
        <div
          class="bg-white rounded-xl p-6 w-full max-w-md shadow-2xl text-center"
        >
          <div class="w-12 h-12 bg-purple-50 text-purple-600 rounded-full flex items-center justify-center mx-auto mb-3">
            <svg class="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 11V7a4 4 0 118 0m-4 8v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2z" />
            </svg>
          </div>
          <h2 class="text-lg font-bold mb-2 text-gray-900">关闭两步验证 (2FA)</h2>
          <p class="text-sm text-gray-600 leading-relaxed mb-6">
            确定要为用户
            <strong class="text-gray-900 font-semibold">{{ userToDisable2FA?.user_name }}</strong>
            强制关闭两步验证吗？<br />
            <span class="text-xs text-gray-400 mt-1 block">关闭后该用户将不再需要动态验证码，恢复为仅凭账号密码登录。</span>
          </p>
          <div class="flex justify-center gap-3">
            <button
              type="button"
              @click="closeDisable2FADialog"
              :disabled="disabling2FA"
              class="px-5 py-2 border border-gray-300 rounded-lg hover:bg-gray-50 text-sm font-medium text-gray-700 disabled:opacity-50"
            >
              取消
            </button>
            <button
              type="button"
              @click="executeDisable2FA"
              :disabled="disabling2FA"
              class="px-5 py-2 bg-purple-600 text-white rounded-lg hover:bg-purple-700 text-sm font-medium transition-colors inline-flex items-center gap-1.5 disabled:opacity-50"
            >
              <span v-if="disabling2FA" class="animate-spin w-4 h-4 border-2 border-white border-t-transparent rounded-full"></span>
              <span>{{ disabling2FA ? '关闭中...' : '确认关闭' }}</span>
            </button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- SSO Sync Modal -->
    <Teleport to="body">
      <div
        v-if="showSsoModal"
        class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-[9990]"
        @click.self="showSsoModal = false"
      >
        <div
          class="bg-white rounded-lg shadow-2xl flex flex-col transition-all"
          :class="isMobile ? 'w-full h-full rounded-none' : 'w-[800px] max-h-[85vh]'"
        >
          <!-- Modal Header -->
          <div class="px-6 py-4 border-b border-gray-100 flex justify-between items-center">
            <div>
              <h2 class="text-xl font-black text-gray-900 flex items-center gap-2">
                <UserGroupIcon class="w-6 h-6 text-indigo-600" />
                同步 SSO 用户
              </h2>
              <p class="text-xs text-gray-400 mt-0.5">从 Laplace SSO 中心同步用户，默认分配“普通用户”身份。</p>
            </div>
            <button @click="showSsoModal = false" class="p-2 hover:bg-gray-100 rounded-full transition-colors">
              <svg class="w-5 h-5 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>

          <!-- Modal Toolbar -->
          <div class="px-6 py-3 bg-gray-50 flex flex-col sm:flex-row gap-3 items-center">
            <div class="relative flex-1 w-full">
              <input
                v-model="ssoSearchQuery"
                type="search"
                placeholder="搜索用户名、姓名或部门..."
                class="w-full pl-9 pr-4 py-2 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-indigo-500 transition-all"
              />
              <svg class="absolute left-3 top-2.5 w-4 h-4 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
              </svg>
            </div>
            <div class="flex items-center gap-4">
              <label class="flex items-center gap-2 cursor-pointer group">
                <input
                  type="checkbox"
                  v-model="hideSynced"
                  class="h-4 w-4 text-indigo-600 rounded border-gray-300 focus:ring-indigo-500"
                />
                <span class="text-xs font-bold text-gray-500 group-hover:text-gray-700 transition-colors">隐藏已同步</span>
              </label>
              <div class="text-[11px] text-gray-400 whitespace-nowrap border-l border-gray-200 pl-4">
                已选 <span class="text-indigo-600 font-bold">{{ selectedSsoUsernames.length }}</span> 人
              </div>
            </div>
          </div>

          <!-- Modal Content -->
          <div class="flex-1 overflow-y-auto p-0 min-h-[300px]">
            <div v-if="loadingSso" class="h-64 flex flex-col items-center justify-center text-gray-500">
              <div class="w-8 h-8 border-4 border-indigo-600 border-t-transparent rounded-full animate-spin"></div>
              <p class="mt-4 text-sm font-medium">正在获取 SSO 数据...</p>
            </div>

            <div v-else-if="filteredSsoUsers.length === 0" class="h-64 flex flex-col items-center justify-center text-gray-400">
              <svg class="w-12 h-12 opacity-20 mb-2" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z" />
              </svg>
              <p class="text-sm">未找到匹配的用户</p>
            </div>

            <table v-else class="min-w-full divide-y divide-gray-100">
              <thead class="bg-gray-50 sticky top-0 z-10">
                <tr>
                  <th class="px-6 py-3 text-left w-10">
                    <input
                      type="checkbox"
                      :checked="isAllSsoSelected"
                      @change="toggleSsoSelectAll"
                      class="h-4 w-4 text-indigo-600 rounded border-gray-300 focus:ring-indigo-500"
                    />
                  </th>
                  <th class="px-4 py-3 text-left text-[11px] font-bold text-gray-400 uppercase tracking-wider">用户名/姓名</th>
                  <th class="px-4 py-3 text-left text-[11px] font-bold text-gray-400 uppercase tracking-wider">部门/职位</th>
                  <th class="px-4 py-3 text-right text-[11px] font-bold text-gray-400 uppercase tracking-wider">状态</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-gray-100">
                <tr
                  v-for="user in filteredSsoUsers"
                  :key="user.code"
                  class="hover:bg-indigo-50/30 transition-colors"
                  :class="user.is_synced ? 'opacity-50 grayscale-[0.5]' : 'cursor-pointer'"
                  @click="!user.is_synced && (selectedSsoUsernames.includes(user.code) ? selectedSsoUsernames = selectedSsoUsernames.filter(c => c !== user.code) : selectedSsoUsernames.push(user.code))"
                >
                  <td class="px-6 py-3" @click.stop>
                    <input
                      v-if="!user.is_synced"
                      type="checkbox"
                      :value="user.code"
                      v-model="selectedSsoUsernames"
                      class="h-4 w-4 text-indigo-600 rounded border-gray-300 focus:ring-indigo-500"
                    />
                  </td>
                  <td class="px-4 py-3">
                    <div class="flex flex-col">
                      <span class="text-sm font-bold text-gray-900">{{ user.name }}</span>
                      <span class="text-[10px] font-mono text-gray-400 uppercase tracking-tight">{{ user.code }}</span>
                    </div>
                  </td>
                  <td class="px-4 py-3">
                    <div class="flex flex-col max-w-[200px] overflow-hidden">
                      <span class="text-[11px] text-gray-600 truncate">{{ user.department || '-' }}</span>
                      <span class="text-[10px] text-gray-400 truncate">{{ user.position || '-' }}</span>
                    </div>
                  </td>
                  <td class="px-4 py-3 text-right">
                    <span
                      v-if="user.is_synced"
                      class="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-black bg-gray-100 text-gray-400 border border-gray-200"
                    >
                      已同步
                    </span>
                    <span
                      v-else
                      class="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-black bg-green-100 text-green-700 border border-green-200"
                    >
                      待同步
                    </span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <!-- Modal Footer -->
          <div class="px-6 py-4 border-t border-gray-100 flex justify-end gap-3 bg-gray-50 rounded-b-lg">
            <button
              @click="showSsoModal = false"
              class="px-6 py-2 border border-gray-300 rounded-lg hover:bg-gray-100 text-sm font-medium transition-all"
            >
              关闭
            </button>
            <button
              @click="executeSsoSync"
              :disabled="selectedSsoUsernames.length === 0 || syncingSso"
              class="px-8 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed text-sm font-bold shadow-lg shadow-indigo-100 flex items-center gap-2"
            >
              <span v-if="syncingSso" class="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin"></span>
              立即同步选中的 {{ selectedSsoUsernames.length }} 个用户
            </button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- SSO Sync Confirmation Modal -->
    <Teleport to="body">
      <div
        v-if="showSsoConfirmModal"
        class="fixed inset-0 bg-black bg-opacity-60 flex items-center justify-center z-[9995]"
        @click.self="showSsoConfirmModal = false"
      >
        <div class="bg-white rounded-xl shadow-2xl w-full max-w-lg overflow-hidden animate-fade-in-up">
          <div class="px-6 py-4 border-b border-gray-100 bg-gray-50/50">
            <h3 class="text-lg font-black text-gray-900 flex items-center gap-2">
              <UserGroupIcon class="w-5 h-5 text-indigo-600" />
              确认同步配置
            </h3>
          </div>

          <div class="p-6 space-y-5">
            <div class="bg-indigo-50/50 p-4 rounded-lg border border-indigo-100">
              <p class="text-sm text-indigo-900 font-bold mb-1">已选择 {{ selectedSsoUsernames.length }} 个用户</p>
              <div class="flex flex-wrap gap-1 mt-2">
                <span v-for="uname in selectedSsoUsernames.slice(0, 10)" :key="uname" class="text-[10px] bg-white px-2 py-0.5 rounded border border-indigo-200 text-indigo-600 font-mono">{{ uname }}</span>
                <span v-if="selectedSsoUsernames.length > 10" class="text-[10px] text-gray-400 self-center">...及其他 {{ selectedSsoUsernames.length - 10 }} 人</span>
              </div>
            </div>

            <!-- Role Assignment -->
            <div class="space-y-4">
              <div>
                <label class="block text-xs font-black text-gray-400 uppercase mb-2 tracking-widest">分配系统身份</label>
                <select
                  v-model="ssoSyncRole"
                  class="w-full border border-gray-200 rounded-lg px-4 py-2 text-sm focus:ring-2 focus:ring-indigo-500 transition-all outline-none"
                >
                  <option value="user">普通用户 (User)</option>
                  <option value="admin">管理员 (Admin)</option>
                </select>
              </div>

              <div>
                <div class="flex justify-between items-center mb-2">
                  <label class="block text-xs font-black text-gray-400 uppercase tracking-widest">分配业务角色</label>
                  <span class="text-[10px] text-gray-400">可选</span>
                </div>
                <div class="border border-gray-100 rounded-lg p-3 max-h-48 overflow-y-auto bg-gray-50/30 grid grid-cols-1 gap-2">
                  <label
                    v-for="role in businessRoles"
                    :key="role.id"
                    class="flex items-center space-x-2 text-sm cursor-pointer hover:bg-white p-2 rounded transition-colors"
                    :class="ssoSyncRoleIds.includes(role.id) ? 'bg-white shadow-sm ring-1 ring-indigo-500/20' : ''"
                  >
                    <input
                      type="checkbox"
                      :value="role.id"
                      v-model="ssoSyncRoleIds"
                      class="h-4 w-4 rounded text-indigo-600 focus:ring-indigo-500"
                    />
                    <span class="text-xs font-bold" :class="ssoSyncRoleIds.includes(role.id) ? 'text-indigo-600' : 'text-gray-600'">{{ role.name }}</span>
                  </label>
                </div>
              </div>
            </div>
          </div>

          <div class="px-6 py-4 bg-gray-50 border-t border-gray-100 flex justify-end gap-3">
            <button
              @click="showSsoConfirmModal = false"
              class="px-6 py-2 text-gray-500 hover:text-gray-700 font-medium text-sm"
            >
              返回调整
            </button>
            <button
              @click="confirmExecuteSsoSync"
              :disabled="syncingSso"
              class="px-8 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-50 text-sm font-black shadow-lg shadow-indigo-200 flex items-center gap-2 transition-all transform hover:-translate-y-0.5"
            >
              <span v-if="syncingSso" class="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin"></span>
              确认同步入库
            </button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>

  <!-- System Default Quota Modal -->
  <Teleport to="body">
    <div
      v-if="showSystemQuotaModal"
      class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-[9990] p-4"
      @click.self="showSystemQuotaModal = false"
    >
      <div class="bg-white rounded-2xl shadow-2xl w-full max-w-lg max-h-[90vh] overflow-y-auto p-6">
        <div class="flex items-center justify-between mb-4">
          <div>
            <h2 class="text-xl font-bold text-gray-900">系统默认额度</h2>
            <p class="text-xs text-gray-500 mt-1">未配置用户/角色策略时的全局月 Token 上限</p>
          </div>
          <button
            @click="showSystemQuotaModal = false"
            class="p-2 text-gray-400 hover:text-gray-600 rounded-lg hover:bg-gray-100"
          >
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
        <QuotaPolicyPanel scope-type="system" />
        <div class="mt-6 flex justify-end pt-4 border-t border-gray-100">
          <button
            type="button"
            @click="showSystemQuotaModal = false"
            class="px-4 py-2.5 border border-gray-300 rounded-lg hover:bg-gray-50 text-sm font-medium"
          >
            关闭
          </button>
        </div>
      </div>
    </div>
  </Teleport>

  <ThirdPartyUserSyncDrawer
    :visible="showThirdPartyDrawer"
    @close="showThirdPartyDrawer = false"
    @synced="onThirdPartySynced"
  />

  <UserImportDialog
    :visible="showImportDialog"
    @close="showImportDialog = false"
    @imported="fetchUsers"
  />
</template>

<script setup lang="ts">
import { ref, onMounted, computed, onUnmounted, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import axios from "../utils/axios";
import { maskEmail } from "../utils/maskEmail";
import { modelApi } from "../api/model";
import {
  shouldShowModelSearch,
  groupPermissionModels,
} from "../utils/modelSelect";
import { persistUserInfo } from "../utils/userSession";
import { useToast } from "../composables/useToast";
import { useBranding } from "../composables/useBranding";
import { useUser } from "../composables/useUser";
import { getMenuDescendantIds, type PermissionNode } from "../constants/permissions";
import { filterBusinessRoles, summarizeSelectedRoles } from "../utils/roleFilter";
import { copyToClipboard } from "../utils/clipboard";
import {
  extraDataJsonToPairs,
  isExtraDataJsonObject,
  pairsToExtraDataJson,
  type ExtraDataPair,
} from "../utils/userExtraData";
import Switch from "../components/Switch.vue";
import ForbiddenResourceTabs from "../components/system/ForbiddenResourceTabs.vue";
import AgentRestrictionDialog from "../components/system/AgentRestrictionDialog.vue";
import { parseForbiddenCommands } from "../utils/forbiddenCommands";
import { countForbiddenResourceBadge } from "../utils/forbiddenResourceBadge";
import {
  agentRestrictionCacheKey,
  agentRestrictionCountsPath,
  countAgentRestrictionBadge,
  type AgentRestrictionLists,
} from "../utils/agentRestrictions";
import { writeAgentRestriction } from "../utils/agentRestrictionCache";
import {
  peekForbiddenCatalog,
  subscribeForbiddenCatalog,
  type ForbiddenCatalogSnapshot,
} from "../utils/forbiddenCatalogCache";
import {
  applyMenuCascadeToggle,
  applyMenuEntryToggle,
  buildUserPermissionPayload,
  emptyPermissionLists,
  resolveUserPermissionEditState,
  type InheritedForbiddenLists,
  type MenuCascadeMode,
} from "../utils/permissionLists";
import { extractApiErrorDetail } from "../utils/apiErrorDetail";
import MenuAccessTree from "../components/user/MenuAccessTree.vue";
import RoleList from "../components/RoleList.vue";
import ThirdPartyUserSyncDrawer from "../components/ThirdPartyUserSyncDrawer.vue";
import UserImportDialog from "../components/user/UserImportDialog.vue";
import DepartmentPicker from "../components/department/DepartmentPicker.vue";
// 与部门成员页共用同一个密码弹窗：等保规则说明与实时合规指标只有一份实现
import PasswordResetDialog from "../components/PasswordResetDialog.vue";
import QuotaPolicyPanel from "../components/admin/QuotaPolicyPanel.vue";
import {
  KeyIcon,
  ArrowPathIcon,
  ArrowUpTrayIcon,
  PencilSquareIcon,
  TrashIcon,
  PlusIcon,
  UserGroupIcon,
  LockClosedIcon,
  ChevronDownIcon,
  EllipsisHorizontalIcon,
  ChartBarIcon,
  FunnelIcon,
} from "@heroicons/vue/24/outline";

const { showToast } = useToast();
const { branding } = useBranding();
const { hasPermission } = useUser();

const canEditUser = computed(() => hasPermission("element:user:edit"));
const canViewUserKey = computed(() => hasPermission("element:user:view_key"));
const canResetUserKey = computed(() => hasPermission("element:user:reset_key"));
const canDeleteUser = computed(() => hasPermission("element:user:delete"));
const canOpenSystemQuota = computed(() => hasPermission("menu:system:users"));
const showMoreMenuPanel = computed(
  () =>
    (showSsoSync.value && canEditUser.value) ||
    canEditUser.value ||
    canOpenSystemQuota.value,
);
const hasRowMoreActions = computed(
  () => canEditUser.value || canResetUserKey.value || canDeleteUser.value,
);
const hasAnyRowAction = computed(
  () => canEditUser.value || canViewUserKey.value || hasRowMoreActions.value,
);

const windowWidth = ref(window.innerWidth);
const isMobile = computed(() => windowWidth.value < 768);
const handleResize = () => {
  windowWidth.value = window.innerWidth;
};

onMounted(() => {
  window.addEventListener("resize", handleResize);
  document.addEventListener("click", closeMenus);
});
onUnmounted(() => {
  window.removeEventListener("resize", handleResize);
  document.removeEventListener("click", closeMenus);
});

// State
const ssoEnabled = ref(false);
const showSsoSync = computed(
  () => ssoEnabled.value && !branding.value.hide_login_sso
);
const users = ref<any[]>([]);
const businessRoles = ref<any[]>([]);
/**
 * 业务角色是否加载失败。
 *
 * 角色列表走独立的 `/api/portal/roles`（需要「角色管理」查看权限）。失败时如果只
 * `console.error`，界面就是一个空网格——看起来像「系统里没有这个角色」，而实际上
 * 可能只是这一次请求没成功（用户反馈过：角色管理里明明有 default，编辑用户却搜不到）。
 */
const roleListError = ref(false);
const roleListErrorStatus = ref<number | null>(null);
const loading = ref(false);
const total = ref(0);
const page = ref(1);
const size = ref(15);
const totalPages = ref(0);

// --- 待审核视图 ---
// 数据源与主列表同一个接口（GET /users?status=2），只是换成卡片渲染并收起无关操作。
// 不复用主表格：批量勾选、API Key、角色编辑、2FA 徽标对「待审核」全是噪音。
const activeView = ref<"list" | "review" | "wechat">("list");
const pendingCount = ref(0);
const reviewApproveTarget = ref<any>(null);
const reviewRejectTarget = ref<any>(null);
const reviewRoleIds = ref<number[]>([]);
const reviewSubmitting = ref(false);
// 行内按钮的 loading 态；后端没有「单条审核」接口，用 id 标记正在处理的那一条
const reviewSubmittingId = ref<number | null>(null);
// 审核结果发信反馈：记录「最近一次发信失败」的用户（userId -> 审核结果 approved），
// 只有这些用户才显示「重发通知邮件」入口；审核成功且邮件已发出不入表。
const mailFailedUsers = ref<Record<number, boolean>>({});
const resendingMailId = ref<number | null>(null);

// 重发时 approved 必须取该用户的实际审核结果：启用=true，禁用=false
const reviewApprovedFor = (user: any): boolean => {
  if (!user) return false;
  const recorded = mailFailedUsers.value[user.id];
  if (recorded === true || recorded === false) return recorded;
  return user.status === 1;
};
const canResendReviewMail = (user: any): boolean =>
  Boolean(user) && (user.id in mailFailedUsers.value);

// 审核响应带 email_sent / email_error：不能把「审核成功但邮件没发出去」当成一切正常
const reportReviewMailResult = (
  data: any,
  fallbackMessage: string,
  failedMessage: string,
  userId: number,
  approved: boolean
) => {
  const next = { ...mailFailedUsers.value };
  if (data?.email_sent === true) {
    delete next[userId];
    mailFailedUsers.value = next;
    showToast(`${fallbackMessage}，通知邮件已发送`, "success");
  } else if (data?.email_sent === false) {
    next[userId] = approved;
    mailFailedUsers.value = next;
    showToast(`${failedMessage}：${data?.email_error || "未知原因"}`, "warning");
  } else {
    // 后端未返回发信字段（旧版本）时保持原有提示
    showToast(fallbackMessage, "success");
  }
};

const resendReviewMail = async (userId: number, approved: boolean) => {
  if (!userId) return;
  resendingMailId.value = userId;
  try {
    const { data } = await axios.post(
      `/api/portal/management/users/${userId}/notify-review-mail`,
      { approved }
    );
    if (data?.ok) {
      const next = { ...mailFailedUsers.value };
      delete next[userId];
      mailFailedUsers.value = next;
    }
    showToast(data?.message || (data?.ok ? "通知邮件已重发" : "重发失败"), data?.ok ? "success" : "error");
  } catch (e: any) {
    showToast(`重发失败: ${e.response?.data?.detail || e.message}`, "error");
  } finally {
    resendingMailId.value = null;
  }
};

const fetchPendingCount = async () => {
  try {
    const res = await axios.get("/api/portal/management/users/pending-count");
    pendingCount.value = Number(res.data?.count || 0);
  } catch (e) {
    console.error(e);
    pendingCount.value = 0;
  }
};

const switchView = async (view: "list" | "review" | "wechat") => {
  if (activeView.value === view) return;
  activeView.value = view;
  closeMenus();
  clearSelection();
  page.value = 1;
  if (view === "review") {
    // 待审核视图固定按 status=2 拉取；离开时还原，避免污染主列表筛选
    statusFilter.value = "2";
    await Promise.all([fetchUsers(), fetchPendingCount()]);
  } else if (view === "wechat") {
    // 同样要还原 statusFilter，否则会把 status=2 带到绑定视图
    statusFilter.value = "";
    bindingPage.value = 1;
    await fetchBindings();
  } else {
    statusFilter.value = "";
    await fetchUsers();
  }
};

const openApproveDialog = async (user: any) => {
  reviewRejectTarget.value = null;
  reviewRoleIds.value = [];
  reviewApproveTarget.value = user;
  // 角色列表可能在进入页面时因缺少 menu:system:roles 而没加载到；
  // 拿不到就降级为「纯通过」，不阻塞审核动作。
  if (businessRoles.value.length === 0) {
    await fetchBusinessRoles();
  }
};

const closeApproveDialog = () => {
  reviewApproveTarget.value = null;
  reviewRoleIds.value = [];
};

const openRejectDialog = (user: any) => {
  reviewApproveTarget.value = null;
  reviewRejectTarget.value = user;
};

const closeRejectDialog = () => {
  reviewRejectTarget.value = null;
};

const approveUser = async () => {
  const target = reviewApproveTarget.value;
  if (!target) return;
  reviewSubmitting.value = true;
  reviewSubmittingId.value = target.id;
  try {
    const { data } = await axios.patch(
      `/api/portal/management/users/${target.id}/status`,
      {
        status: 1,
        role_ids: reviewRoleIds.value,
      }
    );
    reportReviewMailResult(
      data,
      `已启用账号 ${target.user_name}`,
      `已启用账号 ${target.user_name}，但通知邮件未发送`,
      target.id,
      true
    );
    closeApproveDialog();
    await Promise.all([fetchUsers(), fetchPendingCount()]);
  } catch (e: any) {
    showToast(e.response?.data?.detail || "审核失败，请重试", "error");
  } finally {
    reviewSubmitting.value = false;
    reviewSubmittingId.value = null;
  }
};

const rejectUser = async () => {
  const target = reviewRejectTarget.value;
  if (!target) return;
  reviewSubmitting.value = true;
  reviewSubmittingId.value = target.id;
  try {
    const { data } = await axios.patch(
      `/api/portal/management/users/${target.id}/status`,
      {
        status: 0,
      }
    );
    reportReviewMailResult(
      data,
      `已禁用账号 ${target.user_name}`,
      `已禁用账号 ${target.user_name}，但通知邮件未发送`,
      target.id,
      false
    );
    closeRejectDialog();
    await Promise.all([fetchUsers(), fetchPendingCount()]);
  } catch (e: any) {
    showToast(e.response?.data?.detail || "操作失败，请重试", "error");
  } finally {
    reviewSubmitting.value = false;
    reviewSubmittingId.value = null;
  }
};

// Filters
const searchQuery = ref("");

// ---------- 知识库访问审批 ----------
// 审批人是「库归属人」而不是管理员，所以这个入口按"我能审批什么"来查，
// 而不是按"谁有用户管理权限"。
const showAccessReviews = ref(false);
const accessReviews = ref<any[]>([]);
const accessReviewsLoading = ref(false);
const accessReviewTotal = ref(0);
const reviewComments = ref<Record<number, string>>({});
const reviewingRequestId = ref<number | null>(null);
const pendingReviewCount = ref(0);

const fetchPendingReviewCount = async () => {
  try {
    const response = await axios.get("/api/portal/ragflow/access-requests/pending");
    pendingReviewCount.value = response.data?.data?.total || 0;
  } catch {
    pendingReviewCount.value = 0;
  }
};

const openAccessReviews = () => {
  showAccessReviews.value = true;
  void fetchAccessReviews();
};

const fetchAccessReviews = async () => {
  accessReviewsLoading.value = true;
  try {
    const response = await axios.get("/api/portal/ragflow/access-requests/pending");
    const data = response.data?.data || {};
    accessReviews.value = data.items || [];
    accessReviewTotal.value = data.total || 0;
    pendingReviewCount.value = accessReviewTotal.value;
  } catch (err: any) {
    showToast(err?.response?.data?.detail || "读取待审批申请失败", "error");
  } finally {
    accessReviewsLoading.value = false;
  }
};

const decideAccessReview = async (item: any, approve: boolean) => {
  if (reviewingRequestId.value) return;
  reviewingRequestId.value = item.id;
  try {
    await axios.post(
      `/api/portal/ragflow/access-requests/${item.id}/${approve ? "approve" : "reject"}`,
      { comment: (reviewComments.value[item.id] || "").trim() || null },
    );
    showToast(approve ? "已通过，对方现在可以访问" : "已驳回", "success");
    delete reviewComments.value[item.id];
    await fetchAccessReviews();
  } catch (err: any) {
    showToast(err?.response?.data?.detail || "审批失败", "error");
  } finally {
    reviewingRequestId.value = null;
  }
};
// 角色成员抽屉里的「↗」会带 ?q=<user_name> 跳过来，这里只做搜索预填
const route = useRoute();
const router = useRouter();
const roleFilter = ref("");
const statusFilter = ref("");
// 部门维度筛选：deptFilter 为空表示不限；includeSub 决定是否含子部门
const deptFilter = ref("");
const includeSub = ref(false);
const deptOptions = ref<{ id: number; name: string; depth: number }[]>([]);
const roleSearchQuery = ref("");
const showMoreMenu = ref(false);

// 身份 / 状态 / 部门合并进「筛选」面板：取值与接口参数保持不变
const showFilterPanel = ref(false);
const roleFilterOptions = [
  { value: "", label: "全部" },
  { value: "admin", label: "管理员" },
  { value: "user", label: "普通用户" },
] as const;
// 刻意不提供「待审核」：待审核账号只出现在「待审核」页签，
// 在用户列表里它们的操作按钮都用不了，列出来只会误导
const statusFilterOptions = [
  { value: "", label: "全部" },
  { value: "1", label: "启用" },
  { value: "0", label: "禁用" },
] as const;
const openRowMenuId = ref<number | null>(null);
const openRowMenuUser = ref<any>(null);
const rowMenuStyle = ref<Record<string, string>>({});

const activeFilterCount = computed(
  () =>
    Number(Boolean(roleFilter.value)) +
    Number(Boolean(statusFilter.value)) +
    Number(Boolean(deptFilter.value))
);
const hasActiveFilters = computed(
  () =>
    Boolean(searchQuery.value.trim()) ||
    Boolean(roleFilter.value) ||
    Boolean(statusFilter.value) ||
    Boolean(deptFilter.value)
);
const isSearchEmpty = computed(
  () => hasActiveFilters.value && users.value.length === 0
);

const closeMenus = () => {
  showMoreMenu.value = false;
  showFilterPanel.value = false;
  openRowMenuId.value = null;
  openRowMenuUser.value = null;
};

// 筛选面板与「更多」菜单互斥，避免两个浮层叠在一起
const toggleFilterPanel = () => {
  showFilterPanel.value = !showFilterPanel.value;
  if (showFilterPanel.value) {
    showMoreMenu.value = false;
    openRowMenuId.value = null;
    openRowMenuUser.value = null;
  }
};
const applyFilters = () => {
  page.value = 1;
  fetchUsers();
};
// 点当前已选项不重复请求；面板保持打开，方便连续调身份 + 状态
const setRoleFilter = (value: string) => {
  if (roleFilter.value === value) return;
  roleFilter.value = value;
  applyFilters();
};
const setStatusFilter = (value: string) => {
  if (statusFilter.value === value) return;
  statusFilter.value = value;
  applyFilters();
};
const toggleRowMenu = (user: any, event?: MouseEvent) => {
  showMoreMenu.value = false;
  if (openRowMenuId.value === user.id) {
    openRowMenuId.value = null;
    openRowMenuUser.value = null;
    return;
  }
  const trigger = event?.currentTarget as HTMLElement | undefined;
  if (trigger) {
    const rect = trigger.getBoundingClientRect();
    const menuWidth = 160;
    const extraItems = canEditUser.value && user.two_factor_enabled ? 36 : 0;
    const estimatedHeight = (user.user_name === "admin" ? 88 : 128) + extraItems;
    const gap = 4;
    // 默认贴在按钮下方；仅当下方空间不足时再向上翻
    const spaceBelow = window.innerHeight - rect.bottom - gap;
    const openDown = spaceBelow >= estimatedHeight;
    const top = openDown
      ? rect.bottom + gap
      : Math.max(8, rect.top - estimatedHeight - gap);
    const left = Math.min(
      Math.max(8, rect.right - menuWidth),
      window.innerWidth - menuWidth - 8
    );
    rowMenuStyle.value = {
      top: `${top}px`,
      left: `${left}px`,
    };
  }
  openRowMenuId.value = user.id;
  openRowMenuUser.value = user;
};

// 批量启用 / 禁用：选择态仅对当前页有效，翻页或刷新后清空
const selectedUserIds = ref<number[]>([]);
const showBatchStatusDialog = ref(false);
const batchStatusTarget = ref<0 | 1>(0);
const batchSubmitting = ref(false);
const BATCH_STATUS_MAX_USERS = 200;

const isAllPageSelected = computed(
  () =>
    users.value.length > 0 &&
    users.value.every((u) => selectedUserIds.value.includes(u.id)),
);
const isPagePartiallySelected = computed(
  () =>
    users.value.some((u) => selectedUserIds.value.includes(u.id)) &&
    !isAllPageSelected.value,
);

// Dialogs
const showCreateDialog = ref(false);
const showEditDialog = ref(false);
const showDeleteDialog = ref(false);
const showRegenerateDialog = ref(false);
const showSetPasswordDialog = ref(false);
const showDisable2FADialog = ref(false);
const showViewKeyDialog = ref(false);
const showSsoModal = ref(false);
const showThirdPartyDrawer = ref(false);
const showImportDialog = ref(false);
const showSystemQuotaModal = ref(false);
const userToDelete = ref<any>(null);
const userToRegenerate = ref<any>(null);
const userToSetPassword = ref<any>(null);
const userToDisable2FA = ref<any>(null);
const userToViewKey = ref<any>(null);
const loadingViewKey = ref(false);
const settingPassword = ref(false);
const disabling2FA = ref(false);
const viewedApiKey = ref("");

// SSO Sync State
const ssoUsers = ref<any[]>([]);
const loadingSso = ref(false);
const syncingSso = ref(false);
const ssoSearchQuery = ref("");
const selectedSsoUsernames = ref<string[]>([]);
const showSsoConfirmModal = ref(false);
const ssoSyncRole = ref("user");
const ssoSyncRoleIds = ref<number[]>([]);
const hideSynced = ref(true);

const filteredSsoUsers = computed(() => {
  let list = ssoUsers.value;
  if (hideSynced.value) {
    list = list.filter((u) => !u.is_synced);
  }
  if (!ssoSearchQuery.value) return list;
  const q = ssoSearchQuery.value.toLowerCase();
  return list.filter(
    (u) =>
      u.code.toLowerCase().includes(q) ||
      u.name.toLowerCase().includes(q) ||
      (u.department && u.department.toLowerCase().includes(q))
  );
});

const isAllSsoSelected = computed(() => {
  const syncable = filteredSsoUsers.value.filter((u) => !u.is_synced);
  return (
    syncable.length > 0 &&
    syncable.every((u) => selectedSsoUsernames.value.includes(u.code))
  );
});

const openSsoModal = () => {
  showSsoModal.value = true;
  selectedSsoUsernames.value = [];
  ssoSearchQuery.value = "";
  fetchSsoUsers();
};

const fetchSsoUsers = async () => {
  loadingSso.value = true;
  try {
    const res = await axios.get("/api/portal/management/sso-users");
    ssoUsers.value = res.data.items;
  } catch (e) {
    showToast("获取 SSO 用户列表失败", "error");
  } finally {
    loadingSso.value = false;
  }
};

const toggleSsoSelectAll = () => {
  if (isAllSsoSelected.value) {
    selectedSsoUsernames.value = [];
  } else {
    selectedSsoUsernames.value = filteredSsoUsers.value
      .filter((u) => !u.is_synced)
      .map((u) => u.code);
  }
};

const executeSsoSync = () => {
  if (selectedSsoUsernames.value.length === 0) return;
  ssoSyncRole.value = "user";
  ssoSyncRoleIds.value = [];
  showSsoConfirmModal.value = true;
};

const confirmExecuteSsoSync = async () => {
  syncingSso.value = true;
  try {
    const res = await axios.post("/api/portal/management/sso-sync", {
      usernames: selectedSsoUsernames.value,
      role: ssoSyncRole.value,
      role_ids: ssoSyncRoleIds.value,
    });
    showToast(res.data.message || "同步成功", "success");
    showSsoConfirmModal.value = false;
    showSsoModal.value = false;
    fetchUsers();
  } catch (e) {
    showToast("同步失败", "error");
  } finally {
    syncingSso.value = false;
  }
};

// Form
const formData = ref({
  user_name: "",
  real_name: "",
  email: "",
  role: "user",
  // 部门归属走独立的 PUT /users/{id}/departments（整集保存）；
  // 部门代码 / 组织路径由后端按主部门派生，前端不再直接编辑
  dept_ids: [] as number[],
  primary_dept_id: null as number | null,
  extra_data: "",
  role_ids: [] as number[],
  remark: "",
});

// Extra Data Tabs and Pairs
const extraDataTab = ref<"visual" | "json">("visual");
const extraDataPairs = ref<ExtraDataPair[]>([]);

const addExtraDataPair = () => {
  extraDataPairs.value.push({ key: "", value: "" });
};

const removeExtraDataPair = (index: number) => {
  extraDataPairs.value.splice(index, 1);
};

// Syncing functions
// 值一律按字符串写回：第三方同步写入的雪花 ID 走 Number() 会被改写精度
// （"116573125898872954882" → 116573125898872960000），详见 utils/userExtraData.ts
const syncPairsToExtraData = () => {
  formData.value.extra_data = pairsToExtraDataJson(extraDataPairs.value);
};

const syncExtraDataToPairs = () => {
  const raw = formData.value.extra_data;
  // JSON 手写模式下写坏了：保留可视化面板里已有的键值对，等用户改好再同步，
  // 否则一按标签就会把面板清空，看着像数据丢了。
  if (raw && String(raw).trim() && !isExtraDataJsonObject(raw)) {
    console.warn("extra_data 不是合法的 JSON 对象，暂不同步到可视化面板");
    return;
  }
  extraDataPairs.value = extraDataJsonToPairs(raw);
};

const handleExtraDataTabChange = (tab: "visual" | "json") => {
  if (tab === "json" && extraDataTab.value === "visual") {
    syncPairsToExtraData();
  } else if (tab === "visual" && extraDataTab.value === "json") {
    syncExtraDataToPairs();
  }
  extraDataTab.value = tab;
};
const editingUserId = ref<number | null>(null);
const submitting = ref(false);
const error = ref("");
const createdApiKey = ref("");
const regeneratedApiKey = ref("");

// Permission State
const activeTab = ref<"info" | "permissions" | "quota">("info");
const activePermissionSubTab = ref<"assets" | "ui">("assets");
const resourceTypes = ["agents", "datasets", "metadata", "apis", "models", "forbidden_configs"] as const;
type ResourceTab = (typeof resourceTypes)[number];
type AssignableResourceType = Exclude<ResourceTab, "forbidden_configs">;
const activeResTab = ref<ResourceTab>("agents");

const resourceConfig: Record<
  (typeof resourceTypes)[number],
  { label: string; color: string; icon: string }
> = {
  agents: { label: "智能体", color: "blue", icon: "🤖" },
  datasets: { label: "知识库", color: "green", icon: "📚" },
  metadata: { label: "数据集", color: "orange", icon: "💾" },
  apis: { label: "外部API", color: "purple", icon: "🔗" },
  models: { label: "可用模型", color: "indigo", icon: "🧠" },
  forbidden_configs: { label: "禁用资源", color: "red", icon: "🚫" },
};

const loadingResources = ref(false);
const allResources = ref<{
  agents: any[];
  datasets: any[];
  metadata: any[];
  apis: any[];
  models: any[];
}>({ agents: [], datasets: [], metadata: [], apis: [], models: [] });
const permissionData = ref<{
  agents: string[];
  datasets: string[];
  metadata: string[];
  apis: string[];
  menus: string[];
  elements: string[];
  forbidden_tools: string[];
  forbidden_commands: string[];
  forbidden_skills: string[];
  models: string[];
}>({
  agents: [],
  datasets: [],
  metadata: [],
  apis: [],
  menus: [],
  elements: [],
  forbidden_tools: [],
  forbidden_commands: [],
  forbidden_skills: [],
  models: [],
});

const forbiddenCommandsText = ref("");

/**
 * 角色来源的禁用项（只读）：`permissions`（用户 ∪ 角色）里有、`direct_permissions` 里没有的
 * 部分。它们只用于展示——payload 只由 permissionData（直配缓冲）构建，所以这里没有回写通道。
 * 旧后端不返回 `direct_permissions` 时这里恒为空，全部按直配处理（见 permissionLists.ts）。
 */
const inheritedForbidden = ref<InheritedForbiddenLists>({
  forbidden_tools: [],
  forbidden_commands: [],
  forbidden_skills: [],
});

/**
 * 角色授予的菜单 / 功能点（只读）：并集里有、直配里没有的部分。
 *
 * 这是「编辑用户时看不出角色已给了哪些页面」的修法：后端已同时返回并集与直配，
 * 差集在这里算，树里以灰勾 + 「角色」徽标呈现，永不进入 PUT payload。
 */
const inheritedMenus = ref<string[]>([]);
const inheritedElements = ref<string[]>([]);

/** 汇总条：直配与角色来源不重叠（后者是差集），直接相加即去重后的授予总数。 */
const grantedMenuCount = computed(
  () => permissionData.value.menus.length + inheritedMenus.value.length,
);
const grantedElementCount = computed(
  () => permissionData.value.elements.length + inheritedElements.value.length,
);

/** 只读区展示用：按「工具 / 命令 / 技能」三行渲染，没有内容不占位。 */
const inheritedForbiddenRows = computed(() =>
  [
    {
      key: "forbidden_tools",
      label: "禁用工具",
      items: inheritedForbidden.value.forbidden_tools,
    },
    {
      key: "forbidden_commands",
      label: "禁用命令关键字",
      items: inheritedForbidden.value.forbidden_commands,
    },
    {
      key: "forbidden_skills",
      label: "禁用技能",
      items: inheritedForbidden.value.forbidden_skills,
    },
  ].filter((row) => (row.items || []).length > 0),
);

/**
 * 禁用目录快照（工具 / 技能候选），只用于徽标计数：要判断"哪些选中项属于同一个别名组"
 * 就必须有目录。这里只订阅共享缓存，不额外发请求——目录由 ForbiddenToolCatalog 统一加载，
 * 一页里的两个实例共享同一次请求（见 utils/forbiddenCatalogCache.ts）。
 */
const forbiddenCatalogSnapshot = ref<ForbiddenCatalogSnapshot>({
  candidates: [],
  skillCandidates: [],
});
const syncForbiddenCatalogSnapshot = () => {
  const snapshot = peekForbiddenCatalog();
  if (snapshot) forbiddenCatalogSnapshot.value = snapshot;
};
let unsubscribeForbiddenCatalog: (() => void) | null = null;

// F8：徽标计数需要候选目录（判断哪些选中项属于同一个别名组），目录由 ForbiddenToolCatalog
// 加载，这里只订阅共享缓存；卸载必须退订，否则回调会一直持有本页状态。
onMounted(() => {
  syncForbiddenCatalogSnapshot();
  unsubscribeForbiddenCatalog = subscribeForbiddenCatalog(syncForbiddenCatalogSnapshot);
});
onUnmounted(() => {
  unsubscribeForbiddenCatalog?.();
  unsubscribeForbiddenCatalog = null;
});

/**
 * tab 徽标口径：禁用资源 = 工具条目数 + 命令条目数 + 技能条目数（用户视角，一个别名组算 1），
 * 其余 tab = 已勾选资产数。计数与角色页共用同一个纯函数，避免两页数字对不上。
 */
const resourceTabCount = (type: string) =>
  type === "forbidden_configs"
    ? countForbiddenResourceBadge({
        forbiddenTools: permissionData.value.forbidden_tools,
        toolCandidates: forbiddenCatalogSnapshot.value.candidates,
        forbiddenCommandsText: forbiddenCommandsText.value,
        forbiddenSkills: permissionData.value.forbidden_skills,
        skillCandidates: forbiddenCatalogSnapshot.value.skillCandidates,
      })
    : ((permissionData.value as any)[type]?.length || 0);

/**
 * 打开对话框时冻结的「真实身份」：权限只对"后端返回的就是普通用户"的账号有意义。
 * 不能用可编辑的 formData.role —— 把管理员改成普通用户时它已经是 user，但权限从未拉过，
 * 于是空的 / 上一个用户残留的 permissionData 会被整表 PUT 出去（越权复制 + 清空黑名单）。
 */
const permissionSubjectRole = ref("");
/** 权限是否已成功加载：没加载成功就禁止提交，否则一次网络抖动会把真实配置整体清掉。 */
const permissionsLoaded = ref(false);
/** 权限树来自接口（菜单已入库）；加载失败时禁止提交，避免空树把用户权限清空 */
const permissionTree = ref<PermissionNode[]>([]);

/** 接口节点 → 权限树节点：`permission_code` 是唯一可提交 id，无码节点用 `code`（分组容器） */
const toPermissionNode = (node: any): PermissionNode => ({
  id: node.permission_code || node.code,
  label: node.label,
  children: (node.children || []).map(toPermissionNode),
});

const loadPermissionTree = async () => {
  const response = await axios.get("/api/portal/menus/tree");
  const nodes = response.data?.nodes;
  if (!Array.isArray(nodes) || nodes.length === 0) {
    throw new Error("权限树为空");
  }
  permissionTree.value = nodes.map(toPermissionNode);
};
const permissionsLoadError = ref("");
const canEditPermissions = computed(
  () => showEditDialog.value && permissionSubjectRole.value === "user",
);

/** 清空权限编辑缓冲：打开 / 关闭对话框、以及身份切换时都要调用，杜绝跨用户残留。 */
const resetPermissionBuffers = () => {
  permissionData.value = emptyPermissionLists();
  inheritedForbidden.value = {
    forbidden_tools: [],
    forbidden_commands: [],
    forbidden_skills: [],
  };
  inheritedMenus.value = [];
  inheritedElements.value = [];
  forbiddenCommandsText.value = "";
  permissionsLoaded.value = false;
  permissionsLoadError.value = "";
  // 智能体级「功能限定」的计数与弹框目标也属于**这个用户**：不复位会让上一个用户的
  // 角标残留（获取逻辑对已有键会跳过请求，于是直接显示别人的数字）。
  agentRestrictionCounts.value = {};
  agentRestrictionTarget.value = null;
};

// F3：身份一变就清空缓冲。切到非 user 时尤其重要——保存判定虽然已经基于真实身份，
// 但残留数据留在内存里迟早会被某条新路径写出去。
watch(
  () => formData.value.role,
  (nextRole) => {
    if (nextRole === permissionSubjectRole.value) return;
    resetPermissionBuffers();
    // 权限 tab 会随身份消失：停在权限页的用户要被送回基本信息，
    // 否则两个 tab 内容区的 v-show 同时为 false，弹窗看起来是一片空白。
    if (activeTab.value === "permissions") activeTab.value = "info";
  },
);

/**
 * 智能体级「功能限定」：目标是「该用户 × 单个智能体 × 资源」，与全局禁用取并集。
 *
 * 角标要显示每个**已勾选**智能体上的已禁条目数，而接口按智能体提供，所以只在
 * 「智能体」tab 可见时按需加载，并走 agentRestrictionCache 去重（同一次打开里
 * 同一个智能体只请求一次；保存后由弹框回填，不必再 GET）。
 */
const agentRestrictionTarget = ref<{ id: string; label: string } | null>(null);
const showAgentRestrictionDialog = ref(false);
const agentRestrictionCounts = ref<Record<string, number>>({});
const agentRestrictionTargetId = computed(() => agentRestrictionTarget.value?.id || "");
const agentRestrictionTargetLabel = computed(
  () => agentRestrictionTarget.value?.label || "",
);

const agentRestrictionCacheKeyFor = (agentId: string) =>
  agentRestrictionCacheKey("user", editingUserId.value ?? "", agentId);

const applyAgentRestrictionCount = (agentId: string, lists: AgentRestrictionLists) => {
  agentRestrictionCounts.value = {
    ...agentRestrictionCounts.value,
    [String(agentId)]: countAgentRestrictionBadge(lists),
  };
};

/** 卡片角标：只反映已知的限制数（目录未加载时按去重计数，口径与弹框一致）。 */
const agentRestrictionCount = (agentId: string) =>
  agentRestrictionCounts.value[String(agentId)] || 0;

const openAgentRestriction = (res: any) => {
  agentRestrictionTarget.value = {
    id: String(res?.id ?? ""),
    label: String(res?.platform_name || res?.display_name || res?.name || res?.id || ""),
  };
  showAgentRestrictionDialog.value = true;
};

const onAgentRestrictionSaved = (lists: AgentRestrictionLists) => {
  const agentId = agentRestrictionTarget.value?.id;
  if (agentId) applyAgentRestrictionCount(agentId, lists);
};

/**
 * 已勾选智能体的已禁数量：**一次批量接口**取齐，而不是逐个智能体 GET。
 *
 * 走批量不只是省请求：回填时顺带写进限制缓存，管理员随后打开弹框即命中，不再多打一次。
 * 整批失败时所有卡片角标显示 0，入口仍可用（打开弹框会给出明确报错）。
 */
const loadAgentRestrictionCounts = async () => {
  const userId = editingUserId.value;
  if (!canEditPermissions.value || !userId) return;
  const pending = (permissionData.value.agents || [])
    .map((id: any) => String(id))
    .filter((id: string) => !(id in agentRestrictionCounts.value));
  if (!pending.length) return;
  try {
    const response = await axios.get(
      agentRestrictionCountsPath("user", userId, pending),
    );
    const restrictions = response?.data?.restrictions || {};
    for (const agentId of pending) {
      const lists = writeAgentRestriction(
        agentRestrictionCacheKeyFor(agentId),
        restrictions[agentId] || { forbidden_tools: [], forbidden_skills: [] },
      );
      applyAgentRestrictionCount(agentId, lists);
    }
  } catch (error) {
    console.warn("加载智能体功能限定计数失败", error);
  }
};

// Tree Logic
/**
 * 树的勾选写入：勾菜单只授予入口（解耦，不再级联功能点），功能点直接翻转。
 * 菜单行的「全选 / 清空」走共享级联函数，并跳过角色已授予的子项。
 * 只写直配缓冲；角色来源项没有任何写入通道。
 */
const toggleTreeItem = (itemId: string) => {
  if (itemId.startsWith("menu:")) {
    const result = applyMenuEntryToggle(
      permissionData.value.menus,
      permissionData.value.elements,
      itemId,
      getMenuDescendantIds(permissionTree.value, itemId),
      inheritedMenus.value,
      inheritedElements.value,
    );
    permissionData.value.menus = result.menus;
    permissionData.value.elements = result.elements;
    return;
  }

  // 双重保险：树里已拦截，这里再兜一层，避免别的入口把继承项写成直配。
  if (
    !permissionData.value.elements.includes(itemId) &&
    inheritedElements.value.includes(itemId)
  ) {
    showToast("该权限来自所属角色，需到角色管理调整", "warning");
    return;
  }

  const idx = permissionData.value.elements.indexOf(itemId);
  if (idx > -1) permissionData.value.elements.splice(idx, 1);
  else permissionData.value.elements.push(itemId);
};

/** 菜单行的「全选 / 清空」：显式级联它的全部后代，不改变入口以外的其他菜单。 */
const toggleMenuDescendants = (menuId: string, mode: MenuCascadeMode) => {
  const result = applyMenuCascadeToggle(
    permissionData.value.menus,
    permissionData.value.elements,
    menuId,
    getMenuDescendantIds(permissionTree.value, menuId),
    mode,
    inheritedMenus.value,
    inheritedElements.value,
  );
  permissionData.value.menus = result.menus;
  permissionData.value.elements = result.elements;
};

/** 树里点到「仅角色来源」的节点：只提示，不修改——直配区不该出现继承项。 */
const onInheritedBlocked = (itemId: string) => {
  void itemId;
  showToast("该权限来自所属角色，需到角色管理调整", "warning");
};

// Computed
const activeAssignableResourceType = computed<AssignableResourceType | null>(() =>
  activeResTab.value === "forbidden_configs" ? null : activeResTab.value,
);

/**
 * 「可用模型」候选：只列 llm / multimodal。
 *
 * 已勾选但已停用的模型保留在列表里（标注「已停用」），否则保存时会静默丢掉白名单配置。
 * 数据源取全量模型（含停用），因此请求时带 include_inactive。
 */
const availableModels = computed(() => {
  const selected = permissionData.value.models || [];
  return (allResources.value.models || [])
    .filter((m: any) => m?.type === "llm" || m?.type === "multimodal")
    .filter((m: any) => m?.is_active !== false || selected.includes(m?.model_id))
    .map((m: any) => ({
      id: m.model_id,
      name: m.name || m.model_id,
      description: m.model_id,
      is_active: m.is_active !== false,
    }));
});

// 「可用模型」页搜索：模型太少时不显示搜索框（阈值与输入框的模型切换器一致）
const modelSearchQuery = ref("");
const showModelSearch = computed(() =>
  shouldShowModelSearch(availableModels.value.length),
);

// 切换资源 Tab 时清空搜索词，避免把上一页的过滤条件带到新列表
watch(activeResTab, () => {
  modelSearchQuery.value = "";
});

// 智能体 tab 可见时按需补齐各已勾选智能体的已禁数量角标（勾选变化后也要对齐）
watch(
  [activeResTab, () => permissionData.value.agents, canEditPermissions],
  () => {
    if (activeResTab.value === "agents" && canEditPermissions.value) {
      void loadAgentRestrictionCounts();
    }
  },
  { deep: true },
);

/** models 页的分组：已选一段、其余一段（其它资源页不使用） */
const modelResourceGroups = computed(() =>
  groupPermissionModels(
    availableModels.value,
    modelSearchQuery.value,
    permissionData.value.models,
  ),
);

const currentResources = computed(() => {
  const type = activeAssignableResourceType.value;
  if (!type) return [];
  if (type === "models") {
    // 两段拍平后交给下游（全选 / 计数 / 底部「已勾选 N」），行为与平铺列表完全一致
    const { selected, others } = modelResourceGroups.value;
    return [...selected, ...others];
  }
  return allResources.value[type];
});

/**
 * 列表渲染条目：models 页插入两段小标题，其它资源页保持平铺。
 *
 * item 就是模型/资源对象本身（外加 entryType / entryKey 两个渲染用字段），
 * 这样卡片模板里的 res.* 绑定与既有契约完全不变。
 */
const modelResourceEntries = computed<any[]>(() => {
  if (activeResTab.value !== "models") {
    // 走 visibleResources：失联知识库按开关隐藏，已授权的除外
    return visibleResources.value.map((res: any) => ({
      ...res,
      entryType: "item",
      entryKey: res.id,
    }));
  }
  const { selected, others } = modelResourceGroups.value;
  const entries: any[] = [];
  if (selected.length) {
    entries.push({
      entryType: "header",
      entryKey: "header-selected",
      label: `已选 ${selected.length} 个`,
    });
    selected.forEach((res: any) =>
      entries.push({ ...res, entryType: "item", entryKey: res.id }),
    );
  }
  if (others.length) {
    entries.push({
      entryType: "header",
      entryKey: "header-others",
      label: `其余 ${others.length} 个`,
    });
    others.forEach((res: any) =>
      entries.push({ ...res, entryType: "item", entryKey: res.id }),
    );
  }
  return entries;
});

/** 空列表文案：区分「搜索没命中」与「本来就没有可用资源」。 */
const emptyResourceText = computed(() => {
  if (activeResTab.value === "models" && modelSearchQuery.value.trim()) {
    return "没有匹配的模型";
  }
  // 全部失联且未勾选「显示失联」时，要说清楚是这个原因，否则看起来像"没有资源可分配"
  if (activeResTab.value === "datasets" && missingDatasetCount.value) {
    return `已隐藏 ${missingDatasetCount.value} 个失联知识库`;
  }
  return "暂无可用资源";
});

const isMissingKnowledgeBase = (res: any) => {
  if (activeResTab.value !== "datasets") return false;
  return Boolean(res?.is_missing_in_ragflow || res?.status === "missing");
};

/** 失联知识库开关：默认隐藏，但「已授权」的失联库始终保留（见 visibleResources） */
const showMissingDatasets = ref(false)

/** 当前 tab 下失联知识库的数量：0 时不显示开关，避免无谓的干扰 */
const missingDatasetCount = computed(() =>
  activeResTab.value === "datasets"
    ? currentResources.value.filter((res: any) => isMissingKnowledgeBase(res)).length
    : 0,
)

/** 该资源是否已被授权：已授权的失联库必须保留，否则用户看不到自己配过什么 */
const isSelectedDatasetResource = (res: any) =>
  Boolean(((permissionData.value as any)[activeResTab.value] as string[] | undefined)?.includes(res.id))

/**
 * 实际渲染的资源：隐藏失联知识库。
 *
 * 已授权的失联库不会被隐藏 —— 用户想看的正是「我之前配过的那个库现在失联了」，
 * 藏起来会导致保存时把它静默剔除。
 */
const visibleResources = computed(() => {
  if (activeResTab.value !== "datasets" || showMissingDatasets.value) {
    return currentResources.value
  }
  return currentResources.value.filter(
    (res: any) => !isMissingKnowledgeBase(res) || isSelectedDatasetResource(res),
  )
})

/** 可见范围徽标：与知识库页三档口径保持一致 */
const KNOWLEDGE_SCOPE_BADGE: Record<string, { label: string; cls: string }> = {
  public: { label: "公共级", cls: "bg-blue-50 text-blue-600" },
  team: { label: "部门级", cls: "bg-violet-50 text-violet-600" },
  private: { label: "个人私有", cls: "bg-amber-50 text-amber-700" },
};

const knowledgeScopeOf = (res: any): "public" | "team" | "private" => {
  const token = String(res?.visibility || "").trim().toLowerCase();
  return token === "team" || token === "private" ? token : "public";
};

/** 兜底项：字段异常时也不让徽标消失 */
const FALLBACK_SCOPE_BADGE = { label: "公共级", cls: "bg-blue-50 text-blue-600" };

const knowledgeScopeBadge = (res: any) =>
  KNOWLEDGE_SCOPE_BADGE[knowledgeScopeOf(res)] ?? FALLBACK_SCOPE_BADGE;

/**
 * 个人私有知识库不参与授权：判定时会被直接跳过，勾选它也拿不到访问权。
 * 这里禁用勾选并标注原因，避免管理员做一次「配了但没生效」的空操作。
 */
const isPrivateKnowledgeBase = (res: any) =>
  activeResTab.value === "datasets" && knowledgeScopeOf(res) === "private";

/** 「可用模型」页里的停用项：保留勾选态并打标，不做自动清除。 */
const isDisabledModel = (res: any) =>
  activeResTab.value === "models" && res?.is_active === false;

// 停用标注文案：成员列表里也有同名徽章，这里集中定义，避免两处字面量互相干扰
const DISABLED_MODEL_LABEL = "已停用";

/**
 * 请求方法徽章配色。
 *
 * 仅「外部API」资源带 method 字段，其余 tab 不会渲染该徽章。
 */
const methodBadgeClass = (method: string) => {
  switch (String(method || "").toUpperCase()) {
    case "GET":
      return "bg-emerald-50 text-emerald-600";
    case "POST":
      return "bg-blue-50 text-blue-600";
    case "PUT":
    case "PATCH":
      return "bg-amber-50 text-amber-600";
    case "DELETE":
      return "bg-rose-50 text-rose-600";
    default:
      return "bg-gray-100 text-gray-500";
  }
};

const selectableResources = computed(() =>
  // 私有库不能参与「全选」：它在卡片上是 disabled 的，一旦被全选写进
  // permissionData 就会出现「置灰却被勾上」的矛盾状态，且保存时被后端静默剔除。
  currentResources.value.filter(
    (r: any) => !isMissingKnowledgeBase(r) && !isPrivateKnowledgeBase(r),
  ),
);

const isAllSelected = computed(
  () => {
    const type = activeAssignableResourceType.value;
    return Boolean(
      type &&
      selectableResources.value.length > 0 &&
      selectableResources.value.every((r: any) =>
        permissionData.value[type].includes(r.id),
      ),
    );
  },
);

// 角色按 name 或 code 过滤：老库的 default 角色 name 是「默认用户」之类，
// 只匹配 name 会让搜 "de" 的结果为空（角色管理页能搜到是因为后端两列都查）。
const filteredBusinessRoles = computed(() =>
  filterBusinessRoles(businessRoles.value, roleSearchQuery.value),
);

// Actions
/**
 * 部门筛选选项：扁平成「缩进 + 名称」交给原生 select。
 * 没有部门读权限时静默降级为不显示筛选器——用户管理本身不该因此报错。
 */
const loadDeptOptions = async () => {
  try {
    const response = await axios.get("/api/portal/departments/tree", {
      params: { include_disabled: false },
    });
    const flat: { id: number; name: string; depth: number }[] = [];
    const walk = (nodes: any[], depth: number) => {
      for (const node of nodes || []) {
        flat.push({ id: Number(node.id), name: node.name, depth });
        if (node.children?.length) walk(node.children, depth + 1);
      }
    };
    walk(response.data?.items || [], 0);
    deptOptions.value = flat;
  } catch {
    deptOptions.value = [];
  }
};

const fetchUsers = async () => {
  loading.value = true;
  try {
    const params: any = { page: page.value, size: size.value };
    if (searchQuery.value) params.search = searchQuery.value;
    if (roleFilter.value) params.role = roleFilter.value;
    if (statusFilter.value) params.status = statusFilter.value;
    if (deptFilter.value) {
      params.dept_id = Number(deptFilter.value);
      if (includeSub.value) params.include_sub = true;
    }
    // 「用户列表」不显示待审核账号：它们的审核/编辑等操作在列表里本来就用不了，
    // 只应出现在「待审核」页签。待审核视图自身按 status=2 拉取，不能再排除，
    // 所以这里连带判断 statusFilter，避免两个条件互斥导致列表空掉。
    if (activeView.value === "list" && statusFilter.value !== "2") {
      params.exclude_status = 2;
    }
    const response = await axios.get("/api/portal/management/users", {
      params,
    });
    users.value = response.data.items;
    total.value = response.data.total;
    totalPages.value = Math.ceil(total.value / size.value);
    // 列表数据变化后清空选择，避免"看到 3 个却操作了 50 个"
    clearSelection();
  } catch (e: any) {
    showToast("获取列表失败", "error");
  } finally {
    loading.value = false;
  }
};

// --- 微信绑定视图（spec §7.6）---
// 一行 = 一条绑定记录：同一账号将来可能挂多个微信身份，不能按用户聚合。
const bindings = ref<any[]>([]);
const bindingPage = ref(1);
const bindingSize = ref(15);
const bindingTotal = ref(0);
const bindingTotalPages = ref(0);
const bindingSearch = ref("");
const bindingLoading = ref(false);

const fetchBindings = async () => {
  bindingLoading.value = true;
  try {
    const params: any = { page: bindingPage.value, size: bindingSize.value };
    if (bindingSearch.value) params.search = bindingSearch.value;
    const response = await axios.get("/api/portal/management/wechat-bindings", { params });
    bindings.value = response.data.items || [];
    bindingTotal.value = response.data.total || 0;
    bindingTotalPages.value = Math.ceil(bindingTotal.value / bindingSize.value);
  } catch (e: any) {
    showToast("获取微信绑定列表失败", "error");
  } finally {
    bindingLoading.value = false;
  }
};

/** 取完整 openid 并复制：明文单独走接口，避免整页明文下发 */
const copyBindingIdentity = async (row: any) => {
  try {
    const response = await axios.get(
      `/api/portal/management/wechat-bindings/${row.id}/identity`
    );
    const ok = await copyToClipboard(response.data?.openid || "");
    showToast(ok ? "已复制完整 openid" : "复制失败", ok ? "success" : "error");
  } catch (e: any) {
    showToast("读取完整 openid 失败", "error");
  }
};

const businessRolesTotal = ref(0);
/** 只认最后一次请求的响应：快速输入时旧响应可能后到，会把结果覆盖回上一个关键词 */
let roleFetchSeq = 0;

/**
 * 拉业务角色，关键词交给后端 `search`。
 *
 * 为什么必须走后端：列表按 created_at 倒序只回一页，而 `default` 这类默认角色创建得
 * 最早、必然排在最后一页——本地再怎么过滤也搜不到它（角色管理页能搜到，是因为它把
 * 关键词传给了后端，在后端做全表匹配）。这里与角色管理页对齐。
 */
const fetchBusinessRoles = async (keyword = "") => {
  const seq = ++roleFetchSeq;
  try {
    const response = await axios.get("/api/portal/roles", {
      params: { page: 1, size: 100, search: keyword.trim() || undefined },
    });
    if (seq !== roleFetchSeq) return; // 过期响应直接丢弃
    businessRoles.value = response.data.items || [];
    businessRolesTotal.value = response.data.total ?? businessRoles.value.length;
    roleListError.value = false;
    roleListErrorStatus.value = null;
  } catch (e: any) {
    if (seq !== roleFetchSeq) return;
    console.error(e);
    roleListError.value = true;
    // 带上状态码：403 是权限问题、其他是服务端问题，用户能据此自助判断
    roleListErrorStatus.value = e?.response?.status ?? null;
    showToast(
      `获取角色列表失败${roleListErrorStatus.value ? `（HTTP ${roleListErrorStatus.value}）` : ""}，请确认当前账号有角色管理查看权限或编辑用户权限`,
      "error",
    );
  }
};

/** 输入即搜（300ms 防抖）；只在弹窗真的打开时请求，关闭后不空打接口。 */
let roleSearchTimer: ReturnType<typeof setTimeout> | null = null;
watch(roleSearchQuery, (value) => {
  if (!showCreateDialog.value && !showEditDialog.value) return;
  if (roleSearchTimer) clearTimeout(roleSearchTimer);
  roleSearchTimer = setTimeout(() => {
    void fetchBusinessRoles(value);
  }, 300);
});

// 已选角色数按表单里的 id 算：老角色常不在当前一页里，界面要说明它没丢
const selectedRoleSummary = computed(() =>
  summarizeSelectedRoles(formData.value.role_ids, businessRoles.value),
);

/**
 * 「确保角色列表已加载」的唯一入口。
 *
 * onMounted 那次请求可能因为权限/网络失败，之后弹窗必须能重试；新建与编辑走同一个
 * 入口，避免两条路径各自判断、各自漂移。
 */
const ensureBusinessRoles = async () => {
  if (businessRoles.value.length > 0) return;
  await fetchBusinessRoles();
};

const fetchResources = async () => {
  if (loadingResources.value) return;
  loadingResources.value = true;
  try {
    // 1. Fetch Local Core Resources (High Priority)
    try {
      const r2 = await axios.get("/api/portal/management/resources/available");
      const available = r2.data;
      if (available) {
        allResources.value.agents = available.agents || [];
        allResources.value.metadata = (available.metadata || []).map(
          (m: any) => ({ ...m, id: String(m.id) }),
        );
        allResources.value.apis = available.apis || [];
      }
    } catch (localErr) {
      console.error("Failed to fetch local resources", localErr);
      showToast("加载核心资源列表失败", "error");
    }

    // 1.5 可用模型白名单候选：需含停用模型，才能展示「已勾选但已停用」的项
    try {
      const r3 = await modelApi.list(undefined, true);
      allResources.value.models = r3.data || [];
    } catch (modelErr) {
      console.warn("Failed to fetch models for chat whitelist", modelErr);
    }

    // 2. Fetch RagFlow Datasets (Optional/External)
    try {
      // page_size 必须远大于 100：RAGFlow 侧还有大量 meta-* 元数据数据集，
      // 传 100 只会拿到第一页（往往被 meta-* 占满），过滤后为空 —— 于是
      // merge 把所有知识库都判成「已失联」，分配权限时一个都选不到。
      // >100 时后端会自动翻页聚合，这里直接要全量。
      const r1 = await axios.get("/api/portal/ragflow/datasets", {
        params: { page_size: 1000 },
      });
      const datasets = r1.data.data || r1.data || [];
      allResources.value.datasets = datasets;
      // 失联知识库**不**从勾选里剔除：它很可能正是用户之前授权过的库，
      // 剔掉会让卡片「看得见但没勾选」，一保存就把授权静默删掉。
      const privateIds = collectPrivateDatasetIds();
      if (privateIds.size > 0) {
        permissionData.value.datasets = (
          permissionData.value.datasets || []
        ).filter((id: string) => !privateIds.has(id));
      }
    } catch (ragErr) {
      console.warn(
        "RagFlow service unreachable, skipping dataset permissions",
        ragErr,
      );
      // We don't show an error toast here to avoid annoying the user if they don't care about RagFlow right now
    }
  } catch (e) {
    console.error("General Fetch Resources Error", e);
  } finally {
    loadingResources.value = false;
  }
};

/** 个人私有知识库：授权对它无效，回显与保存时都剔除，避免留下永不生效的记录。 */
const collectPrivateDatasetIds = (): Set<string> =>
  new Set(
    (allResources.value.datasets || [])
      .filter((d: any) => knowledgeScopeOf(d) === "private")
      .map((d: any) => String(d.id)),
  );

const fetchUserPermissions = async (userId: number) => {
  permissionsLoaded.value = false;
  permissionsLoadError.value = "";
  try {
    const [response] = await Promise.all([
      axios.get(`/api/portal/management/users/${userId}/permissions`),
      // 树与授权并行拉取：树是勾选的必需上下文，失败就走统一的错误分支并禁止提交
      loadPermissionTree(),
    ]);
    // 回显取「直配」，并集（permissions）只用来算角色来源项。
    // 直接拿并集回显再整份 PUT，会把角色级禁用固化成用户级记录：角色页之后取消禁用，
    // 该用户仍被禁；反方向在用户页取消角色级禁用也无效，却提示成功。
    const state = resolveUserPermissionEditState(response.data);
    // 只剔除私有库（授权对它无效）；失联库保留勾选状态，
    // 让用户一眼看到「这个库我之前授权过，现在失联了」
    const privateIds = collectPrivateDatasetIds();
    permissionData.value = {
      ...state.direct,
      datasets: state.direct.datasets.filter((id: string) => !privateIds.has(id)),
    };
    inheritedForbidden.value = state.inherited;
    inheritedMenus.value = state.inheritedMenus;
    inheritedElements.value = state.inheritedElements;
    forbiddenCommandsText.value = (state.direct.forbidden_commands || []).join(", ");
    permissionsLoaded.value = true;
  } catch (e: any) {
    // 这里原先连 toast 都没有，只把 permissionData 清空——用户看到空配置，
    // 一保存就把真实配置清了。现在明确提示，并由 permissionsLoaded 挡住提交。
    resetPermissionBuffers();
    permissionsLoadError.value = extractApiErrorDetail(
      e?.response?.data,
      "权限配置加载失败，请关闭后重新打开重试",
    );
    showToast(permissionsLoadError.value, "error");
  }
};

const saveUser = async () => {
  if (!formData.value.user_name) {
    showToast("请输入用户名", "warning");
    return;
  }
  // Ensure extra_data is synced from pairs before saving
  if (extraDataTab.value === "visual") {
    syncPairsToExtraData();
  }
  submitting.value = true;
  try {
    if (showEditDialog.value && editingUserId.value) {
      const updatePayload = {
        real_name: formData.value.real_name,
        // 后端以「字段是否出现在请求里」判定是否修改邮箱：null / 缺省 = 不修改，
        // 空串 = 清空。所以这里必须始终带上 email，且清空时传空串，
        // 否则管理员永远删不掉一个填错的邮箱。
        email: formData.value.email || "",
        role: formData.value.role,
        extra_data: formData.value.extra_data,
        role_ids: formData.value.role_ids,
        remark: formData.value.remark,
      };
      // F4：先权限后资料。原先资料先提交、权限后提交，权限失败时用户看到「失败」，
      // 但资料其实已经改了——提示与最终状态互相矛盾。
      //
      // 提交权限要同时满足两件事：打开时后端角色就是 user（canEditPermissions，
      // 否则权限从没拉过，提交出去的是空表），并且这次保存之后仍然是 user——
      // 改成管理员时权限没有意义，而且身份切换已经按 F3 清空了缓冲。
      const submitsPermissions =
        canEditPermissions.value && formData.value.role === "user";
      if (submitsPermissions) {
        // F7：权限没成功加载就不能提交。空缓冲写回去等于把用户真实配置整体清掉，
        // 而这次操作的本意可能只是改个备注。
        if (!permissionsLoaded.value) {
          showToast(
            permissionsLoadError.value ||
              "权限配置尚未成功加载，已取消保存以免覆盖现有配置，请关闭后重新打开重试",
            "warning",
          );
          return;
        }
        permissionData.value.forbidden_commands = parseForbiddenCommands(
          forbiddenCommandsText.value,
        );
        // payload 只由直配缓冲构建：角色来源的禁用项没有任何进 payload 的通道。
        const permissionPayload = buildUserPermissionPayload(
          permissionData.value,
          {
            // 私有库授权不生效：别再把它写回数据库。
            // 失联库的授权必须原样提交，否则一次保存就把它丢了。
            excludedDatasetIds: Array.from(collectPrivateDatasetIds()),
          },
        );
        try {
          await axios.put(
            `/api/portal/management/users/${editingUserId.value}/permissions`,
            permissionPayload,
          );
        } catch (permError: any) {
          // 权限没保存成功就不动资料，避免「提示失败但资料已改」。
          const reason = extractApiErrorDetail(
            permError?.response?.data,
            "操作失败",
          );
          error.value = reason;
          showToast(`权限保存失败：${reason}`, "error");
          return;
        }
      }

      await axios.put(
        `/api/portal/management/users/${editingUserId.value}`,
        updatePayload,
      );

      // Sync LocalStorage if updating CURRENT USER
      try {
        const userInfoStr = localStorage.getItem('user_info');
        if (userInfoStr) {
          const userInfo = JSON.parse(userInfoStr);
          // Compare ID (support both id and user_id fields)
          const currentId = userInfo.user_id || userInfo.id;
          if (String(currentId) === String(editingUserId.value)) {
            // Update the local session data
            const updatedUserInfo = {
              ...userInfo,
              ...updatePayload
            };
            persistUserInfo(updatedUserInfo);
            console.log('User Session Sync Success:', updatedUserInfo);
          } else {
            console.log('Not current user, skipping sync. Current:', currentId, 'Edited:', editingUserId.value);
          }
        }
      } catch (syncErr) {
        console.warn('LocalStorage Sync Error:', syncErr);
      }
      // 部门归属单独提交（整集语义）。失败不回滚资料：先把"已更新"如实告诉管理员，
      // 再提示部门没存上，避免出现"提示失败但资料其实改了"的矛盾。
      try {
        await axios.put(
          `/api/portal/management/users/${editingUserId.value}/departments`,
          {
            dept_ids: formData.value.dept_ids,
            primary_dept_id: formData.value.primary_dept_id,
          },
        );
      } catch (deptErr: any) {
        showToast(
          extractApiErrorDetail(
            deptErr?.response?.data,
            "资料已更新，但部门归属保存失败，请重新打开编辑再试",
          ),
          "warning",
        );
        closeDialogs();
        fetchUsers();
        return;
      }
      showToast("更新成功", "success");
      closeDialogs();
      fetchUsers();
    } else {
      const res = await axios.post(
        "/api/portal/management/users",
        formData.value,
      );
      // 新建后补写部门归属：后端创建接口不接收部门集合，避免一个请求里混两种语义
      const createdId = res.data?.id;
      if (createdId && formData.value.dept_ids.length) {
        try {
          await axios.put(
            `/api/portal/management/users/${createdId}/departments`,
            {
              dept_ids: formData.value.dept_ids,
              primary_dept_id: formData.value.primary_dept_id,
            },
          );
        } catch (deptErr: any) {
          showToast(
            extractApiErrorDetail(
              deptErr?.response?.data,
              "用户已创建，但部门归属保存失败，请编辑该用户重新设置",
            ),
            "warning",
          );
        }
      }
      createdApiKey.value = res.data.api_key;
      fetchUsers();

      // 自动关闭“创建用户”主弹窗
      closeDialogs();

      // 自动拉起独立的查看 API Key 弹窗以供复制
      userToViewKey.value = {
        id: res.data.id,
        user_name: res.data.user_name,
        real_name: res.data.real_name
      };
      viewedApiKey.value = res.data.api_key;
      loadingViewKey.value = false;
      showViewKeyDialog.value = true;

      showToast("创建用户成功", "success");
    }
  } catch (e: any) {
    // `error` 没有对应的模板出口，只赋值等于静默失败——管理员看不到
    // 「该邮箱已被其他账号使用」这类 400 detail，只会以为没反应。
    // 422 的 detail 是数组，必须经提取函数变成人话，否则 toast 上显示 `[object Object]`。
    error.value = extractApiErrorDetail(e?.response?.data, "操作失败");
    showToast(error.value, "error");
  } finally {
    submitting.value = false;
  }
};

const toggleStatus = async (user: any) => {
  try {
    const newStatus = user.status === 1 ? 0 : 1;
    await axios.patch(`/api/portal/management/users/${user.id}/status`, {
      status: newStatus,
    });
    showToast("状态更新成功", "success");
    fetchUsers();
  } catch (e) {
    showToast("更新失败", "error");
  }
};

const clearSelection = () => {
  selectedUserIds.value = [];
};

const toggleSelectUser = (userId: number) => {
  if (selectedUserIds.value.includes(userId)) {
    selectedUserIds.value = selectedUserIds.value.filter((id) => id !== userId);
    return;
  }
  if (selectedUserIds.value.length >= BATCH_STATUS_MAX_USERS) {
    showToast(`单次最多操作 ${BATCH_STATUS_MAX_USERS} 个用户，请分批处理`, "warning");
    return;
  }
  selectedUserIds.value = [...selectedUserIds.value, userId];
};

const toggleSelectAllPage = () => {
  if (isAllPageSelected.value) {
    clearSelection();
    return;
  }
  if (users.value.length > BATCH_STATUS_MAX_USERS) {
    showToast(`单次最多操作 ${BATCH_STATUS_MAX_USERS} 个用户，请分批处理`, "warning");
    return;
  }
  selectedUserIds.value = users.value.map((u) => u.id);
};

const openBatchStatusDialog = (targetStatus: 0 | 1) => {
  if (selectedUserIds.value.length === 0) return;
  batchStatusTarget.value = targetStatus;
  showBatchStatusDialog.value = true;
};

const closeBatchStatusDialog = () => {
  if (batchSubmitting.value) return;
  showBatchStatusDialog.value = false;
};

const submitBatchStatus = async () => {
  if (selectedUserIds.value.length === 0) {
    showBatchStatusDialog.value = false;
    return;
  }
  const targetStatus = batchStatusTarget.value;
  const ids = [...selectedUserIds.value];
  batchSubmitting.value = true;
  try {
    const res = await axios.patch("/api/portal/management/users/batch-status", {
      user_ids: ids,
      status: targetStatus,
    });
    const data = res.data || {};
    const updated = Number(data.updated ?? 0);
    const messages = [
      `已${targetStatus === 0 ? "禁用" : "启用"} ${updated} 个用户`,
    ];
    if (data.skipped_self) messages.push("已跳过当前登录账号");
    if (data.not_found) messages.push(`${data.not_found} 个用户不存在`);
    showToast(messages.join("，"), updated > 0 ? "success" : "warning");
    showBatchStatusDialog.value = false;
    clearSelection();
    await fetchUsers();
  } catch (e: any) {
    showToast(e.response?.data?.detail || "批量操作失败", "error");
  } finally {
    batchSubmitting.value = false;
  }
};

const deleteUser = async () => {
  if (!userToDelete.value) return;
  try {
    await axios.delete(`/api/portal/management/users/${userToDelete.value.id}`);
    showToast("删除成功", "success");
    showDeleteDialog.value = false;
    fetchUsers();
  } catch (e) {
    showToast("删除失败", "error");
  }
};

const viewApiKey = async (user: any) => {
  userToViewKey.value = user;
  viewedApiKey.value = "";
  showViewKeyDialog.value = true;
  loadingViewKey.value = true;
  try {
    const res = await axios.get(`/api/portal/management/api-key/${user.id}`);
    viewedApiKey.value = res.data.api_key;
  } catch (e) {
    showToast("获取失败", "error");
  } finally {
    loadingViewKey.value = false;
  }
};

const regenerateApiKey = (user: any) => {
  userToRegenerate.value = user;
  regeneratedApiKey.value = "";
  showRegenerateDialog.value = true;
};
const executeRegenerateApiKey = async () => {
  try {
    const res = await axios.post(
      `/api/portal/management/users/${userToRegenerate.value.id}/reset-key`,
    );
    regeneratedApiKey.value = res.data.api_key;
    fetchUsers();
  } catch (e) {
    showToast("重置失败", "error");
  }
};

const openSetPasswordDialog = (user: any) => {
  userToSetPassword.value = user;
  showSetPasswordDialog.value = true;
};
const closeSetPasswordDialog = () => {
  showSetPasswordDialog.value = false;
  userToSetPassword.value = null;
  settingPassword.value = false;
};
const executeSetPassword = async (password: string) => {
  if (!userToSetPassword.value?.id) {
    showToast("未选择用户，请重新打开设置密码", "warning");
    return;
  }
  settingPassword.value = true;
  try {
    await axios.post(
      `/api/portal/management/users/${userToSetPassword.value.id}/set-password`,
      { password },
    );
    showToast("密码设置成功", "success");
    closeSetPasswordDialog();
  } catch (e: any) {
    showToast(e.response?.data?.detail || "密码设置失败", "error");
  } finally {
    settingPassword.value = false;
  }
};

const openDisable2FADialog = (user: any) => {
  userToDisable2FA.value = user;
  showDisable2FADialog.value = true;
};
const closeDisable2FADialog = () => {
  showDisable2FADialog.value = false;
  userToDisable2FA.value = null;
  disabling2FA.value = false;
};
const executeDisable2FA = async () => {
  if (!userToDisable2FA.value?.id) return;
  disabling2FA.value = true;
  try {
    const res = await axios.post(`/api/portal/management/users/${userToDisable2FA.value.id}/disable-2fa`);
    if (res.data?.status === "success") {
      showToast(res.data?.message || "两步验证已成功关闭", "success");
      closeDisable2FADialog();
      fetchUsers();
    } else {
      showToast(res.data?.message || "关闭失败", "error");
    }
  } catch (e: any) {
    showToast(e.response?.data?.detail || "关闭两步验证失败", "error");
  } finally {
    disabling2FA.value = false;
  }
};

const confirmDelete = (user: any) => {
  userToDelete.value = user;
  showDeleteDialog.value = true;
};

// Helpers
// 新增用户时自动勾选的默认业务角色（按名称或编码匹配，忽略大小写）
const AUTO_CHECKED_ROLE_KEYS = ["default"];

const findAutoCheckedRoleIds = (): number[] => {
  return businessRoles.value
    .filter((role: any) => {
      const code = String(role?.code ?? "").trim().toLowerCase();
      const name = String(role?.name ?? "").trim().toLowerCase();
      return (
        AUTO_CHECKED_ROLE_KEYS.includes(code) ||
        AUTO_CHECKED_ROLE_KEYS.includes(name)
      );
    })
    .map((role: any) => role.id);
};

const openCreateDialog = async () => {
  // 首次进入页面时角色列表可能尚未返回，补齐后再做默认勾选
  await ensureBusinessRoles();
  showCreateDialog.value = true;
  formData.value = {
    user_name: "",
    real_name: "",
    email: "",
    role: "user",
    dept_ids: [],
    primary_dept_id: null,
    extra_data: "",
    role_ids: findAutoCheckedRoleIds(),
    remark: "",
  };
  extraDataPairs.value = [];
  extraDataTab.value = "visual";
  activeTab.value = "info";
  createdApiKey.value = "";
  roleSearchQuery.value = "";
};
const editUser = async (user: any) => {
  editingUserId.value = user.id;
  // 用户 A → 用户 B 可能都是普通用户，`resetPermissionBuffers` 的身份 watch 不会触发，
  // 所以打开时也要显式复位，否则 B 的角标沿用 A 的数字。
  agentRestrictionCounts.value = {};
  agentRestrictionTarget.value = null;
  formData.value = {
    user_name: user.user_name,
    real_name: user.real_name || "",
    email: user.email || "",
    role: user.role,
    dept_ids: Array.isArray(user.dept_ids) ? [...user.dept_ids] : [],
    primary_dept_id: user.primary_dept_id ?? null,
    extra_data: user.extra_data || "",
    role_ids: user.role_ids || [],
    remark: user.remark || "",
  };
  syncExtraDataToPairs();
  extraDataTab.value = "visual";
  showEditDialog.value = true;
  activeTab.value = "info";
  roleSearchQuery.value = "";
  // 真实身份来自后端行数据，并在打开时冻结：之后表单里改角色不再影响权限归属，
  // 否则「管理员改成普通用户再保存」会拿空的/残缺的缓冲整表覆盖权限。
  permissionSubjectRole.value = String(user.role || "");
  resetPermissionBuffers();
  // 角色列表与权限数据一起补齐：只靠 onMounted 那一次，失败后弹窗里会一直是空列表
  const pending: Promise<unknown>[] = [ensureBusinessRoles()];
  if (canEditPermissions.value) {
    pending.push(fetchResources(), fetchUserPermissions(user.id));
  }
  await Promise.all(pending);
};
const closeDialogs = () => {
  showCreateDialog.value = false;
  showEditDialog.value = false;
  error.value = "";
  createdApiKey.value = "";
  editingUserId.value = null;
  // 关闭时连同真实身份一起复位：上一个用户的残留不能跨用户存活
  permissionSubjectRole.value = "";
  resetPermissionBuffers();
};
const closeViewKeyDialog = () => {
  showViewKeyDialog.value = false;
  userToViewKey.value = null;
};
const closeRegenerateDialog = () => {
  showRegenerateDialog.value = false;
  userToRegenerate.value = null;
};
const toggleSelectAll = () => {
  const type = activeAssignableResourceType.value;
  if (!type) return;
  if (isAllSelected.value) {
    permissionData.value[type] = [];
  } else {
    permissionData.value[type] = selectableResources.value.map(
      (r: any) => r.id,
    );
  }
};
const formatDate = (dateStr: string) =>
  dateStr ? new Date(dateStr).toLocaleString() : "-";
const copyApiKey = async () => {
  const success = await copyToClipboard(createdApiKey.value);
  if (success) {
    showToast("复制成功", "success");
  } else {
    showToast("复制失败，请手动复制", "error");
  }
};
const copyViewedApiKey = async () => {
  const success = await copyToClipboard(viewedApiKey.value);
  if (success) {
    showToast("复制成功", "success");
  } else {
    showToast("复制失败，请手动复制", "error");
  }
};
const copyRegeneratedApiKey = async () => {
  const success = await copyToClipboard(regeneratedApiKey.value);
  if (success) {
    showToast("复制成功", "success");
  } else {
    showToast("复制失败，请手动复制", "error");
  }
};

let searchTimeout: any = null;
const debouncedSearch = () => {
  clearTimeout(searchTimeout);
  searchTimeout = setTimeout(() => {
    page.value = 1;
    fetchUsers();
  }, 500);
};
const resetFilters = () => {
  searchQuery.value = "";
  roleFilter.value = "";
  statusFilter.value = "";
  deptFilter.value = "";
  includeSub.value = false;
  page.value = 1;
  fetchUsers();
};

const fetchPublicConfig = async () => {
  try {
    const res = await axios.get("/api/portal/auth/config/public");
    ssoEnabled.value = res.data?.data?.yovole_sso_enabled === true;
  } catch (e) {
    console.error("Failed to fetch public config:", e);
  }
};

const onThirdPartySynced = () => {
  fetchUsers();
};

onMounted(async () => {
  // 深链 ?q= 来自角色成员抽屉 / 部门成员列表的「↗」：先落搜索词再拉列表，
  // 避免先请求一次全量再搜
  const deepLinkQuery = typeof route.query.q === "string" ? route.query.q.trim() : "";
  if (deepLinkQuery) {
    searchQuery.value = deepLinkQuery;
  }
  // 深链 ?edit= 来自部门成员列表的「编辑」：等列表回来再从里面取那一行打开，
  // 这样拿到的就是列表同款完整字段（dept_ids / role_ids 等），不必另开一个详情接口。
  const deepLinkEditId = typeof route.query.edit === "string" ? route.query.edit.trim() : "";
  loadDeptOptions();
  await fetchUsers();
  fetchBusinessRoles();
  fetchPublicConfig();
  fetchPendingCount();
  // 待我审批的知识库访问申请：有就亮红点，否则入口还在但不打扰
  void fetchPendingReviewCount();
  if (deepLinkEditId) {
    const target = users.value.find((item: any) => String(item.id) === deepLinkEditId);
    if (target) {
      await editUser(target);
    }
    // 打开后立刻抹掉 query：否则刷新页面会再弹一次，返回上一页也会被它拽回来
    const { edit: _edit, ...restQuery } = route.query;
    router.replace({ query: restQuery });
  }
});
</script>

<style scoped>
.custom-tooltip {
  position: relative;
}
.custom-tooltip::after {
  content: attr(data-tooltip);
  position: absolute;
  bottom: 120%;
  left: 50%;
  transform: translateX(-50%);
  background: rgba(0, 0, 0, 0.8);
  color: #fff;
  padding: 4px 8px;
  border-radius: 4px;
  font-size: 12px;
  white-space: nowrap;
  visibility: hidden;
  opacity: 0;
  transition: 0.2s;
}
.custom-tooltip:hover::after {
  visibility: visible;
  opacity: 1;
}
</style>
