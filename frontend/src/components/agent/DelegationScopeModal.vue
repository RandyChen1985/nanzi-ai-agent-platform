<script setup lang="ts">
/**
 * 调度白名单配置：限制某个智能体通过 sub_agent_call / sub_agent_batch_call
 * 可以委派哪些子智能体。
 *
 * 语义（与后端 `resolve_delegation_allowlist` 严格一致）：
 * - 未配置（undefined）= 系统级全量、自建不可委派（历史行为）；
 * - 空数组 = 显式禁止委派任何子智能体；
 * - 非空 = 只允许名单内，系统级仍要过 agent 授权、自建仍要过本人创建。
 *
 * 因此「清空」不是「恢复默认」，必须二次确认，避免用户误以为回到全量。
 *
 * 层级：本弹窗由「智能体版本抽屉」内部触发，而抽屉是 `z-[9990]`、它自带的小弹窗
 * 是 `z-[10000]`。这里必须压过它们，否则弹窗会渲染到抽屉后面的页面上
 * （用户实测反馈「调度配置显示到后面去了」）。用内联 style 传值，避免依赖
 * Tailwind 是否为任意值类生成样式。
 */
import { computed, ref, watch } from "vue";
import Modal from "../Modal.vue";

/** 必须高于 `AgentVersionEditorDrawer` 的 z-[9990] 与它内置弹窗的 z-[10000]。 */
const DELEGATION_SCOPE_Z_INDEX = 10010;

export interface DelegationScopeAgent {
  id: string;
  name: string;
  display_name?: string | null;
  description?: string | null;
  capabilities?: string[] | null;
  is_system?: boolean | null;
  is_enabled?: boolean | null;
  created_by?: string | null;
  /** 专家团主专家：它需要再委派一层才能用到团队，而委派深度上限是 1，所以不能直接选 */
  is_expert_team_lead?: boolean | null;
  team_name?: string | null;
  team_member_ids?: string[] | null;
}

type ScopeTab = "system" | "mine" | "stale";

const props = defineProps<{
  model: boolean;
  toolName: string;
  selectedIds: string[];
  agents: DelegationScopeAgent[];
  /** 当前用户被授予的 agent 权限 id；null 表示拿不到权限信息，此时不标灰。 */
  authorizedAgentIds?: string[] | null;
  readonly?: boolean;
}>();

const emit = defineEmits<{
  (e: "update:model", value: boolean): void;
  (e: "save", ids: string[]): void;
}>();

const localSelected = ref<string[]>([]);
const keyword = ref("");
const onlySelected = ref(false);
const showClearConfirm = ref(false);
const activeTab = ref<ScopeTab>("system");

const knownIds = computed(() => new Set(props.agents.map((a) => a.id)));

/** 名单里有、但智能体已经被删除的 id：保留配置但明确标失效。 */
const missingIds = computed(() => localSelected.value.filter((id) => !knownIds.value.has(id)));

const isAuthorized = (agent: DelegationScopeAgent): boolean => {
  if (props.authorizedAgentIds == null) return true;
  if (agent.is_system === false) return true; // 自建只看归属，不看 agent 授权表
  return props.authorizedAgentIds.includes(agent.id);
};

const systemAgents = computed(() =>
  props.agents.filter((a) => a.is_system !== false && a.is_enabled !== false),
);
const myAgents = computed(() =>
  props.agents.filter((a) => a.is_system === false && a.is_enabled !== false),
);

const matchKeyword = (agent: DelegationScopeAgent): boolean => {
  if (!keyword.value) return true;
  const q = keyword.value.toLowerCase();
  return (
    agent.name.toLowerCase().includes(q) ||
    (agent.display_name || "").toLowerCase().includes(q) ||
    (agent.description || "").toLowerCase().includes(q) ||
    (agent.capabilities || []).some((c) => String(c).toLowerCase().includes(q))
  );
};

const visibleSystem = computed(() =>
  systemAgents.value.filter((a) => matchKeyword(a) && (!onlySelected.value || localSelected.value.includes(a.id))),
);
const visibleMine = computed(() =>
  myAgents.value.filter((a) => matchKeyword(a) && (!onlySelected.value || localSelected.value.includes(a.id))),
);

/** tab 上的数字跟随搜索条件：搜「知识库」时两个 tab 直接告诉你去哪儿看。 */
const tabs = computed(() => {
  const list: Array<{ key: ScopeTab; label: string; count: number; danger?: boolean }> = [
    { key: "system", label: "系统智能体", count: visibleSystem.value.length },
    { key: "mine", label: "我的智能体", count: visibleMine.value.length },
  ];
  if (missingIds.value.length) {
    list.push({ key: "stale", label: "已失效", count: missingIds.value.length, danger: true });
  }
  return list;
});

const currentList = computed(() => {
  if (activeTab.value === "mine") return visibleMine.value;
  if (activeTab.value === "system") return visibleSystem.value;
  return [];
});

/** 打开时落到第一个有内容的 tab，避免一进来就是空列表。 */
const pickDefaultTab = () => {
  if (visibleSystem.value.length) {
    activeTab.value = "system";
  } else if (visibleMine.value.length) {
    activeTab.value = "mine";
  } else {
    activeTab.value = missingIds.value.length ? "stale" : "system";
  }
};

watch(
  () => props.model,
  (open) => {
    if (!open) {
      showClearConfirm.value = false;
      return;
    }
    localSelected.value = [...(props.selectedIds || [])];
    keyword.value = "";
    onlySelected.value = false;
    pickDefaultTab();
  },
  { immediate: true },
);

// 搜索把当前 tab 过滤空了、另一个 tab 有命中时自动切过去，免得用户以为没结果。
watch([keyword, onlySelected], () => {
  if (currentList.value.length) return;
  if (!visibleSystem.value.length && !visibleMine.value.length && missingIds.value.length) {
    activeTab.value = "stale";
    return;
  }
  if (activeTab.value === "system" && visibleMine.value.length) activeTab.value = "mine";
  else if (activeTab.value === "mine" && visibleSystem.value.length) activeTab.value = "system";
});

/**
 * 团主专家**可以**选进来，但要看清两件事：
 *
 * 1. 它在自己的团里是主专家，被委派进来时深度已经是 1（MAX_DELEGATION_DEPTH = 1），
 *    这一轮只能用**个人能力**，不会再调度它自己的团成员——提示词那一侧会按深度改口，
 *    不会再推它去撞 depth_exceeded。
 * 2. 这是合法用法，不是错误：不少智能体本身就带一个团，同时又是别处需要的专才，
 *    「当子专家用」和「当团用」只是两种取用方式。
 *
 * 真正想要的是**那个团的能力**时，才该改用成员——见下面的 adoptTeamMembers 快捷操作。
 */
const isTeamLead = (agent: DelegationScopeAgent) => agent.is_expert_team_lead === true;
const isSelectable = (agent: DelegationScopeAgent) => isAuthorized(agent);

const teamMembersOf = (agent: DelegationScopeAgent) =>
  (agent.team_member_ids || [])
    .map((id) => props.agents.find((item) => item.id === id))
    .filter((item): item is DelegationScopeAgent => Boolean(item));

const adoptTeamMembers = (agent: DelegationScopeAgent) => {
  if (props.readonly) return;
  const ids = teamMembersOf(agent)
    .filter((member) => isAuthorized(member))
    .map((member) => member.id);
  if (!ids.length) return;
  // 快照，不是引用：以后主专家改了成员，这里不会自动同步
  const next = new Set(localSelected.value);
  ids.forEach((id) => next.add(id));
  localSelected.value = Array.from(next);
};

const toggle = (agent: DelegationScopeAgent) => {
  if (props.readonly) return;
  if (!isSelectable(agent)) return;
  const idx = localSelected.value.indexOf(agent.id);
  if (idx >= 0) {
    localSelected.value.splice(idx, 1);
  } else {
    localSelected.value.push(agent.id);
  }
};

const clearAll = () => {
  localSelected.value = [];
  showClearConfirm.value = false;
};

const submit = () => {
  emit("save", [...localSelected.value]);
  emit("update:model", false);
};

const close = () => emit("update:model", false);

const displayName = (agent: DelegationScopeAgent) => agent.display_name || agent.name;
</script>

<template>
  <Modal
    :show="model"
    title="调度白名单"
    size="max-w-3xl"
    :z-index="DELEGATION_SCOPE_Z_INDEX"
    @close="close"
    @update:model-value="close"
  >
    <template #header-extra>
      <code class="rounded border border-gray-200 bg-gray-50 px-1.5 py-0.5 text-[11px] text-gray-600">
        {{ toolName }}
      </code>
    </template>

    <div class="-mt-2">
      <p class="text-xs leading-relaxed text-gray-500">
        限制本智能体可以委派的子智能体范围。不配置则维持默认，配置后名单外的智能体一律拒绝。
      </p>

      <!-- 规则提示 -->
      <div class="mt-3 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-xs leading-relaxed text-gray-600">
        <span class="font-bold text-gray-800">未配置</span> = 全部「已授权给你的系统智能体」；
        <span class="font-bold text-gray-800">配置后</span> = 只从名单中调度。<br />
        你自己的智能体默认<span class="font-semibold text-amber-600">不可被委派</span>，加入名单后才开通；
        系统智能体即使进名单，仍要校验你的 agent 授权。<br />
        该名单同时作用于<code class="rounded bg-white px-1">sub_agent_batch_call</code>，批量委派不单独配置。
      </div>

      <!-- 搜索 + 只看已选：容器画边框，内部控件必须 border-0 p-0
           （全局 input 带 1px 边框与 padding，否则会出现双线 + 高度撑开） -->
      <div class="mt-3 flex items-center gap-2">
        <div
          class="flex h-9 min-w-0 flex-1 items-center gap-2 rounded-lg border border-gray-200 bg-white px-3 transition focus-within:border-primary focus-within:ring-2 focus-within:ring-primary/10"
        >
          <svg class="h-3.5 w-3.5 shrink-0 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-4.35-4.35M17 11a6 6 0 11-12 0 6 6 0 0112 0z" />
          </svg>
          <input
            v-model="keyword"
            type="search"
            placeholder="搜索名称、标识或能力…"
            class="h-full min-w-0 flex-1 border-0 bg-transparent p-0 text-xs text-gray-800 outline-none placeholder:text-gray-400"
          />
          <button
            v-if="keyword"
            type="button"
            class="shrink-0 rounded p-0.5 text-gray-400 transition hover:bg-gray-100 hover:text-gray-600"
            title="清空搜索"
            @click="keyword = ''"
          >
            <svg class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
        <label
          class="flex h-9 shrink-0 cursor-pointer items-center gap-1.5 rounded-lg border px-3 text-xs transition"
          :class="onlySelected
            ? 'border-primary/40 bg-primary/5 text-primary'
            : 'border-gray-200 bg-white text-gray-600 hover:border-gray-300'"
        >
          <input v-model="onlySelected" type="checkbox" class="accent-primary" />
          只看已选
        </label>
      </div>

      <!-- 分组切换 -->
      <div class="mt-3 flex items-center gap-1 border-b border-gray-200">
        <button
          v-for="tab in tabs"
          :key="tab.key"
          type="button"
          class="-mb-px flex items-center gap-1.5 border-b-2 px-3 py-2 text-xs font-medium transition"
          :class="activeTab === tab.key
            ? (tab.danger ? 'border-red-400 text-red-600' : 'border-primary text-primary')
            : (tab.danger ? 'border-transparent text-red-400 hover:text-red-500' : 'border-transparent text-gray-500 hover:text-gray-800')"
          @click="activeTab = tab.key"
        >
          {{ tab.label }}
          <span
            class="rounded-full px-1.5 py-px text-[10px] leading-4"
            :class="activeTab === tab.key
              ? (tab.danger ? 'bg-red-50 text-red-600' : 'bg-primary/10 text-primary')
              : 'bg-gray-100 text-gray-500'"
          >
            {{ tab.count }}
          </span>
        </button>
      </div>

      <div class="mt-1">
        <!-- 我的智能体：默认不可委派的说明放在 tab 内，避免只挂在分组标题上被切走 -->
        <div
          v-if="activeTab === 'mine'"
          class="mt-2 rounded-md bg-amber-50 px-2.5 py-1.5 text-[11px] leading-relaxed text-amber-700"
        >
          你自己的智能体默认不可被委派，勾选后才会开通；系统仍会校验「仅限本人创建」。
        </div>

        <!-- 系统智能体 / 我的智能体 -->
        <div
          v-for="agent in currentList"
          :key="agent.id"
          class="flex cursor-pointer items-start gap-2.5 rounded-lg px-2 py-2 transition hover:bg-gray-50"
          :class="[
            localSelected.includes(agent.id) ? 'bg-primary/5' : '',
            !isSelectable(agent) ? 'cursor-not-allowed opacity-55 hover:bg-transparent' : '',
          ]"
          @click="toggle(agent)"
        >
          <div
            class="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded border"
            :class="localSelected.includes(agent.id) ? 'border-primary bg-primary' : 'border-gray-300 bg-white'"
          >
            <svg
              v-if="localSelected.includes(agent.id)"
              class="h-3.5 w-3.5 text-white"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7" />
            </svg>
          </div>
          <div class="min-w-0 flex-1">
            <div class="flex flex-wrap items-center gap-1.5">
              <span class="text-xs font-semibold text-gray-800">{{ displayName(agent) }}</span>
              <code class="text-[10px] text-gray-400">{{ agent.name }}</code>
              <span
                v-if="agent.is_system === false"
                class="rounded border border-gray-200 bg-gray-50 px-1 text-[10px] text-gray-500"
              >
                我的
              </span>
            </div>
            <div class="mt-0.5 text-[11px] leading-relaxed text-gray-500">{{ agent.description || "无描述" }}</div>
            <button
              v-if="isTeamLead(agent) && isAuthorized(agent)"
              type="button"
              class="mt-1 rounded-md border border-amber-200 bg-amber-50 px-2 py-0.5 text-[10px] font-medium text-amber-700 transition hover:bg-amber-100 disabled:cursor-not-allowed disabled:opacity-60"
              :disabled="readonly || !teamMembersOf(agent).length"
              @click.stop="adoptTeamMembers(agent)"
            >
              {{ teamMembersOf(agent).length ? `改用这 ${teamMembersOf(agent).length} 位成员` : "该团还没有成员" }}
            </button>
            <div v-if="agent.capabilities && agent.capabilities.length" class="mt-1 flex flex-wrap gap-1">
              <span
                v-for="cap in agent.capabilities"
                :key="cap"
                class="rounded border border-gray-200 bg-gray-50 px-1 py-px text-[10px] text-gray-500"
              >
                {{ cap }}
              </span>
            </div>
          </div>
          <span
            v-if="isTeamLead(agent) && isAuthorized(agent)"
            class="shrink-0 whitespace-nowrap rounded-full border border-teal-200 bg-teal-50 px-2 py-0.5 text-[10px] text-teal-700"
            :title="`它在「${agent.team_name || agent.display_name}」里是主专家；被委派进来时深度已到上限，只能用个人能力，不会调度它的团成员。想要整个团的能力请改用成员。`"
          >
            团主 · 仅个人能力
          </span>
          <span
            v-else-if="!isAuthorized(agent)"
            class="shrink-0 whitespace-nowrap rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-[10px] text-amber-700"
          >
            无 agent 授权
          </span>
          <span
            v-else-if="agent.is_system === false && !localSelected.includes(agent.id)"
            class="shrink-0 whitespace-nowrap text-[10px] text-amber-600"
          >
            未加入
          </span>
        </div>

        <!-- 已失效 -->
        <template v-if="activeTab === 'stale'">
          <div v-for="id in missingIds" :key="id" class="flex items-center gap-2.5 rounded-lg px-2 py-2">
            <div class="h-4 w-4 shrink-0 rounded border border-gray-300 bg-gray-100"></div>
            <div class="min-w-0 flex-1 text-xs text-gray-500">
              智能体已被删除或对你不可见（<code class="rounded bg-gray-100 px-1">{{ id }}</code>）
            </div>
            <span class="shrink-0 rounded-full border border-red-200 bg-red-50 px-2 py-0.5 text-[10px] text-red-600">已失效</span>
            <button
              v-if="!readonly"
              type="button"
              class="shrink-0 text-[11px] text-gray-400 hover:text-red-500"
              @click="localSelected = localSelected.filter((x) => x !== id)"
            >
              移除
            </button>
          </div>
        </template>

        <div v-if="!currentList.length && activeTab !== 'stale'" class="py-10 text-center text-xs text-gray-400">
          {{ onlySelected
            ? "这个分组里还没有勾选任何智能体"
            : (keyword ? "没有匹配的智能体" : "这个分组暂无智能体") }}
        </div>
      </div>
    </div>

    <template #footer>
      <div class="flex items-center gap-2">
        <span class="text-xs text-gray-500">
          已选 <b class="text-gray-800">{{ localSelected.length }}</b> 个智能体
        </span>
        <button
          v-if="!readonly"
          type="button"
          class="text-xs text-red-500 transition hover:text-red-600 disabled:cursor-not-allowed disabled:text-gray-300"
          :disabled="!localSelected.length"
          @click="showClearConfirm = true"
        >
          清空
        </button>
        <span class="flex-1"></span>
        <button
          type="button"
          class="rounded-lg border border-gray-200 bg-white px-3.5 py-1.5 text-xs text-gray-600 transition hover:bg-gray-50"
          @click="close"
        >
          取消
        </button>
        <button
          v-if="!readonly"
          type="button"
          class="rounded-lg bg-gray-900 px-3.5 py-1.5 text-xs font-semibold text-white transition hover:bg-gray-800"
          @click="submit"
        >
          保存
        </button>
      </div>
    </template>
  </Modal>

  <!-- 清空二次确认：层级要高于 Modal，否则被压在下面又是一次「显示到后面」 -->
  <Teleport to="body">
    <div
      v-if="model && showClearConfirm"
      class="fixed inset-0 flex items-center justify-center bg-black/40 p-4"
      :style="{ zIndex: DELEGATION_SCOPE_Z_INDEX + 10 }"
      @click.self="showClearConfirm = false"
    >
      <div class="w-full max-w-sm rounded-xl bg-white p-5 shadow-2xl">
        <h4 class="text-sm font-bold text-gray-900">清空调度白名单？</h4>
        <p class="mt-2 text-xs leading-relaxed text-gray-500">
          清空后本智能体<span class="font-semibold text-red-600">将无法委派任何子智能体</span>（工具仍保留，但每次调用都会被拒绝）。<br />
          如果只是想恢复默认范围（系统级全量），请点「取消」，并删除该工具的调度范围配置。
        </p>
        <div class="mt-4 flex justify-end gap-2">
          <button
            type="button"
            class="rounded-lg border border-gray-200 px-3 py-1.5 text-xs text-gray-600"
            @click="showClearConfirm = false"
          >
            取消
          </button>
          <button type="button" class="rounded-lg bg-gray-900 px-3 py-1.5 text-xs font-semibold text-white" @click="clearAll">
            确认清空
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>
