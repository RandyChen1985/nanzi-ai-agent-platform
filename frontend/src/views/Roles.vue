<template>
  <div class="space-y-5">
    <!-- Header：与技能工作台一致，搜索进顶栏 -->
    <div class="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-4">
      <div class="min-w-0">
        <h1 class="text-xl sm:text-2xl font-bold text-gray-900">角色管理</h1>
        <p class="text-sm text-gray-500 mt-1">配置角色权限、菜单能力与用户归属</p>
      </div>

      <div class="flex flex-col sm:flex-row sm:items-center gap-2.5 sm:gap-3">
        <div class="relative w-full sm:w-64 lg:w-72">
          <span class="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
            <svg class="h-4 w-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
            </svg>
          </span>
          <input
            v-model="searchQuery"
            @input="debouncedSearch"
            type="search"
            placeholder="搜索角色名称或代码..."
            class="w-full pl-9 pr-3 py-2 bg-white border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none text-sm transition-all shadow-sm"
          />
        </div>

        <button
          v-if="searchQuery.trim()"
          type="button"
          @click="resetFilters"
          class="px-3 py-2 text-sm text-gray-500 hover:text-gray-800 bg-white border border-gray-300 rounded-lg shadow-sm hover:bg-gray-50 transition-colors shrink-0 self-start sm:self-auto"
        >
          清除
        </button>

        <button
          @click="fetchRoles"
          class="p-2 text-gray-500 hover:text-primary bg-white border border-gray-300 rounded-lg shadow-sm hover:bg-gray-50 transition-colors shrink-0 self-start sm:self-auto"
          title="刷新列表"
        >
          <svg class="w-4 h-4" :class="{ 'animate-spin': loading }" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
          </svg>
        </button>

        <button
          @click="openCreateDialog"
          class="flex items-center justify-center gap-2 px-4 py-2 bg-primary text-white rounded-lg shadow-sm hover:bg-primary-dark transition-all font-medium text-sm shrink-0"
        >
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4" />
          </svg>
          <span class="hidden sm:inline">创建角色</span>
          <span class="sm:hidden">创建</span>
        </button>
      </div>
    </div>

    <!-- Default Role Hint：缺失 default 角色时提示一键初始化 -->
    <div
      v-if="defaultRoleMissing"
      class="flex flex-col sm:flex-row sm:items-center gap-3 sm:gap-4 bg-amber-50 border border-amber-200 rounded-lg px-4 py-3.5 shadow-sm"
    >
      <div class="flex items-start gap-3 min-w-0 flex-1">
        <svg class="w-5 h-5 text-amber-500 shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z" />
        </svg>
        <div class="min-w-0">
          <p class="text-sm font-semibold text-amber-900">尚未初始化 default 角色</p>
          <p class="text-xs text-amber-700 mt-0.5 leading-relaxed">
            新增用户时会自动勾选 default 业务角色，当前角色列表中不存在该角色。建议一键初始化一个空的 default 角色，再按需为其分配权限。
          </p>
        </div>
      </div>
      <button
        type="button"
        @click="initializeDefaultRole"
        :disabled="initializingDefaultRole"
        class="shrink-0 inline-flex items-center justify-center gap-2 px-4 py-2 bg-amber-500 text-white rounded-lg shadow-sm hover:bg-amber-600 disabled:opacity-60 disabled:cursor-not-allowed transition-colors font-medium text-sm self-start sm:self-auto"
      >
        <svg v-if="initializingDefaultRole" class="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
          <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
          <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path>
        </svg>
        <svg v-else class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4" />
        </svg>
        <span>{{ initializingDefaultRole ? '初始化中...' : '一键初始化 default 角色' }}</span>
      </button>
    </div>

    <!-- Loading -->
    <div v-if="loading" class="flex flex-col items-center justify-center py-16">
      <div class="w-10 h-10 border-4 border-primary border-t-transparent rounded-full animate-spin"></div>
      <p class="text-sm text-gray-500 mt-4 font-medium">加载角色列表...</p>
    </div>

    <!-- Empty -->
    <div
      v-else-if="roles.length === 0"
      class="flex flex-col items-center justify-center min-h-[320px] bg-white border border-gray-200 rounded-lg shadow-sm px-6"
    >
      <svg class="w-14 h-14 text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z" />
      </svg>
      <template v-if="isSearchEmpty">
        <p class="text-sm text-gray-500 mt-4 font-semibold">无匹配「{{ searchQuery.trim() }}」的角色</p>
        <p class="text-xs text-gray-400 mt-1">试试其他关键词，或清除搜索后浏览全部</p>
        <button
          type="button"
          @click="resetFilters"
          class="mt-5 px-4 py-2 text-sm font-medium text-blue-600 border border-blue-200 rounded-lg hover:bg-blue-50 transition-colors"
        >
          清除搜索
        </button>
      </template>
      <template v-else>
        <p class="text-sm text-gray-500 mt-4 font-semibold">暂无角色</p>
        <p class="text-xs text-gray-400 mt-1">创建角色后可为用户分配权限与菜单能力</p>
        <button
          type="button"
          @click="openCreateDialog"
          class="mt-5 px-4 py-2 text-sm font-medium text-white bg-primary hover:bg-primary-dark rounded-lg transition-colors"
        >
          创建角色
        </button>
      </template>
    </div>

    <!-- Role List -->
    <div v-else>
      <!-- Desktop Table -->
      <div v-if="!isMobile" class="bg-white border border-gray-200 shadow-sm rounded-lg overflow-hidden">
        <div class="overflow-x-auto">
          <table class="min-w-full divide-y divide-gray-200">
            <thead class="bg-gray-50">
              <tr>
                <th class="px-5 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">角色</th>
                <th class="px-5 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">描述</th>
                <th class="px-5 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">用户数</th>
                <th class="px-5 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">创建时间</th>
                <th class="px-5 py-3 text-right text-xs font-semibold text-gray-500 uppercase tracking-wider">操作</th>
              </tr>
            </thead>
            <tbody class="bg-white divide-y divide-gray-200">
              <tr v-for="role in roles" :key="role.id" class="group hover:bg-gray-50/80 transition-colors">
                <td class="px-5 py-4 whitespace-nowrap">
                  <div class="flex items-center gap-2.5 min-w-0">
                    <div class="flex items-center justify-center w-9 h-9 rounded-xl bg-blue-50 text-blue-600 shrink-0">
                      <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
                      </svg>
                    </div>
                    <div class="min-w-0">
                      <div class="flex items-center gap-1.5 flex-wrap">
                        <span class="text-sm font-semibold text-gray-900">{{ role.name }}</span>
                        <span
                          v-if="isSystemRole(role)"
                          class="shrink-0 px-1.5 py-0.5 text-[9px] font-semibold rounded-full bg-slate-100 text-slate-600"
                        >系统</span>
                      </div>
                      <div class="mt-0.5 flex items-center gap-1.5 text-xs text-gray-400">
                        <span class="font-mono bg-gray-100 text-gray-600 px-1.5 py-0.5 rounded text-[10px]">{{ role.code }}</span>
                        <span class="text-gray-300">·</span>
                        <span class="font-mono">#{{ role.id }}</span>
                      </div>
                    </div>
                  </div>
                </td>
                <td class="px-5 py-4 text-sm text-gray-500 max-w-xs truncate" :title="role.description">
                  {{ role.description || '—' }}
                </td>
                <td class="px-5 py-4 whitespace-nowrap">
                  <span class="inline-flex items-center bg-gray-100 text-gray-700 px-2 py-0.5 rounded-full text-xs font-medium">
                    {{ role.user_count }}
                  </span>
                </td>
                <td class="px-5 py-4 whitespace-nowrap text-sm text-gray-500 font-mono">
                  {{ formatDate(role.created_at) }}
                </td>
                <td class="px-5 py-4 whitespace-nowrap text-right text-sm font-medium">
                  <div class="flex justify-end items-center gap-1">
                    <button
                      @click="editRole(role)"
                      class="p-1.5 rounded-lg text-blue-600 hover:bg-blue-50 transition-colors"
                      title="编辑角色"
                    >
                      <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" /></svg>
                    </button>
                    <button
                      @click="openPermissionDialog(role)"
                      class="p-1.5 rounded-lg text-indigo-600 hover:bg-indigo-50 transition-colors"
                      title="分配权限"
                    >
                      <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" /></svg>
                    </button>
                    <button
                      @click="openUserAssignmentDialog(role)"
                      class="p-1.5 rounded-lg text-emerald-600 hover:bg-emerald-50 transition-colors"
                      title="分配用户"
                    >
                      <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4.354a4 4 0 110 5.292M15 21H3v-1a6 6 0 0112 0v1zm0 0h6v-1a6 6 0 00-9-5.197M13 7a4 4 0 11-8 0 4 4 0 018 0z" /></svg>
                    </button>
                    <button
                      @click="confirmDelete(role)"
                      class="p-1.5 rounded-lg text-gray-400 hover:text-red-600 hover:bg-red-50 transition-all opacity-0 group-hover:opacity-100 focus:opacity-100"
                      title="删除角色"
                    >
                      <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" /></svg>
                    </button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <div class="bg-gray-50 px-4 py-3 border-t border-gray-200 flex flex-col sm:flex-row items-center justify-between gap-3">
          <div class="text-sm text-gray-700">
            共 {{ total }} 条，第 {{ page }}/{{ totalPages }} 页
          </div>
          <div class="flex gap-2 w-full sm:w-auto">
            <button @click="page > 1 && (page--, fetchRoles())" :disabled="page <= 1" class="flex-1 sm:flex-none px-4 py-2 border border-gray-300 rounded-lg bg-white hover:bg-gray-50 disabled:opacity-50 text-sm font-medium">上一页</button>
            <button @click="page < totalPages && (page++, fetchRoles())" :disabled="page >= totalPages" class="flex-1 sm:flex-none px-4 py-2 border border-gray-300 rounded-lg bg-white hover:bg-gray-50 disabled:opacity-50 text-sm font-medium">下一页</button>
          </div>
        </div>
      </div>

      <!-- Mobile Card List -->
      <div v-else class="space-y-3">
        <div
          v-for="role in roles"
          :key="role.id"
          class="bg-white border border-gray-200 rounded-lg p-4 shadow-sm"
        >
          <div class="flex justify-between items-start gap-3">
            <div class="flex items-start gap-3 min-w-0 flex-1">
              <div class="flex items-center justify-center w-9 h-9 rounded-xl bg-blue-50 text-blue-600 shrink-0">
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
                </svg>
              </div>
              <div class="min-w-0">
                <div class="flex items-center gap-1.5 flex-wrap">
                  <h3 class="text-base font-semibold text-gray-900 truncate">{{ role.name }}</h3>
                  <span
                    v-if="isSystemRole(role)"
                    class="shrink-0 px-1.5 py-0.5 text-[9px] font-semibold rounded-full bg-slate-100 text-slate-600"
                  >系统</span>
                </div>
                <p class="text-xs font-mono text-gray-500 mt-0.5">{{ role.code }}</p>
              </div>
            </div>
            <span class="bg-gray-100 text-gray-700 px-2 py-0.5 rounded-full text-[10px] font-semibold shrink-0">
              {{ role.user_count }} 用户
            </span>
          </div>

          <p class="mt-3 text-xs text-gray-500 line-clamp-2 leading-relaxed">
            {{ role.description || '暂无描述' }}
          </p>

          <div class="flex justify-between items-center pt-3 mt-3 border-t border-gray-100">
            <span class="text-[10px] text-gray-400 font-mono">#{{ role.id }} · {{ formatDate(role.created_at).split(' ')[0] }}</span>
            <div class="flex items-center gap-1">
              <button @click="editRole(role)" class="p-1.5 rounded-lg text-blue-600 hover:bg-blue-50" title="编辑">
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" /></svg>
              </button>
              <button @click="openPermissionDialog(role)" class="p-1.5 rounded-lg text-indigo-600 hover:bg-indigo-50" title="权限">
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" /></svg>
              </button>
              <button @click="openUserAssignmentDialog(role)" class="p-1.5 rounded-lg text-emerald-600 hover:bg-emerald-50" title="用户">
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4.354a4 4 0 110 5.292M15 21H3v-1a6 6 0 0112 0v1zm0 0h6v-1a6 6 0 00-9-5.197M13 7a4 4 0 11-8 0 4 4 0 018 0z" /></svg>
              </button>
              <button @click="confirmDelete(role)" class="p-1.5 rounded-lg text-gray-400 hover:text-red-600 hover:bg-red-50" title="删除">
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" /></svg>
              </button>
            </div>
          </div>
        </div>

        <div class="bg-white border border-gray-200 rounded-lg px-4 py-3 flex flex-col sm:flex-row items-center justify-between gap-3">
          <div class="text-sm text-gray-700">
            共 {{ total }} 条，第 {{ page }}/{{ totalPages }} 页
          </div>
          <div class="flex gap-2 w-full sm:w-auto">
            <button @click="page > 1 && (page--, fetchRoles())" :disabled="page <= 1" class="flex-1 sm:flex-none px-4 py-2 border border-gray-300 rounded-lg hover:bg-gray-50 disabled:opacity-50 text-sm font-medium">上一页</button>
            <button @click="page < totalPages && (page++, fetchRoles())" :disabled="page >= totalPages" class="flex-1 sm:flex-none px-4 py-2 border border-gray-300 rounded-lg hover:bg-gray-50 disabled:opacity-50 text-sm font-medium">下一页</button>
          </div>
        </div>
      </div>
    </div>

    <!-- Create/Edit Dialog -->
    <div v-if="showCreateDialog || showEditDialog" class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-[9990]" @click.self="closeDialogs">
      <div
        class="bg-white p-6 w-full max-w-md flex flex-col"
        :class="isMobile ? 'h-full rounded-none' : 'rounded-lg shadow-2xl'"
      >
        <div class="flex justify-between items-center mb-4">
            <h2 class="text-xl font-bold">{{ showEditDialog ? '编辑角色' : '创建角色' }}</h2>
            <button v-if="isMobile" @click="closeDialogs" class="p-2 text-gray-400">
                <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" /></svg>
            </button>
        </div>

        <div class="space-y-4 flex-1 overflow-y-auto pr-1">
            <div>
                <label class="block text-xs font-bold text-gray-400 uppercase mb-1">角色代码</label>
                <input
                  v-model="formData.code"
                  type="text"
                  :disabled="showEditDialog"
                  class="w-full border border-gray-300 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:bg-gray-100 text-sm"
                  placeholder="例如: finance_manager"
                />
                <p class="text-[10px] text-gray-400 mt-1">唯一标识符，创建后不可修改</p>
            </div>

            <div>
                <label class="block text-xs font-bold text-gray-400 uppercase mb-1">角色名称</label>
                <input
                  v-model="formData.name"
                  type="text"
                  class="w-full border border-gray-300 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500 text-sm"
                  placeholder="例如: 财务经理"
                />
            </div>

            <div>
                <label class="block text-xs font-bold text-gray-400 uppercase mb-1">描述</label>
                <textarea
                  v-model="formData.description"
                  rows="3"
                  class="w-full border border-gray-300 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500 text-sm"
                  placeholder="角色职责描述..."
                ></textarea>
            </div>

            <div v-if="error" class="bg-red-50 border border-red-200 rounded-lg p-3 text-xs text-red-800 animate-shake">
                {{ error }}
            </div>
        </div>

        <div class="mt-6 flex flex-col sm:flex-row justify-end gap-2 pt-4 border-t border-gray-200">
          <button @click="closeDialogs" class="order-2 sm:order-1 px-4 py-2.5 border border-gray-300 rounded-lg hover:bg-gray-50 text-sm font-medium">
            取消
          </button>
          <button
            @click="saveRole"
            :disabled="submitting"
            class="order-1 sm:order-2 px-4 py-2.5 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 text-sm font-bold shadow-md shadow-blue-100"
          >
            {{ submitting ? '保存中...' : (showEditDialog ? '保存更新' : '立即创建') }}
          </button>
        </div>
      </div>
    </div>

    <!-- Permission Dialog -->
    <div v-if="showPermissionDialog" class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-[9990]" @click.self="closePermissionDialog">
        <div
            class="bg-white flex flex-col shadow-2xl transition-all duration-300 overflow-hidden"
            :class="isMobile ? 'w-full h-full rounded-none min-h-0' : 'rounded-2xl p-6 w-full max-w-4xl max-h-[90vh] min-h-0'"
        >
            <!-- Header -->
            <div class="flex-shrink-0 px-4 py-4 sm:px-0 sm:pt-0 border-b border-gray-100 sm:border-0 flex justify-between items-center">
                <div>
                    <h2 class="text-xl font-bold flex items-center gap-2">
                        分配权限
                        <span v-if="!isMobile" class="text-xs font-normal text-gray-400 bg-gray-50 px-2 py-0.5 rounded border">{{ currentRole?.code }}</span>
                    </h2>
                    <p class="text-xs text-gray-500 mt-0.5">当前角色: <span class="font-bold text-gray-700">{{ currentRole?.name }}</span></p>
                </div>
                <button v-if="isMobile" @click="closePermissionDialog" class="p-2 text-gray-400">
                    <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" /></svg>
                </button>
            </div>

            <!-- Main Tabs (Assets vs UI) -->
            <div class="flex-shrink-0 flex items-center space-x-1 p-1 bg-gray-100 rounded-xl my-4 sm:my-6 w-full max-w-md mx-auto">
                <button
                    @click="activeMainTab = 'assets'"
                    :class="activeMainTab === 'assets' ? 'bg-white text-blue-600 shadow-sm' : 'text-gray-500 hover:text-gray-700'"
                    class="flex-1 py-2 rounded-lg text-xs font-bold transition-all"
                >
                    📦 资产授权
                </button>
                <button
                    @click="activeMainTab = 'ui'"
                    :class="activeMainTab === 'ui' ? 'bg-white text-blue-600 shadow-sm' : 'text-gray-500 hover:text-gray-700'"
                    class="flex-1 py-2 rounded-lg text-xs font-bold transition-all"
                >
                    🎨 菜单功能
                </button>
                <button
                    @click="activeMainTab = 'quota'"
                    :class="activeMainTab === 'quota' ? 'bg-white text-blue-600 shadow-sm' : 'text-gray-500 hover:text-gray-700'"
                    class="flex-1 py-2 rounded-lg text-xs font-bold transition-all"
                >
                    📊 额度
                </button>
            </div>

            <div class="flex-1 min-h-0 overflow-hidden flex flex-col px-4 sm:px-0">
                <!-- Tab 1: Data Assets -->
                <template v-if="activeMainTab === 'assets'">
                    <div class="flex flex-col flex-1 min-h-0 gap-2">
                    <!-- Resource Type Tabs：固定高度，避免被下方列表 flex 挤没 -->
                    <div class="flex-shrink-0 overflow-x-auto scrollbar-hide">
                        <div class="inline-flex min-w-full sm:min-w-0 items-center gap-1 p-1 bg-gray-100 rounded-xl">
                            <button
                                v-for="type in resourceTypes"
                                :key="type"
                                @click="activeResTab = type"
                                :class="[
                                    activeResTab === type
                                        ? 'bg-white shadow-sm ' + resourceConfig[type].tabTextActive
                                        : 'text-gray-600 hover:text-gray-800 hover:bg-white/50'
                                ]"
                                class="shrink-0 px-2.5 sm:px-3 py-2 font-bold text-xs leading-snug transition-all whitespace-nowrap inline-flex items-center gap-1.5 rounded-lg"
                            >
                                <span
                                    class="inline-flex h-4 w-4 shrink-0 items-center justify-center [&>svg]:h-full [&>svg]:w-full"
                                    v-html="resourceConfig[type].icon"
                                />
                                <span>{{ resourceConfig[type].label }}</span>
                                <span
                                    v-if="type !== 'forbidden_configs'"
                                    class="text-[10px] leading-none min-w-[1.25rem] text-center px-1.5 py-0.5 rounded-full bg-gray-100 text-gray-600"
                                    :class="activeResTab === type ? 'bg-gray-50' : ''"
                                >
                                    {{ (permissionData as any)[type].length }}
                                </span>
                            </button>
                        </div>
                    </div>

                    <!-- Checkbox List -->
                    <div class="flex-1 min-h-0 overflow-y-auto bg-gray-50 p-3 sm:p-4 rounded-xl border border-gray-200">
                        <!-- Mobile Hint -->
                        <div v-if="activeResTab === 'agents'" class="mb-3 bg-blue-50 border border-blue-100 rounded-lg p-2.5 flex items-start gap-2">
                            <svg class="w-4 h-4 text-blue-500 mt-0.5 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                            </svg>
                            <p class="text-[10px] sm:text-xs text-blue-700 leading-tight">此处仅配置<b>系统级</b>公共智能体权限。</p>
                        </div>

                        <div v-if="loadingResources" class="flex flex-col items-center justify-center py-12 gap-3">
                            <div class="relative">
                                <div class="w-9 h-9 rounded-full border-[3px] border-blue-100"></div>
                                <div class="absolute inset-0 w-9 h-9 rounded-full border-[3px] border-blue-500 border-t-transparent animate-spin"></div>
                            </div>
                            <div class="text-center space-y-0.5">
                                <p class="text-sm font-medium text-gray-600">正在加载权限资源</p>
                                <p class="text-xs text-gray-400">请稍候…</p>
                            </div>
                        </div>
                        <div v-if="activeResTab === 'forbidden_configs'" class="space-y-4 text-left">
                            <div class="bg-red-50 border border-red-100 rounded-lg p-2.5 flex items-start gap-2 mb-2 select-none">
                                <svg class="w-4 h-4 text-red-500 mt-0.5 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/>
                                </svg>
                                <div class="text-[10px] sm:text-xs text-red-700 leading-tight">
                                    <p class="font-bold mb-0.5">安全策略与黑名单说明</p>
                                    <p class="opacity-80">配置禁用工具后该角色下的用户完全无法调用该工具。配置禁用命令后，当智能体尝试替该角色下的用户运行含有敏感词的系统命令时，将被运行时强行拦截。</p>
                                </div>
                            </div>

                            <!-- 1. 禁用工具 Checkbox 组 -->
                            <div class="border border-gray-150 rounded-xl p-3 bg-white shadow-sm">
                                <h4 class="text-xs font-bold text-gray-700 mb-2.5 flex items-center gap-1.5 select-none">
                                    <span class="w-1.5 h-1.5 bg-red-500 rounded-full"></span>
                                    选择要禁用的工具（全局拉黑）
                                </h4>
                                <div class="grid grid-cols-1 sm:grid-cols-2 gap-2">
                                    <label v-for="tool in availableToolsToForbid" :key="tool.id" class="flex items-start p-2.5 rounded-lg border transition-all shadow-sm bg-white hover:border-red-200 cursor-pointer">
                                        <input
                                            type="checkbox"
                                            :value="tool.id"
                                            v-model="permissionData.forbidden_tools"
                                            class="h-4 w-4 rounded mt-0.5 flex-shrink-0 border-gray-300 focus:ring-2 text-red-600 focus:ring-red-500"
                                        />
                                        <div class="ml-2.5 min-w-0 flex-1">
                                            <p class="font-bold text-gray-900 truncate text-xs leading-tight">{{ tool.name }}</p>
                                            <span class="text-gray-400 text-[9px] block truncate font-mono mt-0.5">{{ tool.description }}</span>
                                        </div>
                                    </label>
                                </div>
                            </div>

                            <!-- 1.2 其他自定义与扩展工具禁用 -->
                            <div v-show="otherAvailableTools.length > 0" class="border border-gray-150 rounded-xl p-3 bg-white shadow-sm">
                                <h4 class="text-xs font-bold text-gray-700 mb-2 flex items-center justify-between select-none">
                                    <span class="flex items-center gap-1.5">
                                        <span class="w-1.5 h-1.5 bg-indigo-500 rounded-full"></span>
                                        自定义 API 与 MCP 扩展工具禁用
                                    </span>
                                    <input
                                        v-model="toolSearchQuery"
                                        type="search"
                                        placeholder="搜索工具名..."
                                        class="text-[10px] border border-gray-200 rounded px-2 py-0.5 w-[140px] focus:outline-none focus:border-indigo-500"
                                    />
                                </h4>
                                <div class="max-h-[140px] overflow-y-auto border border-gray-100 rounded-lg p-2 bg-gray-50 custom-scrollbar grid grid-cols-1 sm:grid-cols-2 gap-1.5">
                                    <label v-for="tool in filteredOtherTools" :key="tool.id" class="flex items-center p-1.5 rounded border bg-white hover:bg-gray-50 transition-colors cursor-pointer text-xs">
                                        <input
                                            type="checkbox"
                                            :value="tool.id"
                                            v-model="permissionData.forbidden_tools"
                                            class="h-3.5 w-3.5 rounded text-indigo-600 focus:ring-indigo-500"
                                        />
                                        <div class="ml-1.5 min-w-0 flex-1">
                                            <p class="font-bold text-gray-900 truncate text-[11px] leading-tight">{{ tool.name }}</p>
                                            <p class="text-[9px] text-gray-400 truncate mt-0.5">{{ tool.description }}</p>
                                        </div>
                                    </label>
                                </div>
                            </div>

                            <!-- 2. 禁用 Bash 命令关键字 -->
                            <div class="border border-gray-150 rounded-xl p-3 bg-white shadow-sm flex flex-col">
                                <h4 class="text-xs font-bold text-gray-700 mb-2 flex items-center gap-1.5 select-none">
                                    <span class="w-1.5 h-1.5 bg-amber-500 rounded-full"></span>
                                    自定义禁用命令关键字
                                </h4>
                                <p class="text-[10px] text-gray-400 mb-2">针对 exec_command 终端工具，输入禁止该角色下的用户运行的命令名或命令序列（例如：rm, shutdown, mv, wget），用英文逗号隔开。</p>
                                <textarea
                                    v-model="forbiddenCommandsText"
                                    placeholder="例如: rm, shutdown, mv, wget, curl"
                                    class="w-full text-xs sm:text-sm border border-gray-200 rounded-lg p-2.5 min-h-[85px] focus:ring-amber-500 focus:border-amber-500 custom-scrollbar focus:outline-none"
                                ></textarea>
                            </div>
                        </div>

                        <div v-else-if="currentResources.length === 0" class="text-center py-10 text-gray-500 text-xs italic">（暂无资源）</div>
                        <div v-else class="grid grid-cols-1 xs:grid-cols-2 lg:grid-cols-3 gap-2">
                            <label
                                v-for="res in currentResources"
                                :key="res.id"
                                class="flex items-start p-2.5 rounded-lg border transition-all shadow-sm group bg-white"
                                :class="[
                                    isMissingKnowledgeBase(res)
                                      ? 'opacity-70 cursor-not-allowed border-amber-200 bg-amber-50/40'
                                      : 'cursor-pointer hover:border-blue-200',
                                    resCardActiveClass(res.id)
                                ]"
                                :title="isMissingKnowledgeBase(res) ? '该知识库已在 RAGFlow 失联，不可分配权限' : undefined"
                            >
                                <input
                                    type="checkbox"
                                    :value="res.id"
                                    v-model="(permissionData as any)[activeResTab]"
                                    :disabled="isMissingKnowledgeBase(res)"
                                    class="h-4 w-4 rounded mt-0.5 flex-shrink-0 border-gray-300 focus:ring-2 disabled:cursor-not-allowed disabled:opacity-50"
                                    :class="resCheckboxClass()"
                                />
                                <div class="ml-2.5 min-w-0 flex-1">
                                    <div class="flex items-center gap-1.5 mb-0.5 min-w-0">
                                        <span class="font-bold text-gray-900 block truncate text-xs leading-tight">{{ res.platform_name || res.display_name || res.name }}</span>
                                        <span
                                          v-if="res.method"
                                          class="flex-shrink-0 text-[9px] px-1 py-0.5 rounded font-mono font-bold leading-none"
                                          :class="methodBadgeClass(res.method)"
                                        >{{ res.method }}</span>
                                        <span
                                          v-if="isMissingKnowledgeBase(res)"
                                          class="flex-shrink-0 text-[9px] px-1.5 py-0.5 rounded-full bg-amber-100 text-amber-700 font-semibold leading-none"
                                        >失联</span>
                                    </div>
                                    <span
                                      v-if="res.path"
                                      class="text-gray-500 text-[9px] block truncate font-mono leading-tight"
                                    >{{ res.path }}</span>
                                    <span class="text-gray-400 text-[9px] block truncate font-mono">{{ res.description || res.id }}</span>
                                </div>
                            </label>
                        </div>
                    </div>
                    <div v-if="activeResTab !== 'forbidden_configs'" class="flex-shrink-0 flex justify-between items-center py-1 text-[10px] text-gray-500 px-1">
                        <span>已勾选: <b class="text-blue-600">{{ (permissionData as any)[activeResTab].length }}</b></span>
                        <button
                            @click="toggleSelectAll"
                            class="font-black hover:underline text-blue-600"
                        >
                            {{ isAllSelected ? '全部取消' : '全选本页' }}
                        </button>
                    </div>
                    </div>
                </template>

                <!-- Tab 2: UI Interface Permissions -->
                <template v-else-if="activeMainTab === 'ui'">
                    <div class="flex-1 overflow-y-auto bg-gray-50 p-3 sm:p-6 rounded-xl border border-gray-200">
                        <div class="space-y-4">
                            <div v-for="menu in MENU_TREE" :key="menu.id" class="bg-white rounded-xl border border-gray-200 overflow-hidden shadow-sm">
                                <!-- Menu Parent -->
                                <div class="px-3 py-2.5 bg-gray-50 flex items-center justify-between border-b border-gray-100">
                                    <div class="flex items-center gap-2">
                                        <svg class="w-4 h-4 text-blue-600 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M4 5.5A1.5 1.5 0 015.5 4h13A1.5 1.5 0 0120 5.5v13a1.5 1.5 0 01-1.5 1.5h-13A1.5 1.5 0 014 18.5v-13zM8 8h8M8 12h8M8 16h5" />
                                        </svg>
                                        <span class="text-[10px] font-bold text-blue-600">菜单权限</span>
                                        <input
                                            type="checkbox"
                                            :checked="isItemSelected(menu.id)"
                                            :indeterminate="isMenuPartiallySelected(menu.id)"
                                            @change="toggleTreeItem(menu.id)"
                                            class="h-4 w-4 text-blue-600 rounded border-gray-300 focus:ring-blue-500 cursor-pointer"
                                        />
                                        <span class="font-black text-gray-800 text-xs sm:text-sm">{{ menu.label }}</span>
                                    </div>
                                </div>

                                <!-- Children Elements -->
                                <div v-if="menu.children && menu.children.length > 0" class="p-3">
                                    <div class="flex items-center gap-1.5 mb-2 text-[10px] font-bold text-gray-500">
                                        <svg class="w-3.5 h-3.5 text-indigo-500" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M9 5h6M9 9h6M9 13h6M9 17h4M5 5.5A1.5 1.5 0 016.5 4h11A1.5 1.5 0 0119 5.5v13a1.5 1.5 0 01-1.5 1.5h-11A1.5 1.5 0 015 18.5v-13z" />
                                        </svg>
                                        <span>功能点权限</span>
                                        <span class="font-normal text-gray-400">控制页面内按钮和操作</span>
                                    </div>
                                    <div class="grid grid-cols-1 xs:grid-cols-2 gap-2">
                                    <div
                                        v-for="child in menu.children"
                                        :key="child.id"
                                        @click="toggleTreeItem(child.id)"
                                        class="flex items-center gap-2.5 p-2 rounded-lg border border-transparent hover:bg-blue-50 transition-all cursor-pointer group"
                                        :class="isItemSelected(child.id) ? 'bg-blue-50/50 border-blue-100 shadow-inner' : ''"
                                    >
                                        <div
                                            class="w-3.5 h-3.5 rounded border flex-shrink-0 flex items-center justify-center transition-all"
                                            :class="isItemSelected(child.id) ? 'bg-blue-600 border-blue-600' : 'bg-white border-gray-300'"
                                        >
                                            <svg v-if="isItemSelected(child.id)" class="w-2.5 h-2.5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7" /></svg>
                                        </div>
                                        <span class="text-[11px] font-medium text-gray-700 leading-tight">{{ child.label }}</span>
                                    </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                    <div class="mt-2 mb-2 text-[10px] text-gray-400 italic px-1 flex justify-between">
                        <span>勾选菜单控制导航，勾选子项控制按钮</span>
                        <span class="text-blue-600 font-bold">已选菜单: {{ permissionData.menus.length }}</span>
                    </div>
                </template>

                <!-- Tab 3: Quota -->
                <template v-else-if="activeMainTab === 'quota'">
                    <div class="flex-1 min-h-0 overflow-y-auto px-1">
                        <QuotaPolicyPanel
                            v-if="currentRole"
                            scope-type="role"
                            :scope-id="currentRole.id"
                        />
                    </div>
                </template>
            </div>

            <div class="flex-shrink-0 p-4 sm:p-0 mt-2 sm:mt-6 flex flex-col sm:flex-row justify-end gap-2 sm:pt-4 border-t border-gray-100 sm:border-t-0">
                <button @click="closePermissionDialog" class="order-2 sm:order-1 px-4 py-2.5 border border-gray-300 rounded-lg hover:bg-gray-50 text-sm font-medium">{{ activeMainTab === 'quota' ? '关闭' : '取消' }}</button>
                <button
                    v-if="activeMainTab !== 'quota'"
                    @click="savePermissions"
                    :disabled="submittingPerms"
                    class="order-1 sm:order-2 px-4 py-2.5 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 text-sm font-bold shadow-lg shadow-blue-200"
                >
                    {{ submittingPerms ? '...' : '确认并保存配置' }}
                </button>
            </div>
        </div>
    </div>

    <!-- User Assignment Dialog (Refactored to Dual Column) -->
    <div v-if="showUserAssignmentDialog" class="fixed inset-0 bg-black bg-opacity-60 flex items-center justify-center z-[9990] backdrop-blur-sm" @click.self="requestCloseUserAssignmentDialog">
        <div
            class="bg-white flex flex-col shadow-2xl transition-all duration-500 overflow-hidden"
            :class="isMobile ? 'w-full h-full rounded-none' : 'rounded-2xl p-4 w-full max-w-6xl max-h-[92vh]'"
            role="dialog"
            aria-modal="true"
            aria-labelledby="assign-users-title"
        >
            <!-- Header：标题与角色信息同一行，压缩纵向占用 -->
            <div class="px-3 py-2 sm:px-1 sm:pt-0 border-b border-gray-100 sm:border-0 flex justify-between items-center gap-3 mb-2">
                <div class="flex items-center gap-3 flex-wrap min-w-0">
                    <h2 id="assign-users-title" class="text-lg font-black text-gray-900 tracking-tight flex items-center gap-2 shrink-0">
                        分配角色用户
                        <span
                            v-if="hasUnsavedChanges"
                            class="px-2 py-0.5 text-[10px] font-bold rounded-full bg-amber-100 text-amber-700 border border-amber-200"
                        >未保存</span>
                    </h2>
                    <p class="text-xs text-gray-500 flex items-center gap-1.5 min-w-0">
                        <span class="inline-block w-2 h-2 rounded-full bg-blue-500 shrink-0"></span>
                        <span class="shrink-0">当前角色:</span>
                        <span class="font-bold text-gray-800 bg-gray-100 px-2 py-0.5 rounded truncate">{{ currentRole?.name }} ({{ currentRole?.code }})</span>
                        <span v-if="loadingAssigned" class="text-gray-400 shrink-0">· 加载成员中...</span>
                    </p>
                </div>
                <button
                    @click="requestCloseUserAssignmentDialog"
                    aria-label="关闭分配角色用户弹窗"
                    class="w-8 h-8 flex items-center justify-center rounded-full hover:bg-gray-100 text-gray-400 transition-all shrink-0"
                >
                    <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M6 18L18 6M6 6l12 12" /></svg>
                </button>
            </div>

            <!-- Dual Column Content -->
            <div class="flex-1 flex flex-col sm:flex-row gap-2 sm:gap-3 min-h-0 px-1">
                <!-- Left: Available Users -->
                <div class="flex-1 flex flex-col min-h-0 bg-gray-50/50 rounded-xl border border-gray-100 p-2.5">
                    <div class="flex items-center justify-between gap-2 mb-1.5 px-1">
                        <div class="flex items-center gap-1.5 min-w-0">
                            <span class="text-[11px] font-black text-gray-400 uppercase tracking-widest whitespace-nowrap">可选用户 ·候选</span>
                            <span class="px-2 py-0.5 bg-gray-200 text-gray-600 text-[9px] font-bold rounded-full shrink-0" aria-live="polite" :title="`候选池共 ${availableTotal} 人，其中 ${pendingAddedCount} 人已在本弹窗中选中待保存`">{{ availableRemaining }}</span>
                        </div>
                        <span class="shrink-0 text-[10px] text-gray-400 whitespace-nowrap">仅显示未加入</span>
                    </div>

                    <!-- 批量勾选：只改变左栏勾选态，必须再点 »（或双击）才会移到右侧 -->
                    <div class="flex items-center gap-3 mb-2 px-1 flex-wrap">
                        <button
                            type="button"
                            @click="checkAllVisible"
                            :disabled="filteredAvailableUsers.length === 0"
                            class="text-[10px] font-bold text-gray-500 hover:underline py-0.5 disabled:opacity-40 disabled:cursor-not-allowed whitespace-nowrap"
                            title="只勾选当前页显示的用户"
                        >全选本页</button>
                        <button
                            v-if="availableRemaining > 0"
                            type="button"
                            @click="requestSelectAllFiltered"
                            :disabled="selectingAllFiltered"
                            class="text-[10px] font-bold text-blue-600 hover:underline py-0.5 disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap"
                            :title="`勾选当前筛选出的 ${availableRemaining} 名用户（跨页），不会直接移动`"
                        >{{ selectingAllFiltered ? '处理中...' : `全选筛选结果(${availableRemaining})` }}</button>
                        <button
                            v-if="candidateCheckedCount > 0"
                            type="button"
                            @click="deselectAllFiltered"
                            class="text-[10px] font-bold text-amber-600 hover:underline py-0.5 whitespace-nowrap"
                            title="取消左栏已勾选的用户（不影响右侧成员）"
                        >取消全选({{ candidateCheckedCount }})</button>
                    </div>

                    <div class="relative mb-2">
                        <input
                            v-model="userSearchQuery"
                            @input="debouncedUserSearch"
                            type="search"
                            aria-label="搜索候选用户"
                            placeholder="搜索候选用户..."
                            class="w-full bg-white border border-gray-200 rounded-xl px-3 py-2 pl-9 focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-all text-sm"
                        />
                        <svg class="w-4 h-4 text-gray-400 absolute left-3 top-2.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                        </svg>
                    </div>

                    <div class="flex-1 overflow-y-auto custom-scrollbar pr-1 space-y-1" role="list">
                        <div v-if="loadingUsers" class="text-center py-16 text-gray-400 text-xs animate-pulse">正在获取候选库...</div>
                        <div v-else-if="filteredAvailableUsers.length === 0" class="text-center py-16 text-gray-400 text-xs italic">
                            <template v-if="userSearchQuery.trim()">没有匹配「{{ userSearchQuery.trim() }}」的用户</template>
                            <template v-else-if="assignedUserIds.length > 0">全部用户都已加入该角色</template>
                            <template v-else>暂无可分配用户</template>
                        </div>
                        <button
                            v-for="user in filteredAvailableUsers"
                            :key="'avail-'+user.id"
                            type="button"
                            role="listitem"
                            @click="toggleCandidateChecked(user.id)"
                            @dblclick="moveOneToAssigned(user.id)"
                            :aria-pressed="isCandidateChecked(user.id)"
                            :aria-label="isCandidateChecked(user.id) ? `取消勾选 ${user.real_name || user.user_name}` : `勾选 ${user.real_name || user.user_name}（双击直接加入）`"
                            :class="isCandidateChecked(user.id)
                                ? 'border-blue-300 bg-blue-50/70 ring-1 ring-blue-500/20'
                                : 'bg-white border-gray-100 hover:border-blue-300 hover:shadow-md hover:shadow-blue-500/5'"
                            class="w-full text-left flex items-center p-2 rounded-lg border transition-all cursor-pointer group active:scale-[0.99]"
                            :title="`单击勾选，双击直接加入`"
                        >
                            <span
                                :class="isCandidateChecked(user.id) ? 'bg-blue-600 border-blue-600 text-white' : 'border-gray-300 bg-white text-transparent'"
                                class="w-4 h-4 rounded border-2 flex items-center justify-center shrink-0 transition-colors"
                                aria-hidden="true"
                            >
                                <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="4" d="M5 13l4 4L19 7" /></svg>
                            </span>
                            <div
                                class="ml-2.5 w-7 h-7 rounded-full bg-blue-50 text-blue-600 border border-blue-100 flex items-center justify-center font-bold text-[11px] shrink-0 group-hover:bg-blue-600 group-hover:text-white transition-colors"
                            >
                                {{ user.real_name?.[0] || user.user_name?.[0] }}
                            </div>
                            <div class="ml-3 flex-1 min-w-0">
                                <div class="font-bold text-gray-800 text-sm truncate flex items-center gap-1.5" :title="user.real_name || user.user_name">
                                    <span class="truncate">{{ user.real_name || user.user_name }}</span>
                                    <span
                                        v-if="isPendingRemoved(user.id)"
                                        class="shrink-0 px-1.5 py-0.5 text-[9px] font-bold rounded-full bg-amber-100 text-amber-600"
                                        title="本次移出的原有成员，保存后才会真正回到候选池"
                                    >已移出</span>
                                    <span
                                        v-if="isUserDisabled(user)"
                                        class="shrink-0 px-1.5 py-0.5 text-[9px] font-bold rounded-full bg-gray-200 text-gray-500"
                                        title="该用户已停用，分配后需先启用才能登录"
                                    >已停用</span>
                                </div>
                                <div class="text-[10px] text-gray-400 truncate font-mono" :title="`@${user.user_name}`">@{{ user.user_name }}</div>
                            </div>
                            <span class="shrink-0 text-[9px] text-gray-300 group-hover:text-blue-500 opacity-0 group-hover:opacity-100 transition-opacity whitespace-nowrap">双击加入</span>
                        </button>
                    </div>

                    <!-- Candidate Pagination：候选池按服务端分页，避免只加载前 1000 条 -->
                    <div
                        v-if="availableTotalPages > 1"
                        class="flex items-center justify-between gap-2 mt-2 pt-2 border-t border-gray-100 px-1 shrink-0"
                    >
                        <button
                            type="button"
                            @click="goAvailablePage(-1)"
                            :disabled="availablePage <= 1 || loadingUsers"
                            class="px-3 py-1.5 text-xs font-bold text-gray-600 bg-white border border-gray-200 rounded-lg hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                        >上一页</button>
                        <span class="text-[11px] text-gray-500 font-medium whitespace-nowrap">第 {{ availablePage }} / {{ availableTotalPages }} 页</span>
                        <div class="flex items-center gap-1.5">
                            <input
                                v-model="availablePageInput"
                                @keyup.enter="jumpToAvailablePage"
                                type="number"
                                min="1"
                                :max="availableTotalPages"
                                aria-label="跳转到指定页"
                                placeholder="页码"
                                class="w-16 px-2 py-1.5 text-xs text-center border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500"
                            />
                            <button
                                type="button"
                                @click="jumpToAvailablePage"
                                :disabled="!availablePageInput || loadingUsers"
                                class="px-2.5 py-1.5 text-xs font-bold text-gray-600 bg-white border border-gray-200 rounded-lg hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                            >跳转</button>
                        </div>
                        <button
                            type="button"
                            @click="goAvailablePage(1)"
                            :disabled="availablePage >= availableTotalPages || loadingUsers"
                            class="px-3 py-1.5 text-xs font-bold text-gray-600 bg-white border border-gray-200 rounded-lg hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                        >下一页</button>
                    </div>
                </div>

                <!-- Middle Transfer：只有勾选了才会点亮，点它才真正移动用户 -->
                <div class="flex flex-row lg:flex-col justify-center items-center gap-3 shrink-0 py-1 lg:py-0">
                    <button
                        type="button"
                        @click="moveCheckedToAssigned"
                        :disabled="candidateCheckedCount === 0"
                        :aria-label="candidateCheckedCount > 0 ? `把勾选的 ${candidateCheckedCount} 名用户加入已选` : '请先在左侧勾选用户'"
                        :title="candidateCheckedCount > 0 ? `把勾选的 ${candidateCheckedCount} 名用户加入已选` : '请先在左侧勾选用户'"
                        :class="candidateCheckedCount > 0
                            ? 'bg-blue-600 text-white shadow-lg shadow-blue-200 hover:bg-blue-700'
                            : 'bg-gray-100 text-gray-300 cursor-not-allowed'"
                        class="w-9 h-9 rounded-xl flex items-center justify-center transition-all active:scale-95"
                    >
                        <svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M13 5l7 7-7 7M5 5l7 7-7 7" /></svg>
                    </button>
                    <button
                        type="button"
                        @click="moveCheckedToCandidate"
                        :disabled="assignedCheckedCount === 0"
                        :aria-label="assignedCheckedCount > 0 ? `把勾选的 ${assignedCheckedCount} 名用户移出已选` : '请先在右侧勾选用户'"
                        :title="assignedCheckedCount > 0 ? `把勾选的 ${assignedCheckedCount} 名用户移出已选` : '请先在右侧勾选用户'"
                        :class="assignedCheckedCount > 0
                            ? 'bg-white text-blue-600 border border-blue-300 shadow-md hover:bg-blue-50'
                            : 'bg-gray-100 text-gray-300 cursor-not-allowed'"
                        class="w-9 h-9 rounded-xl flex items-center justify-center transition-all active:scale-95"
                    >
                        <svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M11 19l-7-7 7-7m8 14l-7-7 7-7" /></svg>
                    </button>
                </div>

                <!-- Right: Selected Users -->
                <div class="flex-1 flex flex-col min-h-0 bg-blue-50/30 rounded-xl border border-blue-100/50 p-2.5 relative overflow-hidden">
                    <div class="absolute inset-0 bg-grid-slate-100 [mask-image:linear-gradient(0deg,#fff,rgba(255,255,255,0.6))] pointer-events-none"></div>
                    <div class="flex items-center justify-between mb-3 px-1 relative z-10">
                        <div class="flex items-center gap-2">
                            <span class="text-[11px] font-black text-blue-500 uppercase tracking-widest whitespace-nowrap">已选用户 ·成员</span>
                            <span class="px-2 py-0.5 bg-blue-600 text-white text-[9px] font-bold rounded-full shadow-sm shadow-blue-200 shrink-0" aria-live="polite">{{ assignedUserIds.length }}</span>
                        </div>
                        <div class="flex items-center gap-2 shrink-0">
                            <button
                                v-if="assignedUsers.length > 0"
                                type="button"
                                @click="assignedCheckedCount > 0 ? clearAssignedSelection() : (selectedAssignedIds = assignedUsers.map(u => u.id))"
                                :class="assignedCheckedCount > 0 ? 'text-amber-600' : 'text-blue-500'"
                                class="text-[10px] font-bold hover:underline py-0.5 whitespace-nowrap"
                                :title="assignedCheckedCount > 0 ? `取消已勾选的 ${assignedCheckedCount} 名用户` : '勾选已选列表中的全部用户'"
                            >{{ assignedCheckedCount > 0 ? `取消勾选(${assignedCheckedCount})` : '全选' }}</button>
                            <button
                                v-if="assignedUserIds.length > 0"
                                type="button"
                                @click="requestRemoveAll"
                                class="text-[10px] font-bold text-red-500 hover:underline py-0.5 whitespace-nowrap"
                                :aria-label="`移除已选的 ${assignedUserIds.length} 名用户`"
                            >移除已选 {{ assignedUserIds.length }} 人</button>
                        </div>
                    </div>

                    <!-- 批量加入后的撤销入口：只回退本次批量新增的人 -->
                    <div
                        v-if="bulkAddedIds.length > 0"
                        class="flex items-center justify-between gap-2 mb-2 px-2.5 py-1.5 rounded-lg bg-amber-50 border border-amber-200 relative z-10"
                    >
                        <span class="text-[11px] font-medium text-amber-800 truncate">
                            刚批量加入 {{ bulkAddedIds.length }} 名用户
                        </span>
                        <button
                            type="button"
                            @click="requestUndoBulkAdd"
                            class="shrink-0 text-[11px] font-bold text-amber-700 hover:underline py-0.5"
                        >撤销</button>
                    </div>

                    <div v-if="assignedUserIds.length > 0" class="relative mb-2 shrink-0 z-10">
                        <input
                            v-model="assignedSearchQuery"
                            type="search"
                            aria-label="在已选成员中查找"
                            placeholder="在已选成员中查找..."
                            class="w-full bg-white border border-blue-100 rounded-xl px-3 py-2 pl-9 focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-all text-sm"
                        />
                        <svg class="w-4 h-4 text-blue-300 absolute left-3 top-2.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                        </svg>
                    </div>

                    <div class="flex-1 overflow-y-auto custom-scrollbar pr-1 space-y-1 relative z-10" role="list">
                        <!-- 加载中 / 加载失败：必须与“该角色没有成员”区分，否则容易误保存清空成员 -->
                        <div v-if="loadingAssigned" class="flex flex-col items-center justify-center h-full text-center py-16">
                            <div class="w-8 h-8 border-4 border-blue-500 border-t-transparent rounded-full animate-spin"></div>
                            <span class="text-xs text-blue-400/80 font-medium mt-3">正在加载角色成员...</span>
                        </div>
                        <div v-else-if="assignedLoadFailed" class="flex flex-col items-center justify-center h-full text-center py-16 px-4">
                            <div class="w-12 h-12 bg-red-50 text-red-400 rounded-2xl flex items-center justify-center mb-3">
                                <svg class="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z" /></svg>
                            </div>
                            <span class="text-xs text-red-500 font-medium">角色成员加载失败，为避免误清空成员，保存已禁用</span>
                            <button
                                type="button"
                                @click="currentRole && fetchRoleUsers(currentRole.id)"
                                class="mt-3 px-4 py-1.5 text-xs font-bold text-blue-600 border border-blue-200 rounded-lg hover:bg-blue-50 transition-colors"
                            >重新加载</button>
                        </div>
                        <div v-else-if="assignedUserIds.length === 0" class="flex flex-col items-center justify-center h-full text-center py-16">
                            <div class="w-12 h-12 bg-blue-100 text-blue-400 rounded-2xl flex items-center justify-center mb-3">
                                <svg class="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M12 4v16m8-8H4" /></svg>
                            </div>
                            <span class="text-xs text-blue-400/80 font-medium">在左侧勾选用户后点 » 加入</span>
                        </div>
                        <div
                            v-else-if="assignedUsers.length === 0"
                            class="text-center py-16 text-blue-400/80 text-xs italic"
                        >没有匹配「{{ assignedSearchQuery.trim() }}」的成员</div>
                        <button
                            v-for="user in assignedUsers"
                            :key="'sel-'+user.id"
                            type="button"
                            role="listitem"
                            @click="toggleAssignedChecked(user.id)"
                            @dblclick="moveOneToCandidate(user.id)"
                            :aria-pressed="isAssignedChecked(user.id)"
                            :aria-label="isAssignedChecked(user.id) ? `取消勾选 ${user.real_name || user.user_name}` : `勾选 ${user.real_name || user.user_name}（双击直接移出）`"
                            :class="isAssignedChecked(user.id)
                                ? 'border-blue-400 ring-2 ring-blue-500/20'
                                : 'border-blue-200 hover:border-red-300 ring-1 ring-blue-500/5'"
                            class="w-full text-left flex items-center p-2 rounded-lg bg-white transition-all cursor-pointer group active:scale-[0.99] hover:shadow-lg hover:shadow-red-500/5"
                            title="单击勾选，双击直接移出"
                        >
                            <span
                                :class="isAssignedChecked(user.id) ? 'bg-blue-600 border-blue-600 text-white' : 'border-gray-300 bg-white text-transparent'"
                                class="w-4 h-4 rounded border-2 flex items-center justify-center shrink-0 transition-colors"
                                aria-hidden="true"
                            >
                                <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="4" d="M5 13l4 4L19 7" /></svg>
                            </span>
                            <div class="ml-2.5 w-7 h-7 rounded-full bg-blue-600 text-white flex items-center justify-center font-bold text-[11px] shadow-md shadow-blue-200 group-hover:bg-red-500 group-hover:shadow-red-200 transition-colors">
                                {{ user.real_name?.[0] || user.user_name?.[0] }}
                            </div>
                            <div class="ml-3 flex-1 min-w-0">
                                <div class="font-bold text-gray-900 text-sm truncate flex items-center gap-1.5" :title="user.real_name || user.user_name">
                                    <span class="truncate">{{ user.real_name || user.user_name }}</span>
                                    <span
                                        v-if="isUserDisabled(user)"
                                        class="shrink-0 px-1.5 py-0.5 text-[9px] font-bold rounded-full bg-gray-200 text-gray-500"
                                        title="该用户已停用，分配后需先启用才能登录"
                                    >已停用</span>
                                </div>
                                <div class="text-[10px] text-blue-500/60 truncate font-mono group-hover:text-red-400">
                                    <span v-if="user.detail_missing" class="text-amber-500">详情未加载 · ID {{ user.id }}</span>
                                    <span v-else>@{{ user.user_name }}</span>
                                </div>
                            </div>
                            <svg class="w-4 h-4 text-gray-300 group-hover:text-red-500 transition-all opacity-0 group-hover:opacity-100" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M6 18L18 6M6 6l12 12" /></svg>
                        </button>
                    </div>
                </div>
            </div>

            <!-- Footer -->
            <div class="mt-2 flex flex-col sm:flex-row justify-between items-center gap-2 border-t border-gray-100 pt-3 px-1">
                <div class="flex items-center gap-4">
                    <div class="flex flex-col">
                        <span class="text-[10px] font-black text-gray-400 uppercase tracking-tighter">当前选择状态</span>
                        <span class="text-sm font-bold text-gray-800">
                            已选 {{ assignedUserIds.length }} 名用户 ·
                            <span class="text-gray-400">
                                {{ loadingAssigned ? '正在加载成员...' : `可分配 ${availableRemaining} 人` }}
                            </span>
                        </span>
                        <span v-if="candidateCheckedCount > 0" class="text-[11px] font-bold text-blue-600 mt-0.5" aria-live="polite">
                            已勾选 {{ candidateCheckedCount }} 人待加入，点 » 移动
                        </span>
                        <span v-else-if="assignedCheckedCount > 0" class="text-[11px] font-bold text-amber-600 mt-0.5" aria-live="polite">
                            已勾选 {{ assignedCheckedCount }} 人待移出，点 « 移动
                        </span>
                    </div>
                </div>
                <div class="flex gap-3 w-full sm:w-auto">
                    <button
                        @click="requestCloseUserAssignmentDialog"
                        :disabled="submittingUserAssignment"
                        class="flex-1 sm:flex-none px-5 py-2 border border-gray-200 rounded-xl hover:bg-gray-50 disabled:opacity-50 text-sm font-bold text-gray-500 transition-all"
                    >取消更改</button>
                    <button
                        @click="saveUserAssignments"
                        :disabled="!canSubmitAssignment"
                        :title="loadingAssigned ? '正在加载角色成员，请稍候' : (assignedLoadFailed ? '成员加载失败，请先重新加载' : '')"
                        class="flex-1 sm:flex-none px-8 py-2 bg-blue-600 text-white rounded-xl hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed text-sm font-black shadow-xl shadow-blue-200 transition-all active:scale-[0.98]"
                    >
                        <template v-if="submittingUserAssignment">正在同步...</template>
                        <template v-else-if="loadingAssigned">加载成员中...</template>
                        <template v-else>确认分配</template>
                    </button>
                </div>
            </div>
        </div>
    </div>

    <!-- Confirm Dialog：丢弃未保存更改 / 移除全部 / 批量加入 -->
    <div
        v-if="confirmDialog"
        class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-[9995]"
        @click.self="closeConfirmDialog"
    >
        <div class="bg-white rounded-xl p-6 w-full max-w-md shadow-2xl" role="dialog" aria-modal="true">
            <div class="flex items-start gap-3">
                <div
                    :class="confirmDialog.tone === 'danger' ? 'bg-red-50 text-red-500' : 'bg-blue-50 text-blue-500'"
                    class="w-10 h-10 rounded-xl flex items-center justify-center shrink-0"
                >
                    <svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z" />
                    </svg>
                </div>
                <div class="min-w-0">
                    <h3 class="text-base font-bold text-gray-900">{{ confirmDialog.title }}</h3>
                    <p class="text-sm text-gray-500 mt-1.5 leading-relaxed">{{ confirmDialog.message }}</p>
                </div>
            </div>
            <div class="flex justify-end gap-3 mt-6">
                <button
                    type="button"
                    @click="closeConfirmDialog"
                    class="px-4 py-2 border border-gray-300 rounded-lg hover:bg-gray-50 text-sm font-medium text-gray-600"
                >取消</button>
                <button
                    type="button"
                    @click="runConfirm"
                    :class="confirmDialog.tone === 'danger'
                        ? 'bg-red-600 hover:bg-red-700'
                        : 'bg-blue-600 hover:bg-blue-700'"
                    class="px-4 py-2 text-white rounded-lg text-sm font-bold transition-colors"
                >{{ confirmDialog.confirmText }}</button>
            </div>
        </div>
    </div>

    <!-- Delete Confirmation Dialog -->
    <div v-if="showDeleteDialog" class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-[9990]" @click.self="showDeleteDialog = false">
      <div class="bg-white rounded-lg p-6 w-full max-w-md">
        <h2 class="text-xl font-bold mb-4 text-red-600">确认删除</h2>
        <p class="text-gray-700 mb-6">确定要删除角色 <strong>{{ roleToDelete?.name }}</strong> 吗？<br><span class="text-sm text-gray-500">删除后，关联该角色的用户将失去相关权限。</span></p>
        <div class="flex justify-end gap-3">
          <button @click="showDeleteDialog = false" class="px-4 py-2 border border-gray-300 rounded-lg hover:bg-gray-50">
            取消
          </button>
          <button @click="deleteRole" class="px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700">
            确认删除
          </button>
        </div>
      </div>
    </div>

  </div>
</template>

<script setup lang="ts">
import { ref, shallowRef, onMounted, computed, onUnmounted } from 'vue'
import axios from '../utils/axios'
import { useToast } from '../composables/useToast'
import { MENU_TREE, getMenuDescendantIds } from '../constants/permissions'
import QuotaPolicyPanel from '../components/admin/QuotaPolicyPanel.vue'

const { showToast } = useToast()

const windowWidth = ref(window.innerWidth)
const isMobile = computed(() => windowWidth.value < 768)
const handleResize = () => { windowWidth.value = window.innerWidth }

onMounted(() => {
  window.addEventListener('resize', handleResize)
})
onUnmounted(() => {
  window.removeEventListener('resize', handleResize)
})

// State
const roles = ref<any[]>([])
const loading = ref(false)
const total = ref(0)
const page = ref(1)
const size = ref(20)
const totalPages = ref(0)

const searchQuery = ref('')
const isSearchEmpty = computed(
  () => searchQuery.value.trim().length > 0 && roles.value.length === 0
)

const isSystemRole = (role: { code?: string }) => {
  const code = (role.code || '').toLowerCase()
  return code === 'default' || code === 'admin' || code === 'system'
}

// 新增用户时会自动勾选 default 业务角色，这里负责探测其是否存在
const DEFAULT_ROLE_KEY = 'default'
const defaultRoleExists = ref(true) // 未确认前按存在处理，避免误报提示
const checkingDefaultRole = ref(false)
const initializingDefaultRole = ref(false)
const defaultRoleMissing = computed(
  () => !checkingDefaultRole.value && !defaultRoleExists.value
)

const matchesDefaultRole = (role: { code?: string; name?: string }) => {
  const code = String(role?.code ?? '').trim().toLowerCase()
  const name = String(role?.name ?? '').trim().toLowerCase()
  return code === DEFAULT_ROLE_KEY || name === DEFAULT_ROLE_KEY
}

// Dialogs
const showCreateDialog = ref(false)
const showEditDialog = ref(false)
const showDeleteDialog = ref(false)
const showPermissionDialog = ref(false)
const showUserAssignmentDialog = ref(false)
const roleToDelete = ref<any>(null)
const currentRole = ref<any>(null)

// Forms
const formData = ref({
    code: '',
    name: '',
    description: ''
})
const editingRoleId = ref<number | null>(null)
const submitting = ref(false)
const submittingPerms = ref(false)
const submittingUserAssignment = ref(false)
const error = ref('')

// User Assignment State
// 候选用户走服务端分页 + 搜索（不能再一次性拉 1000 条当全集，平台用户数已超过该上限）；
// 已选成员以角色成员接口返回的全量 ID 为准，详情单独缓存，两者范围必须一致。
const availableUsers = ref<any[]>([])
const availableTotal = ref(0)
const availablePage = ref(1)
const availableSize = ref(50)
const availablePageInput = ref('')
const loadingUsers = ref(false)
const selectingAllFiltered = ref(false)
const userSearchQuery = ref('')

const assignedUserIds = ref<number[]>([])
const assignedUserMap = ref<Record<number, any>>({})
const assignedSearchQuery = ref('')
// 「全选筛选结果」的撤销记录：只记本次批量新增的 ID，撤销时不会误删此前手动加的人
const bulkAddedIds = ref<number[]>([])
// 打开弹窗时的成员快照，用于判断「未保存变更」；成员加载失败时禁止提交，避免把成员清空
const assignedSnapshot = ref<number[]>([])
const loadingAssigned = ref(false)
const assignedLoadFailed = ref(false)
const USERS_PAGE_SIZE = 1000
// 「全选筛选结果」时最多拉取的页数，避免超大用户表把前端拖垮
const MAX_SELECT_ALL_PAGES = 20

// 统一确认弹窗（丢弃更改 / 移除全部 / 批量加入）
const confirmDialog = shallowRef<{
    title: string
    message: string
    confirmText: string
    tone: 'danger' | 'primary'
    onConfirm: () => void
} | null>(null)

const askConfirm = (options: {
    title: string
    message: string
    confirmText: string
    tone: 'danger' | 'primary'
    onConfirm: () => void
}) => {
    confirmDialog.value = options
}

const closeConfirmDialog = () => {
    confirmDialog.value = null
}

const runConfirm = () => {
    const action = confirmDialog.value?.onConfirm
    confirmDialog.value = null
    action?.()
}

// Permissions
const activeMainTab = ref<'assets' | 'ui' | 'quota'>('assets')
const activeResTab = ref<'agents' | 'datasets' | 'metadata' | 'apis' | 'forbidden_configs'>('agents')
const resourceTypes = ['agents', 'datasets', 'metadata', 'apis', 'forbidden_configs'] as const

const resourceConfig: Record<typeof resourceTypes[number], { label: string, color: string, tabActive: string, tabTextActive: string, cardSelected: string, icon: string }> = {
    agents: {
        label: '智能体',
        color: 'blue',
        tabActive: 'text-blue-600 border-blue-600 bg-blue-50',
        tabTextActive: 'text-blue-600',
        cardSelected: 'ring-1 ring-blue-400 border-blue-200 bg-blue-50',
        icon: `<svg fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" /></svg>`
    },
    datasets: {
        label: '知识库',
        color: 'green',
        tabActive: 'text-green-600 border-green-600 bg-green-50',
        tabTextActive: 'text-green-600',
        cardSelected: 'ring-1 ring-green-400 border-green-200 bg-green-50',
        icon: `<svg fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" /></svg>`
    },
    metadata: {
        label: '数据集',
        color: 'orange',
        tabActive: 'text-orange-600 border-orange-600 bg-orange-50',
        tabTextActive: 'text-orange-600',
        cardSelected: 'ring-1 ring-orange-400 border-orange-200 bg-orange-50',
        icon: `<svg fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4m0 5c0 2.21-3.582 4-8 4s-8-1.79-8-4" /></svg>`
    },
    apis: {
        label: 'API',
        color: 'purple',
        tabActive: 'text-purple-600 border-purple-600 bg-purple-50',
        tabTextActive: 'text-purple-600',
        cardSelected: 'ring-1 ring-purple-400 border-purple-200 bg-purple-50',
        icon: `<svg fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4" /></svg>`
    },
    forbidden_configs: {
        label: '禁用工具与命令',
        color: 'red',
        tabActive: 'text-red-600 border-red-600 bg-red-50',
        tabTextActive: 'text-red-600',
        cardSelected: 'ring-1 ring-red-400 border-red-200 bg-red-50',
        icon: `<svg fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M18.364 18.364A9 9 0 005.636 5.636m12.728 12.728A9 9 0 015.636 5.636m12.728 12.728L5.636 5.636" /></svg>`
    }
}
const loadingResources = ref(false)
const allResources = ref<{
    agents: any[],
    datasets: any[],
    metadata: any[],
    apis: any[]
}>({
    agents: [],
    datasets: [],
    metadata: [],
    apis: []
})
const permissionData = ref<{
    agents: string[],
    datasets: string[],
    metadata: string[],
    apis: string[],
    menus: string[],
    elements: string[],
    forbidden_tools: string[],
    forbidden_commands: string[]
}>({
    agents: [],
    datasets: [],
    metadata: [],
    apis: [],
    menus: [],
    elements: [],
    forbidden_tools: [],
    forbidden_commands: []
})

const forbiddenCommandsText = ref("")

const availableToolsToForbid = [
  { id: "exec_command", name: "执行终端命令 (exec_command)", description: "在沙箱中执行 Bash/shell 命令行" },
  { id: "write_file", name: "写入/修改文件 (write_file)", description: "对沙箱目录物理写入或修改代码文件" },
  { id: "read_file", name: "读取文件内容 (read_file)", description: "读取沙箱工作目录中的文本或源代码" },
  { id: "search_qa_examples", name: "检索经验库 (search_qa_examples)", description: "检索历史问答优质案例及 SQL" },
  { id: "manage_process", name: "进程管理 (manage_process)", description: "查看或终止沙箱中的系统进程" }
]

const otherAvailableTools = ref<any[]>([])
const toolSearchQuery = ref("")

const filteredOtherTools = computed(() => {
    const query = toolSearchQuery.value.trim().toLowerCase()
    if (!query) return otherAvailableTools.value
    return otherAvailableTools.value.filter(t =>
        t.name.toLowerCase().includes(query) ||
        t.description.toLowerCase().includes(query)
    )
})

const fetchAllSystemTools = async () => {
    try {
        const [resPortal, resMcp] = await Promise.all([
            axios.get("/api/portal/tools"),
            axios.get("/api/portal/tools/mcp").catch(() => ({ data: [] }))
        ])

        const dynamicMapped = (resPortal.data || []).map((t: any) => ({
            id: t.name,
            name: t.name,
            description: t.description || "自定义 API 工具",
            category: "api"
        }))

        const mcpMapped = (Array.isArray(resMcp.data) ? resMcp.data : (resMcp.data.data || [])).map((t: any) => ({
            id: t.name,
            name: t.name,
            description: t.description || "MCP 注册工具",
            category: "mcp"
        }))

        const forbiddenIds = new Set(["exec_command", "write_file", "read_file", "manage_process"])
        otherAvailableTools.value = [...dynamicMapped, ...mcpMapped].filter(t => !forbiddenIds.has(t.name))
    } catch (e) {
        console.error("Failed to fetch all tools for permission mapping", e)
    }
}

const isMissingKnowledgeBase = (res: any) => {
    if (activeResTab.value !== 'datasets') return false
    return Boolean(res?.is_missing_in_ragflow || res?.status === 'missing')
}

/**
 * 请求方法徽章配色。
 *
 * 仅「外部API」资源带 method 字段，其余 tab 不会渲染该徽章。
 */
const methodBadgeClass = (method: string) => {
    switch (String(method || '').toUpperCase()) {
        case 'GET': return 'bg-emerald-50 text-emerald-600'
        case 'POST': return 'bg-blue-50 text-blue-600'
        case 'PUT':
        case 'PATCH': return 'bg-amber-50 text-amber-600'
        case 'DELETE': return 'bg-rose-50 text-rose-600'
        default: return 'bg-gray-100 text-gray-500'
    }
}

const resCardActiveClass = (resId: string) => {
    const type = activeResTab.value
    const selected = (permissionData.value as any)[type]?.includes(resId)
    return selected ? resourceConfig[type].cardSelected : ''
}

const resCheckboxClass = () => {
    const map: Record<typeof resourceTypes[number], string> = {
        agents: 'text-blue-600 focus:ring-blue-500',
        datasets: 'text-green-600 focus:ring-green-500',
        metadata: 'text-orange-600 focus:ring-orange-500',
        apis: 'text-purple-600 focus:ring-purple-500',
        forbidden_configs: 'text-red-600 focus:ring-red-500',
    }
    return map[activeResTab.value]
}

// UI Tree Logic
const toggleTreeItem = (itemId: string) => {
    const isMenu = itemId.startsWith('menu:');
    const targetArray = isMenu ? permissionData.value.menus : permissionData.value.elements;

    if (isMenu) {
        const descendantIds = getMenuDescendantIds(itemId);
        const shouldSelect = !targetArray.includes(itemId);
        if (shouldSelect) {
            if (!targetArray.includes(itemId)) targetArray.push(itemId);
            descendantIds.forEach(id => {
                if (!permissionData.value.elements.includes(id)) permissionData.value.elements.push(id);
            });
        } else {
            const nextMenus = targetArray.filter(id => id !== itemId);
            permissionData.value.menus = nextMenus;
            permissionData.value.elements = permissionData.value.elements.filter(id => !descendantIds.includes(id));
        }
        return;
    }

    const idx = targetArray.indexOf(itemId);
    if (idx > -1) {
        targetArray.splice(idx, 1);
    } else {
        targetArray.push(itemId);
    }
}

const isMenuPartiallySelected = (menuId: string) => {
    const descendantIds = getMenuDescendantIds(menuId);
    const selectedCount = descendantIds.filter(id => permissionData.value.elements.includes(id)).length;
    return selectedCount > 0 && selectedCount < descendantIds.length;
}

const isItemSelected = (itemId: string) => {
    const isMenu = itemId.startsWith('menu:');
    const targetArray = isMenu ? permissionData.value.menus : permissionData.value.elements;
    return targetArray.includes(itemId);
}

// User Assignment Logic
const availableTotalPages = computed(
    () => Math.max(1, Math.ceil(availableTotal.value / availableSize.value))
)

const isUserDisabled = (user: any) => Number(user?.status) === 0

// 候选列表 = 还没被选中的用户：服务端已排除该角色的既有成员，
// 这里再排掉本次会话中刚勾选（还没保存）的人，保证左右两栏不会出现同一个人
// 本次被移出的“原有成员”：服务端此刻仍认为他是成员，候选池里没有他，
// 必须本地补回左栏，否则用户移出后想加回来会找不到人
const pendingRemovedUsers = computed(() => {
    const keyword = userSearchQuery.value.trim().toLowerCase()
    return assignedSnapshot.value
        .filter(id => !assignedUserIds.value.includes(id))
        .map(id => assignedUserMap.value[id] || {
            id,
            user_name: `#${id}`,
            real_name: `用户 #${id}`,
            detail_missing: true,
        })
        .filter((u: any) => !keyword
            || (u.user_name || '').toLowerCase().includes(keyword)
            || (u.real_name || '').toLowerCase().includes(keyword))
})

const isPendingRemoved = (userId: number) =>
    assignedSnapshot.value.includes(userId) && !assignedUserIds.value.includes(userId)

const filteredAvailableUsers = computed(() => {
    const base = availableUsers.value.filter(u => !assignedUserIds.value.includes(u.id))
    // 待移出的原成员不参与服务端分页，只在第一页补在顶部，避免每页重复出现
    if (availablePage.value !== 1 || pendingRemovedUsers.value.length === 0) return base
    const baseIds = new Set(base.map(u => u.id))
    return [...pendingRemovedUsers.value.filter(u => !baseIds.has(u.id)), ...base]
})

// 本地刚勾选（还没保存）的人数：他们仍在服务端候选池里，展示计数要扣掉；
// 本次移出的原成员保存后会回到候选池，先加回来
const pendingAddedCount = computed(
    () => assignedUserIds.value.filter(id => !assignedSnapshot.value.includes(id)).length
)
const pendingRemovedCount = computed(
    () => assignedSnapshot.value.filter(id => !assignedUserIds.value.includes(id)).length
)
const availableRemaining = computed(
    () => Math.max(0, availableTotal.value - pendingAddedCount.value + pendingRemovedCount.value)
)

const cacheAssignedUsers = (items: any[]) => {
    if (!items?.length) return
    const next = { ...assignedUserMap.value }
    items.forEach((u: any) => {
        if (u?.id != null) next[u.id] = u
    })
    assignedUserMap.value = next
}

// 已选列表由全量 ID 驱动：详情缺失时也要渲染占位行，
// 保证「徽标数字 == 列表行数」，不再出现数字与列表对不上的情况
const assignedUsers = computed(() => {
    const keyword = assignedSearchQuery.value.trim().toLowerCase()
    return assignedUserIds.value
        .map(id => assignedUserMap.value[id] || {
            id,
            user_name: `#${id}`,
            real_name: `用户 #${id}`,
            detail_missing: true,
        })
        .filter((u: any) => {
            if (!keyword) return true
            return (u.user_name || '').toLowerCase().includes(keyword)
                || (u.real_name || '').toLowerCase().includes(keyword)
        })
})

const normalizeIds = (ids: number[]) => [...ids].sort((a, b) => a - b).join(',')

const hasUnsavedChanges = computed(
    () => normalizeIds(assignedUserIds.value) !== normalizeIds(assignedSnapshot.value)
)

// 成员还在加载（或加载失败）时不允许提交：保存是整集替换语义，
// 若此时 assignedUserIds 仍是空的占位状态，点确认会把角色成员全部清空
const canSubmitAssignment = computed(
    () => !submittingUserAssignment.value && !loadingAssigned.value && !assignedLoadFailed.value
)

const clearBulkUndo = () => {
    bulkAddedIds.value = []
}

// 双列穿梭框：左右两栏各自维护“勾选态”，勾选不等于已经移动。
// 左栏勾选后必须点中间的 » 或双击条目，才会进入右侧已选列表。
const selectedCandidateIds = ref<number[]>([])
const selectedAssignedIds = ref<number[]>([])

const isCandidateChecked = (userId: number) => selectedCandidateIds.value.includes(userId)
const isAssignedChecked = (userId: number) => selectedAssignedIds.value.includes(userId)
const candidateCheckedCount = computed(() => selectedCandidateIds.value.length)
const assignedCheckedCount = computed(() => selectedAssignedIds.value.length)

// 勾选 / 取消勾选左栏条目（仅改变勾选态，不移动）
const toggleCandidateChecked = (userId: number) => {
    const idx = selectedCandidateIds.value.indexOf(userId)
    if (idx > -1) {
        selectedCandidateIds.value.splice(idx, 1)
    } else {
        selectedCandidateIds.value.push(userId)
    }
}

const toggleAssignedChecked = (userId: number) => {
    const idx = selectedAssignedIds.value.indexOf(userId)
    if (idx > -1) {
        selectedAssignedIds.value.splice(idx, 1)
    } else {
        selectedAssignedIds.value.push(userId)
    }
}

const clearCandidateSelection = () => {
    selectedCandidateIds.value = []
}

const clearAssignedSelection = () => {
    selectedAssignedIds.value = []
}

// 已勾选的候选详情（当前页能拿到的部分），移动时用来填充右侧展示
const collectCheckedCandidateDetails = () =>
    filteredAvailableUsers.value.filter(u => selectedCandidateIds.value.includes(u.id))

// » ：把左栏勾选的用户移入右侧已选列表
const moveCheckedToAssigned = () => {
    const moving = [...selectedCandidateIds.value]
    if (moving.length === 0) return
    cacheAssignedUsers(collectCheckedCandidateDetails())
    const merged = new Set(assignedUserIds.value)
    moving.forEach(id => merged.add(id))
    assignedUserIds.value = Array.from(merged)
    // 记录撤销范围：只记本次真正新增的，撤销时不会误删此前已有的人
    const before = new Set(assignedSnapshot.value)
    bulkAddedIds.value = moving.filter(id => !before.has(id))
    selectedCandidateIds.value = []
    showToast(`已加入 ${moving.length} 名用户`, 'success')
}

// « ：把右侧勾选的用户移回左侧候选
const moveCheckedToCandidate = () => {
    const moving = [...selectedAssignedIds.value]
    if (moving.length === 0) return
    const removing = new Set(moving)
    assignedUserIds.value = assignedUserIds.value.filter(id => !removing.has(id))
    selectedAssignedIds.value = []
    clearBulkUndo()
    showToast(`已移出 ${moving.length} 名用户`, 'success')
}

// 双击直接移动单个条目
const moveOneToAssigned = (userId: number) => {
    if (assignedUserIds.value.includes(userId)) return
    cacheAssignedUsers(filteredAvailableUsers.value.filter(u => u.id === userId))
    assignedUserIds.value = [...assignedUserIds.value, userId]
    const idx = selectedCandidateIds.value.indexOf(userId)
    if (idx > -1) selectedCandidateIds.value.splice(idx, 1)
    if (!assignedSnapshot.value.includes(userId)) {
        bulkAddedIds.value = [userId]
    }
}

const moveOneToCandidate = (userId: number) => {
    assignedUserIds.value = assignedUserIds.value.filter(id => id !== userId)
    const idx = selectedAssignedIds.value.indexOf(userId)
    if (idx > -1) selectedAssignedIds.value.splice(idx, 1)
    clearBulkUndo()
}

// 撤销上一次批量移入：「只回退那次真正新增的人」，保留原本已有的成员
const undoBulkAdd = () => {
    const count = bulkAddedIds.value.length
    if (count === 0) return
    const toRemove = new Set(bulkAddedIds.value)
    assignedUserIds.value = assignedUserIds.value.filter(id => !toRemove.has(id))
    clearBulkUndo()
    showToast(`已撤销批量加入的 ${count} 名用户`, 'success')
}

const requestUndoBulkAdd = () => {
    const count = bulkAddedIds.value.length
    if (count === 0) return
    askConfirm({
        title: '撤销批量加入',
        message: `将从已选列表中移除本次移入的 ${count} 名用户，此前已在该角色中的成员保持不变。确定继续吗？`,
        confirmText: `移除 ${count} 人`,
        tone: 'danger',
        onConfirm: undoBulkAdd,
    })
}

// 勾选当前页全部候选（只是勾选，不移动）
const checkAllVisible = () => {
    const pageIds = filteredAvailableUsers.value.map(u => u.id)
    const merged = new Set([...selectedCandidateIds.value, ...pageIds])
    selectedCandidateIds.value = Array.from(merged)
}

const removeAll = () => {
    clearBulkUndo()
    selectedAssignedIds.value = []
    assignedUserIds.value = []
}

const requestRemoveAll = () => {
    const count = assignedUserIds.value.length
    if (count === 0) return
    const originalCount = assignedSnapshot.value.length
    let message = originalCount > 0
        ? `将把已选的 ${count} 名用户全部移出该角色（其中 ${originalCount} 名是该角色原本的成员），保存后生效。`
        : `将把已选的 ${count} 名用户全部移出该角色，保存后生效。`
    if (bulkAddedIds.value.length > 0) {
        message += ' 只想撤销刚才批量移入的人，请改用上方的「撤销」。'
    }
    askConfirm({
        title: '移除全部已选用户',
        message,
        confirmText: `移除 ${count} 人`,
        tone: 'danger',
        onConfirm: removeAll,
    })
}

// 「全选筛选结果」只是勾选，不移动用户，所以不需要二次确认
const requestSelectAllFiltered = () => {
    if (availableRemaining.value === 0) return
    selectAllFiltered()
}

// 跨页收集当前筛选条件下的用户（含详情）。
// `excludeRoleMembers`：加入类操作传 true（范围与左侧候选一致）；
// 取消类操作传 false —— 已选成员已被左侧排除，必须查全量才找得回它们。
// 页数上限 MAX_SELECT_ALL_PAGES，超出时 complete=false，由调用方提示用户缩小范围。
const collectFilteredUsers = async (excludeRoleMembers: boolean) => {
    const items: any[] = []
    let page = 1
    let total = availableTotal.value
    while (page <= MAX_SELECT_ALL_PAGES) {
        const params: any = { page, size: USERS_PAGE_SIZE }
        const keyword = userSearchQuery.value.trim()
        if (keyword) params.search = keyword
        if (excludeRoleMembers && currentRole.value?.id) {
            params.exclude_role_id = currentRole.value.id
        }
        const response = await axios.get('/api/portal/management/users', { params })
        const pageItems: any[] = response.data.items || []
        total = response.data.total ?? 0
        items.push(...pageItems)
        if (pageItems.length === 0 || items.length >= total) break
        page += 1
    }
    return { items, total, complete: items.length >= total }
}

// 全选筛选结果：只是把筛选结果全部勾选上，不会移动任何用户
const selectAllFiltered = async () => {
    if (selectingAllFiltered.value) return
    selectingAllFiltered.value = true
    try {
        const { items, total, complete } = await collectFilteredUsers(true)
        cacheAssignedUsers(items)
        const merged = new Set([...selectedCandidateIds.value, ...items.map((u: any) => u.id)])
        selectedCandidateIds.value = Array.from(merged)

        if (!complete) {
            showToast(
                `批量勾选已达上限（${items.length}/${total}），请用搜索缩小范围后再试`,
                'warning',
            )
        } else {
            showToast(`已勾选 ${items.length} 名用户，点 » 或双击即可加入`, 'success')
        }
    } catch (e) {
        console.error('Select All Filtered Failed', e)
        showToast('批量勾选失败', 'error')
    } finally {
        selectingAllFiltered.value = false
    }
}

// 「取消全选」：只清空左栏的勾选态，不移动、也不移除任何成员，因此无需确认
const deselectAllFiltered = () => {
    const count = selectedCandidateIds.value.length
    clearCandidateSelection()
    if (count > 0) {
        showToast(`已取消 ${count} 名用户的勾选`, 'success')
    }
}

const openUserAssignmentDialog = async (role: any) => {
    currentRole.value = role
    showUserAssignmentDialog.value = true
    userSearchQuery.value = ''
    assignedSearchQuery.value = ''
    assignedUserIds.value = []
    assignedUserMap.value = {}
    assignedSnapshot.value = []
    loadingAssigned.value = true
    assignedLoadFailed.value = false
    clearBulkUndo()
    clearCandidateSelection()
    clearAssignedSelection()
    availableUsers.value = []
    availableTotal.value = 0
    availablePage.value = 1
    availablePageInput.value = ''

    await Promise.all([
        fetchAvailableUsers(),
        fetchRoleUsers(role.id)
    ])
}

const fetchAvailableUsers = async () => {
    loadingUsers.value = true
    try {
        const params: any = {
            page: availablePage.value,
            size: availableSize.value,
        }
        const keyword = userSearchQuery.value.trim()
        if (keyword) params.search = keyword
        // 候选池 = 还没属于该角色的用户，交给后端排除（分页下 total 才准确）
        if (currentRole.value?.id) {
            params.exclude_role_id = currentRole.value.id
        }

        const response = await axios.get('/api/portal/management/users', { params })
        availableUsers.value = response.data.items || []
        availableTotal.value = response.data.total || 0
    } catch (e) {
        console.error('Fetch Users Failed', e)
        availableUsers.value = []
        availableTotal.value = 0
        showToast('获取用户列表失败', 'error')
    } finally {
        loadingUsers.value = false
    }
}

let userSearchTimeout: any = null
const debouncedUserSearch = () => {
    clearTimeout(userSearchTimeout)
    userSearchTimeout = setTimeout(() => {
        availablePage.value = 1
        availablePageInput.value = ''
        // 筛选口径变化后，上一次批量的撤销范围不再对应
        clearBulkUndo()
        fetchAvailableUsers()
    }, 300)
}

const goAvailablePage = (delta: number) => {
    const next = availablePage.value + delta
    if (next < 1 || next > availableTotalPages.value) return
    availablePage.value = next
    fetchAvailableUsers()
}

const jumpToAvailablePage = () => {
    const parsed = Number.parseInt(availablePageInput.value, 10)
    if (!Number.isFinite(parsed)) return
    const target = Math.min(Math.max(parsed, 1), availableTotalPages.value)
    availablePageInput.value = ''
    if (target === availablePage.value) return
    availablePage.value = target
    fetchAvailableUsers()
}

const fetchRoleUsers = async (roleId: number) => {
    loadingAssigned.value = true
    assignedLoadFailed.value = false
    try {
        // user_ids 是全量成员 ID（保存按整集替换，必须完整）；
        // items 是成员详情，取一页用于展示，超出部分由占位行兜底
        const response = await axios.get(`/api/portal/roles/${roleId}/users`, {
            params: { page: 1, size: USERS_PAGE_SIZE }
        })
        assignedUserIds.value = response.data.user_ids || []
        assignedSnapshot.value = [...assignedUserIds.value]
        cacheAssignedUsers(response.data.items || [])
    } catch (e) {
        console.error('Fetch Role Users Failed', e)
        // 加载失败必须显式失败：否则界面看起来像“该角色没有成员”，
        // 管理员点确认就会把成员清空
        assignedLoadFailed.value = true
        assignedUserIds.value = []
        assignedSnapshot.value = []
        showToast('获取角色成员失败，请重试', 'error')
    } finally {
        loadingAssigned.value = false
    }
}

const saveUserAssignments = async () => {
    if (!currentRole.value) return
    // 成员未加载完（或加载失败）时提交会把成员清空，这里再兜一层
    if (loadingAssigned.value || assignedLoadFailed.value) return
    submittingUserAssignment.value = true
    try {
        const count = assignedUserIds.value.length
        await axios.post(
            `/api/portal/roles/${currentRole.value.id}/users`,
            { user_ids: assignedUserIds.value }
        )
        showToast(`已保存，当前共 ${count} 名成员`, 'success')
        closeUserAssignmentDialog()
        fetchRoles() // Refresh list to update user_count
    } catch (e: any) {
        showToast(e.response?.data?.detail || '保存失败', 'error')
    } finally {
        submittingUserAssignment.value = false
    }
}

const closeUserAssignmentDialog = () => {
    showUserAssignmentDialog.value = false
    currentRole.value = null
    confirmDialog.value = null
}

// X / 遮罩 / 「取消更改」统一走这里：有未保存改动时先确认，避免误关丢改动
const requestCloseUserAssignmentDialog = () => {
    if (submittingUserAssignment.value) return
    if (!hasUnsavedChanges.value) {
        closeUserAssignmentDialog()
        return
    }
    askConfirm({
        title: '放弃未保存的更改',
        message: `你对「${currentRole.value?.name || '该角色'}」的成员调整尚未保存，关闭后将丢失。确定放弃吗？`,
        confirmText: '放弃更改',
        tone: 'danger',
        onConfirm: closeUserAssignmentDialog,
    })
}

// Computed
const currentResources = computed(() => {
    if (activeResTab.value === 'forbidden_configs') return []
    return allResources.value[activeResTab.value] || []
})

const selectableResources = computed(() =>
    currentResources.value.filter((r: any) => !isMissingKnowledgeBase(r))
)

const isAllSelected = computed(() => {
    const current = selectableResources.value
    if (current.length === 0) return false
    if (activeResTab.value === 'forbidden_configs') return false
    const selected = permissionData.value[activeResTab.value]
    return current.every((r: any) => selected.includes(r.id))
})

// Actions

const fetchRoles = async () => {
    loading.value = true
    try {
        const params: any = { page: page.value, size: size.value }
        if (searchQuery.value) params.search = searchQuery.value

        const response = await axios.get('/api/portal/roles', {
            params
        })
        roles.value = response.data.items
        total.value = response.data.total
        totalPages.value = Math.ceil(total.value / size.value)
    } catch (e: any) {
        console.error('Fetch Roles Error:', e)
        showToast('获取角色列表失败', 'error')
    } finally {
        loading.value = false
    }
}

let searchTimeout: any = null
const debouncedSearch = () => {
    clearTimeout(searchTimeout)
    searchTimeout = setTimeout(() => {
        page.value = 1
        fetchRoles()
    }, 500)
}

const checkDefaultRole = async () => {
    checkingDefaultRole.value = true
    try {
        // 角色列表是分页的，这里按关键词检索后再精确匹配，避免只凭当前页误判
        const response = await axios.get('/api/portal/roles', {
            params: { page: 1, size: 1000, search: DEFAULT_ROLE_KEY }
        })
        const items: any[] = response.data?.items || []
        defaultRoleExists.value = items.some(matchesDefaultRole)
    } catch (e: any) {
        // 探测失败时不做提示，避免误报
        console.error('Check Default Role Error:', e)
        defaultRoleExists.value = true
    } finally {
        checkingDefaultRole.value = false
    }
}

const initializeDefaultRole = async () => {
    if (initializingDefaultRole.value) return
    initializingDefaultRole.value = true
    try {
        await axios.post('/api/portal/roles', {
            code: DEFAULT_ROLE_KEY,
            name: DEFAULT_ROLE_KEY,
            description: '新增用户时默认勾选的角色（初始不含任何权限）'
        })
        defaultRoleExists.value = true
        showToast('default 角色初始化成功，请按需分配权限', 'success')
        page.value = 1
        await fetchRoles()
    } catch (e: any) {
        const status = e.response?.status
        if (status === 400) {
            // 已存在（例如多人同时初始化）时按已存在处理，不当作失败
            defaultRoleExists.value = true
            showToast('default 角色已存在', 'warning')
            await fetchRoles()
        } else {
            showToast(e.response?.data?.detail || '初始化 default 角色失败', 'error')
        }
    } finally {
        initializingDefaultRole.value = false
    }
}

const resetFilters = () => {
    searchQuery.value = ''
    page.value = 1
    fetchRoles()
}

const openCreateDialog = () => {
    formData.value = { code: '', name: '', description: '' }
    showCreateDialog.value = true
}

const editRole = (role: any) => {
    editingRoleId.value = role.id
    formData.value = {
        code: role.code,
        name: role.name,
        description: role.description
    }
    showEditDialog.value = true
}

const saveRole = async () => {
    if (!formData.value.code || !formData.value.name) {
        error.value = '代码和名称必填'
        return
    }
    submitting.value = true
    error.value = ''

    try {
        if (showEditDialog.value && editingRoleId.value) {
            await axios.put(`/api/portal/roles/${editingRoleId.value}`, formData.value)
            showToast('更新成功', 'success')
        } else {
            await axios.post('/api/portal/roles', formData.value)
            showToast('创建成功', 'success')
        }
        closeDialogs()
        fetchRoles()
        // 新建/重命名可能正好补上或改掉 default 角色，重新探测提示层
        checkDefaultRole()
    } catch (e: any) {
        error.value = e.response?.data?.detail || '操作失败'
    } finally {
        submitting.value = false
    }
}

const confirmDelete = (role: any) => {
    roleToDelete.value = role
    showDeleteDialog.value = true
}

const deleteRole = async () => {
    if (!roleToDelete.value) return
    try {
        await axios.delete(`/api/portal/roles/${roleToDelete.value.id}`)
        showToast('删除成功', 'success')
        showDeleteDialog.value = false
        fetchRoles()
        // 删掉的若是 default 角色，需要重新提示初始化
        checkDefaultRole()
    } catch (e: any) {
        showToast(e.response?.data?.detail || '删除失败', 'error')
    }
}

const openPermissionDialog = async (role: any) => {
    currentRole.value = role
    showPermissionDialog.value = true
    activeMainTab.value = 'assets'
    await Promise.all([
        fetchResources(),
        fetchRolePermissions(role.id)
    ])
}

const fetchResources = async () => {
    if (loadingResources.value) return
    loadingResources.value = true
    try {
        const results = await Promise.allSettled([
            axios.get('/api/portal/ragflow/datasets', { params: { page_size: 100 } }),
            axios.get('/api/portal/management/resources/available')
        ])

         const handleResult = (result: PromiseSettledResult<any>) => result.status === 'fulfilled' ? result.value.data : null
         const extractItems = (data: any) => Array.isArray(data) ? data : (data?.data || [])

         const ragDatasets = extractItems(handleResult(results[0]))
         const available = handleResult(results[1])

         allResources.value.datasets = ragDatasets
         // 失联知识库不可再分配：从当前勾选中剔除
         const missingIds = new Set(
           (ragDatasets || [])
             .filter((d: any) => d?.is_missing_in_ragflow || d?.status === 'missing')
             .map((d: any) => d.id)
         )
         if (missingIds.size > 0) {
           permissionData.value.datasets = (permissionData.value.datasets || []).filter(
             (id: string) => !missingIds.has(id)
           )
         }

         if (available) {
             allResources.value.agents = available.agents || []
             allResources.value.metadata = (available.metadata || []).map((m: any) => ({ ...m, id: String(m.id) }))
             allResources.value.apis = available.apis || []
         }
    } catch (e) {
        console.error('Fetch Resources Error', e)
    } finally {
        loadingResources.value = false
    }
}

const fetchRolePermissions = async (roleId: number) => {
     try {
        const response = await axios.get(`/api/portal/roles/${roleId}/permissions`)
        const perms = response.data.permissions
        const missingIds = new Set(
            (allResources.value.datasets || [])
                .filter((d: any) => d?.is_missing_in_ragflow || d?.status === 'missing')
                .map((d: any) => d.id)
        )
        permissionData.value = {
            agents: perms.agents || [],
            datasets: (perms.datasets || []).filter((id: string) => !missingIds.has(id)),
            metadata: perms.metadata || [],
            apis: perms.apis || [],
            menus: perms.menus || [],
            elements: perms.elements || [],
            forbidden_tools: perms.forbidden_tools || [],
            forbidden_commands: perms.forbidden_commands || []
        }
        forbiddenCommandsText.value = (perms.forbidden_commands || []).join(", ")
    } catch (e) {
        console.error('Fetch Permissions Failed', e)
        permissionData.value = {
            agents: [],
            datasets: [],
            metadata: [],
            apis: [],
            menus: [],
            elements: [],
            forbidden_tools: [],
            forbidden_commands: []
        }
        forbiddenCommandsText.value = ""
    }
}

const savePermissions = async () => {
    if (!currentRole.value) return
    submittingPerms.value = true
    try {
        const missingIds = new Set(
            (allResources.value.datasets || [])
                .filter((d: any) => d?.is_missing_in_ragflow || d?.status === 'missing')
                .map((d: any) => d.id)
        )
        permissionData.value.forbidden_commands = forbiddenCommandsText.value
          .split(",")
          .map(cmd => cmd.trim())
          .filter(cmd => cmd.length > 0)

        const payload = {
            ...permissionData.value,
            datasets: (permissionData.value.datasets || []).filter((id: string) => !missingIds.has(id)),
        }
        await axios.put(
            `/api/portal/roles/${currentRole.value.id}/permissions`,
            payload
        )
        showToast('权限保存成功', 'success')
        closePermissionDialog()
    } catch (e: any) {
        showToast('保存权限失败', 'error')
    } finally {
        submittingPerms.value = false
    }
}

const toggleSelectAll = () => {
    const type = activeResTab.value
    if (type === 'forbidden_configs') return
    if (isAllSelected.value) {
        permissionData.value[type] = []
    } else {
        permissionData.value[type] = selectableResources.value.map((r: any) => r.id)
    }
}

const closeDialogs = () => {
    showCreateDialog.value = false
    showEditDialog.value = false
    error.value = ''
    editingRoleId.value = null
}

const closePermissionDialog = () => {
    showPermissionDialog.value = false
    currentRole.value = null
}

const formatDate = (dateStr: string) => {
    if (!dateStr) return '-'
    return new Date(dateStr).toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

onMounted(() => {
    fetchRoles()
    fetchAllSystemTools()
    checkDefaultRole()
})

</script>
