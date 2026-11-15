<script setup lang="ts">
/**
 * 管理员站内信群发面板（仅 admin 可见，入口在「系统配置与诊断 → 系统诊断」二级栏）。
 *
 * 三个必须守住的交互约束：
 * 1. 收件人只提交「范围」（用户 / 部门 / 角色），最终名单由服务端解析；
 * 2. 任何范围或正文改动都会把已预览的命中人数清空，必须重新预览才能发送——
 *    否则管理员看着 300 人点发送，实际可能发给了 500 人；
 * 3. 发送前二次确认弹窗里再次展示人数与标题，群发不可撤回。
 */
import { computed, nextTick, onMounted, ref, watch } from "vue";
import axios from "../../utils/axios";
import DepartmentPicker from "../department/DepartmentPicker.vue";

const userIds = ref<number[]>([]);
const deptIds = ref<number[]>([]);
const roleIds = ref<number[]>([]);

const title = ref("");
const content = ref("");
const level = ref<"info" | "success" | "warning" | "error">("info");

const levelOptions = [
  { value: "info", label: "普通", hint: "蓝色" },
  { value: "success", label: "成功", hint: "绿色" },
  { value: "warning", label: "提醒", hint: "橙色" },
  { value: "error", label: "重要", hint: "红色" },
] as const;

// ---- 用户搜索 ----
const userKeyword = ref("");
const userResults = ref<any[]>([]);
const userSearching = ref(false);
const selectedUsers = ref<Record<number, string>>({});
let searchTimer: ReturnType<typeof setTimeout> | null = null;

const searchUsers = async () => {
  const keyword = userKeyword.value.trim();
  if (!keyword) {
    userResults.value = [];
    return;
  }
  userSearching.value = true;
  try {
    const res = await axios.get("/api/portal/management/users", {
      params: { search: keyword, size: 20, status: 1 },
    });
    userResults.value = res.data?.items || [];
  } catch {
    userResults.value = [];
  } finally {
    userSearching.value = false;
  }
};

const onUserKeywordInput = () => {
  if (searchTimer) clearTimeout(searchTimer);
  searchTimer = setTimeout(searchUsers, 300);
};

const userLabel = (user: any) => `${user.real_name || user.user_name}（${user.user_name}）`;

const toggleUser = (user: any) => {
  const id = Number(user.id);
  const next = [...userIds.value];
  const index = next.indexOf(id);
  if (index >= 0) {
    next.splice(index, 1);
    delete selectedUsers.value[id];
  } else {
    next.push(id);
    selectedUsers.value[id] = userLabel(user);
  }
  userIds.value = next;
};

const removeSelectedUser = (id: number) => {
  userIds.value = userIds.value.filter((item) => item !== id);
  delete selectedUsers.value[id];
};

// ---- 角色 ----
const roles = ref<any[]>([]);
const rolesLoading = ref(false);

const loadRoles = async () => {
  rolesLoading.value = true;
  try {
    const res = await axios.get("/api/portal/roles", { params: { page: 1, size: 200 } });
    roles.value = res.data?.items || [];
  } catch {
    roles.value = [];
  } finally {
    rolesLoading.value = false;
  }
};

// ---- 预览 ----
const preview = ref<{ total: number; sample: any[] } | null>(null);
const previewLoading = ref(false);
const previewError = ref("");

const audiencePayload = () => ({
  user_ids: userIds.value,
  dept_ids: deptIds.value,
  role_ids: roleIds.value,
});
const hasAudience = computed(
  () => userIds.value.length > 0 || deptIds.value.length > 0 || roleIds.value.length > 0,
);
const canPreview = computed(() => hasAudience.value && !previewLoading.value);

const resetPreview = () => {
  preview.value = null;
  previewError.value = "";
};

// 收件人区域折叠：默认展开；折叠后只留一行范围摘要，把版面让给消息正文与历史列表
const audienceCollapsed = ref(false);

// 回填重发时，赋值会触发下面的 watch 清空预览 —— 那会立刻把刚跑出来的预览冲掉。
// 用一个标志位在回填期间抑制重置，回填完再手动跑一次预览。
let suppressPreviewReset = false;

// 范围或正文一变，之前的命中人数就不可信了，必须重新预览
watch([userIds, deptIds, roleIds, title, content, level], () => {
  if (!suppressPreviewReset) resetPreview();
}, { deep: true });

const runPreview = async () => {
  if (!canPreview.value) return;
  previewLoading.value = true;
  previewError.value = "";
  try {
    const res = await axios.post("/api/portal/broadcast/preview", {
      audience: audiencePayload(),
      limit: 10,
    });
    preview.value = res.data?.data || { total: 0, sample: [] };
  } catch (error: any) {
    preview.value = null;
    previewError.value = error.response?.data?.detail || "预览失败，请稍后重试";
  } finally {
    previewLoading.value = false;
  }
};

// ---- 发送 ----
const confirmOpen = ref(false);
const sending = ref(false);
const resultMessage = ref("");
const sendError = ref("");

const audienceSummary = computed(() => {
  const parts: string[] = [];
  if (userIds.value.length) parts.push(`指定用户 ${userIds.value.length} 人`);
  if (deptIds.value.length) parts.push(`部门 ${deptIds.value.length} 个（含下级）`);
  if (roleIds.value.length) parts.push(`角色 ${roleIds.value.length} 个`);
  return parts.join(" + ") || "未选择";
});

const canSend = computed(
  () =>
    hasAudience.value &&
    title.value.trim().length > 0 &&
    content.value.trim().length > 0 &&
    Boolean(preview.value && preview.value.total > 0) &&
    !sending.value,
);

const openConfirm = () => {
  if (!canSend.value) return;
  sendError.value = "";
  confirmOpen.value = true;
};

const doSend = async () => {
  if (!preview.value || sending.value) return;
  sending.value = true;
  sendError.value = "";
  try {
    const res = await axios.post("/api/portal/broadcast/send", {
      audience: audiencePayload(),
      title: title.value.trim(),
      content: content.value.trim(),
      level: level.value,
      confirm_total: preview.value.total,
    });
    resultMessage.value = `已发送 ${res.data?.data?.sent ?? 0} 条站内信`;
    confirmOpen.value = false;
    title.value = "";
    content.value = "";
    resendBaseline.value = null;
    resetPreview();
    // 新的一次群发已经落库，历史列表立刻刷新（第一页就是刚发的这条）
    await loadRecords(1);
  } catch (error: any) {
    const status = error.response?.status;
    const detail = error.response?.data?.detail || "发送失败，请稍后重试";
    if (status === 409) {
      // 命中人数在预览后发生了变化：必须重新预览，不能按旧人数继续发
      resetPreview();
      sendError.value = detail;
      confirmOpen.value = false;
      return;
    }
    sendError.value = detail;
    confirmOpen.value = false;
  } finally {
    sending.value = false;
  }
};

// ---- 历史发送记录 ----
const records = ref<any[]>([]);
const recordsTotal = ref(0);
const recordsPage = ref(1);
const RECORDS_PAGE_SIZE = 10;
const recordsLoading = ref(false);
const recordsError = ref("");
const recordDetail = ref<any | null>(null);
const resendLoadingId = ref<number | null>(null);
// 回填重发时记下当次的历史命中人数，用来在预览结果里对比「历史 vs 现在」
const resendBaseline = ref<{ id: number; recipient_count: number } | null>(null);

const recordsPageCount = computed(() =>
  Math.max(1, Math.ceil(recordsTotal.value / RECORDS_PAGE_SIZE)),
);

const loadRecords = async (page = recordsPage.value) => {
  recordsLoading.value = true;
  recordsError.value = "";
  try {
    const res = await axios.get("/api/portal/broadcast/records", {
      params: { page, size: RECORDS_PAGE_SIZE },
    });
    records.value = res.data?.data?.items || [];
    recordsTotal.value = Number(res.data?.data?.total || 0);
    recordsPage.value = page;
  } catch (error: any) {
    recordsError.value = error.response?.data?.detail || "历史记录加载失败";
  } finally {
    recordsLoading.value = false;
  }
};

/** 范围摘要：历史列表里一行说清「发给了谁的范围」。 */
const audienceLabel = (audience: any) => {
  const parts: string[] = [];
  if (audience?.user_ids?.length) parts.push(`指定 ${audience.user_ids.length} 人`);
  if (audience?.dept_ids?.length) parts.push(`${audience.dept_ids.length} 个部门`);
  if (audience?.role_ids?.length) parts.push(`${audience.role_ids.length} 个角色`);
  return parts.join(" + ") || "未指定范围";
};

const formatTime = (value?: string | null) =>
  value ? new Date(value).toLocaleString("zh-CN") : "";

const openRecordDetail = async (record: any) => {
  try {
    const res = await axios.get(`/api/portal/broadcast/records/${record.id}`);
    recordDetail.value = res.data?.data || null;
  } catch (error: any) {
    recordsError.value = error.response?.data?.detail || "详情加载失败";
  }
};

/**
 * 一键重发 = 把历史输入装回表单 + 自动预览。
 *
 * 刻意不直接调用发送接口：历史记录只存「范围」而不存最终名单，人员可能已经变了；
 * 装回后重新预览，让「历史 128 人 / 现在 130 人」的差异摆在操作者眼前，
 * 再点一次发送确认。
 */
const resendRecord = async (record: any) => {
  if (resendLoadingId.value !== null) return;
  resendLoadingId.value = Number(record.id);
  sendError.value = "";
  try {
    const res = await axios.get(`/api/portal/broadcast/records/${record.id}`);
    const detail = res.data?.data;
    if (!detail) throw new Error("记录不存在");

    suppressPreviewReset = true;
    title.value = detail.title || "";
    content.value = detail.content || "";
    level.value = (detail.level || "info") as typeof level.value;
    userIds.value = [...(detail.audience?.user_ids || [])];
    deptIds.value = [...(detail.audience?.dept_ids || [])];
    roleIds.value = [...(detail.audience?.role_ids || [])];
    // 记录不存名单快照，用户标签只能用 ID 占位；想看到名字就重新搜索勾选
    selectedUsers.value = {};
    audienceCollapsed.value = false;
    resendBaseline.value = {
      id: Number(detail.id),
      recipient_count: Number(detail.recipient_count || 0),
    };
    resultMessage.value = "";
    await nextTick();
    suppressPreviewReset = false;

    await runPreview();
  } catch (error: any) {
    suppressPreviewReset = false;
    sendError.value = error.response?.data?.detail || "回填失败，请稍后重试";
  } finally {
    resendLoadingId.value = null;
  }
};

onMounted(() => {
  loadRoles();
  loadRecords(1);
});
</script>

<template>
  <div class="flex min-h-0 min-w-0 flex-1 flex-col gap-4 overflow-y-auto custom-scrollbar pb-6">
    <!-- 顶部说明 -->
    <div class="rounded-xl border border-gray-200/80 bg-white px-4 py-3 shadow-xs">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <div class="min-w-0">
          <h2 class="text-sm font-black text-gray-800">站内信群发</h2>
          <p class="mt-0.5 text-[11px] leading-relaxed text-gray-500">
            把同一条消息写入多个用户的门户消息中心（右上角铃铛）。
          </p>
        </div>
        <span class="rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-[10px] font-bold text-amber-700">仅管理员可用</span>
      </div>
      <p class="mt-2 rounded-lg bg-gray-50 px-3 py-2 text-[10.5px] leading-relaxed text-gray-500">
        发送后不会推送到钉钉 / 邮件等外部渠道，也不可撤回；站内信按平台策略保留 30 天。
        部门范围自动包含其全部下级部门，同一个人只会收到一条。
      </p>
    </div>

    <!-- 收件人：可折叠，折叠后只留一行范围摘要 -->
    <div class="rounded-xl border border-gray-200/80 bg-white p-4 shadow-xs">
      <div class="flex flex-wrap items-center justify-between gap-2" :class="audienceCollapsed ? '' : 'mb-3'">
        <h3 class="text-xs font-black uppercase tracking-widest text-gray-400">收件人</h3>
        <div class="flex items-center gap-2">
          <span class="text-[11px] font-bold text-gray-600">{{ audienceSummary }}</span>
          <button
            type="button"
            class="rounded-lg border border-gray-200 px-2 py-1 text-[11px] font-bold text-gray-500 hover:bg-gray-50"
            :aria-expanded="!audienceCollapsed"
            @click="audienceCollapsed = !audienceCollapsed"
          >
            {{ audienceCollapsed ? "展开选择" : "收起" }}
          </button>
        </div>
      </div>

      <div v-show="!audienceCollapsed" class="grid gap-3 lg:grid-cols-3">
        <!-- 指定用户 -->
        <div class="rounded-lg border border-gray-200 p-3">
          <p class="mb-2 text-[11px] font-bold text-gray-600">指定用户</p>
          <input
            v-model="userKeyword"
            type="search"
            placeholder="搜索姓名或登录名"
            class="w-full rounded-lg border border-gray-300 px-2.5 py-1.5 text-xs focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary/30"
            @input="onUserKeywordInput"
          />
          <div v-if="userSearching" class="mt-2 text-[10px] text-gray-400">搜索中…</div>
          <div v-else-if="userKeyword.trim() && !userResults.length" class="mt-2 text-[10px] text-gray-400">
            没有匹配的启用账号
          </div>
          <div v-else-if="userResults.length" class="mt-2 max-h-40 space-y-0.5 overflow-y-auto">
            <label
              v-for="user in userResults"
              :key="user.id"
              class="flex cursor-pointer items-center gap-1.5 rounded px-1 py-1 text-[11px] text-gray-700 hover:bg-gray-50"
            >
              <input
                type="checkbox"
                class="h-3.5 w-3.5 rounded border-gray-300"
                :checked="userIds.includes(Number(user.id))"
                @change="toggleUser(user)"
              />
              <span class="truncate">{{ userLabel(user) }}</span>
            </label>
          </div>
          <div v-if="userIds.length" class="mt-2 flex flex-wrap gap-1 border-t border-gray-100 pt-2">
            <button
              v-for="id in userIds"
              :key="id"
              type="button"
              class="inline-flex items-center gap-1 rounded-full bg-gray-100 px-2 py-0.5 text-[10px] font-bold text-gray-600 hover:bg-red-50 hover:text-red-600"
              :title="`移除 ${selectedUsers[id] || id}`"
              @click="removeSelectedUser(id)"
            >
              {{ selectedUsers[id] || `用户 ${id}` }}
              <span aria-hidden="true">×</span>
            </button>
          </div>
        </div>

        <!-- 部门 -->
        <div class="rounded-lg border border-gray-200 p-3">
          <p class="mb-2 text-[11px] font-bold text-gray-600">部门<span class="ml-1 font-normal text-gray-400">（含下级）</span></p>
          <DepartmentPicker v-model="deptIds" />
          <p class="mt-2 text-[10px] leading-relaxed text-gray-400">
            已选 {{ deptIds.length }} 个部门，其下级部门成员会一并命中。
          </p>
        </div>

        <!-- 角色 -->
        <div class="rounded-lg border border-gray-200 p-3">
          <p class="mb-2 text-[11px] font-bold text-gray-600">角色</p>
          <div v-if="rolesLoading" class="text-[10px] text-gray-400">加载中…</div>
          <div v-else-if="!roles.length" class="text-[10px] text-gray-400">暂无业务角色</div>
          <div v-else class="max-h-40 space-y-0.5 overflow-y-auto">
            <label
              v-for="role in roles"
              :key="role.id"
              class="flex cursor-pointer items-center gap-1.5 rounded px-1 py-1 text-[11px] text-gray-700 hover:bg-gray-50"
            >
              <input
                v-model="roleIds"
                type="checkbox"
                :value="Number(role.id)"
                class="h-3.5 w-3.5 rounded border-gray-300"
              />
              <span class="truncate">{{ role.name }}</span>
            </label>
          </div>
        </div>
      </div>
    </div>

    <!-- 消息内容 -->
    <div class="rounded-xl border border-gray-200/80 bg-white p-4 shadow-xs">
      <h3 class="mb-3 text-xs font-black uppercase tracking-widest text-gray-400">消息内容</h3>
      <div class="space-y-3">
        <div>
          <label class="mb-1 block text-[11px] font-bold text-gray-600">标题</label>
          <input
            v-model="title"
            maxlength="200"
            placeholder="例如：平台将于今晚 22:00 进行维护"
            class="w-full rounded-lg border border-gray-300 px-3 py-2 text-xs focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary/30"
          />
        </div>
        <div>
          <label class="mb-1 block text-[11px] font-bold text-gray-600">正文</label>
          <textarea
            v-model="content"
            rows="8"
            maxlength="10000"
            placeholder="支持 Markdown：标题、列表、表格、加粗等"
            class="w-full resize-y rounded-lg border border-gray-300 px-3 py-2 text-xs leading-relaxed focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary/30"
          ></textarea>
          <p class="mt-1 text-[10px] text-gray-400">{{ content.length }} / 10000 · 用户点开铃铛里的消息会看到渲染后的排版</p>
        </div>
        <div>
          <label class="mb-1 block text-[11px] font-bold text-gray-600">级别</label>
          <div class="flex flex-wrap gap-2">
            <label
              v-for="option in levelOptions"
              :key="option.value"
              class="flex cursor-pointer items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-[11px] font-bold"
              :class="level === option.value ? 'border-primary/40 bg-primary/5 text-primary' : 'border-gray-200 text-gray-600'"
            >
              <input v-model="level" type="radio" :value="option.value" class="h-3 w-3" />
              {{ option.label }}
              <span class="font-normal text-gray-400">{{ option.hint }}</span>
            </label>
          </div>
        </div>
      </div>
    </div>

    <!-- 预览与发送 -->
    <div class="rounded-xl border border-gray-200/80 bg-white p-4 shadow-xs">
      <div class="flex flex-wrap items-center gap-2">
        <button
          type="button"
          class="rounded-lg bg-gray-100 px-3 py-2 text-xs font-bold text-gray-600 hover:bg-gray-200 disabled:cursor-not-allowed disabled:opacity-50"
          :disabled="!canPreview"
          @click="runPreview"
        >
          {{ previewLoading ? "解析中…" : "预览命中人数" }}
        </button>
        <button
          type="button"
          class="rounded-lg bg-primary px-3 py-2 text-xs font-bold text-white hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
          :disabled="!canSend"
          @click="openConfirm"
        >
          发送站内信
        </button>
        <span v-if="preview" class="text-[11px] font-bold" :class="preview.total ? 'text-emerald-600' : 'text-amber-600'">
          命中 {{ preview.total }} 个启用账号{{ preview.total ? "" : "（无人可发）" }}
        </span>
        <span v-else class="text-[11px] text-gray-400">改动收件人或正文后需重新预览</span>
        <!-- 回填重发：把「历史人数 vs 现在人数」摆出来，人员变了要看得见 -->
        <span
          v-if="resendBaseline && preview"
          class="rounded-full px-2 py-0.5 text-[10px] font-bold"
          :class="preview.total === resendBaseline.recipient_count ? 'bg-gray-100 text-gray-500' : 'bg-amber-50 text-amber-700'"
        >
          历史命中 {{ resendBaseline.recipient_count }} 人
          <template v-if="preview.total !== resendBaseline.recipient_count">
            → 现在 {{ preview.total }} 人（人员有变动，请确认后再发）
          </template>
        </span>
      </div>

      <p v-if="previewError" class="mt-2 text-[11px] text-red-500">{{ previewError }}</p>
      <p v-if="resultMessage" class="mt-2 text-[11px] font-bold text-emerald-600">{{ resultMessage }}</p>
      <p v-if="sendError && !confirmOpen" class="mt-2 text-[11px] text-red-500">{{ sendError }}</p>

      <div v-if="preview?.sample?.length" class="mt-2 rounded-lg bg-gray-50 px-3 py-2">
        <p class="text-[10px] font-bold text-gray-400">抽样名单（前 {{ preview.sample.length }} 人）</p>
        <p class="mt-1 text-[11px] leading-relaxed text-gray-600">
          <span v-for="(user, index) in preview.sample" :key="user.user_id">
            {{ user.real_name || user.user_name }}<span v-if="index < preview.sample.length - 1">、</span>
          </span>
        </p>
      </div>
    </div>

    <!-- 历史发送记录 -->
    <div class="rounded-xl border border-gray-200/80 bg-white p-4 shadow-xs">
      <div class="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 class="text-xs font-black uppercase tracking-widest text-gray-400">历史发送</h3>
        <div class="flex items-center gap-2">
          <span class="text-[11px] text-gray-400">共 {{ recordsTotal }} 次</span>
          <button
            type="button"
            class="rounded-lg border border-gray-200 px-2 py-1 text-[11px] font-bold text-gray-500 hover:bg-gray-50 disabled:opacity-50"
            :disabled="recordsLoading"
            @click="loadRecords(recordsPage)"
          >{{ recordsLoading ? "加载中…" : "刷新" }}</button>
        </div>
      </div>

      <p v-if="recordsError" class="text-[11px] text-red-500">{{ recordsError }}</p>
      <p v-else-if="recordsLoading && !records.length" class="text-[11px] text-gray-400">加载中…</p>
      <p v-else-if="!records.length" class="text-[11px] text-gray-400">还没有群发记录。</p>
      <div v-else class="space-y-1.5">
        <div
          v-for="record in records"
          :key="record.id"
          class="flex flex-wrap items-center gap-2 rounded-lg border border-gray-100 px-3 py-2"
        >
          <div class="min-w-0 flex-1">
            <p class="truncate text-[12px] font-bold text-gray-700">{{ record.title }}</p>
            <p class="mt-0.5 truncate text-[10.5px] text-gray-400">
              {{ formatTime(record.created_at) }} · {{ audienceLabel(record.audience) }} ·
              命中 {{ record.recipient_count }} 人 ·
              {{ record.sender_real_name || record.sender_user_name || "管理员" }}
            </p>
          </div>
          <button
            type="button"
            class="rounded-lg border border-gray-200 px-2 py-1 text-[11px] font-bold text-gray-500 hover:bg-gray-50"
            @click="openRecordDetail(record)"
          >查看</button>
          <button
            type="button"
            class="rounded-lg bg-gray-100 px-2 py-1 text-[11px] font-bold text-gray-600 hover:bg-gray-200 disabled:opacity-50"
            :disabled="resendLoadingId !== null"
            @click="resendRecord(record)"
          >{{ resendLoadingId === Number(record.id) ? "准备中…" : "重发" }}</button>
        </div>
      </div>

      <div v-if="recordsTotal > RECORDS_PAGE_SIZE" class="mt-3 flex items-center justify-end gap-2 text-[11px] text-gray-500">
        <button
          type="button"
          class="rounded-lg border border-gray-200 px-2 py-1 font-bold disabled:opacity-40"
          :disabled="recordsPage <= 1 || recordsLoading"
          @click="loadRecords(recordsPage - 1)"
        >上一页</button>
        <span>{{ recordsPage }} / {{ recordsPageCount }}</span>
        <button
          type="button"
          class="rounded-lg border border-gray-200 px-2 py-1 font-bold disabled:opacity-40"
          :disabled="recordsPage >= recordsPageCount || recordsLoading"
          @click="loadRecords(recordsPage + 1)"
        >下一页</button>
      </div>
    </div>

    <!-- 记录详情 -->
    <teleport to="body">
      <div
        v-if="recordDetail"
        class="fixed inset-0 z-[280] flex items-center justify-center bg-black/35 p-4 backdrop-blur-[1px]"
        @click.self="recordDetail = null"
      >
        <div class="w-full max-w-lg rounded-2xl bg-white p-5 shadow-2xl">
          <div class="flex items-start justify-between gap-3">
            <h3 class="min-w-0 text-sm font-black text-gray-800">{{ recordDetail.title }}</h3>
            <button
              type="button"
              class="shrink-0 rounded-full p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-600"
              aria-label="关闭"
              @click="recordDetail = null"
            >✕</button>
          </div>
          <p class="mt-1 text-[11px] text-gray-400">
            {{ formatTime(recordDetail.created_at) }} · {{ audienceLabel(recordDetail.audience) }} ·
            命中 {{ recordDetail.recipient_count }} 人 ·
            {{ recordDetail.sender_real_name || recordDetail.sender_user_name || "管理员" }}
          </p>
          <pre class="mt-3 max-h-64 overflow-y-auto whitespace-pre-wrap rounded-lg bg-gray-50 px-3 py-2 text-[11.5px] leading-relaxed text-gray-700">{{ recordDetail.content }}</pre>
          <div class="mt-4 flex justify-end gap-2">
            <button
              type="button"
              class="rounded-lg border px-3 py-2 text-xs font-bold text-gray-600"
              @click="recordDetail = null"
            >关闭</button>
            <button
              type="button"
              class="rounded-lg bg-primary px-3 py-2 text-xs font-bold text-white"
              @click="resendRecord(recordDetail); recordDetail = null"
            >回填重发</button>
          </div>
        </div>
      </div>
    </teleport>

    <!-- 二次确认 -->
    <teleport to="body">
      <div
        v-if="confirmOpen"
        class="fixed inset-0 z-[280] flex items-center justify-center bg-black/35 p-4 backdrop-blur-[1px]"
        @click.self="confirmOpen = false"
      >
        <div class="w-full max-w-md rounded-2xl bg-white p-5 shadow-2xl">
          <h3 class="text-sm font-black text-gray-800">确认发送站内信？</h3>
          <p class="mt-2 text-xs leading-relaxed text-gray-500">
            将向 <b class="text-gray-800">{{ preview?.total ?? 0 }}</b> 个启用账号写入这条消息，发送后不可撤回。
          </p>
          <div class="mt-3 space-y-1 rounded-lg bg-gray-50 px-3 py-2 text-[11px] text-gray-600">
            <p><span class="text-gray-400">范围：</span>{{ audienceSummary }}</p>
            <p class="truncate"><span class="text-gray-400">标题：</span>{{ title.trim() }}</p>
          </div>
          <p v-if="sendError" class="mt-2 text-[11px] text-red-500">{{ sendError }}</p>
          <div class="mt-4 flex justify-end gap-2">
            <button
              type="button"
              class="rounded-lg border px-3 py-2 text-xs font-bold text-gray-600 disabled:opacity-50"
              :disabled="sending"
              @click="confirmOpen = false"
            >取消</button>
            <button
              type="button"
              class="rounded-lg bg-primary px-3 py-2 text-xs font-bold text-white disabled:opacity-60"
              :disabled="sending"
              @click="doSend"
            >{{ sending ? "发送中…" : "确认发送" }}</button>
          </div>
        </div>
      </div>
    </teleport>
  </div>
</template>
