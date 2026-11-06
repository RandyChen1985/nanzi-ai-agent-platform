<script setup lang="ts">
/**
 * 知识库「编辑权限范围」设置弹窗。
 *
 * 控制「能打开这个库的人是否也能改文档」，与管理权严格分开：拿到编辑权的人
 * 改不了可见范围、授权名单，也删不了库。
 *
 * 默认值必须与策略化之前的行为一致（public → 只读、team → 部门成员可编辑），
 * 所以这里不给「默认」任何额外提示，只是把当前库的既有策略回显出来。
 */
import { computed, ref, watch } from "vue";
import axios from "@/utils/axios";
import Modal from "@/components/Modal.vue";

interface EditorItem {
  type: "user";
  user_id: number;
  user_name: string;
  real_name?: string;
}

const props = withDefaults(
  defineProps<{
    show: boolean;
    dataset: any;
  }>(),
  {},
);

const emit = defineEmits<{
  (e: "close"): void;
  (e: "saved", policy: any): void;
}>();

const MODE_LABELS: Record<string, string> = {
  readonly: "只读",
  granted: "仅被授权的人可编辑",
  all_viewers: "所有可见者都可编辑",
  team_members: "本部门成员可编辑",
  selected: "指定成员可编辑",
};

const visibility = computed(() => {
  const raw = String(props.dataset?.visibility || "public").toLowerCase();
  return raw === "team" || raw === "private" ? raw : "public";
});

const isPrivate = computed(() => visibility.value === "private");

/** 选项按可见范围裁剪：TEAM 库没有「被授权者/所有可见者」这两档 */
const modes = computed(() => {
  if (visibility.value === "team") {
    return [
      { key: "readonly", title: "只读", desc: "部门成员只能查看和检索，只有归属人与管理员能改文档。" },
      { key: "team_members", title: "本部门成员可编辑", desc: "归属部门及其下级部门的成员可以上传、删除、解析文档，但改不了库设置。" },
      { key: "selected", title: "指定成员可编辑", desc: "只有下面选中的人可以编辑，部门成员回到只读。" },
    ];
  }
  return [
    { key: "readonly", title: "只读", desc: "被授权的人只能查看和检索，不能上传或删除文档。" },
    { key: "granted", title: "仅被授权的人可编辑", desc: "在「授权访问」里被显式加进来的人可以编辑；因系统默认库而可见的人不算。" },
    { key: "all_viewers", title: "所有可见者都可编辑", desc: "凡是能打开本库的人都能编辑，含系统默认库带来的可见者。" },
    { key: "selected", title: "指定成员可编辑", desc: "只有下面选中的人可以编辑，其他人仍然只读。" },
  ];
});

const mode = ref<string>("readonly");
/**
 * 候选用户自己拉：/metadata/candidates 只认 metadata:edit 或 user:edit，
 * 而能配置编辑策略的人只需要 element:knowledge:edit + 库管理权，复用会拿到空列表。
 */
const users = ref<any[]>([]);
const loadingUsers = ref(false);
const editors = ref<EditorItem[]>([]);
const keyword = ref("");
const saving = ref(false);
const errorMessage = ref("");

/** 打开时回显当前策略：没有策略就按可见范围的默认值显示 */
watch(
  () => [props.show, props.dataset?.ragflow_dataset_id, props.dataset?.id],
  () => {
    if (!props.show) return;
    const policy = props.dataset?.edit_policy || {};
    const fallback = visibility.value === "team" ? "team_members" : "readonly";
    mode.value = String(policy.mode || fallback);
    editors.value = Array.isArray(policy.editors)
      ? policy.editors
          .filter((item: any) => item && item.type !== "role" && item.user_id)
          .map((item: any) => ({
            type: "user" as const,
            user_id: Number(item.user_id),
            user_name: String(item.user_name || ""),
            real_name: String(item.real_name || ""),
          }))
      : [];
    keyword.value = "";
    errorMessage.value = "";
    void loadUsers();
  },
  { immediate: true },
);

const loadUsers = async () => {
  if (users.value.length || loadingUsers.value) return;
  const datasetId = props.dataset?.ragflow_dataset_id || props.dataset?.id;
  if (!datasetId) return;
  loadingUsers.value = true;
  try {
    const res = await axios.get(
      `/api/portal/ragflow/datasets/${encodeURIComponent(String(datasetId))}/edit-policy/candidates`,
    );
    users.value = Array.isArray(res?.data?.data?.users) ? res.data.data.users : [];
  } catch {
    // 拉不到就只剩搜索框提示，不阻塞其他策略的保存
    users.value = [];
  } finally {
    loadingUsers.value = false;
  }
};

const filteredUsers = computed(() => {
  const picked = new Set(editors.value.map((item) => item.user_id));
  const q = keyword.value.trim().toLowerCase();
  return (users.value || [])
    .filter((user) => user && user.id != null && !picked.has(Number(user.id)))
    .filter((user) => {
      if (!q) return true;
      return (
        String(user.real_name || "").toLowerCase().includes(q) ||
        String(user.user_name || "").toLowerCase().includes(q) ||
        (user.departments || []).some((dept: any) => String(dept?.name || "").toLowerCase().includes(q))
      );
    })
    .slice(0, 50);
});

const editorLabel = (item: EditorItem) => item.real_name || item.user_name || `用户 ${item.user_id}`;

const addEditor = (user: any) => {
  editors.value = [
    ...editors.value,
    {
      type: "user",
      user_id: Number(user.id),
      user_name: String(user.user_name || ""),
      real_name: String(user.real_name || ""),
    },
  ];
};

const removeEditor = (userId: number) => {
  editors.value = editors.value.filter((item) => item.user_id !== userId);
};

const submit = async () => {
  if (saving.value) return;
  if (mode.value === "selected" && !editors.value.length) {
    errorMessage.value = "「指定成员可编辑」至少要选择一个人";
    return;
  }
  const datasetId = props.dataset?.ragflow_dataset_id || props.dataset?.id;
  if (!datasetId) return;
  saving.value = true;
  errorMessage.value = "";
  try {
    const res = await axios.put(`/api/portal/ragflow/datasets/${encodeURIComponent(String(datasetId))}/edit-policy`, {
      mode: mode.value,
      editors: mode.value === "selected" ? editors.value : [],
    });
    emit("saved", res?.data?.data?.edit_policy || { mode: mode.value, editors: editors.value });
    emit("close");
  } catch (err: any) {
    // 5xx 只给通用文案：内部异常信息（如 AttributeError）不该展示给使用者，
    // 4xx 的 detail 是面向前端写的校验说明，可以直接显示。
    const status = Number(err?.response?.status || 0);
    errorMessage.value =
      status >= 500
        ? "保存失败，请稍后重试"
        : String(err?.response?.data?.detail || "保存失败，请稍后重试");
  } finally {
    saving.value = false;
  }
};
</script>

<template>
  <Modal :show="show" title="设置编辑权限范围" size="max-w-lg" @close="emit('close')">
    <div class="space-y-4">
      <p class="text-[11.5px] leading-relaxed text-gray-500">
        控制<strong>能打开这个库的人</strong>是否也能改里面的文档。改库设置、分配授权、删库仍然只有归属人与管理员能做。
      </p>

      <div v-if="isPrivate" class="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-[11.5px] leading-relaxed text-amber-700">
        个人私有库只有归属人可见，没有「别人」可以授权编辑，因此不支持设置编辑权限范围。
      </div>

      <template v-else>
        <div class="space-y-2">
          <button
            v-for="item in modes"
            :key="item.key"
            type="button"
            class="flex w-full items-start gap-2.5 rounded-xl border px-3 py-2.5 text-left transition-colors"
            :class="mode === item.key ? 'border-primary/40 bg-primary/5' : 'border-gray-200 hover:bg-gray-50'"
            @click="mode = item.key"
          >
            <span
              class="mt-0.5 h-3.5 w-3.5 shrink-0 rounded-full border-2"
              :class="mode === item.key ? 'border-primary bg-primary ring-2 ring-white ring-inset' : 'border-gray-300'"
            ></span>
            <span class="min-w-0">
              <b class="block text-xs text-gray-800">{{ item.title }}</b>
              <small class="mt-0.5 block text-[11px] leading-relaxed text-gray-500">{{ item.desc }}</small>
            </span>
          </button>
        </div>

        <div v-if="mode === 'selected'" class="rounded-xl border border-dashed border-gray-200 px-3 py-2.5">
          <div class="mb-1.5 text-[11px] font-semibold text-gray-400">
            可编辑的成员<span class="ml-1 font-normal text-gray-300">只支持选具体的人，不支持角色</span>
          </div>
          <p class="-mt-1 mb-1.5 text-[10.5px] leading-relaxed text-gray-400">
            只对<strong>本来就能打开这个库</strong>的人生效：编辑权不会新增可见性，选了看不见本库的人也不会生效。
          </p>
          <div v-if="editors.length" class="mb-2 flex flex-wrap gap-1.5">
            <span
              v-for="item in editors"
              :key="item.user_id"
              class="inline-flex items-center gap-1 rounded-md bg-primary/10 px-1.5 py-0.5 text-[11px] font-semibold text-primary"
            >
              {{ editorLabel(item) }}
              <button type="button" class="text-primary/70 hover:text-primary" @click="removeEditor(item.user_id)">
                <svg class="h-2.5 w-2.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6">
                  <path d="M6 6l12 12M18 6L6 18" stroke-linecap="round" />
                </svg>
              </button>
            </span>
          </div>
          <input
            v-model="keyword"
            type="search"
            placeholder="搜索用户姓名、账号或部门…"
            class="mb-2 w-full rounded-lg border border-gray-200 bg-gray-50 px-2.5 py-1.5 text-[11.5px] focus:border-primary focus:bg-white focus:outline-none"
          />
          <div class="max-h-[22vh] overflow-y-auto divide-y divide-gray-100">
            <button
              v-for="user in filteredUsers"
              :key="user.id"
              type="button"
              class="flex w-full items-center justify-between gap-2 px-1.5 py-1.5 text-left text-[11.5px] text-gray-600 hover:bg-gray-50"
              @click="addEditor(user)"
            >
              <span class="truncate">{{ user.real_name || user.user_name }}</span>
              <span class="shrink-0 text-[10px] text-gray-400">{{ user.user_name }}</span>
            </button>
            <p v-if="loadingUsers" class="py-4 text-center text-[11px] text-gray-400">正在加载用户…</p>
          <p v-else-if="!filteredUsers.length" class="py-4 text-center text-[11px] text-gray-400">没有可添加的用户</p>
          </div>
        </div>

        <p
          v-if="errorMessage"
          class="rounded-lg border border-rose-200 bg-rose-50 px-2.5 py-2 text-[11.5px] text-rose-600"
        >{{ errorMessage }}</p>
      </template>
    </div>

    <template #footer>
      <div class="flex items-center justify-end gap-2">
        <button
          type="button"
          class="rounded-lg border border-gray-200 px-3 py-1.5 text-xs font-semibold text-gray-600 hover:bg-gray-50"
          @click="emit('close')"
        >取消</button>
        <button
          type="button"
          class="rounded-lg bg-primary px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-60"
          :disabled="saving || isPrivate"
          @click="submit"
        >{{ saving ? '保存中…' : '保存' }}</button>
      </div>
    </template>
  </Modal>
</template>
