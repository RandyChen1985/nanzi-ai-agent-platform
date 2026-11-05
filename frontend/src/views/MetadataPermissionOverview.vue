<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import axios from '@/utils/axios'
import { useUser } from '../composables/useUser'

/**
 * 数据集权限总览（只读）。
 *
 * 解决的是「逐个点进数据集才能知道有没有配授权」的问题：一屏看清谁进得来、
 * 能看哪些表、行级怎么限制、什么时候到期。这里是只读视图——改权限仍然回
 * 各数据集的「权限管理」页签，避免一个列表就能改全平台授权。
 */
const router = useRouter()
const { isAdmin, hasPermission } = useUser()

const canView = computed(() => isAdmin.value || hasPermission('element:metadata:edit'))

const loading = ref(false)
const keyword = ref('')
const filterKey = ref('all')
const sortKey = ref('risk')
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const summary = ref<Record<string, number>>({})
const datasets = ref<any[]>([])
const openId = ref<number | null>(null)
const loadError = ref('')

const filters = [
  { key: 'all', label: '全部' },
  { key: 'unauthorized', label: '无人可访问' },
  { key: 'soon', label: '有到期' },
  { key: 'narrowed', label: '有收窄' },
  { key: 'rowlevel', label: '有行级' },
  { key: 'all_baseline', label: '有全员' },
]

const filterCount = (key: string) => {
  const s = summary.value
  if (key === 'unauthorized') return s.nobody ?? 0
  if (key === 'soon') return s.soon ?? 0
  if (key === 'narrowed') return s.narrowed ?? 0
  if (key === 'rowlevel') return s.rowlevel ?? 0
  if (key === 'all_baseline') return s.all_baseline ?? 0
  return s.datasets ?? 0
}

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize.value)))

const fetchData = async () => {
  if (!canView.value) return
  loading.value = true
  loadError.value = ''
  try {
    const res = await axios.get('/api/portal/metadata/dataset-permission-overview', {
      params: {
        keyword: keyword.value.trim(),
        filter: filterKey.value,
        page: page.value,
        page_size: pageSize.value,
        sort: sortKey.value,
      },
    })
    const payload = res.data?.data || {}
    summary.value = payload.summary || {}
    datasets.value = payload.datasets || []
    total.value = payload.total ?? 0
    if (page.value > totalPages.value) page.value = totalPages.value
  } catch (e: any) {
    loadError.value = e.response?.data?.detail || e.message || '加载权限总览失败'
    datasets.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

// 搜索防抖，避免每敲一个字打一次接口
let searchTimer: ReturnType<typeof setTimeout> | null = null
watch(keyword, () => {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    page.value = 1
    void fetchData()
  }, 300)
})
watch([filterKey, sortKey], () => {
  page.value = 1
  void fetchData()
})
watch(page, () => void fetchData())

const toggleOpen = (id: number) => {
  openId.value = openId.value === id ? null : id
}

const goToPermission = (ds: any) => {
  router.push({ path: `/dashboard/metadata/${ds.id}`, query: { tab: 'permissions' } })
}

const setFilter = (key: string) => {
  filterKey.value = key
}

const refresh = () => {
  openId.value = null
  void fetchData()
}

const subjectParts = (ds: any) => {
  const parts: { text: string; cls: string }[] = []
  if (ds.has_all) parts.push({ text: '全员基线', cls: 'bg-emerald-50 text-emerald-700 border-emerald-200' })
  if (ds.roles) parts.push({ text: `${ds.roles} 角色`, cls: 'bg-sky-50 text-sky-700 border-sky-200' })
  if (ds.users) parts.push({ text: `${ds.users} 用户`, cls: 'bg-violet-50 text-violet-700 border-violet-200' })
  return parts
}

const expireText = (ds: any) => {
  if (ds.alive === 0) return '—'
  if (ds.dead) return `${ds.dead} 条已过期`
  if (ds.soon) return `${ds.soon_min_days} 天后到期`
  return '长期'
}

const scopeText = (ds: any) => {
  if (ds.alive === 0) return '—'
  if (!ds.narrowed) return '全部表'
  return `${ds.narrowed} 条收窄`
}

const rowPolicyText = (policy: string) => {
  if (policy === 'none') return '不做行级过滤'
  if (policy === 'custom') return '自定义条件'
  return '继承数据集策略'
}

const grantScopeText = (ds: any, grant: any) => {
  if (grant.scope === 'all') return '全部表'
  const total = ds.table_count ? ` / ${ds.table_count} 张` : ' 张'
  return `指定 ${grant.table_count}${total}`
}

const grantExpireText = (grant: any) => {
  if (!grant.expire_at) return '长期'
  if (grant.expired) return `${grant.expire_at} 已过期`
  return `${grant.expire_at} 到期`
}

onMounted(() => {
  void fetchData()
})
</script>

<template>
  <div class="space-y-5">
    <!-- 头部 -->
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 class="flex flex-wrap items-center gap-2 text-xl font-bold text-gray-900">
          <svg class="h-5 w-5 text-emerald-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
          </svg>
          数据集权限总览
          <span class="rounded-full border border-violet-200 bg-violet-50 px-2 py-0.5 text-[10px] font-bold text-violet-700">
            仅管理员 · 只读
          </span>
        </h1>
        <p class="mt-1 max-w-3xl text-xs leading-relaxed text-gray-500">
          一屏看清哪些数据集配了授权、授给了谁、有没有收窄到表、有没有到期。这里是只读视图，
          改权限仍然回到各个数据集的「权限管理」页签。
        </p>
      </div>
      <div class="flex items-center gap-2">
        <button
          type="button"
          class="rounded-xl border border-gray-200 px-3 py-2 text-xs font-semibold text-gray-600 transition-colors hover:bg-gray-50"
          @click="router.push('/dashboard/metadata')"
        >
          返回元数据管理
        </button>
        <button
          type="button"
          class="rounded-xl border border-gray-200 px-3 py-2 text-xs font-semibold text-gray-600 transition-colors hover:bg-gray-50 disabled:opacity-50"
          :disabled="loading"
          @click="refresh"
        >
          刷新
        </button>
      </div>
    </div>

    <!-- 无权限 -->
    <div
      v-if="!canView"
      class="rounded-xl border border-gray-200 bg-white px-6 py-16 text-center"
    >
      <div class="mx-auto mb-3 grid h-11 w-11 place-items-center rounded-xl border border-gray-200 bg-gray-50 text-gray-400">
        <svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
        </svg>
      </div>
      <p class="text-sm font-bold text-gray-800">这个页面只有管理员能看</p>
      <p class="mx-auto mt-1.5 max-w-md text-xs leading-relaxed text-gray-500">
        授权对象名单本身就是管理信息：知道「谁能进哪个数据集」等于知道组织的数据边界。
      </p>
    </div>

    <template v-else>
      <!-- 统计条 -->
      <div class="grid grid-cols-2 divide-gray-100 overflow-hidden rounded-xl border border-gray-200 bg-white sm:grid-cols-3 lg:grid-cols-6 lg:divide-x">
        <div class="border-b border-gray-100 px-4 py-3 lg:border-b-0">
          <p class="text-[10px] font-bold uppercase tracking-wide text-gray-400">数据集</p>
          <p class="mt-1 text-xl font-bold leading-none text-gray-900">{{ summary.datasets ?? 0 }}</p>
          <p class="mt-1 text-[10px] text-gray-400">全部数据集</p>
        </div>
        <div class="border-b border-gray-100 px-4 py-3 lg:border-b-0">
          <p class="text-[10px] font-bold uppercase tracking-wide text-gray-400">已授权</p>
          <p class="mt-1 text-xl font-bold leading-none text-emerald-600">{{ summary.granted ?? 0 }}</p>
          <p class="mt-1 text-[10px] text-gray-400">
            覆盖 {{ summary.datasets ? Math.round(((summary.granted ?? 0) / summary.datasets) * 100) : 0 }}%
          </p>
        </div>
        <div class="border-b border-gray-100 px-4 py-3 lg:border-b-0">
          <p class="text-[10px] font-bold uppercase tracking-wide text-gray-400">无人可访问</p>
          <p class="mt-1 text-xl font-bold leading-none text-red-600">{{ summary.nobody ?? 0 }}</p>
          <p class="mt-1 text-[10px] text-gray-400">
            未配置 {{ summary.unauthorized ?? 0 }} · 已过期 {{ summary.stale ?? 0 }}
          </p>
        </div>
        <div class="border-b border-gray-100 px-4 py-3 lg:border-b-0">
          <p class="text-[10px] font-bold uppercase tracking-wide text-gray-400">7 天内到期</p>
          <p class="mt-1 text-xl font-bold leading-none text-amber-600">{{ summary.soon ?? 0 }}</p>
          <p class="mt-1 text-[10px] text-gray-400">需要续期</p>
        </div>
        <div class="border-b border-gray-100 px-4 py-3 sm:border-b-0">
          <p class="text-[10px] font-bold uppercase tracking-wide text-gray-400">有表级收窄</p>
          <p class="mt-1 text-xl font-bold leading-none text-amber-600">{{ summary.narrowed ?? 0 }}</p>
          <p class="mt-1 text-[10px] text-gray-400">只授权了部分表</p>
        </div>
        <div class="px-4 py-3">
          <p class="text-[10px] font-bold uppercase tracking-wide text-gray-400">有行级限制</p>
          <p class="mt-1 text-xl font-bold leading-none text-gray-900">{{ summary.rowlevel ?? 0 }}</p>
          <p class="mt-1 text-[10px] text-gray-400">自定义条件或不过滤</p>
        </div>
      </div>

      <!-- 列表 -->
      <div class="overflow-hidden rounded-xl border border-gray-200 bg-white">
        <div class="flex flex-wrap items-center gap-2 border-b border-gray-100 px-4 py-3">
          <label class="flex min-w-[15rem] flex-1 items-center gap-1.5 rounded-lg border border-gray-200 bg-gray-50/60 px-2.5 py-1.5 text-gray-400">
            <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
            </svg>
            <input
              v-model="keyword"
              type="search"
              placeholder="搜索数据集名称 / 标识 / 授权对象"
              class="w-full bg-transparent text-xs text-gray-700 placeholder:text-gray-400 focus:outline-none"
            />
          </label>
          <div class="flex flex-wrap gap-1.5">
            <button
              v-for="f in filters"
              :key="f.key"
              type="button"
              class="inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-[11px] font-semibold transition-colors"
              :class="filterKey === f.key
                ? 'border-gray-900 bg-gray-900 text-white'
                : 'border-gray-200 bg-white text-gray-600 hover:bg-gray-50'"
              @click="setFilter(f.key)"
            >
              {{ f.label }}
              <span class="text-[10px]" :class="filterKey === f.key ? 'text-white/70' : 'text-gray-400'">
                {{ filterCount(f.key) }}
              </span>
            </button>
          </div>
          <select
            v-model="sortKey"
            class="ml-auto rounded-lg border border-gray-200 bg-white px-2 py-1.5 text-[11px] text-gray-600 focus:outline-none"
          >
            <option value="risk">默认：按风险排序</option>
            <option value="name">按名称</option>
            <option value="updated">按最近更新</option>
          </select>
        </div>

        <div v-if="loading" class="py-16 text-center text-xs text-gray-400">正在加载授权分配…</div>
        <div v-else-if="loadError" class="py-16 text-center text-xs text-red-500">{{ loadError }}</div>

        <div v-else class="overflow-x-auto">
          <table class="w-full text-xs">
            <thead>
              <tr class="border-b border-gray-100 bg-gray-50/60 text-[10px] uppercase tracking-wide text-gray-400">
                <th class="px-4 py-2.5 text-left font-bold">数据集</th>
                <th class="px-4 py-2.5 text-left font-bold">授权主体</th>
                <th class="px-4 py-2.5 text-left font-bold">表范围</th>
                <th class="px-4 py-2.5 text-left font-bold">行级</th>
                <th class="px-4 py-2.5 text-left font-bold">有效期</th>
                <th class="px-4 py-2.5 text-left font-bold">最近变更</th>
                <th class="px-4 py-2.5 text-right font-bold">操作</th>
              </tr>
            </thead>
            <tbody>
              <template v-for="ds in datasets" :key="ds.id">
                <tr
                  class="cursor-pointer border-b border-gray-100 transition-colors hover:bg-gray-50/60"
                  :class="[
                    openId === ds.id ? 'bg-gray-50/60' : '',
                    ds.alive === 0 ? 'bg-red-50/40 hover:bg-red-50/60' : '',
                  ]"
                  @click="toggleOpen(ds.id)"
                >
                  <td class="px-4 py-2.5">
                    <div class="flex items-center gap-2">
                      <span class="grid h-7 w-7 shrink-0 place-items-center rounded-lg border border-sky-200 bg-sky-50 text-sky-600">
                        <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <ellipse cx="12" cy="6" rx="7" ry="3" />
                          <path d="M5 6v6c0 1.7 3.1 3 7 3s7-1.3 7-3V6" />
                          <path d="M5 12v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6" />
                        </svg>
                      </span>
                      <div class="min-w-0">
                        <p class="truncate text-[12.5px] font-bold text-gray-900">{{ ds.display_name || ds.name }}</p>
                        <p class="mt-0.5 flex items-center gap-1 truncate text-[10px] text-gray-400">
                          <span class="font-mono">#{{ ds.name }}</span>
                          <span>·</span>
                          <span>{{ ds.data_source || '—' }}</span>
                          <span>·</span>
                          <span>{{ ds.table_count }} 表</span>
                        </p>
                      </div>
                      <svg
                        class="ml-1 h-3 w-3 shrink-0 text-gray-300 transition-transform"
                        :class="openId === ds.id ? 'rotate-90' : ''"
                        fill="none" stroke="currentColor" viewBox="0 0 24 24"
                      >
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
                      </svg>
                    </div>
                  </td>
                  <td class="px-4 py-2.5">
                    <div v-if="ds.alive === 0" class="flex flex-col gap-0.5">
                      <span
                        class="inline-flex w-fit items-center rounded-md border px-1.5 py-0.5 text-[10px] font-bold"
                        :class="ds.total > 0 ? 'border-gray-200 bg-gray-100 text-gray-500' : 'border-red-200 bg-red-50 text-red-600'"
                      >
                        {{ ds.total > 0 ? `${ds.total} 条已全部过期` : '未授权' }}
                      </span>
                      <span class="text-[10px] font-semibold text-red-600">
                        {{ ds.total > 0 ? '当前没人能访问这个数据集' : '任何人进不来这个数据集' }}
                      </span>
                    </div>
                    <div v-else class="flex flex-col gap-1">
                      <div class="flex flex-wrap gap-1">
                        <span
                          v-for="part in subjectParts(ds)"
                          :key="part.text"
                          class="inline-flex items-center rounded-md border px-1.5 py-0.5 text-[10px] font-bold"
                          :class="part.cls"
                        >{{ part.text }}</span>
                      </div>
                      <span v-if="ds.dead" class="text-[10px] text-gray-400">
                        （{{ ds.dead }} 条已过期，不再生效）
                      </span>
                    </div>
                  </td>
                  <td class="px-4 py-2.5">
                    <div class="flex flex-col gap-0.5">
                      <span
                        class="inline-flex w-fit items-center rounded-md border px-1.5 py-0.5 text-[10px] font-bold"
                        :class="ds.narrowed ? 'border-amber-200 bg-amber-50 text-amber-700' : 'border-gray-200 bg-slate-50 text-slate-600'"
                      >{{ scopeText(ds) }}</span>
                      <span v-if="ds.narrowed && ds.has_all" class="text-[10px] text-gray-400">全员仍可见全部表</span>
                    </div>
                  </td>
                  <td class="px-4 py-2.5">
                    <div class="flex flex-wrap gap-1">
                      <span
                        v-if="ds.custom_row"
                        class="inline-flex items-center rounded-md border border-sky-200 bg-sky-50 px-1.5 py-0.5 text-[10px] font-bold text-sky-700"
                      >自定义 {{ ds.custom_row }}</span>
                      <span
                        v-if="ds.no_row"
                        class="inline-flex items-center rounded-md border border-amber-200 bg-amber-50 px-1.5 py-0.5 text-[10px] font-bold text-amber-700"
                      >不过滤 {{ ds.no_row }}</span>
                      <span
                        v-if="!ds.custom_row && !ds.no_row"
                        class="inline-flex items-center rounded-md border border-gray-200 bg-slate-50 px-1.5 py-0.5 text-[10px] font-bold text-slate-600"
                      >{{ ds.alive === 0 ? '—' : '继承数据集策略' }}</span>
                    </div>
                  </td>
                  <td class="px-4 py-2.5">
                    <span
                      class="inline-flex items-center rounded-md border px-1.5 py-0.5 text-[10px] font-bold"
                      :class="ds.dead ? 'border-gray-200 bg-gray-100 text-gray-500'
                        : ds.soon ? 'border-amber-200 bg-amber-50 text-amber-700'
                        : 'border-gray-200 bg-slate-50 text-slate-600'"
                    >{{ expireText(ds) }}</span>
                  </td>
                  <td class="px-4 py-2.5 text-[10px] text-gray-400">{{ ds.updated_at || '—' }}</td>
                  <td class="px-4 py-2.5 text-right">
                    <button
                      type="button"
                      class="rounded-lg border border-gray-200 px-2 py-1 text-[10px] font-semibold text-gray-600 transition-colors hover:border-emerald-300 hover:bg-emerald-50 hover:text-emerald-700"
                      @click.stop="goToPermission(ds)"
                    >
                      去配置
                    </button>
                  </td>
                </tr>

                <!-- 展开：授权明细 -->
                <tr v-if="openId === ds.id" class="border-b border-gray-100 bg-gray-50/40">
                  <td colspan="7" class="px-4 py-3">
                    <p class="mb-2 text-[10px] font-bold uppercase tracking-wide text-gray-400">
                      授权明细 · {{ ds.total }} 条
                    </p>
                    <p v-if="!ds.grants.length" class="text-[11px] text-gray-500">
                      还没有配置任何授权。数据已经同步进来，但在 ChatBI 里检索不到、也查不了。
                    </p>
                    <p
                      v-else-if="ds.alive === 0"
                      class="mb-2 text-[11px] font-semibold text-red-600"
                    >
                      下面 {{ ds.total }} 条授权都已过期，当前没有任何人能用这个数据集；续期其中任意一条即可恢复。
                    </p>
                    <div
                      v-for="(g, idx) in ds.grants"
                      :key="idx"
                      class="mb-1.5 flex flex-wrap items-center gap-2 rounded-lg border border-gray-200 bg-white px-2.5 py-1.5"
                      :class="g.expired ? 'opacity-60' : ''"
                    >
                      <span
                        class="inline-flex shrink-0 items-center rounded-md border px-1.5 py-0.5 text-[10px] font-bold"
                        :class="g.subject === 'all' ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
                          : g.subject === 'role' ? 'border-sky-200 bg-sky-50 text-sky-700'
                          : 'border-violet-200 bg-violet-50 text-violet-700'"
                      >{{ g.subject === 'all' ? '全员基线' : g.subject === 'role' ? '角色' : '用户' }}</span>
                      <span class="min-w-[8rem] text-[11.5px] font-semibold text-gray-800">{{ g.who }}</span>
                      <span class="text-[11px] text-gray-500">表：<b class="font-semibold text-gray-700">{{ grantScopeText(ds, g) }}</b></span>
                      <span class="text-[11px] text-gray-500">
                        行级：<b class="font-semibold text-gray-700">{{ rowPolicyText(g.row_policy) }}</b>
                        <code v-if="g.row_policy === 'custom' && g.row_condition" class="ml-1 rounded bg-indigo-50 px-1 py-0.5 font-mono text-[10px] text-indigo-700">
                          {{ g.row_condition }}
                        </code>
                      </span>
                      <span class="ml-auto flex items-center gap-2">
                        <span
                          class="inline-flex items-center rounded-md border px-1.5 py-0.5 text-[10px] font-bold"
                          :class="g.expired ? 'border-gray-200 bg-gray-100 text-gray-500 line-through'
                            : g.expire_at ? 'border-amber-200 bg-amber-50 text-amber-700'
                            : 'border-gray-200 bg-slate-50 text-slate-600'"
                        >{{ grantExpireText(g) }}</span>
                        <button
                          type="button"
                          class="rounded-md border border-gray-200 px-1.5 py-0.5 text-[10px] font-semibold text-gray-500 transition-colors hover:border-emerald-300 hover:text-emerald-700"
                          @click.stop="goToPermission(ds)"
                        >配置</button>
                      </span>
                    </div>
                  </td>
                </tr>
              </template>

              <tr v-if="!datasets.length">
                <td colspan="7" class="px-4 py-16 text-center text-xs text-gray-400">
                  没有符合条件的数据集
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <!-- 分页 -->
        <div
          v-if="!loading && total > pageSize"
          class="flex items-center justify-between border-t border-gray-100 px-4 py-2.5 text-[11px] text-gray-500"
        >
          <span>共 {{ total }} 个数据集 · 第 {{ page }} / {{ totalPages }} 页</span>
          <div class="flex items-center gap-1.5">
            <button
              type="button"
              class="rounded-lg border border-gray-200 px-2 py-1 font-semibold transition-colors hover:bg-gray-50 disabled:opacity-40"
              :disabled="page <= 1"
              @click="page -= 1"
            >上一页</button>
            <button
              type="button"
              class="rounded-lg border border-gray-200 px-2 py-1 font-semibold transition-colors hover:bg-gray-50 disabled:opacity-40"
              :disabled="page >= totalPages"
              @click="page += 1"
            >下一页</button>
          </div>
        </div>
      </div>

      <!-- 口径说明 -->
      <div class="grid gap-2.5 md:grid-cols-3">
        <div class="rounded-xl border border-gray-200 bg-white px-3.5 py-3">
          <p class="text-[11.5px] font-bold text-gray-800">「无人可访问」＝谁都进不来</p>
          <p class="mt-1 text-[11px] leading-relaxed text-gray-500">
            不是「还没配」这么轻：这个数据集在 ChatBI 里检索不到、也查不了。数据同步进来但没授权，等于白导。
          </p>
        </div>
        <div class="rounded-xl border border-gray-200 bg-white px-3.5 py-3">
          <p class="text-[11.5px] font-bold text-gray-800">「收窄」按条计</p>
          <p class="mt-1 text-[11px] leading-relaxed text-gray-500">
            一条授权收窄到指定表就记一次。同一个数据集里既有全员基线（全部表）又有个人收窄时，两边分别计数，不会互相抵消。
          </p>
        </div>
        <div class="rounded-xl border border-gray-200 bg-white px-3.5 py-3">
          <p class="text-[11.5px] font-bold text-gray-800">过期不等于删除</p>
          <p class="mt-1 text-[11px] leading-relaxed text-gray-500">
            到期后该条授权不再参与判定，但记录保留——续期时改个日期即可，不用重配表范围和行级条件。
          </p>
        </div>
      </div>
    </template>
  </div>
</template>
