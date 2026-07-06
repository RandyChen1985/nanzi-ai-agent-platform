<template>
  <div
    v-if="visible"
    class="fixed inset-0 z-[9999] flex justify-end bg-gray-900/40 backdrop-blur-sm"
    @click.self="emit('close')"
  >
    <div class="w-full max-w-3xl h-full bg-white shadow-2xl flex flex-col animate-slide-in-right">
      <!-- Header -->
      <div class="px-6 py-4 border-b border-gray-100 flex items-center justify-between shrink-0">
        <div>
          <div class="flex items-center gap-2">
            <h2 class="text-lg font-bold text-gray-900">同步第三方用户</h2>
            <button
              type="button"
              class="flex items-center justify-center w-6 h-6 rounded-full bg-white text-indigo-600 border border-gray-200 hover:border-indigo-300 hover:bg-indigo-50 transition-colors shadow-sm"
              title="功能说明"
              @click="showHelpModal = true"
            >
              <span class="font-bold text-xs leading-none">?</span>
            </button>
          </div>
          <p class="text-xs text-gray-400 mt-0.5">配置数据源、字段映射，支持定时与手动同步</p>
        </div>
        <button @click="emit('close')" class="p-2 hover:bg-gray-100 rounded-lg transition-colors">
          <XMarkIcon class="w-5 h-5 text-gray-500" />
        </button>
      </div>

      <!-- Body: single page -->
      <div class="flex-1 overflow-y-auto p-6 space-y-6">
        <!-- Master switch -->
        <div class="flex items-center justify-between p-4 bg-gray-50 rounded-xl border border-gray-100">
          <div>
            <p class="text-sm font-medium text-gray-700">启用第三方用户同步</p>
            <p class="text-xs text-gray-400">关闭后下方配置不可用，定时任务也不会执行</p>
          </div>
          <Switch v-model="config.enabled" />
        </div>

        <div
          class="space-y-6 transition-opacity"
          :class="config.enabled ? '' : 'opacity-50 pointer-events-none select-none'"
        >
          <!-- 数据源与表映射 -->
          <section class="space-y-4">
            <h3 class="text-sm font-bold text-gray-800 border-l-4 border-indigo-500 pl-2">数据源与表映射</h3>

            <div>
              <label class="block text-sm font-medium text-gray-700 mb-2">选择数据源</label>
              <select
                v-model.number="config.connection_config_id"
                class="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 outline-none disabled:bg-gray-50"
                :disabled="!config.enabled"
                @change="onDatasourceChange"
              >
                <option :value="null" disabled>请选择已配置的数据源</option>
                <option v-for="ds in datasources" :key="ds.id" :value="ds.id">
                  {{ ds.name }} ({{ ds.db_type }} / {{ ds.database_name }})
                </option>
              </select>
              <p class="text-xs text-gray-400 mt-1">数据源在「数据源管理」中维护</p>
            </div>

            <div ref="tablePickerRef">
              <label class="block text-sm font-medium text-gray-700 mb-2">用户表</label>
              <div class="relative">
                <button
                  ref="tableTriggerRef"
                  type="button"
                  class="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm text-left focus:ring-2 focus:ring-indigo-500 outline-none disabled:bg-gray-50 disabled:cursor-not-allowed flex items-center justify-between gap-2"
                  :disabled="!config.enabled || !config.connection_config_id || loadingTables"
                  @click.stop="toggleTablePicker"
                >
                  <span v-if="loadingTables" class="text-gray-400">加载中...</span>
                  <span v-else-if="!config.table_name" class="text-gray-400">请选择用户表</span>
                  <span v-else class="flex flex-col min-w-0 flex-1">
                    <span class="truncate font-medium text-gray-900">{{ config.table_name }}</span>
                    <span class="truncate text-xs text-gray-400">{{ selectedTableSubtitle }}</span>
                  </span>
                  <svg
                    class="h-4 w-4 shrink-0 text-gray-400 transition-transform"
                    :class="{ 'rotate-180': tablePickerOpen }"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                  >
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
                  </svg>
                </button>

                <Teleport to="body">
                  <div
                    v-if="tablePickerOpen"
                    ref="tableDropdownRef"
                    class="fixed z-[10050] rounded-lg border border-gray-200 bg-white shadow-xl overflow-hidden flex flex-col"
                    :style="tableDropdownStyle"
                  >
                    <div class="flex items-center gap-2 border-b border-gray-100 px-3 py-2 bg-gray-50 shrink-0">
                      <svg class="w-4 h-4 text-gray-400 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                      </svg>
                      <input
                        ref="tableSearchInputRef"
                        v-model="tableSearchQuery"
                        type="text"
                        class="flex-1 min-w-0 bg-transparent border-none focus:ring-0 text-sm py-0.5 outline-none"
                        placeholder="搜索表名或中文备注..."
                        @click.stop
                      />
                    </div>
                    <div class="overflow-y-auto flex-1 min-h-0">
                      <button
                        v-for="t in filteredTables"
                        :key="t.name"
                        type="button"
                        class="w-full px-3 py-2.5 text-left transition-colors hover:bg-indigo-50"
                        :class="config.table_name === t.name ? 'bg-indigo-50 text-indigo-700' : 'text-gray-800'"
                        @click.stop="selectTable(t.name)"
                      >
                        <div class="text-sm font-medium truncate">{{ t.name }}</div>
                        <div class="text-xs truncate mt-0.5" :class="config.table_name === t.name ? 'text-indigo-500/80' : 'text-gray-400'">
                          {{ tableSubtitle(t) }}
                        </div>
                      </button>
                      <div
                        v-if="!loadingTables && filteredTables.length === 0"
                        class="px-3 py-6 text-center text-sm text-gray-400"
                      >
                        {{ tableSearchQuery.trim() ? '无匹配表' : '暂无可用表' }}
                      </div>
                    </div>
                  </div>
                </Teleport>
              </div>
              <p v-if="tables.length > 0" class="text-xs text-gray-400 mt-1">
                共 {{ tables.length }} 张表，可搜索快速定位
              </p>
            </div>

            <div v-if="config.table_name" class="space-y-3">
              <div class="flex items-center justify-between gap-3">
                <h4 class="text-sm font-semibold text-gray-800">字段映射对比</h4>
                <span class="text-xs text-gray-400">左侧为本平台字段，右侧选择来源表对应列</span>
              </div>

              <div class="rounded-xl border border-gray-200 overflow-hidden">
                <div class="grid grid-cols-[1fr_auto_1fr] items-center bg-gray-50 border-b border-gray-200 text-xs font-semibold text-gray-500">
                  <div class="px-4 py-2.5">
                    <div class="text-gray-700">本平台</div>
                    <div class="mt-0.5 font-normal text-gray-400 truncate">{{ localPlatformLabel }}</div>
                  </div>
                  <div class="px-2 py-2.5 text-center">映射</div>
                  <div class="px-4 py-2.5">
                    <div class="text-gray-700">来源平台</div>
                    <div class="mt-0.5 font-normal text-gray-400 truncate">{{ sourcePlatformLabel }}</div>
                  </div>
                </div>

                <div
                  v-for="field in mappingFields"
                  :key="field.key"
                  class="grid grid-cols-[1fr_auto_1fr] items-center gap-2 px-4 py-3 border-b border-gray-100 last:border-b-0"
                >
                  <div class="min-w-0">
                    <div class="flex items-center gap-2 flex-wrap">
                      <code class="text-xs font-mono text-indigo-700 bg-indigo-50 px-1.5 py-0.5 rounded">{{ field.localField }}</code>
                      <span class="text-sm font-medium text-gray-800">{{ field.label }}</span>
                      <span v-if="field.required" class="text-red-500 text-xs">必填</span>
                    </div>
                    <p class="text-xs text-gray-400 mt-1">{{ field.hint }}</p>
                  </div>

                  <div class="text-gray-300 shrink-0 px-1" aria-hidden="true">
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 7l5 5m0 0l-5 5m5-5H6" />
                    </svg>
                  </div>

                  <div class="min-w-0">
                    <SourceColumnPicker
                      v-model="config.field_map[field.key]"
                      :columns="columns"
                      :loading="loadingColumns"
                      :disabled="!config.enabled"
                      accent="indigo"
                    />
                    <p v-if="config.field_map[field.key]" class="text-xs text-emerald-600 mt-1 truncate">
                      {{ config.table_name }}.{{ config.field_map[field.key] }}
                    </p>
                  </div>
                </div>
              </div>

              <p v-if="columns.length > 0" class="text-xs text-gray-400">
                共 {{ columns.length }} 个字段，可搜索字段名、中文备注或示例值
              </p>

              <div class="space-y-3 pt-1">
                <div class="flex items-center justify-between gap-3">
                  <div>
                    <h4 class="text-sm font-semibold text-gray-800">扩展字段映射</h4>
                    <p class="text-xs text-gray-400 mt-0.5">将来源表多个字段聚合为 JSON，写入本平台 <code class="font-mono text-indigo-600">extra_data</code></p>
                  </div>
                  <button
                    type="button"
                    class="text-xs font-medium text-indigo-600 hover:text-indigo-800 disabled:opacity-50"
                    :disabled="!config.enabled || loadingColumns"
                    @click="addExtraDataMapping"
                  >
                    + 添加映射
                  </button>
                </div>

                <div class="rounded-xl border border-dashed border-gray-200 overflow-hidden">
                  <div class="grid grid-cols-[1fr_auto_1fr] items-center bg-violet-50 border-b border-violet-100 text-xs font-semibold text-gray-500">
                    <div class="px-4 py-2.5">
                      <div class="text-gray-700">本平台</div>
                      <div class="mt-0.5 font-normal text-violet-600 truncate">extra_data · JSON 键名</div>
                    </div>
                    <div class="px-2 py-2.5 text-center">←</div>
                    <div class="px-4 py-2.5">
                      <div class="text-gray-700">来源平台</div>
                      <div class="mt-0.5 font-normal text-gray-400 truncate">{{ sourcePlatformLabel }}</div>
                    </div>
                  </div>

                  <div
                    v-if="config.extra_data_mappings.length === 0"
                    class="px-4 py-8 text-center text-sm text-gray-400"
                  >
                    可选：添加来源字段映射，同步时写入用户扩展 JSON
                  </div>

                  <div
                    v-for="(mapping, index) in config.extra_data_mappings"
                    :key="`extra-${index}`"
                    class="grid grid-cols-[1fr_auto_1fr] items-center gap-2 px-4 py-3 border-b border-gray-100 last:border-b-0"
                  >
                    <div class="min-w-0">
                      <input
                        v-model="mapping.json_key"
                        type="text"
                        placeholder="JSON 键名，如 id / dept_code"
                        class="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm font-mono focus:ring-2 focus:ring-violet-500 outline-none disabled:bg-gray-50"
                        :disabled="!config.enabled"
                      />
                      <p class="text-xs text-gray-400 mt-1">写入 extra_data["{{ mapping.json_key || '...' }}"]</p>
                    </div>

                    <div class="text-gray-300 shrink-0 px-1" aria-hidden="true">
                      <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 7l5 5m0 0l-5 5m5-5H6" />
                      </svg>
                    </div>

                    <div class="min-w-0 flex items-start gap-2">
                      <div class="flex-1 min-w-0">
                        <SourceColumnPicker
                          v-model="mapping.source_column"
                          :columns="columns"
                          :loading="loadingColumns"
                          :disabled="!config.enabled"
                          accent="violet"
                          @update:model-value="onExtraSourceColumnChange(mapping)"
                        />
                        <p v-if="mapping.source_column" class="text-xs text-emerald-600 mt-1 truncate">
                          {{ config.table_name }}.{{ mapping.source_column }}
                        </p>
                      </div>
                      <button
                        type="button"
                        class="p-2 text-red-400 hover:text-red-600 hover:bg-red-50 rounded-lg shrink-0 mt-0.5"
                        :disabled="!config.enabled"
                        @click="removeExtraDataMapping(index)"
                      >
                        <TrashIcon class="w-4 h-4" />
                      </button>
                    </div>
                  </div>
                </div>

                <div
                  v-if="extraDataPreviewExample"
                  class="rounded-lg border border-violet-100 bg-violet-50/50 px-3 py-2"
                >
                  <p class="text-[11px] font-semibold text-violet-700 mb-1">同步后 extra_data 示例</p>
                  <pre class="text-[11px] text-violet-900 font-mono whitespace-pre-wrap break-all">{{ extraDataPreviewExample }}</pre>
                </div>
              </div>
            </div>

            <p class="text-xs text-gray-500 bg-amber-50 border border-amber-100 rounded-lg p-3">
              以用户名为两边系统映射主键：本地不存在则新增（ID 自增、角色默认普通用户、API Key 自动生成）；本地已存在则更新真实姓名、备注与扩展 JSON，不修改角色与 API Key。
            </p>
          </section>

          <!-- 定时同步 -->
          <section class="space-y-3">
            <h3 class="text-sm font-bold text-gray-800 border-l-4 border-indigo-500 pl-2">定时同步</h3>
            <div class="grid grid-cols-2 gap-2">
              <button
                v-for="opt in scheduleOptions"
                :key="opt.value"
                type="button"
                :disabled="!config.enabled"
                @click="config.schedule = opt.value"
                class="px-4 py-3 rounded-xl border text-sm font-medium transition-all text-left disabled:cursor-not-allowed"
                :class="config.schedule === opt.value ? 'border-indigo-500 bg-indigo-50 text-indigo-700' : 'border-gray-200 text-gray-600 hover:border-gray-300'"
              >
                <div>{{ opt.label }}</div>
                <div class="text-xs font-normal mt-0.5 opacity-70">{{ opt.desc }}</div>
              </button>
            </div>
          </section>

          <!-- 手动同步 -->
          <section class="space-y-3">
            <div class="flex items-center justify-between">
              <h3 class="text-sm font-bold text-gray-800 border-l-4 border-indigo-500 pl-2">手动同步</h3>
              <button
                type="button"
                @click="loadPreview"
                :disabled="!config.enabled || loadingPreview || !canPreview"
                class="text-sm text-indigo-600 hover:text-indigo-800 font-medium disabled:opacity-50"
              >
                刷新预览
              </button>
            </div>

            <div class="flex items-center gap-2">
              <input
                v-model="hideExisting"
                type="checkbox"
                id="hide-existing-tp"
                :disabled="!config.enabled"
                class="rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
              />
              <label for="hide-existing-tp" class="text-sm text-gray-600">仅显示待新增用户</label>
            </div>

            <div v-if="loadingPreview" class="h-40 flex items-center justify-center text-gray-400">
              <div class="w-8 h-8 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin"></div>
            </div>

            <div v-else-if="!canPreview" class="h-32 flex items-center justify-center text-gray-400 text-sm bg-gray-50 rounded-xl border border-dashed border-gray-200">
              请先完成数据源与字段映射并保存配置
            </div>

            <div v-else-if="filteredPreview.length === 0" class="h-32 flex items-center justify-center text-gray-400 text-sm bg-gray-50 rounded-xl">
              暂无用户数据
            </div>

            <div v-else class="border border-gray-200 rounded-xl overflow-hidden max-h-64 overflow-y-auto">
              <table class="w-full text-sm">
                <thead class="bg-gray-50 text-gray-500 sticky top-0">
                  <tr>
                    <th class="px-3 py-2 text-left w-10">
                      <input
                        type="checkbox"
                        :checked="isAllSelected"
                        :disabled="!config.enabled"
                        @change="toggleSelectAll"
                        class="rounded border-gray-300 text-indigo-600"
                      />
                    </th>
                    <th class="px-3 py-2 text-left">用户名</th>
                    <th class="px-3 py-2 text-left">真实姓名</th>
                    <th class="px-3 py-2 text-left">备注</th>
                    <th class="px-3 py-2 text-left">同步动作</th>
                  </tr>
                </thead>
                <tbody>
                  <tr
                    v-for="user in filteredPreview"
                    :key="user.user_name"
                    class="border-t border-gray-100 hover:bg-gray-50 cursor-pointer"
                    @click="config.enabled && toggleSelect(user.user_name)"
                  >
                    <td class="px-3 py-2">
                      <input
                        type="checkbox"
                        :checked="selectedUserNames.includes(user.user_name)"
                        :disabled="!config.enabled"
                        @click.stop
                        @change="toggleSelect(user.user_name)"
                        class="rounded border-gray-300 text-indigo-600"
                      />
                    </td>
                    <td class="px-3 py-2 font-medium">{{ user.user_name }}</td>
                    <td class="px-3 py-2">{{ user.real_name || '-' }}</td>
                    <td class="px-3 py-2 text-gray-500 truncate max-w-[120px]">{{ user.remark || '-' }}</td>
                    <td class="px-3 py-2">
                      <span
                        class="text-xs px-2 py-0.5 rounded-full"
                        :class="user.is_existing ? 'bg-blue-50 text-blue-700' : 'bg-green-50 text-green-700'"
                      >
                        {{ user.is_existing ? '将更新' : '待新增' }}
                      </span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>
        </div>
      </div>

      <!-- Footer -->
      <div class="px-6 py-4 border-t border-gray-100 flex items-center justify-between shrink-0 bg-gray-50">
        <button
          type="button"
          @click="saveConfig"
          :disabled="saving"
          class="px-4 py-2 bg-white border border-gray-200 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-100 disabled:opacity-50"
        >
          {{ saving ? '保存中...' : '保存配置' }}
        </button>
        <div class="flex gap-2">
          <button
            type="button"
            @click="runSyncAll"
            :disabled="!config.enabled || syncing || !canPreview || previewUsers.length === 0"
            class="px-4 py-2 bg-indigo-100 text-indigo-700 rounded-lg text-sm font-medium hover:bg-indigo-200 disabled:opacity-50"
          >
            同步全部 ({{ previewUsers.length }})
          </button>
          <button
            type="button"
            @click="runSyncSelected"
            :disabled="!config.enabled || syncing || selectedUserNames.length === 0"
            class="px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm font-medium hover:bg-indigo-700 disabled:opacity-50 flex items-center gap-2"
          >
            <span v-if="syncing" class="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin"></span>
            同步选中 ({{ selectedUserNames.length }})
          </button>
        </div>
      </div>
    </div>

    <!-- 功能说明弹层 -->
    <div
      v-if="showHelpModal"
      class="fixed inset-0 bg-black/45 backdrop-blur-sm z-[10002] flex items-center justify-center p-4"
      @click.self="showHelpModal = false"
    >
      <div class="bg-white rounded-2xl shadow-2xl max-w-2xl w-full max-h-[85vh] overflow-y-auto border border-gray-100 relative">
        <button
          type="button"
          class="absolute top-4 right-4 p-1.5 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded-lg transition-colors"
          @click="showHelpModal = false"
        >
          <XMarkIcon class="w-5 h-5" />
        </button>

        <div class="p-6 sm:p-8 space-y-5">
          <div class="flex items-center gap-3 border-b border-gray-100 pb-4 pr-8">
            <div class="w-8 h-8 rounded-lg bg-indigo-600 text-white flex items-center justify-center font-black text-sm">?</div>
            <div>
              <h3 class="text-lg font-bold text-gray-900">第三方用户同步说明</h3>
              <p class="text-xs text-gray-400 mt-0.5">从外部业务库拉取账号，写入云枢用户表</p>
            </div>
          </div>

          <section class="space-y-2">
            <h4 class="text-sm font-bold text-gray-800 flex items-center gap-2">
              <span class="w-1 h-3.5 bg-indigo-500 rounded-full"></span>
              功能作用
            </h4>
            <p class="text-sm text-gray-600 leading-relaxed pl-3">
              将第三方数据库中的用户账号同步到本平台 <code class="text-indigo-600 bg-indigo-50 px-1 rounded">ai_agent_users</code>，
              用于统一登录身份、API Key 发放与权限管理。支持手动触发与定时自动同步。
            </p>
          </section>

          <section class="space-y-2">
            <h4 class="text-sm font-bold text-gray-800 flex items-center gap-2">
              <span class="w-1 h-3.5 bg-indigo-500 rounded-full"></span>
              核心映射原则
            </h4>
            <ul class="text-sm text-gray-600 leading-relaxed pl-3 space-y-1.5 list-disc list-inside">
              <li><strong>用户名</strong> 是两边系统的唯一映射主键（不是第三方 ID）。</li>
              <li>本地 <strong>不存在</strong> 同名用户 → <strong>新增</strong>：ID 自增，角色默认普通用户，自动生成 API Key。</li>
              <li>本地 <strong>已存在</strong> 同名用户 → <strong>更新</strong>：刷新真实姓名、备注、扩展 JSON；不修改角色与 API Key。</li>
            </ul>
          </section>

          <section class="space-y-2">
            <h4 class="text-sm font-bold text-gray-800 flex items-center gap-2">
              <span class="w-1 h-3.5 bg-violet-500 rounded-full"></span>
              字段怎么配
            </h4>
            <div class="pl-3 space-y-2 text-sm text-gray-600 leading-relaxed">
              <p><strong>基础字段映射</strong>：左侧是本平台字段，右侧选择来源表列。至少配置「用户名」。</p>
              <p><strong>扩展字段映射</strong>：可把来源表多个列聚合为 JSON，写入本平台 <code class="text-violet-600 bg-violet-50 px-1 rounded">extra_data</code>（如 id、部门编码等）。</p>
            </div>
          </section>

          <section class="space-y-2">
            <h4 class="text-sm font-bold text-gray-800 flex items-center gap-2">
              <span class="w-1 h-3.5 bg-amber-500 rounded-full"></span>
              使用提示
            </h4>
            <ul class="text-sm text-gray-600 leading-relaxed pl-3 space-y-1.5 list-disc list-inside">
              <li>预览与同步会使用当前表单配置，可先预览再保存。</li>
              <li>「保存配置」用于持久化设置并注册定时任务。</li>
              <li>扩展 JSON 键名请勿重复；表与字段均支持搜索快速定位。</li>
            </ul>
          </section>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import axios from 'axios'
import { XMarkIcon, TrashIcon } from '@heroicons/vue/24/outline'
import Switch from './Switch.vue'
import SourceColumnPicker from './SourceColumnPicker.vue'
import { useToast } from '../composables/useToast'

interface ExtraDataMapping {
  json_key: string
  source_column: string
}

interface FieldMap {
  user_name: string
  real_name: string | null
  remark: string | null
}

interface SyncConfig {
  enabled: boolean
  connection_config_id: number | null
  table_name: string | null
  field_map: FieldMap
  extra_data_mappings: ExtraDataMapping[]
  schedule: 'off' | 'hourly' | 'daily' | 'weekly'
}

const props = defineProps<{ visible: boolean }>()
const emit = defineEmits<{ close: []; synced: [] }>()
const { showToast } = useToast()

const scheduleOptions = [
  { value: 'off' as const, label: '关闭', desc: '不执行定时同步' },
  { value: 'hourly' as const, label: '每小时', desc: '每小时整点执行' },
  { value: 'daily' as const, label: '每天', desc: '每天凌晨 2:00' },
  { value: 'weekly' as const, label: '每周', desc: '每周一凌晨 2:00' },
]

const mappingFields = [
  {
    key: 'user_name' as const,
    label: '用户名',
    localField: 'user_name',
    required: true,
    hint: '映射主键：按用户名判断新增或更新',
  },
  {
    key: 'real_name' as const,
    label: '真实姓名',
    localField: 'real_name',
    required: false,
    hint: '本地已存在同名用户时更新',
  },
  {
    key: 'remark' as const,
    label: '备注',
    localField: 'remark',
    required: false,
    hint: '本地已存在同名用户时更新',
  },
]

const saving = ref(false)
const syncing = ref(false)
const loadingTables = ref(false)
const loadingColumns = ref(false)
const loadingPreview = ref(false)
const hideExisting = ref(false)
const selectedUserNames = ref<string[]>([])

const datasources = ref<any[]>([])
const tables = ref<any[]>([])
const columns = ref<any[]>([])
const previewUsers = ref<any[]>([])
const tablePickerRef = ref<HTMLElement | null>(null)
const tableTriggerRef = ref<HTMLButtonElement | null>(null)
const tableDropdownRef = ref<HTMLElement | null>(null)
const tableSearchInputRef = ref<HTMLInputElement | null>(null)
const tablePickerOpen = ref(false)
const tableSearchQuery = ref('')
const tableDropdownStyle = ref<Record<string, string>>({})
const showHelpModal = ref(false)

const TABLE_SEARCH_BAR_HEIGHT = 44
const TABLE_MIN_LIST_HEIGHT = 120
const TABLE_PREFERRED_LIST_HEIGHT = 224

const defaultConfig = (): SyncConfig => ({
  enabled: false,
  connection_config_id: null,
  table_name: null,
  field_map: { user_name: '', real_name: null, remark: null },
  extra_data_mappings: [],
  schedule: 'off',
})

const config = ref<SyncConfig>(defaultConfig())

const selectedDatasource = computed(() =>
  datasources.value.find((ds) => ds.id === config.value.connection_config_id) || null,
)

const localPlatformLabel = computed(() => 'ai_agent_users · 云枢用户表')

const sourcePlatformLabel = computed(() => {
  const ds = selectedDatasource.value
  const table = config.value.table_name || '未选表'
  if (!ds) return table
  return `${table} · ${ds.name} (${ds.db_type})`
})

const extraDataPreviewExample = computed(() => {
  const payload: Record<string, string> = {}
  for (const item of config.value.extra_data_mappings) {
    const key = String(item.json_key || '').trim()
    const col = String(item.source_column || '').trim()
    if (!key || !col) continue
    payload[key] = `<${col}>`
  }
  if (Object.keys(payload).length === 0) return ''
  return JSON.stringify(payload, null, 2)
})

const sanitizeExtraDataMappings = () =>
  (config.value?.extra_data_mappings || [])
    .map((item) => ({
      json_key: String(item?.json_key || '').trim(),
      source_column: String(item?.source_column || '').trim(),
    }))
    .filter((item) => item.json_key && item.source_column)

const addExtraDataMapping = () => {
  config.value.extra_data_mappings.push({ json_key: '', source_column: '' })
}

const removeExtraDataMapping = (index: number) => {
  config.value.extra_data_mappings.splice(index, 1)
}

const onExtraSourceColumnChange = (mapping: ExtraDataMapping) => {
  if (!mapping.json_key.trim() && mapping.source_column.trim()) {
    mapping.json_key = mapping.source_column.trim()
  }
}

const canPreview = computed(
  () =>
    config.value.enabled &&
    !!config.value.connection_config_id &&
    !!config.value.table_name &&
    !!config.value.field_map.user_name
)

const filteredPreview = computed(() => {
  let list = previewUsers.value
  if (hideExisting.value) list = list.filter((u) => !u.is_existing)
  return list
})

const isAllSelected = computed(() => {
  const visible = filteredPreview.value
  return visible.length > 0 && visible.every((u) => selectedUserNames.value.includes(u.user_name))
})

const selectedTable = computed(() =>
  tables.value.find((table) => table.name === config.value.table_name) || null,
)

const tableSubtitle = (table: { comment?: string; type?: string }) => {
  const comment = String(table.comment || '').trim()
  if (comment) return comment
  return table.type || '—'
}

const selectedTableSubtitle = computed(() => {
  if (!selectedTable.value) return ''
  return tableSubtitle(selectedTable.value)
})

const filteredTables = computed(() => {
  const keyword = tableSearchQuery.value.trim().toLowerCase()
  if (!keyword) return tables.value
  return tables.value.filter((table) => {
    const name = String(table.name || '').toLowerCase()
    const type = String(table.type || '').toLowerCase()
    const comment = String(table.comment || '').toLowerCase()
    return name.includes(keyword) || type.includes(keyword) || comment.includes(keyword)
  })
})

const closeTablePicker = () => {
  tablePickerOpen.value = false
  tableSearchQuery.value = ''
}

const updateTableDropdownPosition = () => {
  const trigger = tableTriggerRef.value
  if (!trigger) return

  const rect = trigger.getBoundingClientRect()
  const viewportHeight = window.innerHeight
  const spaceBelow = viewportHeight - rect.bottom - 8
  const spaceAbove = rect.top - 8
  const openDown = spaceBelow >= TABLE_MIN_LIST_HEIGHT || spaceBelow >= spaceAbove

  const listHeight = Math.min(
    TABLE_PREFERRED_LIST_HEIGHT,
    Math.max(
      TABLE_MIN_LIST_HEIGHT,
      openDown ? spaceBelow - TABLE_SEARCH_BAR_HEIGHT : spaceAbove - TABLE_SEARCH_BAR_HEIGHT,
    ),
  )
  const totalHeight = listHeight + TABLE_SEARCH_BAR_HEIGHT

  tableDropdownStyle.value = {
    top: openDown ? `${rect.bottom + 4}px` : `${rect.top - totalHeight - 4}px`,
    left: `${rect.left}px`,
    width: `${rect.width}px`,
    height: `${totalHeight}px`,
  }
}

const toggleTablePicker = async () => {
  if (!config.value.enabled || !config.value.connection_config_id || loadingTables.value) return
  tablePickerOpen.value = !tablePickerOpen.value
  if (tablePickerOpen.value) {
    await nextTick()
    updateTableDropdownPosition()
    tableSearchInputRef.value?.focus()
  } else {
    tableSearchQuery.value = ''
  }
}

const selectTable = (tableName: string) => {
  if (config.value.table_name === tableName) {
    closeTablePicker()
    return
  }
  config.value.table_name = tableName
  closeTablePicker()
  onTableChange()
}

const onTablePickerOutside = (event: MouseEvent) => {
  const target = event.target as Node
  if (tablePickerRef.value?.contains(target)) return
  if (tableDropdownRef.value?.contains(target)) return
  closeTablePicker()
}

const onTablePickerReposition = () => {
  if (tablePickerOpen.value) updateTableDropdownPosition()
}

watch(tablePickerOpen, (isOpen) => {
  if (isOpen) nextTick(() => updateTableDropdownPosition())
})

const loadConfig = async () => {
  try {
    const res = await axios.get('/api/portal/management/third-party-sync/config')
    const data = res.data?.data ?? res.data
    config.value = {
      ...defaultConfig(),
      ...data,
      field_map: {
        user_name: data?.field_map?.user_name || '',
        real_name: data?.field_map?.real_name ?? null,
        remark: data?.field_map?.remark ?? null,
      },
      extra_data_mappings: Array.isArray(data?.extra_data_mappings)
        ? data.extra_data_mappings.map((item: ExtraDataMapping) => ({
            json_key: item?.json_key || '',
            source_column: item?.source_column || '',
          }))
        : [],
    }
  } catch {
    showToast('加载同步配置失败', 'error')
  }
}

const loadDatasources = async () => {
  try {
    const res = await axios.get('/api/portal/management/third-party-sync/datasources')
    datasources.value = res.data?.items ?? []
  } catch {
    showToast('加载数据源列表失败', 'error')
  }
}

const loadTables = async () => {
  if (!config.value.connection_config_id) return
  loadingTables.value = true
  try {
    const res = await axios.get('/api/portal/management/third-party-sync/tables', {
      params: { connection_config_id: config.value.connection_config_id },
    })
    tables.value = res.data?.items ?? []
  } catch (e: any) {
    showToast(e.response?.data?.detail || '加载表列表失败', 'error')
  } finally {
    loadingTables.value = false
  }
}

const loadColumns = async () => {
  if (!config.value.connection_config_id || !config.value.table_name) return
  loadingColumns.value = true
  try {
    const res = await axios.get('/api/portal/management/third-party-sync/columns', {
      params: {
        connection_config_id: config.value.connection_config_id,
        table_name: config.value.table_name,
      },
    })
    columns.value = res.data?.items ?? []
  } catch (e: any) {
    showToast(e.response?.data?.detail || '加载字段列表失败', 'error')
  } finally {
    loadingColumns.value = false
  }
}

const onDatasourceChange = () => {
  config.value.table_name = null
  config.value.field_map = { user_name: '', real_name: null, remark: null }
  config.value.extra_data_mappings = []
  tables.value = []
  columns.value = []
  previewUsers.value = []
  closeTablePicker()
  loadTables()
}

const onTableChange = () => {
  config.value.field_map = { user_name: '', real_name: null, remark: null }
  config.value.extra_data_mappings = []
  previewUsers.value = []
  loadColumns()
}

const saveConfig = async () => {
  console.log("saveConfig triggered. config enabled:", config.value?.enabled)
  if (config.value?.enabled) {
    if (!config.value?.field_map?.user_name) {
      showToast('用户名字段映射为必填项', 'error')
      return
    }
    const keys = sanitizeExtraDataMappings().map((item) => item.json_key)
    if (new Set(keys).size !== keys.length) {
      showToast('扩展字段 JSON 键名不能重复', 'error')
      return
    }
  }
  saving.value = true
  try {
    const payload = buildConfigPayload()
    console.log("Saving third-party sync config payload:", payload)
    const res = await axios.put('/api/portal/management/third-party-sync/config', payload)
    console.log("Save config success response:", res.data)
    config.value = {
      ...config.value,
      ...(res.data?.data || {}),
      extra_data_mappings: res.data?.data?.extra_data_mappings || payload.extra_data_mappings,
    }
    showToast('配置已保存', 'success')
    if (canPreview.value) await loadPreview()
  } catch (e: any) {
    console.error("Save config error:", e)
    const errMessage = e.response?.data?.detail || e.message || '保存配置失败'
    showToast(errMessage, 'error')
  } finally {
    saving.value = false
  }
}

const buildConfigPayload = () => ({
  ...config.value,
  extra_data_mappings: sanitizeExtraDataMappings(),
})

const loadPreview = async () => {
  if (!canPreview.value) return
  loadingPreview.value = true
  selectedUserNames.value = []
  try {
    const res = await axios.post('/api/portal/management/third-party-sync/preview', {
      config: buildConfigPayload(),
    })
    previewUsers.value = res.data?.items ?? []
  } catch (e: any) {
    showToast(e.response?.data?.detail || '加载预览失败', 'error')
  } finally {
    loadingPreview.value = false
  }
}

const toggleSelect = (userName: string) => {
  const idx = selectedUserNames.value.indexOf(userName)
  if (idx >= 0) selectedUserNames.value.splice(idx, 1)
  else selectedUserNames.value.push(userName)
}

const toggleSelectAll = () => {
  if (isAllSelected.value) selectedUserNames.value = []
  else selectedUserNames.value = filteredPreview.value.map((u) => u.user_name)
}

const runSync = async (userNames: string[] | null) => {
  syncing.value = true
  try {
    const res = await axios.post('/api/portal/management/third-party-sync/run', {
      user_names: userNames,
      config: buildConfigPayload(),
    })
    showToast(res.data?.message || '同步完成', 'success')
    await loadPreview()
    emit('synced')
  } catch (e: any) {
    showToast(e.response?.data?.detail || '同步失败', 'error')
  } finally {
    syncing.value = false
  }
}

const runSyncSelected = () => runSync(selectedUserNames.value)
const runSyncAll = () => runSync(null)

watch(
  () => props.visible,
  async (v) => {
    if (!v) {
      closeTablePicker()
      showHelpModal.value = false
      return
    }
    previewUsers.value = []
    await Promise.all([loadConfig(), loadDatasources()])
    if (config.value.connection_config_id) await loadTables()
    if (config.value.table_name) await loadColumns()
    if (canPreview.value) await loadPreview()
  }
)

onMounted(() => {
  document.addEventListener('click', onTablePickerOutside)
  window.addEventListener('resize', onTablePickerReposition)
  window.addEventListener('scroll', onTablePickerReposition, true)
  if (props.visible) loadConfig()
})

onBeforeUnmount(() => {
  document.removeEventListener('click', onTablePickerOutside)
  window.removeEventListener('resize', onTablePickerReposition)
  window.removeEventListener('scroll', onTablePickerReposition, true)
})
</script>

<style scoped>
@keyframes slide-in-right {
  from { transform: translateX(100%); }
  to { transform: translateX(0); }
}
.animate-slide-in-right {
  animation: slide-in-right 0.25s ease-out;
}
</style>
