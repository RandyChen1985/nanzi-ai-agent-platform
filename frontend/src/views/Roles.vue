<template>
  <div class="space-y-6">
    <!-- Header -->
    <div class="flex justify-between items-center">
      <h1 class="text-2xl font-bold text-gray-900">角色管理</h1>
      <button
        @click="openCreateDialog"
        class="bg-blue-600 text-white px-4 py-2 rounded-lg hover:bg-blue-700 transition flex items-center gap-2"
      >
        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4" />
        </svg>
        创建角色
      </button>
    </div>

    <!-- Filters -->
    <div class="bg-white shadow rounded-lg p-4">
      <div class="flex flex-col sm:flex-row gap-4">
        <input
          v-model="searchQuery"
          @input="debouncedSearch"
          type="text"
          placeholder="搜索角色名称或代码..."
          class="border border-gray-300 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500 flex-1 text-sm"
        />
        <button
          @click="resetFilters"
          class="border border-gray-300 rounded-lg px-4 py-2 hover:bg-gray-50 transition text-sm font-medium"
        >
          重置
        </button>
      </div>
    </div>

    <!-- Loading -->
    <div v-if="loading" class="bg-white shadow rounded-lg p-12 text-center">
      <div class="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
      <p class="mt-2 text-gray-500">加载中...</p>
    </div>

    <!-- Role List -->
    <div v-else-if="roles.length > 0">
      <!-- Desktop Table -->
      <div v-if="!isMobile" class="bg-white shadow rounded-lg overflow-hidden">
        <div class="overflow-x-auto">
          <table class="min-w-full divide-y divide-gray-200">
            <thead class="bg-gray-50">
              <tr>
                <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider whitespace-nowrap">ID</th>
                <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider whitespace-nowrap">角色代码</th>
                <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider whitespace-nowrap">角色名称</th>
                <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider whitespace-nowrap">描述</th>
                <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider whitespace-nowrap">用户数</th>
                <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider whitespace-nowrap">创建时间</th>
                <th class="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider whitespace-nowrap">操作</th>
              </tr>
            </thead>
            <tbody class="bg-white divide-y divide-gray-200">
              <tr v-for="role in roles" :key="role.id" class="hover:bg-gray-50">
                <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-900">{{ role.id }}</td>
                <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-900 font-mono">{{ role.code }}</td>
                <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-900 font-medium">{{ role.name }}</td>
                <td class="px-6 py-4 text-sm text-gray-500 max-w-xs truncate" :title="role.description">
                  {{ role.description || '-' }}
                </td>
                <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                  <span class="bg-gray-100 text-gray-700 px-2 py-0.5 rounded-full text-xs font-medium">{{ role.user_count }}</span>
                </td>
                <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-500">{{ formatDate(role.created_at) }}</td>
                <td class="px-6 py-4 whitespace-nowrap text-right text-sm font-medium">
                  <div class="flex justify-end items-center gap-3">
                     <button @click="editRole(role)" class="text-blue-600 hover:text-blue-900 transition-colors p-1" title="编辑角色">
                      <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"></path></svg>
                    </button>
                     <button @click="openPermissionDialog(role)" class="text-indigo-600 hover:text-indigo-900 transition-colors p-1" title="分配权限">
                      <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"></path></svg>
                    </button>
                    <button @click="openUserAssignmentDialog(role)" class="text-green-600 hover:text-green-900 transition-colors p-1" title="分配用户">
                      <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4.354a4 4 0 110 5.292M15 21H3v-1a6 6 0 0112 0v1zm0 0h6v-1a6 6 0 00-9-5.197M13 7a4 4 0 11-8 0 4 4 0 018 0z" /></svg>
                    </button>
                    <button
                      @click="confirmDelete(role)"
                      class="text-red-600 hover:text-red-900 transition-colors p-1"
                      title="删除角色"
                    >
                      <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"></path></svg>
                    </button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- Mobile Card List -->
      <div v-else class="space-y-4">
        <div v-for="role in roles" :key="role.id" class="bg-white shadow rounded-lg p-4 space-y-3">
          <div class="flex justify-between items-start">
            <div class="flex-1 min-w-0">
              <h3 class="text-base font-bold text-gray-900 truncate">{{ role.name }}</h3>
              <p class="text-xs font-mono text-gray-500">{{ role.code }}</p>
            </div>
            <div class="bg-blue-50 text-blue-700 px-2 py-0.5 rounded-full text-[10px] font-bold border border-blue-100 whitespace-nowrap">
              {{ role.user_count }} 用户
            </div>
          </div>

          <p class="text-[11px] text-gray-600 line-clamp-2 leading-relaxed bg-gray-50 p-2 rounded border border-gray-100">
            {{ role.description || '暂无描述' }}
          </p>

          <div class="flex justify-between items-center pt-2 border-t border-gray-50">
            <span class="text-[10px] text-gray-400">ID: {{ role.id }} · {{ formatDate(role.created_at).split(' ')[0] }}</span>
            <div class="flex gap-3">
              <button @click="editRole(role)" class="text-blue-600 flex items-center gap-1 text-xs font-medium">
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"></path></svg>
                编辑
              </button>
              <button @click="openPermissionDialog(role)" class="text-indigo-600 flex items-center gap-1 text-xs font-medium">
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"></path></svg>
                权限
              </button>
              <button @click="openUserAssignmentDialog(role)" class="text-green-600 flex items-center gap-1 text-xs font-medium">
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4.354a4 4 0 110 5.292M15 21H3v-1a6 6 0 0112 0v1zm0 0h6v-1a6 6 0 00-9-5.197M13 7a4 4 0 11-8 0 4 4 0 018 0z" /></svg>
                用户
              </button>
              <button @click="confirmDelete(role)" class="text-red-600 flex items-center gap-1 text-xs font-medium">
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"></path></svg>
                删除
              </button>
            </div>
          </div>
        </div>
      </div>

      <!-- Pagination -->
      <div class="bg-white sm:bg-gray-50 px-4 py-3 flex flex-col sm:flex-row items-center justify-between border-t border-gray-200 mt-4 rounded-lg">
        <div class="text-sm text-gray-700 mb-3 sm:mb-0">
          共 {{ total }} 条，第 {{ page }}/{{ totalPages }} 页
        </div>
        <div class="flex gap-2 w-full sm:w-auto">
          <button @click="page > 1 && (page--, fetchRoles())" :disabled="page <= 1" class="flex-1 sm:flex-none px-4 py-2 border border-gray-300 rounded-lg hover:bg-gray-200 disabled:opacity-50 text-sm font-medium">上一页</button>
          <button @click="page < totalPages && (page++, fetchRoles())" :disabled="page >= totalPages" class="flex-1 sm:flex-none px-4 py-2 border border-gray-300 rounded-lg hover:bg-gray-200 disabled:opacity-50 text-sm font-medium">下一页</button>
        </div>
      </div>
    </div>

    <!-- Empty State -->
    <div v-else class="bg-white shadow rounded-lg p-12 text-center">
      <h3 class="mt-2 text-sm font-medium text-gray-900">没有找到角色</h3>
      <p class="mt-1 text-sm text-gray-500">尝试调整筛选条件或创建新角色</p>
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

                        <div v-if="loadingResources" class="text-center py-10 text-gray-500 text-xs">加载资源中...</div>
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
                                        type="text"
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
                                          v-if="isMissingKnowledgeBase(res)"
                                          class="flex-shrink-0 text-[9px] px-1.5 py-0.5 rounded-full bg-amber-100 text-amber-700 font-semibold leading-none"
                                        >失联</span>
                                    </div>
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
                                        <input
                                            type="checkbox"
                                            :checked="isItemSelected(menu.id)"
                                            @change="toggleTreeItem(menu.id)"
                                            class="h-4 w-4 text-blue-600 rounded border-gray-300 focus:ring-blue-500 cursor-pointer"
                                        />
                                        <span class="font-black text-gray-800 text-xs sm:text-sm">{{ menu.label }}</span>
                                    </div>
                                </div>

                                <!-- Children Elements -->
                                <div v-if="menu.children && menu.children.length > 0" class="p-3 grid grid-cols-1 xs:grid-cols-2 gap-2">
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
    <div v-if="showUserAssignmentDialog" class="fixed inset-0 bg-black bg-opacity-60 flex items-center justify-center z-[9990] backdrop-blur-sm" @click.self="closeUserAssignmentDialog">
        <div
            class="bg-white flex flex-col shadow-2xl transition-all duration-500 overflow-hidden"
            :class="isMobile ? 'w-full h-full rounded-none' : 'rounded-3xl p-6 w-full max-w-4xl max-h-[90vh]'"
        >
            <!-- Header -->
            <div class="px-4 py-4 sm:px-2 sm:pt-0 border-b border-gray-100 sm:border-0 flex justify-between items-center mb-4">
                <div>
                    <h2 class="text-2xl font-black text-gray-900 tracking-tight">分配角色用户</h2>
                    <p class="text-xs text-gray-500 mt-1 flex items-center gap-1.5">
                        <span class="inline-block w-2 h-2 rounded-full bg-blue-500"></span>
                        当前角色: <span class="font-bold text-gray-800 bg-gray-100 px-2 py-0.5 rounded">{{ currentRole?.name }} ({{ currentRole?.code }})</span>
                    </p>
                </div>
                <button @click="closeUserAssignmentDialog" class="w-10 h-10 flex items-center justify-center rounded-full hover:bg-gray-100 text-gray-400 transition-all">
                    <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M6 18L18 6M6 6l12 12" /></svg>
                </button>
            </div>

            <!-- Dual Column Content -->
            <div class="flex-1 flex flex-col sm:flex-row gap-6 min-h-0 px-2">
                <!-- Left: Available Users -->
                <div class="flex-1 flex flex-col min-h-0 bg-gray-50/50 rounded-2xl border border-gray-100 p-4">
                    <div class="flex items-center justify-between mb-3 px-1">
                        <div class="flex items-center gap-2">
                            <span class="text-[11px] font-black text-gray-400 uppercase tracking-widest">可选用户 ·候选</span>
                            <span class="px-2 py-0.5 bg-gray-200 text-gray-600 text-[9px] font-bold rounded-full">{{ filteredAvailableUsers.length }}</span>
                        </div>
                        <button @click="addAllVisible" class="text-[10px] font-bold text-blue-600 hover:underline">全选本页</button>
                    </div>

                    <div class="relative mb-3">
                        <input
                            v-model="userSearchQuery"
                            type="text"
                            placeholder="搜索候选用户..."
                            class="w-full bg-white border border-gray-200 rounded-xl px-4 py-2.5 pl-10 focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-all text-sm"
                        />
                        <svg class="w-4 h-4 text-gray-400 absolute left-3.5 top-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                        </svg>
                    </div>

                    <div class="flex-1 overflow-y-auto custom-scrollbar pr-1 space-y-1.5">
                        <div v-if="loadingUsers" class="text-center py-20 text-gray-400 text-xs animate-pulse">正在获取候选库...</div>
                        <div v-else-if="filteredAvailableUsers.length === 0" class="text-center py-20 text-gray-400 text-xs italic">暂无可分配用户</div>
                        <div
                            v-for="user in filteredAvailableUsers"
                            :key="'avail-'+user.id"
                            @click="toggleUser(user.id)"
                            class="flex items-center p-3 rounded-xl bg-white border border-gray-100 hover:border-blue-300 hover:shadow-md hover:shadow-blue-500/5 transition-all cursor-pointer group active:scale-[0.98]"
                        >
                            <div class="w-8 h-8 rounded-full bg-blue-50 text-blue-600 flex items-center justify-center font-bold text-xs border border-blue-100 shrink-0 group-hover:bg-blue-600 group-hover:text-white transition-colors">
                                {{ user.real_name?.[0] || user.user_name?.[0] }}
                            </div>
                            <div class="ml-3 flex-1 min-w-0">
                                <div class="font-bold text-gray-800 text-sm truncate">{{ user.real_name || user.user_name }}</div>
                                <div class="text-[10px] text-gray-400 truncate font-mono">@{{ user.user_name }}</div>
                            </div>
                            <svg class="w-4 h-4 text-gray-300 group-hover:text-blue-500 opacity-0 group-hover:opacity-100 transition-all transform translate-x-1 group-hover:translate-x-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M9 5l7 7-7 7" /></svg>
                        </div>
                    </div>
                </div>

                <!-- Middle Icon (Desktop Only) -->
                <div v-if="!isMobile" class="flex flex-col justify-center gap-4 opacity-20">
                    <svg class="w-6 h-6 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M13 5l7 7-7 7M5 5l7 7-7 7" /></svg>
                </div>

                <!-- Right: Selected Users -->
                <div class="flex-1 flex flex-col min-h-0 bg-blue-50/30 rounded-2xl border border-blue-100/50 p-4 relative overflow-hidden">
                    <div class="absolute inset-0 bg-grid-slate-100 [mask-image:linear-gradient(0deg,#fff,rgba(255,255,255,0.6))] pointer-events-none"></div>
                    <div class="flex items-center justify-between mb-3 px-1 relative z-10">
                        <div class="flex items-center gap-2">
                            <span class="text-[11px] font-black text-blue-500 uppercase tracking-widest">已选用户 ·成员</span>
                            <span class="px-2 py-0.5 bg-blue-600 text-white text-[9px] font-bold rounded-full shadow-sm shadow-blue-200">{{ assignedUserIds.length }}</span>
                        </div>
                        <button @click="removeAll" class="text-[10px] font-bold text-red-500 hover:underline">移除所有</button>
                    </div>

                    <div class="flex-1 overflow-y-auto custom-scrollbar pr-1 space-y-1.5 relative z-10">
                        <div v-if="assignedUserIds.length === 0" class="flex flex-col items-center justify-center h-full text-center py-20">
                            <div class="w-12 h-12 bg-blue-100 text-blue-400 rounded-2xl flex items-center justify-center mb-3">
                                <svg class="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M12 4v16m8-8H4" /></svg>
                            </div>
                            <span class="text-xs text-blue-400/80 font-medium">从左侧点击添加用户</span>
                        </div>
                        <div
                            v-for="user in selectedUsersDetails"
                            :key="'sel-'+user.id"
                            @click="toggleUser(user.id)"
                            class="flex items-center p-3 rounded-xl bg-white border border-blue-200 hover:border-red-300 hover:shadow-lg hover:shadow-red-500/5 transition-all cursor-pointer group active:scale-[0.98] ring-1 ring-blue-500/5"
                        >
                            <div class="w-8 h-8 rounded-full bg-blue-600 text-white flex items-center justify-center font-bold text-xs shadow-md shadow-blue-200 group-hover:bg-red-500 group-hover:shadow-red-200 transition-colors">
                                {{ user.real_name?.[0] || user.user_name?.[0] }}
                            </div>
                            <div class="ml-3 flex-1 min-w-0">
                                <div class="font-bold text-gray-900 text-sm truncate">{{ user.real_name || user.user_name }}</div>
                                <div class="text-[10px] text-blue-500/60 truncate font-mono group-hover:text-red-400">@{{ user.user_name }}</div>
                            </div>
                            <svg class="w-4 h-4 text-gray-300 group-hover:text-red-500 transition-all opacity-0 group-hover:opacity-100" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M6 18L18 6M6 6l12 12" /></svg>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Footer -->
            <div class="mt-6 flex flex-col sm:flex-row justify-between items-center gap-4 border-t border-gray-100 pt-6 px-2">
                <div class="flex items-center gap-4">
                    <div class="flex flex-col">
                        <span class="text-[10px] font-black text-gray-400 uppercase tracking-tighter">当前选择状态</span>
                        <span class="text-sm font-bold text-gray-800">已选 {{ assignedUserIds.length }} 名用户 / <span class="text-gray-400">{{ users.length }} 总数</span></span>
                    </div>
                </div>
                <div class="flex gap-3 w-full sm:w-auto">
                    <button @click="closeUserAssignmentDialog" class="flex-1 sm:flex-none px-6 py-2.5 border border-gray-200 rounded-xl hover:bg-gray-50 text-sm font-bold text-gray-500 transition-all">取消更改</button>
                    <button
                        @click="saveUserAssignments"
                        :disabled="submittingUserAssignment"
                        class="flex-1 sm:flex-none px-10 py-2.5 bg-blue-600 text-white rounded-xl hover:bg-blue-700 disabled:opacity-50 text-sm font-black shadow-xl shadow-blue-200 transition-all active:scale-[0.98]"
                    >
                        {{ submittingUserAssignment ? '正在同步...' : '确认分配' }}
                    </button>
                </div>
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
import { ref, onMounted, computed, onUnmounted } from 'vue'
import axios from 'axios'
import { useToast } from '../composables/useToast'
import { MENU_TREE } from '../constants/permissions'
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
const users = ref<any[]>([])
const loadingUsers = ref(false)
const assignedUserIds = ref<number[]>([])
const userSearchQuery = ref('')

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

    const idx = targetArray.indexOf(itemId);
    if (idx > -1) {
        targetArray.splice(idx, 1);
    } else {
        targetArray.push(itemId);
    }
}

const isItemSelected = (itemId: string) => {
    const isMenu = itemId.startsWith('menu:');
    const targetArray = isMenu ? permissionData.value.menus : permissionData.value.elements;
    return targetArray.includes(itemId);
}

// User Assignment Logic
const filteredAvailableUsers = computed(() => {
    // 1. First, exclude users already assigned to this role
    const available = users.value.filter(u => !assignedUserIds.value.includes(u.id))

    // 2. Then apply search filter
    if (!userSearchQuery.value) return available
    const q = userSearchQuery.value.toLowerCase()
    return available.filter(u =>
        (u.user_name || '').toLowerCase().includes(q) ||
        (u.real_name || '').toLowerCase().includes(q) ||
        (u.email || '').toLowerCase().includes(q)
    )
})

const selectedUsersDetails = computed(() => {
    return users.value.filter(u => assignedUserIds.value.includes(u.id))
})

const toggleUser = (userId: number) => {
    const idx = assignedUserIds.value.indexOf(userId)
    if (idx > -1) {
        assignedUserIds.value.splice(idx, 1)
    } else {
        assignedUserIds.value.push(userId)
    }
}

const addAllVisible = () => {
    filteredAvailableUsers.value.forEach(u => {
        if (!assignedUserIds.value.includes(u.id)) {
            assignedUserIds.value.push(u.id)
        }
    })
}

const removeAll = () => {
    assignedUserIds.value = []
}

const openUserAssignmentDialog = async (role: any) => {
    currentRole.value = role
    showUserAssignmentDialog.value = true
    userSearchQuery.value = ''
    assignedUserIds.value = []

    await Promise.all([
        fetchAllUsers(),
        fetchRoleUsers(role.id)
    ])
}

const fetchAllUsers = async () => {
    if (users.value.length > 0) return
    loadingUsers.value = true
    try {
        const apiKey = localStorage.getItem('api_key')
        // Get all users (setting a large size to get most users for selection)
        const response = await axios.get('/api/portal/management/users', {
            headers: { 'X-API-Key': apiKey },
            params: { page: 1, size: 1000 }
        })
        users.value = response.data.items || []
    } catch (e) {
        console.error('Fetch Users Failed', e)
        showToast('获取用户列表失败', 'error')
    } finally {
        loadingUsers.value = false
    }
}

const fetchRoleUsers = async (roleId: number) => {
    try {
        const apiKey = localStorage.getItem('api_key')
        const response = await axios.get(`/api/portal/roles/${roleId}/users`, {
            headers: { 'X-API-Key': apiKey }
        })
        assignedUserIds.value = response.data.user_ids || []
    } catch (e) {
        console.error('Fetch Role Users Failed', e)
    }
}

const saveUserAssignments = async () => {
    if (!currentRole.value) return
    submittingUserAssignment.value = true
    try {
        const apiKey = localStorage.getItem('api_key')
        await axios.post(
            `/api/portal/roles/${currentRole.value.id}/users`,
            { user_ids: assignedUserIds.value },
            { headers: { 'X-API-Key': apiKey } }
        )
        showToast('用户分配保存成功', 'success')
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
        const apiKey = localStorage.getItem('api_key')
        const params: any = { page: page.value, size: size.value }
        if (searchQuery.value) params.search = searchQuery.value

        const response = await axios.get('/api/portal/roles', {
            headers: { 'X-API-Key': apiKey },
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
        const apiKey = localStorage.getItem('api_key')
        if (showEditDialog.value && editingRoleId.value) {
            await axios.put(`/api/portal/roles/${editingRoleId.value}`, formData.value, { headers: { 'X-API-Key': apiKey } })
            showToast('更新成功', 'success')
        } else {
            await axios.post('/api/portal/roles', formData.value, { headers: { 'X-API-Key': apiKey } })
            showToast('创建成功', 'success')
        }
        closeDialogs()
        fetchRoles()
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
        const apiKey = localStorage.getItem('api_key')
        await axios.delete(`/api/portal/roles/${roleToDelete.value.id}`, { headers: { 'X-API-Key': apiKey } })
        showToast('删除成功', 'success')
        showDeleteDialog.value = false
        fetchRoles()
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
        const apiKey = localStorage.getItem('api_key')
        const results = await Promise.allSettled([
            axios.get('/api/portal/ragflow/datasets', { headers: { 'X-API-Key': apiKey }, params: { page_size: 100 } }),
            axios.get('/api/portal/management/resources/available', { headers: { 'X-API-Key': apiKey } })
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
        const apiKey = localStorage.getItem('api_key')
        const response = await axios.get(`/api/portal/roles/${roleId}/permissions`, { headers: { 'X-API-Key': apiKey } })
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
        const apiKey = localStorage.getItem('api_key')
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
            payload,
            { headers: { 'X-API-Key': apiKey } }
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
})

</script>
