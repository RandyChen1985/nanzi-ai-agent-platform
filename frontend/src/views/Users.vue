<template>
  <div class="space-y-6 relative">
    <!-- Full-page Sync Overlay -->
    <div
      v-if="syncingSso"
      class="fixed inset-0 z-[10000] bg-gray-900/60 backdrop-blur-sm flex flex-col items-center justify-center text-white"
    >
      <div class="w-16 h-16 border-4 border-white border-t-transparent rounded-full animate-spin mb-4"></div>
      <h3 class="text-xl font-black">正在同步 SSO 用户数据...</h3>
      <p class="text-gray-300 mt-2 text-sm">正在为您生成 API Key 并同步权限，请勿刷新页面</p>
    </div>

    <!-- Header -->
    <div class="flex justify-between items-center">
      <h1 class="text-2xl font-bold text-gray-900">用户管理</h1>
      <div class="flex gap-2">
        <button
          v-if="showSsoSync"
          @click="openSsoModal"
          class="bg-indigo-600 text-white px-4 py-2 rounded-lg hover:bg-indigo-700 transition flex items-center gap-2 shadow-sm"
        >
          <UserGroupIcon class="w-5 h-5" />
          同步 SSO 用户
        </button>
        <button
          @click="showThirdPartyDrawer = true"
          class="bg-violet-600 text-white px-4 py-2 rounded-lg hover:bg-violet-700 transition flex items-center gap-2 shadow-sm"
        >
          <ArrowPathIcon class="w-5 h-5" />
          同步第三方用户
        </button>
        <button
          @click="showSystemQuotaModal = true"
          class="bg-amber-600 text-white px-4 py-2 rounded-lg hover:bg-amber-700 transition flex items-center gap-2 shadow-sm"
        >
          <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
          </svg>
          系统默认额度
        </button>
        <button
          @click="openCreateDialog"
          class="bg-blue-600 text-white px-4 py-2 rounded-lg hover:bg-blue-700 transition flex items-center gap-2"
        >
          <PlusIcon class="w-5 h-5" />
          创建用户
        </button>
      </div>
    </div>

    <!-- Filters -->
    <div class="bg-white shadow rounded-lg p-4">
      <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <input
          v-model="searchQuery"
          @input="debouncedSearch"
          type="text"
          placeholder="搜索用户名或姓名..."
          class="border border-gray-300 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500 text-sm"
        />
        <select
          v-model="roleFilter"
          @change="fetchUsers"
          class="border border-gray-300 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500 text-sm"
        >
          <option value="">所有身份</option>
          <option value="admin">管理员</option>
          <option value="user">普通用户</option>
        </select>
        <select
          v-model="statusFilter"
          @change="fetchUsers"
          class="border border-gray-300 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500 text-sm"
        >
          <option value="">所有状态</option>
          <option value="1">启用</option>
          <option value="0">禁用</option>
        </select>
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
      <div
        class="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"
      ></div>
      <p class="mt-2 text-gray-500">加载中...</p>
    </div>

    <!-- User List -->
    <div v-else-if="users.length > 0">
      <!-- Desktop Table -->
      <div v-if="!isMobile" class="bg-white shadow rounded-lg overflow-hidden">
        <div class="overflow-x-auto">
          <table class="min-w-full divide-y divide-gray-200">
            <thead class="bg-gray-50">
              <tr>
                <th
                  class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider whitespace-nowrap"
                >
                  ID
                </th>
                <th
                  class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider whitespace-nowrap"
                >
                  用户名
                </th>
                <th
                  class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider whitespace-nowrap"
                >
                  真实姓名
                </th>
                <th
                  class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider whitespace-nowrap"
                >
                  系统身份/角色
                </th>
                <th
                  class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider whitespace-nowrap"
                >
                  备注
                </th>
                <th
                  class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider whitespace-nowrap"
                >
                  状态
                </th>
                <th
                  class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider whitespace-nowrap"
                >
                  创建时间
                </th>
                <th
                  class="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider whitespace-nowrap"
                >
                  操作
                </th>
              </tr>
            </thead>
            <tbody class="bg-white divide-y divide-gray-200">
              <tr v-for="user in users" :key="user.id" class="hover:bg-gray-50">
                <td
                  class="px-6 py-4 whitespace-nowrap text-sm text-gray-900 font-mono"
                >
                  {{ user.id }}
                </td>
                <td
                  class="px-6 py-4 whitespace-nowrap font-medium text-gray-900"
                >
                  {{ user.user_name }}
                </td>
                <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                  {{ user.real_name || "-" }}
                </td>
                <td class="px-6 py-4 whitespace-nowrap text-sm">
                  <div class="flex flex-col gap-1.5 items-start">
                    <!-- System Role -->
                    <span
                      :class="
                        user.role === 'admin'
                          ? 'bg-purple-100 text-purple-800 border-purple-200'
                          : 'bg-blue-100 text-blue-800 border-blue-200'
                      "
                      class="px-2 py-0.5 text-[11px] font-bold rounded-md border"
                    >
                      {{ user.role === "admin" ? "系统管理员" : "普通用户" }}
                    </span>

                    <!-- Business Roles -->
                    <RoleList
                      v-if="user.role_names && user.role_names.length > 0"
                      :roles="user.role_names"
                      :limit="2"
                    />
                  </div>
                </td>
                <td class="px-6 py-4 text-sm text-gray-500 max-w-xs truncate">
                  {{ user.remark || "-" }}
                </td>
                <td class="px-6 py-4 whitespace-nowrap">
                  <div class="flex items-center">
                    <Switch
                      :model-value="user.status === 1"
                      @update:model-value="toggleStatus(user)"
                    />
                    <span
                      class="ml-2 text-xs"
                      :class="
                        user.status === 1 ? 'text-green-600' : 'text-gray-400'
                      "
                    >
                      {{ user.status === 1 ? "已启用" : "已禁用" }}
                    </span>
                  </div>
                </td>
                <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                  {{ formatDate(user.created_at) }}
                </td>
                <td
                  class="px-6 py-4 whitespace-nowrap text-right text-sm font-medium"
                >
                  <div class="flex justify-end items-center gap-3">
                    <button
                      @click="viewApiKey(user)"
                      class="text-indigo-600 hover:text-indigo-900 transition-colors p-1"
                      title="查看 API Key"
                    >
                      <KeyIcon class="w-5 h-5" />
                    </button>
                    <button
                      @click="editUser(user)"
                      class="text-blue-600 hover:text-blue-900 transition-colors p-1"
                      title="编辑用户/权限"
                    >
                      <PencilSquareIcon class="w-5 h-5" />
                    </button>
                    <button
                      @click="regenerateApiKey(user)"
                      class="text-amber-600 hover:text-amber-900 transition-colors p-1"
                      title="重置 API Key"
                    >
                      <ArrowPathIcon class="w-5 h-5" />
                    </button>
                    <button
                      v-if="user.user_name !== 'admin'"
                      @click="confirmDelete(user)"
                      class="text-red-600 hover:text-red-900 transition-colors p-1"
                      title="删除用户"
                    >
                      <TrashIcon class="w-5 h-5" />
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
        <div
          v-for="user in users"
          :key="user.id"
          class="bg-white shadow rounded-lg p-4 space-y-3"
        >
          <div class="flex justify-between items-start">
            <div class="flex-1 min-w-0">
              <h3 class="text-base font-bold text-gray-900 truncate">
                {{ user.user_name }}
              </h3>
              <p class="text-xs text-gray-500">
                {{ user.real_name || "无真实姓名" }}
              </p>
            </div>
            <div class="flex items-center gap-2">
              <span
                :class="
                  user.role === 'admin'
                    ? 'bg-purple-100 text-purple-800'
                    : 'bg-blue-100 text-blue-800'
                "
                class="px-2 py-0.5 text-[10px] font-bold rounded"
              >
                {{ user.role === "admin" ? "管理员" : "普通用户" }}
              </span>
              <Switch
                :model-value="user.status === 1"
                @update:model-value="toggleStatus(user)"
              />
            </div>
          </div>

          <div
            class="grid grid-cols-2 gap-2 text-[11px] text-gray-500 border-t border-gray-50 pt-2"
          >
            <div>
              <span class="opacity-60">ID:</span>
              <span class="font-mono">{{ user.id }}</span>
            </div>
            <div>
              <span class="opacity-60">创建:</span>
              {{ formatDate(user.created_at).split(" ")[0] }}
            </div>
            <div class="col-span-2 truncate">
              <span class="opacity-60">备注:</span> {{ user.remark || "-" }}
            </div>
          </div>

          <div
            v-if="user.role_names && user.role_names.length > 0"
            class="flex flex-wrap gap-1"
          >
            <span
              v-for="(rname, idx) in user.role_names"
              :key="idx"
              class="bg-gray-50 text-gray-500 px-1.5 py-0.5 text-[9px] rounded border border-gray-100"
            >
              {{ rname }}
            </span>
          </div>

          <div
            class="flex justify-between items-center pt-2 border-t border-gray-50"
          >
            <div class="flex gap-4">
              <button
                @click="viewApiKey(user)"
                class="text-indigo-600 flex items-center gap-1 text-xs"
              >
                <KeyIcon class="w-4 h-4" /> 密钥
              </button>
              <button
                @click="editUser(user)"
                class="text-blue-600 flex items-center gap-1 text-xs"
              >
                <PencilSquareIcon class="w-4 h-4" /> 编辑
              </button>
            </div>
            <div class="flex gap-4">
              <button
                @click="regenerateApiKey(user)"
                class="text-amber-600 flex items-center gap-1 text-xs"
              >
                <ArrowPathIcon class="w-4 h-4" /> 重置
              </button>
              <button
                v-if="user.user_name !== 'admin'"
                @click="confirmDelete(user)"
                class="text-red-600 flex items-center gap-1 text-xs"
              >
                <TrashIcon class="w-4 h-4" /> 删除
              </button>
            </div>
          </div>
        </div>
      </div>

      <!-- Pagination -->
      <div
        class="bg-white sm:bg-gray-50 px-4 py-3 flex flex-col sm:flex-row items-center justify-between border-t border-gray-200 mt-4 rounded-lg shadow sm:shadow-none"
      >
        <div class="text-sm text-gray-700 mb-3 sm:mb-0">
          共 {{ total }} 条，第 {{ page }}/{{ totalPages }} 页
        </div>
        <div class="flex gap-2 w-full sm:w-auto">
          <button
            @click="page > 1 && (page--, fetchUsers())"
            :disabled="page <= 1"
            class="flex-1 sm:flex-none px-4 py-2 border border-gray-300 rounded-lg hover:bg-gray-200 disabled:opacity-50 text-sm font-medium transition-colors"
          >
            上一页
          </button>
          <button
            @click="page < totalPages && (page++, fetchUsers())"
            :disabled="page >= totalPages"
            class="flex-1 sm:flex-none px-4 py-2 border border-gray-300 rounded-lg hover:bg-gray-200 disabled:opacity-50 text-sm font-medium transition-colors"
          >
            下一页
          </button>
        </div>
      </div>
    </div>

    <!-- Empty State -->
    <div
      v-else
      class="bg-white shadow rounded-lg p-12 text-center text-gray-500"
    >
      没有找到用户
    </div>

    <!-- Create/Edit Dialog -->
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

        <!-- Tabs for Edit -->
        <div
          v-if="showEditDialog && formData.role === 'user'"
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
                  >部门代码</label
                >
                <input
                  v-model="formData.dept_code"
                  type="text"
                  class="w-full border border-gray-300 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500 text-sm"
                  placeholder="部门代码 (如: DEPT001)"
                />
              </div>
              <div>
                <label
                  class="block text-xs font-bold text-gray-400 uppercase mb-1"
                  >组织全路径</label
                >
                <input
                  v-model="formData.org_path"
                  type="text"
                  class="w-full border border-gray-300 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500 text-sm"
                  placeholder="例如: yovole/sh/dc1"
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
              <div class="flex justify-between items-center mb-1">
                <label class="block text-xs font-bold text-gray-400 uppercase"
                  >业务角色</label
                >
                <input
                  v-model="roleSearchQuery"
                  type="text"
                  placeholder="搜索角色..."
                  class="text-xs border border-gray-200 rounded px-2 py-0.5 w-32 focus:outline-none focus:border-blue-400"
                />
              </div>
              <div
                class="border border-gray-300 rounded-lg p-3 max-h-40 overflow-y-auto bg-gray-50 grid grid-cols-1 sm:grid-cols-2 gap-2"
              >
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

          <!-- PERMISSIONS TAB -->
          <div
            v-show="activeTab === 'permissions'"
            class="space-y-4 py-2 flex flex-col h-full min-h-[400px]"
          >
            <div
              v-if="loadingResources"
              class="text-center py-10 text-gray-500"
            >
              加载资源中...
            </div>
            <div v-else class="flex flex-col flex-1 overflow-hidden">
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
                    </button>
                  </div>
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
                        <p class="opacity-80">配置禁用工具后该智能体完全无法调用该工具。配置禁用命令后，当智能体尝试替该用户运行含有敏感词的系统命令时，将被运行时强行拦截。</p>
                      </div>
                    </div>

                    <!-- 1. 禁用工具 Checkbox 组 -->
                    <div class="border border-gray-150 rounded-xl p-3 bg-white">
                      <h4 class="text-xs font-bold text-gray-700 mb-2 flex items-center gap-1.5 select-none">
                        <span class="w-1.5 h-1.5 bg-red-500 rounded-full"></span>
                        选择要禁用的工具（全局拉黑）
                      </h4>
                      <div class="grid grid-cols-1 sm:grid-cols-2 gap-2">
                        <label v-for="tool in availableToolsToForbid" :key="tool.id" class="flex items-start p-2 rounded border bg-white hover:bg-gray-50 transition-colors cursor-pointer">
                          <input
                            type="checkbox"
                            :value="tool.id"
                            v-model="permissionData.forbidden_tools"
                            class="mt-1 h-4 w-4 rounded text-red-600 focus:ring-red-500"
                          />
                          <div class="ml-2 min-w-0">
                            <p class="text-xs sm:text-sm font-bold text-gray-900 truncate">{{ tool.name }}</p>
                            <p class="text-[9px] text-gray-400 truncate">{{ tool.description }}</p>
                          </div>
                        </label>
                      </div>
                    </div>

                    <!-- 1.2 其他自定义与扩展工具禁用 -->
                    <div v-show="otherAvailableTools.length > 0" class="border border-gray-150 rounded-xl p-3 bg-white">
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
                        <label
                          v-for="tool in filteredOtherTools"
                          :key="tool.id"
                          class="flex items-center p-1.5 rounded border bg-white hover:bg-gray-50 transition-colors cursor-pointer text-xs"
                        >
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
                    <div class="border border-gray-150 rounded-xl p-3 bg-white flex flex-col">
                      <h4 class="text-xs font-bold text-gray-700 mb-2 flex items-center gap-1.5 select-none">
                        <span class="w-1.5 h-1.5 bg-amber-500 rounded-full"></span>
                        自定义禁用命令关键字
                      </h4>
                      <p class="text-[10px] text-gray-400 mb-2">针对 exec_command 终端工具，输入禁止该用户运行的命令名或命令序列（例如：rm, shutdown, mv, wget），用英文逗号隔开。</p>
                      <textarea
                        v-model="forbiddenCommandsText"
                        placeholder="例如: rm, shutdown, mv, wget, curl"
                        class="w-full text-xs sm:text-sm border border-gray-200 rounded-lg p-2.5 min-h-[80px] focus:ring-amber-500 focus:border-amber-500 custom-scrollbar focus:outline-none"
                      ></textarea>
                    </div>
                  </div>

                  <div
                    v-else-if="currentResources.length === 0"
                    class="text-center py-4 text-gray-500 text-xs"
                  >
                    暂无可用资源
                  </div>
                  <div v-else class="grid grid-cols-1 sm:grid-cols-2 gap-2">
                    <label
                      v-for="res in currentResources"
                      :key="res.id"
                      class="flex items-start p-2 rounded border bg-white transition-colors"
                      :class="
                        isMissingKnowledgeBase(res)
                          ? 'opacity-70 cursor-not-allowed border-amber-200 bg-amber-50/40'
                          : 'hover:bg-gray-50 cursor-pointer'
                      "
                      :title="
                        isMissingKnowledgeBase(res)
                          ? '该知识库已在 RAGFlow 失联，不可分配权限'
                          : undefined
                      "
                    >
                      <input
                        type="checkbox"
                        :value="res.id"
                        v-model="(permissionData as any)[activeResTab]"
                        :disabled="isMissingKnowledgeBase(res)"
                        class="mt-1 h-4 w-4 rounded text-blue-600 focus:ring-blue-500 disabled:cursor-not-allowed disabled:opacity-50"
                      />
                      <div class="ml-2 min-w-0">
                        <div class="flex items-center gap-1.5 min-w-0">
                          <p
                            class="text-xs sm:text-sm font-bold text-gray-900 truncate"
                          >
                            {{ res.platform_name || res.display_name || res.name }}
                          </p>
                          <span
                            v-if="isMissingKnowledgeBase(res)"
                            class="flex-shrink-0 text-[9px] px-1.5 py-0.5 rounded-full bg-amber-100 text-amber-700 font-semibold leading-none"
                            >失联</span
                          >
                        </div>
                        <p class="text-[9px] text-gray-400 truncate font-mono">
                          {{ res.description || res.id }}
                        </p>
                      </div>
                    </label>
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
                  <div
                    v-for="menu in MENU_TREE"
                    :key="menu.id"
                    class="bg-white rounded-lg border border-gray-200 overflow-hidden shadow-sm"
                  >
                    <div
                      class="px-3 py-2 bg-gray-50 flex items-center gap-2 border-b border-gray-100"
                    >
                      <input
                        type="checkbox"
                        :checked="isItemSelected(menu.id)"
                        @change="toggleTreeItem(menu.id)"
                        class="h-4 w-4 text-blue-600 rounded border-gray-300 focus:ring-blue-500"
                      />
                      <span
                        class="text-xs sm:text-sm font-black text-gray-800"
                        >{{ menu.label }}</span
                      >
                    </div>
                    <div
                      v-if="menu.children.length > 0"
                      class="p-2 grid grid-cols-1 xs:grid-cols-2 gap-1.5"
                    >
                      <div
                        v-for="child in menu.children"
                        :key="child.id"
                        @click="toggleTreeItem(child.id)"
                        class="flex items-center gap-2 p-1.5 rounded hover:bg-blue-50 cursor-pointer transition-colors"
                        :class="isItemSelected(child.id) ? 'bg-blue-50' : ''"
                      >
                        <div
                          class="w-3.5 h-3.5 rounded border flex-shrink-0 flex items-center justify-center"
                          :class="
                            isItemSelected(child.id)
                              ? 'bg-blue-600 border-blue-600'
                              : 'bg-white border-gray-300'
                          "
                        >
                          <svg
                            v-if="isItemSelected(child.id)"
                            class="w-2.5 h-2.5 text-white"
                            fill="none"
                            stroke="currentColor"
                            viewBox="0 0 24 24"
                          >
                            <path
                              stroke-linecap="round"
                              stroke-linejoin="round"
                              stroke-width="3"
                              d="M5 13l4 4L19 7"
                            />
                          </svg>
                        </div>
                        <span class="text-[11px] text-gray-600 leading-tight">{{
                          child.label
                        }}</span>
                      </div>
                    </div>
                  </div>
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
          class="mt-4 sm:mt-6 flex flex-col sm:flex-row justify-end gap-2 pt-4 border-t border-gray-200"
        >
          <button
            @click="closeDialogs"
            class="order-2 sm:order-1 px-4 py-2.5 border border-gray-300 rounded-lg hover:bg-gray-50 text-sm font-medium"
          >
            {{ activeTab === 'quota' ? '关闭' : '取消' }}
          </button>
          <button
            v-if="!createdApiKey && activeTab !== 'quota'"
            @click="saveUser"
            :disabled="submitting"
            class="order-1 sm:order-2 px-4 py-2.5 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 text-sm font-bold shadow-md shadow-blue-100"
          >
            {{ showEditDialog ? "保存更新" : "立即创建用户" }}
          </button>
        </div>
      </div>
    </div>

    <!-- View API Key Dialog -->
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
              class="flex-1 px-3 py-2 border border-gray-300 rounded bg-white text-xs font-mono"
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

    <!-- Delete Confirmation -->
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

    <!-- Regenerate Key Dialog -->
    <div
      v-if="showRegenerateDialog"
      class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-[9990]"
      @click.self="closeRegenerateDialog"
    >
      <div
        class="bg-white rounded-lg p-6 w-full max-w-md shadow-xl text-center"
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

    <!-- SSO Sync Modal -->
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
              type="text"
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

    <!-- SSO Sync Confirmation Modal -->
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
  </div>

  <!-- System Default Quota Modal -->
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

  <ThirdPartyUserSyncDrawer
    :visible="showThirdPartyDrawer"
    @close="showThirdPartyDrawer = false"
    @synced="onThirdPartySynced"
  />
</template>

<script setup lang="ts">
import { ref, onMounted, computed, onUnmounted } from "vue";
import axios from "axios";
import { useToast } from "../composables/useToast";
import { useBranding } from "../composables/useBranding";
import { MENU_TREE } from "../constants/permissions";
import Switch from "../components/Switch.vue";
import RoleList from "../components/RoleList.vue";
import ThirdPartyUserSyncDrawer from "../components/ThirdPartyUserSyncDrawer.vue";
import QuotaPolicyPanel from "../components/admin/QuotaPolicyPanel.vue";
import {
  KeyIcon,
  ArrowPathIcon,
  PencilSquareIcon,
  TrashIcon,
  PlusIcon,
  UserGroupIcon,
} from "@heroicons/vue/24/outline";

const { showToast } = useToast();
const { branding } = useBranding();

const windowWidth = ref(window.innerWidth);
const isMobile = computed(() => windowWidth.value < 768);
const handleResize = () => {
  windowWidth.value = window.innerWidth;
};

onMounted(() => {
  window.addEventListener("resize", handleResize);
});
onUnmounted(() => {
  window.removeEventListener("resize", handleResize);
});

// State
const ssoEnabled = ref(false);
const showSsoSync = computed(
  () => ssoEnabled.value && !branding.value.hide_login_sso
);
const users = ref<any[]>([]);
const businessRoles = ref<any[]>([]);
const loading = ref(false);
const total = ref(0);
const page = ref(1);
const size = ref(15);
const totalPages = ref(0);

// Filters
const searchQuery = ref("");
const roleFilter = ref("");
const statusFilter = ref("");
const roleSearchQuery = ref("");

// Dialogs
const showCreateDialog = ref(false);
const showEditDialog = ref(false);
const showDeleteDialog = ref(false);
const showRegenerateDialog = ref(false);
const showViewKeyDialog = ref(false);
const showSsoModal = ref(false);
const showThirdPartyDrawer = ref(false);
const showSystemQuotaModal = ref(false);
const userToDelete = ref<any>(null);
const userToRegenerate = ref<any>(null);
const userToViewKey = ref<any>(null);
const loadingViewKey = ref(false);
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
  role: "user",
  dept_code: "",
  org_path: "",
  extra_data: "",
  role_ids: [] as number[],
  remark: "",
});

// Extra Data Tabs and Pairs
const extraDataTab = ref<"visual" | "json">("visual");
const extraDataPairs = ref<{ key: string; value: string }[]>([]);

const addExtraDataPair = () => {
  extraDataPairs.value.push({ key: "", value: "" });
};

const removeExtraDataPair = (index: number) => {
  extraDataPairs.value.splice(index, 1);
};

// Syncing functions
const syncPairsToExtraData = () => {
  const result: Record<string, any> = {};
  extraDataPairs.value.forEach((pair) => {
    if (pair.key.trim()) {
      let val = pair.value;
      // Try to parse as number or boolean if it looks like one
      if (val.toLowerCase() === "true") result[pair.key] = true;
      else if (val.toLowerCase() === "false") result[pair.key] = false;
      else if (!isNaN(Number(val)) && val.trim() !== "")
        result[pair.key] = Number(val);
      else result[pair.key] = val;
    }
  });
  formData.value.extra_data =
    Object.keys(result).length > 0 ? JSON.stringify(result, null, 2) : "";
};

const syncExtraDataToPairs = () => {
  if (!formData.value.extra_data) {
    extraDataPairs.value = [];
    return;
  }
  try {
    const data =
      typeof formData.value.extra_data === "string"
        ? JSON.parse(formData.value.extra_data)
        : formData.value.extra_data;
    if (data && typeof data === "object") {
      extraDataPairs.value = Object.entries(data).map(([key, value]) => ({
        key,
        value: String(value),
      }));
    }
  } catch (e) {
    console.error("Failed to parse extra_data", e);
    // If parse fails, we'll just keep pairs empty or handle as needed
  }
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
const resourceTypes = ["agents", "datasets", "metadata", "apis", "forbidden_configs"] as const;
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
  forbidden_configs: { label: "禁用工具与命令", color: "red", icon: "🚫" },
};

const loadingResources = ref(false);
const allResources = ref<{
  agents: any[];
  datasets: any[];
  metadata: any[];
  apis: any[];
}>({ agents: [], datasets: [], metadata: [], apis: [] });
const permissionData = ref<{
  agents: string[];
  datasets: string[];
  metadata: string[];
  apis: string[];
  menus: string[];
  elements: string[];
  forbidden_tools: string[];
  forbidden_commands: string[];
}>({
  agents: [],
  datasets: [],
  metadata: [],
  apis: [],
  menus: [],
  elements: [],
  forbidden_tools: [],
  forbidden_commands: [],
});

const forbiddenCommandsText = ref("");

const availableToolsToForbid = [
  { id: "exec_command", name: "执行终端命令 (exec_command)", description: "在沙箱中执行 Bash/shell 命令行" },
  { id: "write_file", name: "写入/修改文件 (write_file)", description: "对沙箱目录物理写入或修改代码文件" },
  { id: "read_file", name: "读取文件内容 (read_file)", description: "读取沙箱工作目录中的文本或源代码" },
  { id: "manage_process", name: "进程管理 (manage_process)", description: "查看或终止沙箱中的系统进程" }
];

const otherAvailableTools = ref<any[]>([]);
const toolSearchQuery = ref("");

const filteredOtherTools = computed(() => {
  const query = toolSearchQuery.value.trim().toLowerCase();
  if (!query) return otherAvailableTools.value;
  return otherAvailableTools.value.filter(
    (t) =>
      t.name.toLowerCase().includes(query) ||
      t.description.toLowerCase().includes(query)
  );
});

const fetchAllSystemTools = async () => {
  try {
    const [resPortal, resMcp] = await Promise.all([
      axios.get("/api/portal/tools"),
      axios.get("/api/portal/tools/mcp").catch(() => ({ data: [] }))
    ]);

    const dynamicMapped = (resPortal.data || []).map((t: any) => ({
      id: t.name,
      name: t.name,
      description: t.description || "自定义 API 工具",
      category: "api"
    }));

    const mcpMapped = (Array.isArray(resMcp.data) ? resMcp.data : (resMcp.data.data || [])).map((t: any) => ({
      id: t.name,
      name: t.name,
      description: t.description || "MCP 注册工具",
      category: "mcp"
    }));

    const forbiddenIds = new Set(["exec_command", "write_file", "read_file", "manage_process"]);
    otherAvailableTools.value = [...dynamicMapped, ...mcpMapped].filter(t => !forbiddenIds.has(t.name));
  } catch (e) {
    console.error("Failed to fetch all tools for permission mapping", e);
  }
};

// Tree Logic
const toggleTreeItem = (itemId: string) => {
  const isMenu = itemId.startsWith("menu:");
  const targetArray = isMenu
    ? permissionData.value.menus
    : permissionData.value.elements;
  const idx = targetArray.indexOf(itemId);
  if (idx > -1) targetArray.splice(idx, 1);
  else targetArray.push(itemId);
};
const isItemSelected = (itemId: string) => {
  const isMenu = itemId.startsWith("menu:");
  const targetArray = isMenu
    ? permissionData.value.menus || []
    : permissionData.value.elements || [];
  return targetArray.includes(itemId);
};

// Computed
const activeAssignableResourceType = computed<AssignableResourceType | null>(() =>
  activeResTab.value === "forbidden_configs" ? null : activeResTab.value,
);

const currentResources = computed(() => {
  const type = activeAssignableResourceType.value;
  return type ? allResources.value[type] : [];
});

const isMissingKnowledgeBase = (res: any) => {
  if (activeResTab.value !== "datasets") return false;
  return Boolean(res?.is_missing_in_ragflow || res?.status === "missing");
};

const selectableResources = computed(() =>
  currentResources.value.filter((r: any) => !isMissingKnowledgeBase(r)),
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

const filteredBusinessRoles = computed(() => {
  if (!roleSearchQuery.value) return businessRoles.value;
  return businessRoles.value.filter((r) =>
    r.name.toLowerCase().includes(roleSearchQuery.value.toLowerCase()),
  );
});

// Actions
const fetchUsers = async () => {
  loading.value = true;
  try {
    const params: any = { page: page.value, size: size.value };
    if (searchQuery.value) params.search = searchQuery.value;
    if (roleFilter.value) params.role = roleFilter.value;
    if (statusFilter.value) params.status = statusFilter.value;
    const response = await axios.get("/api/portal/management/users", {
      params,
    });
    users.value = response.data.items;
    total.value = response.data.total;
    totalPages.value = Math.ceil(total.value / size.value);
  } catch (e: any) {
    showToast("获取列表失败", "error");
  } finally {
    loading.value = false;
  }
};

const fetchBusinessRoles = async () => {
  try {
    const response = await axios.get("/api/portal/roles", {
      params: { page: 1, size: 100 },
    });
    businessRoles.value = response.data.items;
  } catch (e) {
    console.error(e);
  }
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

    // 2. Fetch RagFlow Datasets (Optional/External)
    try {
      const r1 = await axios.get("/api/portal/ragflow/datasets", {
        params: { page_size: 100 },
      });
      const datasets = r1.data.data || r1.data || [];
      allResources.value.datasets = datasets;
      const missingIds = new Set(
        datasets
          .filter(
            (d: any) => d?.is_missing_in_ragflow || d?.status === "missing",
          )
          .map((d: any) => d.id),
      );
      if (missingIds.size > 0) {
        permissionData.value.datasets = (
          permissionData.value.datasets || []
        ).filter((id: string) => !missingIds.has(id));
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

const fetchUserPermissions = async (userId: number) => {
  try {
    const response = await axios.get(
      `/api/portal/management/users/${userId}/permissions`,
    );
    const perms = response.data.permissions;
    const missingIds = new Set(
      (allResources.value.datasets || [])
        .filter(
          (d: any) => d?.is_missing_in_ragflow || d?.status === "missing",
        )
        .map((d: any) => d.id),
    );
    permissionData.value = {
      agents: perms.agents || [],
      datasets: (perms.datasets || []).filter(
        (id: string) => !missingIds.has(id),
      ),
      metadata: perms.metadata || [],
      apis: perms.apis || [],
      menus: perms.menus || [],
      elements: perms.elements || [],
      forbidden_tools: perms.forbidden_tools || [],
      forbidden_commands: perms.forbidden_commands || [],
    };
    forbiddenCommandsText.value = (perms.forbidden_commands || []).join(", ");
  } catch (e) {
    permissionData.value = {
      agents: [],
      datasets: [],
      metadata: [],
      apis: [],
      menus: [],
      elements: [],
      forbidden_tools: [],
      forbidden_commands: [],
    };
    forbiddenCommandsText.value = "";
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
        role: formData.value.role,
        dept_code: formData.value.dept_code,
        org_path: formData.value.org_path,
        extra_data: formData.value.extra_data,
        role_ids: formData.value.role_ids,
        remark: formData.value.remark,
      };
      await axios.put(`/api/portal/management/users/${editingUserId.value}`, updatePayload);

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
            localStorage.setItem('user_info', JSON.stringify(updatedUserInfo));
            console.log('User Session Sync Success:', updatedUserInfo);
          } else {
            console.log('Not current user, skipping sync. Current:', currentId, 'Edited:', editingUserId.value);
          }
        }
      } catch (syncErr) {
        console.warn('LocalStorage Sync Error:', syncErr);
      }
      if (formData.value.role === "user") {
        const missingIds = new Set(
          (allResources.value.datasets || [])
            .filter(
              (d: any) =>
                d?.is_missing_in_ragflow || d?.status === "missing",
            )
            .map((d: any) => d.id),
        );
        permissionData.value.forbidden_commands = forbiddenCommandsText.value
          .split(",")
          .map(cmd => cmd.trim())
          .filter(cmd => cmd.length > 0);

        const payload = {
          ...permissionData.value,
          datasets: (permissionData.value.datasets || []).filter(
            (id: string) => !missingIds.has(id),
          ),
        };
        await axios.put(
          `/api/portal/management/users/${editingUserId.value}/permissions`,
          payload,
        );
      }
      showToast("更新成功", "success");
      closeDialogs();
      fetchUsers();
    } else {
      const res = await axios.post(
        "/api/portal/management/users",
        formData.value,
      );
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
    error.value = e.response?.data?.message || "操作失败";
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

const confirmDelete = (user: any) => {
  userToDelete.value = user;
  showDeleteDialog.value = true;
};

// Helpers
const openCreateDialog = () => {
  showCreateDialog.value = true;
  formData.value = {
    user_name: "",
    real_name: "",
    role: "user",
    dept_code: "",
    org_path: "",
    extra_data: "",
    role_ids: [],
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
  formData.value = {
    user_name: user.user_name,
    real_name: user.real_name || "",
    role: user.role,
    dept_code: user.dept_code || "",
    org_path: user.org_path || "",
    extra_data: user.extra_data || "",
    role_ids: user.role_ids || [],
    remark: user.remark || "",
  };
  syncExtraDataToPairs();
  extraDataTab.value = "visual";
  showEditDialog.value = true;
  activeTab.value = "info";
  roleSearchQuery.value = "";
  if (user.role === "user") {
    await Promise.all([fetchResources(), fetchUserPermissions(user.id)]);
  }
};
const closeDialogs = () => {
  showCreateDialog.value = false;
  showEditDialog.value = false;
  error.value = "";
  editingUserId.value = null;
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
const copyApiKey = () => {
  navigator.clipboard.writeText(createdApiKey.value);
  showToast("复制成功", "success");
};
const copyViewedApiKey = () => {
  navigator.clipboard.writeText(viewedApiKey.value);
  showToast("复制成功", "success");
};
const copyRegeneratedApiKey = () => {
  navigator.clipboard.writeText(regeneratedApiKey.value);
  showToast("复制成功", "success");
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

onMounted(() => {
  fetchUsers();
  fetchBusinessRoles();
  fetchPublicConfig();
  fetchAllSystemTools();
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
